"""DopeKitchen Industries look (logo purple + lime) for tkinter/ttk."""
import tkinter as tk
from tkinter import ttk

BG = "#100a14"
PANEL = "#1a1222"
PANEL2 = "#251a30"
FG = "#f3eaf7"
MUTED = "#a596b0"
ACCENT = "#c681d7"
ACCENT2 = "#9c2b9e"
LIME = "#c2c991"
GOOD = "#3ddc97"
WARN = "#ffb547"
BAD = "#ff5d6c"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
H1 = ("Segoe UI Light", 24)
H2 = ("Segoe UI Semibold", 13)
MONO = ("Cascadia Mono", 9)


def apply(root: tk.Misc):
    root.configure(bg=BG)
    try:  # dark title bar on Windows 10/11
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=FG, font=FONT, fieldbackground=PANEL2, bordercolor=PANEL2,
                lightcolor=PANEL2, darkcolor=PANEL2, troughcolor=PANEL, focuscolor=ACCENT, insertcolor=FG)
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=PANEL)
    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Panel.TLabel", background=PANEL)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    s.configure("H1.TLabel", font=H1, foreground=ACCENT)
    s.configure("H2.TLabel", font=H2)
    s.configure("PanelH2.TLabel", font=H2, background=PANEL)
    s.configure("TButton", background=PANEL2, foreground=FG, padding=(14, 7), borderwidth=0, font=FONT_B)
    s.map("TButton", background=[("active", "#36254a"), ("disabled", PANEL)], foreground=[("disabled", MUTED)])
    s.configure("Accent.TButton", background=ACCENT2, foreground="#ffffff")
    s.map("Accent.TButton", background=[("active", ACCENT), ("disabled", PANEL2)])
    s.configure("Danger.TButton", background="#5a1d26")
    s.map("Danger.TButton", background=[("active", BAD)])
    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=BG, foreground=FG, indicatorbackground=PANEL2, indicatorforeground=ACCENT)
        s.map(w, background=[("active", BG)], indicatorbackground=[("selected", ACCENT2)])
        s.configure("Panel." + w, background=PANEL)
        s.map("Panel." + w, background=[("active", PANEL)])
    s.configure("TEntry", padding=6, fieldbackground=PANEL2, foreground=FG)
    s.configure("TCombobox", padding=5, fieldbackground=PANEL2, background=PANEL2, foreground=FG, arrowcolor=ACCENT)
    s.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", PANEL2)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT2)
    s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL2, thickness=10)
    s.configure("Horizontal.TScale", background=BG, troughcolor=PANEL2)
    s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=28, borderwidth=0)
    s.configure("Treeview.Heading", background=PANEL2, foreground=MUTED, font=FONT_B, borderwidth=0)
    s.map("Treeview", background=[("selected", "#4a2356")])
    s.configure("TNotebook", background=BG, borderwidth=0)
    s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 8), font=FONT_B)
    s.map("TNotebook.Tab", background=[("selected", PANEL2)], foreground=[("selected", ACCENT)])
    s.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=BG, arrowcolor=MUTED, borderwidth=0)
    return s


def reactor(canvas: tk.Canvas, cx, cy, r, phase=0.0, level=0.0):
    """Draw Johnny's animated voice orb."""
    import math
    canvas.delete("reactor")
    glow = r * (1.15 + 0.25 * level)
    for i in range(6, 0, -1):
        rr = glow + i * 4
        shade = 0x10 + i * 3
        canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline="", fill=f"#{shade + 4:02x}0a{shade + 8:02x}",
                           tags="reactor")
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ACCENT, width=2, tags="reactor")
    for k in range(10):
        a = phase + k * math.pi / 5
        x1, y1 = cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62
        x2, y2 = cx + math.cos(a) * r * 0.88, cy + math.sin(a) * r * 0.88
        canvas.create_line(x1, y1, x2, y2, fill=ACCENT2, width=4, tags="reactor")
    core = r * (0.38 + 0.18 * level)
    canvas.create_oval(cx - core, cy - core, cx + core, cy + core, outline="#eef5c8", width=2,
                       fill=LIME if level > 0.05 else "#b553c7", tags="reactor")


_logo_cache = {}


def logo(size: int):
    """The DopeKitchen logo as a Tk image. Cached per Tk root (images can't cross roots) so it isn't garbage-collected."""
    root = tk._default_root
    key = (id(root), size)
    if key not in _logo_cache:
        try:
            import sys
            from pathlib import Path
            from PIL import Image, ImageTk
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            img = Image.open(base / "assets" / "logo.png").resize((size, size), Image.LANCZOS)
            _logo_cache[key] = ImageTk.PhotoImage(img, master=root)
        except Exception:
            _logo_cache[key] = None
    return _logo_cache[key]


def set_window_icon(win: tk.Misc):
    img = logo(64)
    if img:
        try:
            win.iconphoto(True, img)
        except tk.TclError:
            pass
