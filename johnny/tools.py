"""Actions Johnny can take on the computer. Each tool is exposed to the LLM via Ollama tool-calling."""
import ctypes
import datetime as dt
import difflib
import os
import re
import subprocess
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from . import patterns, watcher
from .proc import clean_dll_path, clean_spawn

try:
    import psutil
except ImportError:
    psutil = None

_NO_WINDOW = 0x08000000
user32 = ctypes.windll.user32


class Context:
    """What tools need from the host app. The GUI fills these in."""

    def __init__(self, cfg, memory):
        self.cfg, self.memory = cfg, memory
        self.confirm = lambda question: False          # ask the user yes/no
        self.notify = lambda text: None                # toast / chat line
        self.start_teaching = lambda name: "Teaching mode is unavailable."
        self.run_skill_async = None                    # (skill, variables) -> str
        self.set_timer = None                          # (seconds, label) -> None
        self.make_borderless = None                    # () -> str


# --------------------------------------------------------------------------- registry
TOOLS = {}


def tool(description, **params):
    """params: name=(type, description, required)"""
    def deco(fn):
        props = {k: {"type": v[0], "description": v[1]} for k, v in params.items()}
        required = [k for k, v in params.items() if len(v) < 3 or v[2]]
        TOOLS[fn.__name__] = {
            "fn": fn,
            "schema": {"type": "function", "function": {
                "name": fn.__name__, "description": description,
                "parameters": {"type": "object", "properties": props, "required": required}}},
        }
        return fn
    return deco


def schemas():
    return [t["schema"] for t in TOOLS.values()]


def call(name, args, ctx: Context) -> str:
    t = TOOLS.get(name)
    if not t:
        return f"Unknown tool '{name}'."
    try:
        return str(t["fn"](ctx, **(args or {})))
    except TypeError as e:
        return f"Bad arguments for {name}: {e}"
    except Exception as e:  # tools must never crash the brain
        return f"{name} failed: {e}"


def _allowed(ctx, key, question):
    mode = ctx.cfg["permissions"].get(key, "ask")
    if mode in (True, "always"):
        return True
    if mode in (False, "never"):
        return False
    return ctx.confirm(question)


# --------------------------------------------------------------------------- app discovery
_app_index = None


def _start_menu_apps():
    global _app_index
    if _app_index is None:
        roots = [Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path.home() / "Desktop", Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop"]
        idx = {}
        for r in roots:
            if r.exists():
                for p in r.rglob("*"):
                    if p.suffix.lower() in (".lnk", ".url", ".appref-ms") and "uninstall" not in p.stem.lower():
                        idx.setdefault(p.stem.lower(), str(p))
        for name, uri in _game_library().items():   # games with no Start-menu shortcut
            idx.setdefault(name, uri)
        for name, uri in _store_apps().items():
            idx.setdefault(name, uri)
        _app_index = idx
    return _app_index


def _game_library():
    """{lowercase game name: launch URI} for installed Steam and Epic games."""
    import glob
    import json as _json
    import winreg
    games = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
        vdf = Path(steam, "steamapps", "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
        libs = {steam} | {lib.replace("\\\\", "\\") for lib in re.findall(r'"path"\s+"([^"]+)"', vdf)}
        for lib in libs:
            for m in glob.glob(str(Path(lib, "steamapps", "appmanifest_*.acf"))):
                t = Path(m).read_text(encoding="utf-8", errors="ignore")
                appid, name = re.search(r'"appid"\s+"(\d+)"', t), re.search(r'"name"\s+"([^"]+)"', t)
                if appid and name and "redistributable" not in name.group(1).lower():
                    games[name.group(1).lower()] = f"steam://rungameid/{appid.group(1)}"
    except OSError:
        pass
    for item in glob.glob(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item"):
        try:
            d = _json.loads(Path(item).read_text(encoding="utf-8", errors="ignore"))
            games[d["DisplayName"].lower()] = (f"com.epicgames.launcher://apps/{d['CatalogNamespace']}%3A"
                                               f"{d['CatalogItemId']}%3A{d['AppName']}?action=launch&silent=true")
        except (OSError, ValueError, KeyError):
            pass
    return games


BROWSERS = {"chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe",
            "microsoft edge": "msedge.exe", "firefox": "firefox.exe", "brave": "brave.exe", "opera": "opera.exe"}


def browser_exe(name):
    import winreg
    exe = BROWSERS.get(name.strip().lower())
    if not exe:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe) as k:
                path = winreg.QueryValue(k, None)
                if path and Path(path.strip('"')).exists():
                    return path.strip('"')
        except OSError:
            pass
    return None


ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "paint": "mspaint.exe",
    "command prompt": "cmd.exe", "cmd": "cmd.exe", "powershell": "powershell.exe", "terminal": "wt.exe",
    "task manager": "taskmgr.exe", "file explorer": "explorer.exe", "explorer": "explorer.exe",
    "control panel": "control.exe", "settings": "ms-settings:", "snipping tool": "ms-screenclip:",
    "camera": "microsoft.windows.camera:", "store": "ms-windows-store:", "mail": "outlookmail:",
    "calendar": "outlookcal:", "photos": "ms-photos:", "clock": "ms-clock:",
    "crome": "google chrome", "chrome browser": "google chrome", "google": "google chrome",
    "edge browser": "microsoft edge", "word": "word", "excel": "excel",
}


@clean_spawn
def launch(target: str) -> str:
    name = target.strip().lower().removesuffix(".exe")
    # spoken filler: "the Xbox app", "Dishonored the game on my computer please"
    name = re.sub(r"^(the|my|app|game)\s+", "", name)
    name = re.sub(r"(\s+(the|a)?\s*(app|application|program|game|video game))?(\s+(on|from) (my|the) (pc|computer))?"
                  r"(\s+(for me|please|now))*[.!?]*$", "", name).strip() or name
    alias = ALIASES.get(name)
    if alias and (":" in alias or alias.endswith(".exe")):
        os.startfile(alias)
        return f"Opened {target}."
    name = alias or name
    apps = _start_menu_apps()
    if name in apps:
        os.startfile(apps[name])
        return f"Opened {target}."
    hits = sorted((k for k in apps if name in k), key=len)  # "edge" -> "microsoft edge"
    if not hits:  # typos / punctuation ("counter strike 2"), but never a shorter different app ("photoshop" -> "photos")
        # get_close_matches is ordered best-first, so keep that order
        hits = [k for k in difflib.get_close_matches(name, apps.keys(), n=3, cutoff=0.75) if k not in name]
    if hits:
        best = hits[0]
        os.startfile(apps[best])
        return f"Opened {best.title()}."
    exe = _registered_exe(name)  # exes on PATH or registered under App Paths
    if exe:
        os.startfile(exe)
        return f"Opened {target}."
    return f"I couldn't find an app called {target} installed on this PC, so nothing was opened."


def _registered_exe(name):
    import shutil
    import winreg
    exe = name if name.endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe) as k:
                path = (winreg.QueryValue(k, None) or "").strip('"')
                if path and Path(path).exists():
                    return path
        except OSError:
            pass
    return shutil.which(exe)


def _store_apps():
    """Microsoft Store / UWP apps (Spotify, Xbox, WhatsApp...) have no .lnk - ask Windows for its app list."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                             capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW).stdout
    except Exception:
        return {}
    apps = {}
    for line in out.splitlines():
        if "|" in line:
            n, app_id = line.rsplit("|", 1)
            if n.strip() and "uninstall" not in n.lower():
                apps[n.strip().lower()] = "shell:AppsFolder\\" + app_id.strip()
    return apps


# --------------------------------------------------------------------------- tools
@tool("Get the current local date and time.")
def get_time(ctx):
    return dt.datetime.now().strftime("%A, %B %d %Y, %I:%M %p")


@tool("Get computer health: CPU, memory, disk, battery, uptime.")
def system_status(ctx):
    if not psutil:
        return "psutil not installed."
    parts = [f"CPU {psutil.cpu_percent(interval=0.5):.0f}%",
             f"RAM {psutil.virtual_memory().percent:.0f}% used"]
    d = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    parts.append(f"C: drive {d.free / 1024 ** 3:.0f} GB free of {d.total / 1024 ** 3:.0f} GB")
    b = psutil.sensors_battery()
    if b:
        parts.append(f"battery {b.percent:.0f}%{' charging' if b.power_plugged else ''}")
    up = dt.timedelta(seconds=int(dt.datetime.now().timestamp() - psutil.boot_time()))
    parts.append(f"up {up}")
    return ", ".join(parts)


@tool("Open an application, game or Windows tool by name (e.g. 'chrome', 'spotify', 'calculator').",
      name=("string", "App name"))
def open_app(ctx, name):
    if not ctx.cfg["permissions"].get("open_apps", True):
        return "Opening apps is disabled in settings."
    return launch(name)


@tool("Close a running application by name.", name=("string", "App or process name, e.g. 'spotify'"))
def close_app(ctx, name):
    if not psutil:
        return "psutil not installed."
    n = name.lower().removesuffix(".exe")
    procs = [p for p in psutil.process_iter(["name"]) if p.info["name"] and n in p.info["name"].lower()]
    if not procs:
        return f"{name} doesn't appear to be running."
    if not _allowed(ctx, "control_input", f"Close {len(procs)} process(es) matching '{name}'?"):
        return "The user declined."
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    return f"Closed {name}."


@tool("Open a website, optionally in a specific browser.", url=("string", "Full URL or domain, e.g. facebook.com"),
      browser=("string", "Optional: chrome, edge, firefox, brave or opera. Omit for the default browser", False))
@clean_spawn
def open_url(ctx, url, browser=""):
    if not ctx.cfg["permissions"].get("open_urls", True):
        return "Opening websites is disabled in settings."
    if "://" not in url:
        url = "https://" + url
    if browser:
        exe = browser_exe(browser)
        if not exe:
            return f"I couldn't find {browser} installed, so nothing was opened."
        subprocess.Popen([exe, url], creationflags=_NO_WINDOW)
        return f"Opened {url} in {browser}."
    webbrowser.open(url)
    return f"Opened {url} in the default browser."


@tool("Open a search results page in the browser for the user to browse (web, YouTube, maps, images). "
      "If they want an answer read back to them, use look_up or get_weather instead.",
      query=("string", "Search terms"), site=("string", "'web' (default), 'youtube', 'maps' or 'images'", False))
@clean_spawn
def web_search(ctx, query, site="web"):
    q = urllib.parse.quote_plus(query)
    url = {"youtube": f"https://www.youtube.com/results?search_query={q}",
           "maps": f"https://www.google.com/maps/search/{q}",
           "images": f"https://www.google.com/search?tbm=isch&q={q}"}.get(site, f"https://www.google.com/search?q={q}")
    webbrowser.open(url)
    return f"Searching {site} for '{query}'."


@tool("Media and volume control.", action=("string", "play_pause, next, previous, volume_up, volume_down, mute"))
def media(ctx, action):
    vk = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "volume_up": 0xAF,
          "volume_down": 0xAE, "mute": 0xAD}.get(action)
    if not vk:
        return "Unknown media action."
    for _ in range(5 if action.startswith("volume") else 1):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
    return f"Done: {action.replace('_', ' ')}."


@tool("Lock the computer.")
def lock_computer(ctx):
    user32.LockWorkStation()
    return "Locked."


@tool("Bring an open window to the front.", name=("string", "Part of the window title or app name"))
def focus_app_window(ctx, name):
    ok = watcher.focus_window(name, name if name.endswith(".exe") else "")
    return "Done." if ok else f"No open window matches '{name}'."


@tool("Type text into whatever window is focused, as if from the keyboard.", text=("string", "Text to type"))
def type_text(ctx, text):
    if not _allowed(ctx, "control_input", f"Type this into the active window?\n\n{text[:300]}"):
        return "The user declined."
    from pynput.keyboard import Controller
    Controller().type(text)
    return "Typed."


@tool("Run a Windows command (PowerShell) and return its output. Use only when no other tool fits.",
      command=("string", "PowerShell command"))
def run_command(ctx, command):
    if not _allowed(ctx, "run_shell", f"Johnny wants to run this PowerShell command:\n\n{command}\n\nAllow?"):
        return "The user declined to run the command."
    with clean_dll_path():  # not around the permission prompt above: that can wait for minutes
        r = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                           timeout=120, creationflags=_NO_WINDOW)
    out = (r.stdout + r.stderr).strip()
    return (out[:3000] + ("\n...[truncated]" if len(out) > 3000 else "")) or f"(exit code {r.returncode}, no output)"


@tool("List files in a folder.", path=("string", "Folder path; '~' for home, 'Desktop', 'Downloads', 'Documents'"))
def list_files(ctx, path):
    if not ctx.cfg["permissions"].get("file_access", True):
        return "File access is disabled in settings."
    p = _resolve(path)
    if not p.is_dir():
        return f"{p} isn't a folder."
    items = sorted(p.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:50]
    return "\n".join(f"{'[dir] ' if i.is_dir() else ''}{i.name}" for i in items) or "(empty)"


@tool("Read a text file (first 4000 characters).", path=("string", "File path"))
def read_file(ctx, path):
    if not ctx.cfg["permissions"].get("file_access", True):
        return "File access is disabled in settings."
    p = _resolve(path)
    return p.read_text(encoding="utf-8", errors="replace")[:4000]


@tool("Save a note to the user's Documents\\Johnny Notes folder.",
      title=("string", "Short title"), text=("string", "Note body"))
def save_note(ctx, title, text):
    folder = Path.home() / "Documents" / "Johnny Notes"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{watcher.slug(title)}.md"
    p.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
    return f"Saved to {p}."


@tool("Set a timer or reminder that alerts the user later.",
      minutes=("number", "Minutes from now"), label=("string", "What to remind about"))
def set_reminder(ctx, minutes, label):
    secs = max(1, float(minutes) * 60)
    if ctx.set_timer:
        ctx.set_timer(secs, label)
    else:
        threading.Timer(secs, lambda: ctx.notify(f"Reminder: {label}")).start()
    return f"Reminder set for {float(minutes):g} minute(s): {label}."


@tool("Store a long-term fact about the user (preferences, names, routines).", fact=("string", "The fact"))
def remember(ctx, fact):
    ctx.memory.remember(fact)
    return "Noted."


@tool("Forget remembered facts containing the given text.", text=("string", "Text to match"))
def forget(ctx, text):
    n = ctx.memory.forget(text)
    return f"Forgot {n} item(s)."


@tool("List the tasks (skills) Johnny has learned by watching the user.")
def list_skills(ctx):
    sk = watcher.load_skills()
    if not sk:
        return "No skills learned yet. The user can teach one with Teach mode."
    return "\n".join(f"- {s['name']}: {s.get('description', '')}"
                     + (f" (variables: {', '.join(s['variables'])})" if s.get("variables") else "") for s in sk)


@tool("Perform a learned skill (a task Johnny recorded by watching the user).",
      name=("string", "Skill name"), variables=("object", "Values for the skill's variables, if any", False))
def run_skill(ctx, name, variables=None):
    s = watcher.find_skill(name)
    if not s:
        return f"I don't know a skill called '{name}'. Known: " + ", ".join(x["name"] for x in watcher.load_skills())
    if not _allowed(ctx, "control_input", f"Run the learned skill '{s['name']}'? Johnny will control the "
                                          f"mouse and keyboard. Press Esc to abort at any time."):
        return "The user declined."
    if ctx.run_skill_async:
        return ctx.run_skill_async(s, variables or {})
    ok = watcher.Replayer(s.get("speed", 1.0)).run(s["steps"], variables)
    return "Skill finished." if ok else "Skill aborted."


@tool("Start teaching mode: Johnny watches and records the user doing a task so it can repeat it later.",
      name=("string", "Name for the new skill"))
def start_teaching(ctx, name):
    return ctx.start_teaching(name)


@tool("Report how the user has spent time across apps (from passive observation).",
      hours=("number", "Look-back window in hours, default 24", False))
def usage_report(ctx, hours=24):
    return patterns.usage_report(ctx.memory, float(hours) * 3600)


@tool("Make the game the user is playing borderless fullscreen (so the HUD overlay can stay on top of it).")
def make_game_borderless(ctx):
    return ctx.make_borderless() if ctx.make_borderless else "Game mode isn't running."


@tool("List the automatic routines Johnny runs.")
def list_routines(ctx):
    rs = patterns.load_routines().get("routines", [])
    if not rs:
        return "No routines yet."
    return "\n".join(f"- {r['type']} {r.get('target') or r.get('targets')} "
                     f"{'at ' + r['at'] if r.get('at') else 'on command: ' + r.get('phrase', '')}"
                     f"{'' if r.get('enabled') else ' (disabled)'}" for r in rs)


@tool("Create a routine that opens an app every day at a set time.",
      app=("string", "App name"), at=("string", "24h time HH:MM"),
      days=("string", "'daily' or 'weekdays'", False))
def create_routine(ctx, app, at, days="daily"):
    hh, mm = at.split(":")
    at = f"{int(hh):02d}:{int(mm):02d}"
    data = patterns.load_routines()
    data["routines"].append({"id": f"open:{app}:{at}", "type": "open_app", "target": app, "at": at,
                             "days": days, "enabled": True, "last_run": ""})
    patterns.save_routines(data)
    return f"Routine created: open {app} at {at} ({days})."


# --------------------------------------------------------------------------- online answers (spoken back)
_UA = {"User-Agent": "JohnnyAssistant/1.4 (DopeKitchen Industries; local desktop assistant)"}


def _get_json(url, timeout=12):
    import json as _json
    import urllib.request
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode("utf-8", errors="replace"))


def _online_ok(ctx):
    return ctx.cfg["permissions"].get("online_lookups", True)


@tool("Get the current weather and today's forecast for a place, to tell the user out loud.",
      location=("string", "City or place, e.g. 'Austin' or 'London, UK'. Empty for the user's location", False))
def get_weather(ctx, location=""):
    if not _online_ok(ctx):
        return "Online lookups are turned off in Settings."
    d = _get_json("https://wttr.in/" + urllib.parse.quote(location or "") + "?format=j1")
    c = d["current_condition"][0]
    area = d.get("nearest_area", [{}])[0]
    place = ", ".join(x[0]["value"] for x in (area.get("areaName"), area.get("region")) if x) or location
    today = d["weather"][0]
    rain = max(int(h.get("chanceofrain", 0)) for h in today.get("hourly", [{}]))
    return (f"Weather in {place}: {c['weatherDesc'][0]['value']}, {c['temp_F']}°F ({c['temp_C']}°C), "
            f"feels like {c['FeelsLikeF']}°F, humidity {c['humidity']}%, wind {c['windspeedMiles']} mph. "
            f"Today: high {today['maxtempF']}°F, low {today['mintempF']}°F, up to {rain}% chance of rain.")


@tool("Look something up online and get the facts back to answer the user (people, places, events, history, "
      "how things work, recent results). Use this when the user wants an ANSWER read back, not a browser page.",
      query=("string", "What to look up, as a short search phrase"))
def look_up(ctx, query):
    if not _online_ok(ctx):
        return "Online lookups are turned off in Settings."
    q = urllib.parse.quote(query)
    hits = _get_json("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=3"
                     f"&srsearch={q}").get("query", {}).get("search", [])
    if not hits:
        return f"I couldn't find anything online about '{query}'."
    titles = "|".join(h["title"] for h in hits[:2])
    pages = _get_json("https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1"
                      f"&format=json&redirects=1&titles={urllib.parse.quote(titles)}").get("query", {}).get("pages", {})
    parts = []
    for p in pages.values():
        text = re.sub(r"\s+", " ", p.get("extract", "")).strip()
        if text:
            parts.append(f"[{p.get('title')}] {text[:1200]}")
    return ("Source: Wikipedia.\n" + "\n\n".join(parts)) if parts else f"No summary found for '{query}'."


def _resolve(path: str) -> Path:
    special = {"desktop": Path.home() / "Desktop", "downloads": Path.home() / "Downloads",
               "documents": Path.home() / "Documents", "pictures": Path.home() / "Pictures",
               "music": Path.home() / "Music", "videos": Path.home() / "Videos"}
    return special.get(path.strip().lower(), Path(os.path.expandvars(os.path.expanduser(path))))
