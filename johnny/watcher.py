"""Watch-and-learn: passive activity observation, task recording ("teach mode") and replay."""
import ctypes
import ctypes.wintypes as wt
import json
import re
import threading
import time
from pathlib import Path

from . import config

try:
    import psutil
except ImportError:
    psutil = None

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = mouse = None

user32 = ctypes.windll.user32


# --------------------------------------------------------------------------- windows helpers
def foreground_window():
    """Return (hwnd, title, exe_name)."""
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    if psutil:
        try:
            exe = psutil.Process(pid.value).name()
        except Exception:
            pass
    return hwnd, buf.value, exe


def list_windows():
    """[(hwnd, title, exe)] of visible, titled top-level windows."""
    out = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                exe = ""
                if psutil:
                    try:
                        exe = psutil.Process(pid.value).name()
                    except Exception:
                        pass
                out.append((hwnd, buf.value, exe))
        return True

    user32.EnumWindows(proc(cb), 0)
    return out


def focus_window(title_hint="", exe_hint="") -> bool:
    """Bring the best matching window to the front."""
    title_hint, exe_hint = title_hint.lower(), exe_hint.lower()
    best = None
    for hwnd, title, exe in list_windows():
        if exe_hint and exe.lower() == exe_hint:
            if title_hint and title_hint in title.lower():
                best = hwnd
                break
            best = best or hwnd
        elif title_hint and title_hint in title.lower():
            best = best or hwnd
    if not best:
        return False
    user32.ShowWindow(best, 9)  # SW_RESTORE
    # Windows only lets the foreground process steal focus; an Alt tap unlocks it.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(best)
    return True


def is_private(title: str, exe: str, keywords) -> bool:
    t = f"{title} {exe}".lower()
    return any(k.lower() in t for k in keywords)


# --------------------------------------------------------------------------- passive observer
class Observer(threading.Thread):
    """Logs which app/window has focus and for how long."""

    def __init__(self, memory, privacy_keywords, interval=1.0):
        super().__init__(daemon=True, name="observer")
        self.memory, self.keywords, self.interval = memory, privacy_keywords, interval
        self.running = True
        self.paused = False

    def run(self):
        last, since = None, time.time()
        while self.running:
            time.sleep(self.interval)
            if self.paused:
                continue
            try:
                _, title, exe = foreground_window()
            except Exception:
                continue
            if is_private(title, exe, self.keywords):
                title = "[private]"
            cur = (exe, title)
            if cur != last:
                now = time.time()
                if last and last[0] and now - since >= 2:
                    self.memory.log_activity(last[0], last[1], now - since)
                last, since = cur, now


# --------------------------------------------------------------------------- recorder
SPECIAL_KEYS_ALLOWED = {"enter", "tab", "backspace", "delete", "esc", "up", "down", "left", "right",
                        "home", "end", "page_up", "page_down", "space", "f1", "f2", "f3", "f4", "f5",
                        "f6", "f7", "f8", "f9", "f10", "f11", "f12", "ctrl_l", "ctrl_r", "shift", "shift_r",
                        "alt_l", "alt_r", "alt_gr", "cmd", "cmd_r", "caps_lock", "insert", "print_screen"}


def _key_name(k):
    if hasattr(k, "char") and k.char is not None:
        return k.char, True
    name = getattr(k, "name", None) or str(k).replace("Key.", "")
    if name and name.startswith("<") and hasattr(k, "vk"):  # numpad etc.
        return f"vk{k.vk}", False
    return name, False


class Recorder:
    """Records mouse + keyboard into a list of steps. Stop by calling stop() or pressing the stop hotkey."""

    def __init__(self, privacy_keywords, on_stop=None):
        if not keyboard:
            raise RuntimeError("pynput is not installed - re-run the installer.")
        self.keywords = privacy_keywords
        self.on_stop = on_stop
        self.steps = []
        self.t0 = 0.0
        self._last_move = 0.0
        self._mods = set()
        self._kl = self._ml = None
        self.recording = False
        self._last_window = None

    # -- helpers
    def _t(self):
        return round(time.time() - self.t0, 3)

    def _note_window(self):
        try:
            _, title, exe = foreground_window()
        except Exception:
            return False
        private = is_private(title, exe, self.keywords)
        key = (exe, title)
        if key != self._last_window:
            self._last_window = key
            self.steps.append({"t": self._t(), "type": "window", "exe": exe,
                               "title": "[private]" if private else title})
        return private

    # -- callbacks
    def _on_move(self, x, y):
        now = time.time()
        if now - self._last_move > 0.05:
            self._last_move = now
            self.steps.append({"t": self._t(), "type": "move", "x": x, "y": y})

    def _on_click(self, x, y, button, pressed):
        self._note_window()
        self.steps.append({"t": self._t(), "type": "click", "x": x, "y": y,
                           "button": button.name, "pressed": pressed})

    def _on_scroll(self, x, y, dx, dy):
        self.steps.append({"t": self._t(), "type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy})

    def _on_press(self, k):
        private = self._note_window()
        name, is_char = _key_name(k)
        if name in ("ctrl_l", "ctrl_r", "alt_l", "alt_r", "alt_gr", "shift", "shift_r"):
            self._mods.add(name)
        # stop hotkey: ctrl + alt + s  (s arrives as '\x13' when ctrl is held)
        if self._mods & {"ctrl_l", "ctrl_r"} and self._mods & {"alt_l", "alt_r", "alt_gr"} and \
                (name in ("s", "S", "\x13") or getattr(k, "vk", None) == 0x53):
            self.stop()
            return False
        if private and is_char:
            self.steps.append({"t": self._t(), "type": "key", "key": "*", "char": True, "pressed": True, "redacted": True})
            return
        self.steps.append({"t": self._t(), "type": "key", "key": name, "char": is_char, "pressed": True,
                           "vk": getattr(k, "vk", None)})

    def _on_release(self, k):
        name, is_char = _key_name(k)
        self._mods.discard(name)
        self.steps.append({"t": self._t(), "type": "key", "key": name, "char": is_char, "pressed": False,
                           "vk": getattr(k, "vk", None)})

    # -- control
    def start(self):
        self.steps, self.t0, self.recording = [], time.time(), True
        self._last_window = None
        self._note_window()
        self._kl = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._ml = mouse.Listener(on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._kl.start()
        self._ml.start()

    def stop(self):
        if not self.recording:
            return
        self.recording = False
        for l in (self._kl, self._ml):
            if l:
                l.stop()
        # drop the trailing stop-hotkey keystrokes
        while self.steps and self.steps[-1]["type"] == "key" and \
                self.steps[-1]["key"] in ("ctrl_l", "ctrl_r", "alt_l", "alt_r", "alt_gr"):
            self.steps.pop()
        if self.on_stop:
            threading.Thread(target=self.on_stop, args=(compress(self.steps),), daemon=True).start()


def compress(steps):
    """Merge consecutive plain-character key presses into 'type' steps so they're readable and editable."""
    out, buf, buf_t = [], [], 0.0
    held_mods = set()

    def flush():
        nonlocal buf
        if buf:
            out.append({"t": buf_t, "type": "type", "text": "".join(buf)})
            buf = []

    for s in steps:
        if s["type"] == "key":
            if not s["char"]:
                if s["key"] in ("ctrl_l", "ctrl_r", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_r"):
                    (held_mods.add if s["pressed"] else held_mods.discard)(s["key"])
                if s["key"] in ("shift", "shift_r"):
                    continue  # shift is captured in the character itself
                if s["key"] == "space" and not held_mods:
                    if s["pressed"]:
                        if not buf:
                            buf_t = s["t"]
                        buf.append(" ")
                    continue
                flush()
                out.append(s)
            elif s.get("redacted"):
                flush()
                if not out or out[-1].get("type") != "redacted":
                    out.append({"t": s["t"], "type": "redacted"})
            elif held_mods:
                flush()
                # ctrl+c arrives as '\x03'; replay by virtual-key code instead
                if s.get("vk"):
                    s = {**s, "char": False, "key": f"vk{s['vk']}"}
                out.append(s)
            elif s["pressed"]:
                if not buf:
                    buf_t = s["t"]
                buf.append(s["key"])
        else:
            if s["type"] != "move":
                flush()
            out.append(s)
    flush()
    return out


def template_vars(template: str) -> list[str]:
    return re.findall(r"\{(\w+)\}", template or "")


def describe_steps(steps, limit=60):
    """Human/LLM-readable summary of a recording."""
    lines = []
    for s in steps:
        t = s["type"]
        if t == "window":
            lines.append(f"- switched to {s['exe']} '{s['title'][:60]}'")
        elif t == "click" and s["pressed"]:
            lines.append(f"- {s['button']} click at ({s['x']},{s['y']})")
        elif t == "type":
            lines.append(f"- typed \"{s['text'][:80]}\"")
        elif t == "key" and s["pressed"]:
            lines.append(f"- pressed {s['key']}")
        elif t == "scroll":
            lines.append(f"- scrolled {'down' if s['dy'] < 0 else 'up'}")
        elif t == "redacted":
            lines.append("- typed something private (not recorded)")
    if len(lines) > limit:
        lines = lines[:limit // 2] + [f"... ({len(lines) - limit} more steps) ..."] + lines[-limit // 2:]
    return "\n".join(lines)


# --------------------------------------------------------------------------- skills on disk
def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "skill"


def save_skill(skill: dict) -> Path:
    config.ensure_dirs()
    p = config.SKILLS_DIR / f"{slug(skill['name'])}.json"
    p.write_text(json.dumps(skill, indent=1), encoding="utf-8")
    return p


def load_skills() -> list[dict]:
    config.ensure_dirs()
    out = []
    for p in sorted(config.SKILLS_DIR.glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            s["_path"] = str(p)
            out.append(s)
        except Exception:
            pass
    return out


def find_skill(name: str):
    n = slug(name)
    skills = load_skills()
    for s in skills:
        if slug(s["name"]) == n:
            return s
    for s in skills:  # loose match
        if n in slug(s["name"]) or slug(s["name"]) in n:
            return s
    return None


def delete_skill(name: str) -> bool:
    s = find_skill(name)
    if s:
        Path(s["_path"]).unlink(missing_ok=True)
        return True
    return False


# --------------------------------------------------------------------------- replay
class Replayer:
    """Plays a skill back. Press Esc at any time to abort."""

    def __init__(self, speed=1.0):
        if not keyboard:
            raise RuntimeError("pynput is not installed.")
        self.speed = max(0.1, speed)
        self.abort = False
        self.kb = keyboard.Controller()
        self.ms = mouse.Controller()

    def _key(self, s):
        if s.get("char"):
            return s["key"]
        if s["key"].startswith("vk") and s["key"][2:].isdigit():
            return keyboard.KeyCode.from_vk(int(s["key"][2:]))
        try:
            return getattr(keyboard.Key, s["key"])
        except AttributeError:
            return keyboard.KeyCode.from_vk(s["vk"]) if s.get("vk") else None

    def run(self, steps, variables=None, on_progress=None):
        variables = variables or {}
        esc = keyboard.Listener(on_press=lambda k: setattr(self, "abort", True) if k == keyboard.Key.esc else None)
        esc.start()
        try:
            prev_t = steps[0]["t"] if steps else 0
            pressed = set()
            for i, s in enumerate(steps):
                if self.abort:
                    return False
                delay = (s["t"] - prev_t) / self.speed
                prev_t = s["t"]
                if delay > 0:
                    time.sleep(min(delay, 5.0))
                t = s["type"]
                if t == "window":
                    if s["title"] != "[private]":
                        focus_window(s["title"], s["exe"]) or focus_window("", s["exe"])
                        time.sleep(0.3)
                elif t == "move":
                    self.ms.position = (s["x"], s["y"])
                elif t == "click":
                    self.ms.position = (s["x"], s["y"])
                    btn = getattr(mouse.Button, s["button"], mouse.Button.left)
                    (self.ms.press if s["pressed"] else self.ms.release)(btn)
                elif t == "scroll":
                    self.ms.position = (s["x"], s["y"])
                    self.ms.scroll(s["dx"], s["dy"])
                elif t == "type":
                    text = s["text"]
                    if s.get("template"):
                        text = re.sub(r"\{(\w+)\}", lambda m: str(variables.get(m.group(1), m.group(0))), s["template"])
                    elif s.get("variable"):
                        text = str(variables.get(s["variable"], text))
                    self.kb.type(text)
                elif t == "key":
                    k = self._key(s)
                    if k is None:
                        continue
                    if s["pressed"]:
                        self.kb.press(k)
                        pressed.add(k)
                    else:
                        self.kb.release(k)
                        pressed.discard(k)
                elif t == "wait":
                    time.sleep(float(s.get("seconds", 1)))
                if on_progress:
                    on_progress(i + 1, len(steps))
            for k in pressed:  # never leave modifiers stuck down
                self.kb.release(k)
            return True
        finally:
            esc.stop()
