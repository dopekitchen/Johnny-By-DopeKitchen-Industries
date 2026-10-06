"""Hardware detection used to recommend a model that will actually run well."""
import ctypes
import os
import platform
import shutil
import subprocess


class _MemStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def ram_gb() -> float:
    try:
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return round(st.ullTotalPhys / 1024 ** 3, 1)
    except Exception:
        return 8.0


def gpus() -> list[dict]:
    """Return [{'name', 'vram_gb', 'vendor'}]. NVIDIA via nvidia-smi, others via WMI."""
    found = []
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8, creationflags=_NO_WINDOW,
            ).stdout
            for line in out.strip().splitlines():
                name, mem = [p.strip() for p in line.split(",")]
                found.append({"name": name, "vram_gb": round(int(mem) / 1024, 1), "vendor": "NVIDIA"})
        except Exception:
            pass
    if not found:
        try:
            ps = ("Get-CimInstance Win32_VideoController | "
                  "ForEach-Object { \"$($_.Name)|$($_.AdapterRAM)\" }")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW).stdout
            for line in out.strip().splitlines():
                if "|" not in line:
                    continue
                name, ram = line.rsplit("|", 1)
                vram = round(int(ram) / 1024 ** 3, 1) if ram.strip().isdigit() else 0
                vendor = "AMD" if "AMD" in name or "Radeon" in name else "Intel" if "Intel" in name else "Other"
                found.append({"name": name.strip(), "vram_gb": vram, "vendor": vendor})
        except Exception:
            pass
    return found


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def summary() -> dict:
    g = gpus()
    best_vram = max((x["vram_gb"] for x in g), default=0)
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu": platform.processor() or "Unknown CPU",
        "cores": os.cpu_count() or 4,
        "ram_gb": ram_gb(),
        "gpus": g,
        "best_vram_gb": best_vram,
        "disk_free_gb": round(shutil.disk_usage(os.path.expanduser("~")).free / 1024 ** 3, 1),
    }
