"""In-game HUD: a small always-on-top bar (top-right) with quick controls and real-time settings."""
import ctypes
import tkinter as tk
from tkinter import ttk

from . import game, theme as T, voice

user32 = ctypes.windll.user32
# 64-bit HWND args: without these, HWND_TOPMOST (-1) is passed as a 32-bit int and silently ignored
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
HWND_TOPMOST = ctypes.c_void_p(-1)
BG = "#140d19"
BG2 = "#22172b"
WIDTH = 360


def _no_activate(win):
    """Clicks on the HUD must not pull focus out of the game."""
    try:
        hwnd = user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        ex = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, ex | 0x08000000 | 0x00000080 | 0x00000008)  # NOACTIVATE|TOOLWINDOW|TOPMOST
    except Exception:
        pass


class HUD(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.cfg = app.cfg
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG, highlightthickness=1, highlightbackground=T.ACCENT2)
        self.settings_open = False
        self._drag = None
        self._build()
        self.update_idletasks()
        _no_activate(self)
        self.after(150, self._tick)

    # ------------------------------------------------------------------ layout
    def _btn(self, parent, text, cmd, accent=False):
        b = tk.Label(parent, text=text, bg=T.ACCENT2 if accent else BG2, fg="#ffffff", font=("Segoe UI Semibold", 9),
                     padx=8, pady=4, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=T.ACCENT if accent else "#3a2747"))
        b.bind("<Leave>", lambda e: b.configure(bg=T.ACCENT2 if accent else BG2))
        return b

    def _build(self):
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both")
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=8, pady=(6, 2))
        logo = T.logo(20)
        if logo:
            tk.Label(head, image=logo, bg=BG).pack(side="left", padx=(0, 6))
        tk.Label(head, text=self.cfg["assistant_name"].upper(), bg=BG, fg=T.ACCENT,
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        self.lbl_status = tk.Label(head, text="", bg=BG, fg=T.MUTED, font=("Segoe UI", 9))
        self.lbl_status.pack(side="left", padx=6)
        close = tk.Label(head, text="—", bg=BG, fg=T.MUTED, cursor="hand2", font=("Segoe UI", 10))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.collapse())
        gear = tk.Label(head, text="⚙", bg=BG, fg=T.MUTED, cursor="hand2", font=("Segoe UI", 11))
        gear.pack(side="right", padx=6)
        gear.bind("<Button-1>", lambda e: self.toggle_settings())
        self.dot = tk.Canvas(head, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="right", padx=4)
        for w in (head, self.lbl_status):          # drag the HUD by its header
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", padx=8, pady=4)
        self._btn(row, "🎙 Ask", self.app.game_ask_voice, accent=True).pack(side="left", padx=(0, 4))
        self._btn(row, "👁 Look", self.app.game_look).pack(side="left", padx=4)
        self.btn_learn = self._btn(row, "⏺ Learn", self.app.game_toggle_learn)
        self.btn_learn.pack(side="left", padx=4)
        self.btn_mode = self._btn(row, "", self._cycle_mode)
        self.btn_mode.pack(side="right")

        self.lbl_tip = tk.Label(self.body, text="", bg=BG, fg="#f6e9fa", font=("Segoe UI", 10), wraplength=WIDTH - 20,
                                justify="left", anchor="w")
        self.lbl_tip.pack(fill="x", padx=10, pady=(0, 8))

        self.panel = tk.Frame(self.body, bg=BG2, padx=10, pady=8)
        # collapsed state: just the logo, click to expand
        self.collapsed = False
        self.bubble = tk.Label(self, image=T.logo(40), text="" if T.logo(40) else "J", bg=BG, cursor="hand2",
                               fg=T.ACCENT, font=("Segoe UI Semibold", 16))
        self.bubble.bind("<Button-1>", lambda e: self.expand())
        self._build_settings(self.panel)
        self._refresh_mode()

    def _row(self, parent, label):
        f = tk.Frame(parent, bg=BG2)
        f.pack(fill="x", pady=2)
        tk.Label(f, text=label, bg=BG2, fg=T.MUTED, font=("Segoe UI", 9), width=12, anchor="w").pack(side="left")
        return f

    def _scale(self, parent, var, lo, hi, cmd):
        return tk.Scale(parent, variable=var, from_=lo, to=hi, orient="horizontal", showvalue=True, length=200,
                        bg=BG2, fg=T.FG, troughcolor=BG, highlightthickness=0, bd=0, sliderrelief="flat",
                        activebackground=T.ACCENT, font=("Segoe UI", 8), command=lambda v: cmd())

    def _check(self, parent, text, var, cmd):
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=BG2, fg=T.FG, selectcolor=BG,
                              activebackground=BG2, activeforeground=T.FG, font=("Segoe UI", 9), anchor="w")

    def _build_settings(self, p):
        g = self.cfg["game"]
        self.var_mode = tk.StringVar(value=g["mode"])
        f = self._row(p, "Mode")
        for val, txt in (("off", "Off"), ("coach", "Coach"), ("steer", "Steer")):
            tk.Radiobutton(f, text=txt, value=val, variable=self.var_mode, command=self._mode_changed, bg=BG2,
                           fg=T.FG, selectcolor=BG, activebackground=BG2, font=("Segoe UI", 9)).pack(side="left")
        self.var_coach = tk.IntVar(value=g["coach_interval"])
        self._scale(self._row(p, "Coach every (s)"), self.var_coach, 15, 180, self._save).pack(side="left")
        self.var_steer = tk.IntVar(value=g["steer_interval"])
        self._scale(self._row(p, "Steer every (s)"), self.var_steer, 4, 30, self._save).pack(side="left")
        self.var_speak = tk.BooleanVar(value=g["speak_tips"])
        self._check(p, "Speak tips out loud", self.var_speak, self._save).pack(fill="x")
        self._check(p, f"Hands-free \"Hey {self.cfg['assistant_name']}\"", self.app.wake_loop_on,
                    self.app._toggle_wake).pack(fill="x")

        f = self._row(p, "Microphone")
        devs = [n for n, _ in voice.input_devices()]
        self.var_mic = tk.StringVar(value=self.cfg["voice"].get("mic_device") or "Windows default")
        cb = ttk.Combobox(f, textvariable=self.var_mic, values=["Windows default"] + devs, state="readonly", width=26)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.app.set_mic("" if self.var_mic.get() == "Windows default"
                                                                    else self.var_mic.get()))
        f = self._row(p, "Mic level")
        self.meter = tk.Canvas(f, width=200, height=8, bg=BG, highlightthickness=0)
        self.meter.pack(side="left")
        self.lbl_heard = tk.Label(p, text="", bg=BG2, fg=T.MUTED, font=("Segoe UI", 8), anchor="w",
                                  wraplength=WIDTH - 30, justify="left")
        self.lbl_heard.pack(fill="x")

        self.var_opacity = tk.IntVar(value=int(g["hud_opacity"] * 100))
        self._scale(self._row(p, "HUD opacity %"), self.var_opacity, 40, 100, self._save).pack(side="left")
        f = tk.Frame(p, bg=BG2)
        f.pack(fill="x", pady=(6, 0))
        self.var_always = tk.BooleanVar(value=self.cfg["game"].get("hud_always", False))
        self._check(p, "Always show HUD (even outside games)", self.var_always, self._save).pack(fill="x")
        self._btn(f, "⛶ Make borderless", self.app.game_make_borderless).pack(side="left", padx=(0, 6))
        self._btn(f, "Not a game", self.app.game_mark_not_game).pack(side="left")
        tk.Label(f, text="Ctrl+Alt+H hides/shows", bg=BG2, fg=T.MUTED, font=("Segoe UI", 8)).pack(side="right")

    # ------------------------------------------------------------------ behaviour
    def _save(self):
        g = self.cfg["game"]
        g.update(coach_interval=int(self.var_coach.get()), steer_interval=int(self.var_steer.get()),
                 speak_tips=self.var_speak.get(), hud_opacity=self.var_opacity.get() / 100,
                 hud_always=self.var_always.get())
        self.attributes("-alpha", g["hud_opacity"])
        self.app.save_cfg()
        self.app.hud_refresh_visibility()

    def _mode_changed(self):
        self.app.set_game_mode(self.var_mode.get())
        self._refresh_mode()

    def _cycle_mode(self):
        order = ["off", "coach", "steer"]
        self.var_mode.set(order[(order.index(self.cfg["game"]["mode"]) + 1) % 3])
        self._mode_changed()

    def _refresh_mode(self):
        m = self.cfg["game"]["mode"]
        self.btn_mode.configure(text={"off": "Mode: Off", "coach": "Mode: Coach", "steer": "Mode: Steer 🧭"}[m])

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.panel.pack(fill="x", padx=6, pady=(0, 6))
        else:
            self.panel.pack_forget()
        self.place_window(keep=True)

    def set_status(self, text):
        self.lbl_status.configure(text=text[:34])

    def set_tip(self, text, kind="tip"):
        colors = {"tip": "#f6e9fa", "steer": T.LIME, "you": T.MUTED, "warn": T.WARN}
        self.lbl_tip.configure(text=text, fg=colors.get(kind, "#f6e9fa"))

    def set_learning(self, on):
        self.btn_learn.configure(text="⏹ Stop learning" if on else "⏺ Learn",
                                 bg="#7a1f2b" if on else BG2)

    def _tick(self):
        if not self.winfo_exists():
            return
        app = self.app
        lst = app.listener
        mode = app.mode
        color = {"listening": T.LIME, "thinking": T.ACCENT, "speaking": T.ACCENT}.get(mode, "#4a3a55")
        if app.game_learning:
            color = T.BAD
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 9, 9, fill=color, outline="")
        if self.settings_open:
            lvl = lst.level if lst else 0
            self.meter.delete("all")
            self.meter.create_rectangle(0, 0, int(200 * lvl), 8, fill=T.LIME if lvl > 0.05 else T.ACCENT2, width=0)
            if lst and lst.dead_mic:
                self.lbl_heard.configure(text="⚠ This mic is sending silence — is it muted? Pick another above.",
                                         fg=T.WARN)
            elif lst:
                self.lbl_heard.configure(text=f"Last heard: {lst.last_heard[:80]}" if lst.last_heard else
                                         "Say \"Hey Johnny\" — the bar should move when you talk.", fg=T.MUTED)
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 10 == 0 and self.winfo_ismapped():
            self.keep_on_top()
        if self._ticks % 30 == 0 and self.winfo_ismapped() and not self.cfg["game"].get("hud_pos") and app.game_info:
            self.place_window(app.game_info["hwnd"])        # game moved monitor / changed resolution
        self.after(100, self._tick)

    def keep_on_top(self):
        """Games often make themselves topmost after launching; push the HUD back above them (without focus)."""
        try:
            hwnd = user32.GetAncestor(self.winfo_id(), 2)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)  # TOPMOST, no move/size/activate
        except Exception:
            pass

    def collapse(self):
        self.collapsed = True
        self.body.pack_forget()
        self.bubble.pack()
        self.place_window(self.app.game_info["hwnd"] if self.app.game_info else None)

    def expand(self):
        self.collapsed = False
        self.bubble.pack_forget()
        self.body.pack(fill="both")
        self.place_window(self.app.game_info["hwnd"] if self.app.game_info else None)

    # ------------------------------------------------------------------ placement
    def place_window(self, hwnd=None, keep=False):
        self.update_idletasks()
        h = self.winfo_reqheight()
        w = 52 if self.collapsed else WIDTH
        pos = self.cfg["game"].get("hud_pos")
        if pos:
            x, y = map(int, pos.split(","))
        elif keep and self.winfo_ismapped():
            x, y = self.winfo_x(), self.winfo_y()
        else:
            ml, mt, mr, mb = game.monitor_rect(hwnd) if hwnd else (0, 0, self.winfo_screenwidth(), 0)
            x, y = mr - w - 18, mt + 18
        if pos and self.collapsed:
            x += WIDTH - w                     # keep the bubble at the HUD's right edge
        self.geometry(f"{w}x{h}+{x}+{y}")

    def show_for(self, hwnd):
        self.attributes("-alpha", self.cfg["game"]["hud_opacity"])
        self.place_window(hwnd)
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        _no_activate(self)
        self.keep_on_top()

    def _drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, e):
        if self._drag:
            self.cfg["game"]["hud_pos"] = f"{self.winfo_x()},{self.winfo_y()}"
            self.app.save_cfg()
        self._drag = None
