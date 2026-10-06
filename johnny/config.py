"""Persistent configuration stored in %APPDATA%\\Johnny."""
import json
import os
from copy import deepcopy
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Johnny"
CONFIG_PATH = DATA_DIR / "config.json"
SKILLS_DIR = DATA_DIR / "skills"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "memory.db"

DEFAULTS = {
    "setup_complete": False,
    "user_name": "Sir",
    "assistant_name": "Johnny",
    "personality": "butler",          # butler | friendly | concise | witty
    "ollama_host": "http://127.0.0.1:11434",
    "model": "",
    "temperature": 0.6,
    "context_messages": 20,
    "voice": {
        "tts_enabled": True,
        "tts_voice_id": "",
        "tts_rate": 185,
        "engine": "neural",            # neural (Kokoro, natural) | windows (SAPI)
        "neural_voice": "af_heart",
        "neural_speed": 1.0,
        "stt_enabled": True,
        "stt_model": "base.en",
        "wake_word": "hey johnny",
        "wake_enabled": True,
        "push_to_talk_hotkey": "<ctrl>+<alt>+j",
        "mic_device": "",              # "" = Windows default microphone
        "barge_in": True,              # talking over Johnny cuts him off and he listens
        "listen_after_question": True, # after Johnny asks a question, listen for the answer (no wake word)
        "followup_seconds": 8,
    },
    "learning": {
        "observe_enabled": True,       # passive app-usage observation
        "suggest_automations": True,
        "record_hotkey": "<ctrl>+<alt>+r",
        "stop_hotkey": "<ctrl>+<alt>+s",
        "privacy_keywords": ["password", "login", "sign in", "bank", "paypal", "credit card", "1password", "bitwarden", "keepass"],
    },
    "permissions": {
        "open_apps": True,
        "open_urls": True,
        "run_shell": "ask",            # never | ask | always
        "control_input": "ask",        # replay skills / type text
        "file_access": True,
        "online_lookups": True,        # weather + Wikipedia answers (the only features that go online)
    },
    "game": {
        "enabled": True,               # show the HUD when a game is in the foreground
        "detect_fullscreen": True,
        "mode": "coach",               # off | coach | steer
        "coach_interval": 45,          # seconds between proactive coach checks
        "steer_interval": 8,           # seconds between navigator calls
        "speak_tips": True,
        "hud_opacity": 0.9,
        "hud_always": False,           # keep the HUD on screen even outside games
        "hud_pos": "",                 # "x,y" after the user drags it
        "vision_model": "",            # "" = use the main model (it must support images)
        "always": [],                  # exes the user marked as games
        "never": [],                   # exes the user said aren't games
    },
    "startup_with_windows": False,
    "start_minimized": False,
    "theme": "dark",
}


def _merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs():
    for d in (DATA_DIR, SKILLS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def migrate_from_jarvis() -> bool:
    """Earlier builds were called Jarvis and stored data in %APPDATA%\\Jarvis. Move it over once."""
    old = DATA_DIR.parent / "Jarvis"
    if old.is_dir() and not DATA_DIR.exists():
        try:
            old.rename(DATA_DIR)
        except OSError:
            return False
        cfg = load()
        wake = cfg["voice"].get("wake_word", "")
        if not wake.startswith("hey "):
            cfg["voice"]["wake_word"] = f"hey {cfg['assistant_name'].lower()}"
        save(cfg)
        return True
    return False


def load() -> dict:
    ensure_dirs()
    if CONFIG_PATH.exists():
        try:
            return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return deepcopy(DEFAULTS)


def save(cfg: dict):
    ensure_dirs()
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
