"""Windows integration: run-at-startup, shortcuts, pip installs for optional features."""
import os
import subprocess
import sys
import winreg
from pathlib import Path

_NO_WINDOW = 0x08000000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROJECT_DIR = Path(__file__).resolve().parent.parent


def launch_command() -> tuple[str, str, str]:
    """(target, arguments, working_dir) that starts Johnny without a console window."""
    if getattr(sys, "frozen", False):
        return sys.executable, "", str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), "-m johnny", str(PROJECT_DIR)


def icon_path() -> str:
    bundle = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
    for p in (bundle / "assets" / "johnny.ico", PROJECT_DIR / "assets" / "johnny.ico"):
        if p.exists():
            return str(p)
    return ""


def startup_lnk() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Johnny.lnk"


def set_startup(enabled: bool):
    """A Startup-folder shortcut (unlike the Run key) keeps a working directory, so it works for source installs."""
    lnk = startup_lnk()
    if enabled:
        create_shortcut(lnk, extra_args="--minimized")
    else:
        lnk.unlink(missing_ok=True)
        try:  # clean up older versions that used the Run key
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "Johnny")
        except OSError:
            pass


def create_shortcut(lnk_path: Path, extra_args=""):
    target, args, cwd = launch_command()
    args = f"{args} {extra_args}".strip()
    icon = icon_path()
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath=$env:TGT;$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:CWD;"
        "if($env:ICO){$s.IconLocation=$env:ICO};$s.Description='Johnny AI Butler';$s.Save()"
    )
    env = {**os.environ, "LNK": str(lnk_path), "TGT": target, "ARGS": args, "CWD": cwd, "ICO": icon}
    lnk_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, creationflags=_NO_WINDOW, timeout=30)


def desktop_shortcut():
    create_shortcut(Path.home() / "Desktop" / "Johnny.lnk")


def start_menu_shortcut():
    create_shortcut(Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Johnny.lnk")


def migrate_shortcuts(cfg):
    """Replace shortcuts left by the old 'Jarvis' name with Johnny ones."""
    places = {"desktop": Path.home() / "Desktop" / "Jarvis.lnk",
              "start": Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Jarvis.lnk",
              "startup": Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Jarvis.lnk"}
    had = {k: p.exists() for k, p in places.items()}
    for p in places.values():
        p.unlink(missing_ok=True)
    if had["desktop"]:
        desktop_shortcut()
    if had["start"]:
        start_menu_shortcut()
    if had["startup"] or cfg.get("startup_with_windows"):
        set_startup(True)


def pip_install(packages, on_line=None) -> bool:
    """Install optional packages into the running interpreter (not possible in a frozen build)."""
    if getattr(sys, "frozen", False):
        return False
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
    p = subprocess.Popen([str(py), "-m", "pip", "install", "--disable-pip-version-check", *packages],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=_NO_WINDOW)
    for line in p.stdout:
        if on_line:
            on_line(line.rstrip())
    return p.wait() == 0
