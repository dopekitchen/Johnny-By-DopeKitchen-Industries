"""Entry point: `python -m johnny [--setup] [--minimized]`."""
import ctypes
import sys
import traceback

from . import config


def _single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\JohnnyDopeKitchenMutex")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    try:  # crisp text on high-DPI screens
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    args = sys.argv[1:]
    if "--selftest" in args:  # diagnostics for the packaged build; runs alongside a normal instance
        return _selftest()
    if not _single_instance():
        ctypes.windll.user32.MessageBoxW(None, "Johnny is already running (check the system tray).", "Johnny", 0x40)
        return
    if config.migrate_from_jarvis():
        try:
            from . import winintegration
            winintegration.migrate_shortcuts(config.load())
        except Exception:
            pass
    force_setup = "--setup" in args
    minimized = "--minimized" in args
    while True:
        cfg = config.load()
        if force_setup or not cfg.get("setup_complete"):
            from . import setup_wizard
            if not setup_wizard.run(reconfigure=cfg.get("setup_complete", False)):
                return
            force_setup, minimized = False, False
        from . import app
        if not app.run(minimized):
            return
        force_setup = True


def _selftest():
    """Check every optional engine inside this build and write logs/selftest.txt (no sound is played)."""
    import time
    lines = []

    def check(name, fn):
        t = time.time()
        try:
            lines.append(f"OK    {name}: {fn()} ({time.time() - t:.2f}s)")
        except Exception as e:
            lines.append(f"FAIL  {name}: {e!r}")

    def kokoro():
        from . import neural_voice
        if not neural_voice.available():
            raise RuntimeError("kokoro_onnx not importable")
        if not neural_voice.model_ready():
            return "library OK, model not downloaded yet"
        sp = neural_voice.NeuralSpeaker("af_heart")
        audio, sr = sp._model().create("Self test, one two three.", voice="af_heart", lang="en-us")
        return f"synthesized {len(audio) / sr:.1f}s of audio"

    def sapi():
        import pyttsx3
        e = pyttsx3.init()
        return f"{len(e.getProperty('voices'))} Windows voices"

    def whisper():
        import importlib
        return f"faster_whisper {importlib.import_module('faster_whisper').__version__}"

    def mics():
        from . import voice
        return ", ".join(n for n, _ in voice.input_devices())[:200] or "none"

    def clean_launch():
        import subprocess
        from .proc import clean_dll_path
        buf = ctypes.create_unicode_buffer(1024)
        meipass = getattr(sys, "_MEIPASS", "")
        with clean_dll_path():
            ctypes.windll.kernel32.GetDllDirectoryW(1024, buf)
            during = buf.value
            child_path = subprocess.run(["cmd", "/c", "echo %PATH%"], capture_output=True, text=True,
                                        creationflags=0x08000000).stdout
        ctypes.windll.kernel32.GetDllDirectoryW(1024, buf)
        leaked = bool(meipass) and meipass.lower() in child_path.lower()
        if during or leaked:
            raise RuntimeError(f"children would inherit Johnny's DLLs (dll dir={during!r}, PATH leak={leaked})")
        return f"children get a clean DLL path (restored afterwards: {buf.value == meipass})"

    check("clean program launching", clean_launch)
    check("natural voice (Kokoro)", kokoro)
    check("windows voice (SAPI)", sapi)
    check("speech recognition", whisper)
    check("microphones", mics)
    config.ensure_dirs()
    (config.LOG_DIR / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run():
    """Entry used by both `python -m johnny` and the frozen Johnny.exe."""
    try:
        main()
    except Exception:
        config.ensure_dirs()
        (config.LOG_DIR / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, f"Johnny crashed. Details saved to\n{config.LOG_DIR / 'crash.log'}",
                                         "Johnny", 0x10)
        raise


if __name__ == "__main__":
    run()
