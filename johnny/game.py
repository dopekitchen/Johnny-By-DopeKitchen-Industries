"""Game mode: detect games, see the screen, coach/steer the player, and learn games by watching them played."""
import base64
import ctypes
import ctypes.wintypes as wt
import io
import json
import re
import threading
import time
from collections import Counter

from . import config, watcher

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = mouse = None

user32 = ctypes.windll.user32
GAMES_DIR = config.DATA_DIR / "games"

# Folders games get installed into. Anything running from here counts as a game.
GAME_PATH_HINTS = ("steamapps\\common", "epic games", "riot games", "xboxgames", "gog galaxy\\games", "gog games",
                   "ea games", "ubisoft game launcher\\games", "battle.net", "rockstar games", "\\games\\",
                   "minecraft", "roblox")
# Fullscreen apps that are definitely not games.
NOT_GAMES = {"explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vlc.exe",
             "mpc-hc64.exe", "wmplayer.exe", "powerpnt.exe", "johnny.exe", "python.exe", "pythonw.exe",
             "applicationframehost.exe", "searchhost.exe", "lockapp.exe", "shellexperiencehost.exe",
             "obs64.exe", "discord.exe", "spotify.exe", "code.exe", "windowsterminal.exe", "steam.exe",
             "steamwebhelper.exe", "epicgameslauncher.exe", "textinputhost.exe", "teams.exe", "zoom.exe"}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def window_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def monitor_rect(hwnd):
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    return m.left, m.top, m.right, m.bottom


def is_fullscreen(hwnd):
    if not hwnd or hwnd == user32.GetDesktopWindow() or hwnd == user32.GetShellWindow():
        return False
    l, t, r, b = window_rect(hwnd)
    ml, mt, mr, mb = monitor_rect(hwnd)
    return l <= ml and t <= mt and r >= mr and b >= mb


def exe_path(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        import psutil
        p = psutil.Process(pid.value)
        return p.name(), p.exe()
    except Exception:
        return "", ""


# --------------------------------------------------------------------------- detection
class GameDetector(threading.Thread):
    """Calls on_start(info) when a game takes the foreground and on_end(info) only once the game process exits.

    Games destroy and recreate their window on loading screens / resolution changes and you alt-tab out, so game
    mode is tied to the *process*, not a window handle.
    """

    def __init__(self, cfg, on_start, on_end):
        super().__init__(daemon=True, name="game-detector")
        self.cfg, self.on_start, self.on_end = cfg, on_start, on_end
        self.running = True
        self.current = None

    def classify(self, hwnd):
        exe, path = exe_path(hwnd)
        g = self.cfg["game"]
        low_exe, low_path = exe.lower(), path.lower()
        if not exe or low_exe in NOT_GAMES or low_exe in (x.lower() for x in g["never"]):
            return None
        if low_exe in (x.lower() for x in g["always"]):
            return exe
        if any(h in low_path for h in GAME_PATH_HINTS):
            return exe
        if g["detect_fullscreen"] and is_fullscreen(hwnd):
            return exe
        return None

    def run(self):
        while self.running:
            time.sleep(1.0)
            try:
                hwnd = user32.GetForegroundWindow()
                exe = self.classify(hwnd)
                if exe:
                    pid = window_pid(hwnd)
                    if not self.current or self.current["exe"] != exe:
                        if self.current:
                            self.on_end(self.current)
                        self.current = {"exe": exe, "hwnd": hwnd, "pid": pid, "title": window_title(hwnd),
                                        "since": time.time()}
                        self.on_start(self.current)
                    else:
                        self.current.update(hwnd=hwnd, pid=pid)      # window recreated: just follow it
                elif self.current and not process_alive(self.current["pid"]):
                    ended, self.current = self.current, None
                    self.on_end(ended)
                elif self.current and not user32.IsWindow(self.current["hwnd"]):
                    w = main_window_of(self.current["pid"])           # same game, new window
                    if w:
                        self.current["hwnd"] = w
            except Exception:
                pass


def window_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def process_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return True


def main_window_of(pid):
    """Largest visible top-level window belonging to pid."""
    best, best_area = None, 0
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        nonlocal best, best_area
        if user32.IsWindowVisible(hwnd) and window_pid(hwnd) == pid:
            l, t, r, b = window_rect(hwnd)
            if (r - l) * (b - t) > best_area:
                best, best_area = hwnd, (r - l) * (b - t)
        return True
    user32.EnumWindows(proc(cb), 0)
    return best


def exclusive_fullscreen():
    """True when a Direct3D app owns the screen exclusively - no window (HUD) can be drawn over it."""
    state = ctypes.c_int(0)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:
        return False
    return state.value == 3  # QUNS_RUNNING_D3D_FULL_SCREEN


def make_borderless(hwnd):
    """Turn a windowed game into borderless fullscreen on its monitor (what 'Borderless Gaming' tools do).
    Works when the game runs in Windowed mode; returns a short status string."""
    if not hwnd or not user32.IsWindow(hwnd):
        return "No game window found."
    if exclusive_fullscreen():
        return "The game is in exclusive fullscreen - set it to Windowed in its video settings first, then try again."
    GWL_STYLE, GWL_EXSTYLE = -16, -20
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    style &= ~(0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000)  # caption, frame, min/max, sysmenu
    user32.SetWindowLongW(hwnd, GWL_STYLE, style)
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex & ~(0x00000001 | 0x00000200 | 0x00020000))  # dlg/client/static edges
    ml, mt, mr, mb = monitor_rect(hwnd)
    user32.SetWindowPos(hwnd, 0, ml, mt, mr - ml, mb - mt, 0x0020 | 0x0040)  # FRAMECHANGED | SHOWWINDOW
    return "Done - the game is now borderless fullscreen, so the HUD can stay on top."


# --------------------------------------------------------------------------- vision
def capture(hwnd, max_side=1024):
    """Screenshot the game window -> (jpeg_b64, looks_black). Exclusive-fullscreen games may capture black."""
    if not ImageGrab:
        return None, True
    try:
        box = window_rect(hwnd) if hwnd and user32.IsWindow(hwnd) else None
        img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    except Exception:
        return None, True
    black = max(img.resize((32, 18)).convert("L").getdata()) < 12
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode(), black


# --------------------------------------------------------------------------- per-game knowledge
def _profile_path(exe):
    return GAMES_DIR / f"{watcher.slug(exe.removesuffix('.exe'))}.json"


def load_profile(exe):
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    p = _profile_path(exe)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"exe": exe, "name": "", "notes": [], "controls": {}, "sessions": 0, "minutes": 0.0}


def save_profile(prof):
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    _profile_path(prof["exe"]).write_text(json.dumps(prof, indent=1), encoding="utf-8")


def all_profiles():
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(GAMES_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def profile_context(prof):
    name = prof.get("name") or prof["exe"]
    lines = [f"Game: {name}"]
    if prof.get("controls"):
        lines.append("Controls the player uses most: " + ", ".join(f"{k}" for k in list(prof["controls"])[:12]))
    if prof.get("notes"):
        lines.append("What you've learned about this game and how this player plays:")
        lines += [f"- {n}" for n in prof["notes"][-25:]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- prompts
COACH_PROMPT = """You are {name}, an expert video game coach watching {user} play live. {context}
Look at this screenshot of the game right now. If there is ONE specific, genuinely useful tip for this exact moment
(a threat, an opportunity, a resource running low, a better route, a mechanic they're missing), reply with it in
at most 18 words, spoken style, no preamble. If nothing is worth interrupting for, reply exactly: NONE"""

STEER_PROMPT = """You are {name}, riding along as {user}'s navigator. {user} is driving the controls; you steer.
{context}
From this live screenshot give the single most important instruction for the next few seconds - direction,
target, timing or action - in at most 10 words, imperative ("Go left through the doorway", "Reload now",
"Jump the gap ahead"). Previous call: "{last}". Don't repeat it unless it still applies. If nothing changed, reply: NONE"""

ASK_CONTEXT = """{user} is playing a game right now and the attached image is their current screen. {context}
Answer as a sharp, friendly gaming coach: use what's visible on screen, be specific and brief (2-3 sentences,
it will be spoken aloud) unless they ask for detail."""

IDENTIFY_PROMPT = 'What video game is this screenshot from? The process is "{exe}" and window title "{title}". ' \
                  'Reply with ONLY the game\'s name, or "Unknown".'

LEARN_PROMPT = """You watched {user} play {game} for {minutes:.0f} minutes so you can coach them later.
Key/button usage while playing (most used first): {controls}
The screenshots are moments from the session, in order.
Write 4-8 short, concrete notes worth remembering about this game and how {user} plays it: game mechanics and UI
you can see, their goals/progress, habits, strengths and weaknesses, and controls (e.g. "W/A/S/D move, R reloads").
Reply ONLY as a JSON list of strings."""


class Coach:
    """Talks to the vision model about the current game."""

    def __init__(self, cfg, ollama):
        self.cfg, self.ollama = cfg, ollama
        self.last_call = ""
        self.busy = False

    @property
    def model(self):
        return self.cfg["game"].get("vision_model") or self.cfg["model"]

    def _ask(self, prompt, images, temperature=0.4, max_tokens=120):
        kw = {"think": False}
        msgs = [{"role": "user", "content": prompt, "images": images}]
        try:
            r = self.ollama.chat(self.model, msgs, stream=False, options={"temperature": temperature,
                                                                          "num_predict": max_tokens}, **kw)
        except Exception as e:
            if "think" in str(e).lower():
                r = self.ollama.chat(self.model, msgs, stream=False, options={"temperature": temperature})
            else:
                raise
        txt = r.get("message", {}).get("content", "")
        return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip().strip('"')

    def identify(self, info, img):
        try:
            name = self._ask(IDENTIFY_PROMPT.format(exe=info["exe"], title=info["title"]), [img], 0.1, 20)
            name = name.splitlines()[0].strip(" .")
            return "" if name.lower().startswith("unknown") else name[:60]
        except Exception:
            return ""

    def tip(self, prof, img, mode):
        tmpl = STEER_PROMPT if mode == "steer" else COACH_PROMPT
        txt = self._ask(tmpl.format(name=self.cfg["assistant_name"], user=self.cfg["user_name"],
                                    context=profile_context(prof), last=self.last_call), [img],
                        0.5, 40 if mode == "steer" else 60)
        if not txt or txt.upper().startswith("NONE") or txt == self.last_call:
            return ""
        self.last_call = txt
        return txt

    def learn(self, prof, images, controls, minutes):
        game = prof.get("name") or prof["exe"]
        ctl = ", ".join(f"{k} ({n})" for k, n in controls.most_common(15)) or "not recorded"
        txt = self._ask(LEARN_PROMPT.format(user=self.cfg["user_name"], game=game, minutes=minutes, controls=ctl),
                        images[-8:], 0.3, 500)
        try:
            notes = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
            return [str(n).strip() for n in notes if str(n).strip()][:10]
        except Exception:
            return [line.strip("-• ").strip() for line in txt.splitlines() if len(line.strip()) > 8][:8]


# --------------------------------------------------------------------------- learn-by-playing
class PlaySession:
    """Counts the controls the player uses and keeps periodic screenshots while they play. No keystroke text is kept."""

    MOUSE_NAMES = {"left": "Left click", "right": "Right click", "middle": "Middle click"}

    def __init__(self, hwnd_fn, shot_every=12.0):
        self.hwnd_fn, self.shot_every = hwnd_fn, shot_every
        self.controls = Counter()
        self.images = []
        self.started = time.time()
        self.running = False
        self._held = set()

    def _key(self, k):
        name = getattr(k, "char", None)
        name = name.upper() if name and name.isprintable() else (getattr(k, "name", None) or str(k))
        if name not in self._held:          # count presses, not auto-repeat
            self._held.add(name)
            self.controls[name.replace("_l", "").replace("_r", "").title() if len(name) > 1 else name] += 1

    def _release(self, k):
        name = getattr(k, "char", None)
        name = name.upper() if name and name.isprintable() else (getattr(k, "name", None) or str(k))
        self._held.discard(name)

    def _click(self, x, y, button, pressed):
        if pressed:
            self.controls[self.MOUSE_NAMES.get(button.name, button.name)] += 1

    def _shots(self):
        while self.running:
            img, black = capture(self.hwnd_fn(), 768)
            if img and not black:
                self.images.append(img)
                self.images = self.images[-24:]
            for _ in range(int(self.shot_every * 10)):
                if not self.running:
                    break
                time.sleep(0.1)

    def start(self):
        self.running = True
        if keyboard:
            self._kl = keyboard.Listener(on_press=self._key, on_release=self._release)
            self._ml = mouse.Listener(on_click=self._click)
            self._kl.start()
            self._ml.start()
        threading.Thread(target=self._shots, daemon=True).start()

    def stop(self):
        self.running = False
        for l in (getattr(self, "_kl", None), getattr(self, "_ml", None)):
            if l:
                l.stop()
        return (time.time() - self.started) / 60.0


def merge_notes(prof, notes, controls, minutes):
    seen = {n.lower() for n in prof["notes"]}
    prof["notes"] += [n for n in notes if n.lower() not in seen]
    prof["notes"] = prof["notes"][-60:]
    merged = Counter(prof.get("controls", {}))
    merged.update(controls)
    prof["controls"] = dict(merged.most_common(25))
    prof["sessions"] = prof.get("sessions", 0) + 1
    prof["minutes"] = round(prof.get("minutes", 0) + minutes, 1)
    save_profile(prof)

