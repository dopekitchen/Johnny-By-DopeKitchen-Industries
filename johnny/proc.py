"""Start outside programs without leaking the packaged app's DLL search path.

The PyInstaller bootloader points the Windows DLL search at Johnny's own _internal folder, and every child
process inherits that. Programs Johnny launched (Ollama, Chrome, games...) then loaded Johnny's copies of
shared DLLs like vcruntime140.dll - a crash risk for them, and it locked Johnny's files so upgrades failed.
"""
import contextlib
import ctypes
import functools
import os
import sys
import threading

_lock = threading.Lock()


@contextlib.contextmanager
def clean_dll_path():
    meipass = getattr(sys, "_MEIPASS", "")
    if os.name != "nt" or not getattr(sys, "frozen", False) or not meipass:
        yield
        return
    k32 = ctypes.windll.kernel32
    with _lock:
        old_path = os.environ.get("PATH", "")
        try:
            k32.SetDllDirectoryW(None)  # back to the normal Windows search order for the child
            os.environ["PATH"] = os.pathsep.join(p for p in old_path.split(os.pathsep)
                                                 if not p.lower().startswith(meipass.lower()))
            yield
        finally:
            os.environ["PATH"] = old_path
            k32.SetDllDirectoryW(meipass)


def clean_spawn(fn):
    """Decorator: run fn (which launches programs) with a clean DLL search path."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with clean_dll_path():
            return fn(*a, **kw)
    return wrapper
