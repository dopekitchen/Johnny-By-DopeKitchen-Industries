"""Main Johnny window: chat, voice, teach mode, skills, habits and settings."""
import math
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import (APP_NAME, COMPANY, __version__, config, game, neural_voice, patterns, theme, tools, voice, watcher,
               winintegration)
from .brain import Brain
from .hud import HUD
from .memory import Memory
from .ollama_client import Ollama, OllamaError

T = theme

try:
    from pynput import keyboard as pkb
except ImportError:
    pkb = None

try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None


class JohnnyApp(tk.Tk):
    def __init__(self, start_minimized=False):
        super().__init__()
        self.cfg = config.load()
        self.name_ = self.cfg["assistant_name"]
        self.title(f"{self.name_} — {COMPANY}")
        self.geometry("1100x720")
        self.minsize(900, 600)
        T.apply(self)
        T.set_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray if pystray else self.quit_app)

        self.memory = Memory()
        self.ctx = tools.Context(self.cfg, self.memory)
        self.ctx.confirm = self._confirm_threadsafe
        self.ctx.notify = lambda t: self._ui_q.put(lambda: self.add_line("system", t))
        self.ctx.start_teaching = self._start_teaching_from_tool
        self.ctx.run_skill_async = self._run_skill_from_tool
        self.ctx.set_timer = self._set_timer
        self.ctx.make_borderless = lambda: self._call_ui(self.game_make_borderless)
        self.brain = Brain(self.cfg, self.memory, self.ctx)

        vc = self.cfg["voice"]
        self.speaker = self._make_speaker() if vc["tts_enabled"] else None
        self.listener = voice.Listener(vc["stt_model"], vc.get("mic_device", "")) if vc["stt_enabled"] else None
        if self.listener:
            self.listener.preload()

        self.mode = "idle"          # idle | listening | thinking | speaking | recording | acting
        self.level = 0.0
        self.phase = 0.0
        self.busy = False
        self.recorder = None
        self.timers = []             # (due_ts, label)
        self.suggestions = []
        self.wake_loop_on = tk.BooleanVar(value=bool(vc.get("wake_enabled")) and bool(self.listener and self.listener.available))
        self.wake_active = False
        self._voice_banner = None
        # game mode
        self.hud = None
        self.hud_hidden = False
        self.game_info = None
        self.game_profile = None
        self.game_learning = None
        self._next_coach = 0.0
        self._attach_screen = False
        self._look_hwnd = None
        self._warned_exclusive = set()
        # conversation: barge-in + listening for answers
        self._voice_loop_running = False
        self._followup_pending = False
        self._followup_until = 0.0
        self._barged = False
        self._utt_mode = "wake"
        self._recent_speech = []
        self._ignore_speech_until = 0.0
        self.coach = game.Coach(self.cfg, Ollama(self.cfg["ollama_host"]))
        self._ui_q = queue.Queue()

        self._build()
        self._start_background()
        self.after(40, self._animate)
        self.after(50, self._drain_ui)
        if start_minimized or self.cfg.get("start_minimized"):
            self.after(200, self.hide_to_tray)
        threading.Thread(target=self._boot, daemon=True).start()

    # ================================================================== layout
    def _build(self):
        side = ttk.Frame(self, style="Panel.TFrame", width=260)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.orb = tk.Canvas(side, width=260, height=240, bg=T.PANEL, highlightthickness=0)
        self.orb.pack(pady=(10, 0))
        ttk.Label(side, text=self.name_.upper(), style="PanelH2.TLabel", foreground=T.ACCENT,
                  font=("Segoe UI Semibold", 20)).pack()
        self.lbl_state = ttk.Label(side, text="Starting up…", style="PanelMuted.TLabel")
        self.lbl_state.pack(pady=(0, 14))

        bf = ttk.Frame(side, style="Panel.TFrame", padding=(18, 0))
        bf.pack(fill="x")
        self.btn_talk = ttk.Button(bf, text="🎙  Talk  (Ctrl+Alt+J)", style="Accent.TButton", command=self.talk)
        self.btn_talk.pack(fill="x", pady=4)
        self.btn_teach = ttk.Button(bf, text="⏺  Teach a task  (Ctrl+Alt+R)", command=self.teach)
        self.btn_teach.pack(fill="x", pady=4)
        ttk.Button(bf, text="⏹  Stop / be quiet", command=self.stop_all).pack(fill="x", pady=4)
        self.chk_wake = ttk.Checkbutton(bf, text=f"Hands-free: \"Hey {self.name_}\"", variable=self.wake_loop_on,
                                        style="Panel.TCheckbutton", command=self._toggle_wake)
        self.chk_wake.pack(anchor="w", pady=(10, 0))
        if not (self.listener and self.listener.available):
            self.chk_wake.state(["disabled"])

        info = ttk.Frame(side, style="Panel.TFrame", padding=18)
        info.pack(side="bottom", fill="x")
        brand = ttk.Frame(info, style="Panel.TFrame")
        brand.pack(anchor="w", pady=(0, 10))
        logo = T.logo(40)
        if logo:
            tk.Label(brand, image=logo, bg=T.PANEL).pack(side="left", padx=(0, 10))
        bt = ttk.Frame(brand, style="Panel.TFrame")
        bt.pack(side="left")
        ttk.Label(bt, text="DopeKitchen", style="PanelH2.TLabel", foreground=T.ACCENT).pack(anchor="w")
        ttk.Label(bt, text="Industries", style="PanelMuted.TLabel").pack(anchor="w")
        self.lbl_model = ttk.Label(info, text=f"Model: {self.cfg['model']}", style="PanelMuted.TLabel")
        self.lbl_model.pack(anchor="w")
        ttk.Label(info, text=f"v{__version__} · 100% local", style="PanelMuted.TLabel").pack(anchor="w")

        nb = ttk.Notebook(self)
        nb.pack(side="left", fill="both", expand=True, padx=12, pady=12)
        self.nb = nb
        self._build_chat(nb)
        self._build_skills(nb)
        self._build_habits(nb)
        self._build_games(nb)
        self._build_settings(nb)

    def _build_chat(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Chat  ")
        wrap = ttk.Frame(f, style="Panel.TFrame")
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.chat = tk.Text(wrap, bg=T.PANEL, fg=T.FG, relief="flat", wrap="word", font=("Segoe UI", 11),
                            padx=16, pady=12, spacing1=2, spacing3=6, insertbackground=T.FG, state="disabled",
                            cursor="arrow")
        sb = ttk.Scrollbar(wrap, command=self.chat.yview)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("user_name", foreground=T.LIME, font=("Segoe UI Semibold", 10))
        self.chat.tag_configure("bot_name", foreground=T.ACCENT, font=("Segoe UI Semibold", 10))
        self.chat.tag_configure("user", foreground=T.FG)
        self.chat.tag_configure("bot", foreground="#f6e9fa")
        self.chat.tag_configure("tool", foreground=T.MUTED, font=("Cascadia Mono", 9), lmargin1=18, lmargin2=18)
        self.chat.tag_configure("system", foreground=T.GOOD, font=("Segoe UI", 10, "italic"))
        self.chat.tag_configure("error", foreground=T.BAD)

        bar = ttk.Frame(f, padding=(0, 10, 0, 0))
        bar.pack(fill="x")
        self.entry = tk.Text(bar, height=2, bg=T.PANEL2, fg=T.FG, relief="flat", font=("Segoe UI", 11),
                             padx=12, pady=8, insertbackground=T.ACCENT, wrap="word")
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.focus_set()
        ttk.Button(bar, text="Send", style="Accent.TButton", command=self.send).pack(side="left", padx=(8, 0), fill="y")

    def _build_skills(self, nb):
        f = ttk.Frame(nb, padding=(0, 10))
        nb.add(f, text="  Learned skills  ")
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Tasks Johnny learned by watching you", style="H2.TLabel").pack(side="left")
        ttk.Button(top, text="⏺  Teach new", style="Accent.TButton", command=self.teach).pack(side="right")
        cols = ("name", "desc", "steps", "vars")
        self.skill_tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, w, t in (("name", 200, "Skill"), ("desc", 380, "What it does"), ("steps", 70, "Steps"),
                        ("vars", 160, "Variables")):
            self.skill_tree.heading(c, text=t, anchor="w")
            self.skill_tree.column(c, width=w, anchor="w", stretch=c == "desc")
        self.skill_tree.pack(fill="both", expand=True)
        self.skill_tree.bind("<Double-1>", lambda e: self._skill_action("run"))
        bar = ttk.Frame(f, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        for text, act in (("▶ Run", "run"), ("✎ Rename", "rename"), ("⏱ Speed", "speed"), ("🗑 Delete", "delete")):
            ttk.Button(bar, text=text, command=lambda a=act: self._skill_action(a),
                       style="Danger.TButton" if act == "delete" else "TButton").pack(side="left", padx=(0, 6))
        ttk.Label(bar, text="Press Esc during a replay to abort.", style="Muted.TLabel").pack(side="right")
        self.refresh_skills()

    def _build_habits(self, nb):
        f = ttk.Frame(nb, padding=(0, 10))
        nb.add(f, text="  Habits & routines  ")
        ttk.Label(f, text="Suggestions from watching your habits", style="H2.TLabel").pack(anchor="w")
        self.sugg_frame = ttk.Frame(f)
        self.sugg_frame.pack(fill="x", pady=6)
        ttk.Label(f, text="Routines", style="H2.TLabel").pack(anchor="w", pady=(12, 4))
        self.routine_tree = ttk.Treeview(f, columns=("what", "when", "status"), show="headings", height=6)
        for c, w, t in (("what", 360, "Action"), ("when", 220, "When"), ("status", 120, "Status")):
            self.routine_tree.heading(c, text=t, anchor="w")
            self.routine_tree.column(c, width=w, anchor="w")
        self.routine_tree.pack(fill="x")
        rb = ttk.Frame(f, padding=(0, 6))
        rb.pack(fill="x")
        ttk.Button(rb, text="Enable / disable", command=self._toggle_routine).pack(side="left")
        ttk.Button(rb, text="Delete", style="Danger.TButton", command=self._delete_routine).pack(side="left", padx=6)
        ttk.Button(rb, text="Scan habits now", command=self.check_habits).pack(side="right")
        ttk.Label(f, text="Today's activity", style="H2.TLabel").pack(anchor="w", pady=(12, 4))
        self.lbl_usage = ttk.Label(f, text="", style="Muted.TLabel", wraplength=760, justify="left")
        self.lbl_usage.pack(anchor="w")
        self.refresh_habits()

    def _build_games(self, nb):
        f = ttk.Frame(nb, padding=(0, 10))
        nb.add(f, text="  Games  ")
        c = ttk.Frame(f, style="Panel.TFrame", padding=14)
        c.pack(fill="x")
        ttk.Label(c, text="Game mode", style="PanelH2.TLabel").grid(row=0, column=0, sticky="w")
        self.var_game_on = tk.BooleanVar(value=self.cfg["game"]["enabled"])
        ttk.Checkbutton(c, text="Show the HUD automatically when I'm in a game", variable=self.var_game_on,
                        style="Panel.TCheckbutton", command=self._toggle_game_mode).grid(row=1, column=0,
                                                                                         columnspan=3, sticky="w")
        ttk.Label(c, text="Vision model", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_vision = tk.StringVar(value=self.cfg["game"]["vision_model"] or "(same as main model)")
        self.cmb_vision = ttk.Combobox(c, textvariable=self.var_vision, width=30)
        self.cmb_vision.grid(row=2, column=1, sticky="w", padx=10, pady=(8, 0))
        ttk.Button(c, text="Apply", command=self._apply_vision).grid(row=2, column=2, pady=(8, 0))
        ttk.Label(c, text="Tip: a small vision model (gemma3:4b, qwen2.5vl:7b) leaves more GPU for your game. "
                          "Games must run Borderless/Windowed for Johnny to see them. Ctrl+Alt+H shows/hides the HUD.",
                  style="PanelMuted.TLabel", wraplength=700, justify="left").grid(row=3, column=0, columnspan=3,
                                                                                    sticky="w", pady=(8, 0))
        ttk.Label(f, text="What Johnny has learned about your games", style="H2.TLabel").pack(anchor="w", pady=(14, 4))
        self.games_tree = ttk.Treeview(f, columns=("game", "time", "notes"), show="headings", height=5)
        for col, w, t in (("game", 220, "Game"), ("time", 120, "Watched"), ("notes", 400, "Notes")):
            self.games_tree.heading(col, text=t, anchor="w")
            self.games_tree.column(col, width=w, anchor="w", stretch=col == "notes")
        self.games_tree.pack(fill="x")
        self.games_tree.bind("<<TreeviewSelect>>", lambda e: self._show_game_notes())
        self.lbl_game_notes = ttk.Label(f, text="", style="Muted.TLabel", wraplength=760, justify="left")
        self.lbl_game_notes.pack(anchor="w", pady=6)
        b = ttk.Frame(f)
        b.pack(fill="x")
        ttk.Button(b, text="Forget this game", style="Danger.TButton", command=self._forget_game).pack(side="left")
        self.refresh_games()

    def refresh_games(self):
        self.games_tree.delete(*self.games_tree.get_children())
        self._game_rows = {}
        for prof in game.all_profiles():
            iid = self.games_tree.insert("", "end", values=(
                prof.get("name") or prof["exe"], f"{prof.get('minutes', 0):.0f} min, {prof.get('sessions', 0)} sessions",
                f"{len(prof.get('notes', []))} notes"))
            self._game_rows[iid] = prof

    def _show_game_notes(self):
        sel = self.games_tree.selection()
        prof = self._game_rows.get(sel[0]) if sel else None
        if prof:
            notes = prof.get("notes", [])
            ctl = ", ".join(list(prof.get("controls", {}))[:12])
            self.lbl_game_notes.configure(text=("• " + "\n• ".join(notes[-12:]) if notes else "No notes yet — use "
                                                "⏺ Learn in the HUD while you play.") + (f"\n\nControls: {ctl}" if ctl else ""))

    def _forget_game(self):
        sel = self.games_tree.selection()
        prof = self._game_rows.get(sel[0]) if sel else None
        if prof and messagebox.askyesno("Forget game", f"Forget everything learned about {prof.get('name') or prof['exe']}?"):
            game._profile_path(prof["exe"]).unlink(missing_ok=True)
            self.lbl_game_notes.configure(text="")
            self.refresh_games()

    def _toggle_game_mode(self):
        self.cfg["game"]["enabled"] = self.var_game_on.get()
        self.save_cfg()
        if not self.var_game_on.get() and self.game_info:
            self._game_ended(self.game_info)

    def _apply_vision(self):
        m = self.var_vision.get().strip()
        self.cfg["game"]["vision_model"] = "" if m.startswith("(") else m
        self.save_cfg()
        caps = Ollama(self.cfg["ollama_host"]).capabilities(self.coach.model)
        ok = not caps or "vision" in caps
        self.add_line("system" if ok else "error", f"Game vision model: {self.coach.model}" +
                      ("" if ok else " — this model can't see images, pick one with vision."))

    def _build_mic_settings(self, parent):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Microphone", style="Panel.TLabel", width=12).pack(side="left")
        devs = [n for n, _ in voice.input_devices()]
        self.var_mic = tk.StringVar(value=self.cfg["voice"].get("mic_device") or "Windows default")
        cb = ttk.Combobox(row, textvariable=self.var_mic, values=["Windows default"] + devs, state="readonly", width=40)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.set_mic("" if self.var_mic.get() == "Windows default"
                                                               else self.var_mic.get()))
        self.mic_meter = tk.Canvas(row, width=120, height=10, bg=T.PANEL2, highlightthickness=0)
        self.mic_meter.pack(side="left", padx=10)
        self.lbl_heard = ttk.Label(parent, text="", style="PanelMuted.TLabel", wraplength=700)
        self.lbl_heard.pack(anchor="w", pady=(4, 0))

    def _update_mic_meter(self):
        lst = self.listener
        if not getattr(self, "mic_meter", None) or not lst:
            return
        self.mic_meter.delete("all")
        self.mic_meter.create_rectangle(0, 0, int(120 * lst.level), 10, width=0,
                                        fill=T.LIME if lst.level > 0.05 else T.ACCENT2)
        if lst.dead_mic:
            txt = "⚠ This microphone is sending silence (muted or off) — pick another one."
        elif lst.last_heard:
            txt = f"Last heard: \"{lst.last_heard[:90]}\""
        else:
            txt = "Talk and watch the bar move. If it doesn't, pick a different microphone."
        if self.lbl_heard.cget("text") != txt:
            self.lbl_heard.configure(text=txt)

    def _build_settings(self, nb):
        f = ttk.Frame(nb, padding=(4, 14))
        nb.add(f, text="  Settings  ")
        c = ttk.Frame(f, style="Panel.TFrame", padding=16)
        c.pack(fill="x")
        ttk.Label(c, text="AI model", style="PanelH2.TLabel").grid(row=0, column=0, sticky="w")
        self.var_model = tk.StringVar(value=self.cfg["model"])
        self.cmb_model = ttk.Combobox(c, textvariable=self.var_model, width=36)
        self.cmb_model.grid(row=0, column=1, padx=10, sticky="w")
        ttk.Button(c, text="Apply", command=self._apply_model).grid(row=0, column=2)
        ttk.Label(c, text="Creativity", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=10)
        self.var_temp = tk.DoubleVar(value=self.cfg["temperature"])
        ttk.Scale(c, from_=0.0, to=1.2, variable=self.var_temp, length=260,
                  command=lambda v: self._set("temperature", round(float(v), 2))).grid(row=1, column=1, sticky="w", padx=10)

        c2 = ttk.Frame(f, style="Panel.TFrame", padding=16)
        c2.pack(fill="x", pady=10)
        self.var_tts = tk.BooleanVar(value=self.cfg["voice"]["tts_enabled"])
        ttk.Checkbutton(c2, text="Speak replies", variable=self.var_tts, style="Panel.TCheckbutton",
                        command=self._toggle_tts).pack(anchor="w")
        self._build_voice_settings(c2)
        self.var_stt = tk.BooleanVar(value=bool(self.listener and self.listener.available))
        ttk.Checkbutton(c2, text=f"Voice input (microphone) — push-to-talk and \"Hey {self.name_}\"",
                        variable=self.var_stt, style="Panel.TCheckbutton",
                        command=self._toggle_stt).pack(anchor="w", pady=(4, 0))
        self._build_mic_settings(c2)
        row = ttk.Frame(c2, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 0))
        self.var_barge = tk.BooleanVar(value=self.cfg["voice"].get("barge_in", True))
        ttk.Checkbutton(row, text="Interrupt by talking (I stop mid-sentence and listen)", variable=self.var_barge,
                        style="Panel.TCheckbutton", command=lambda: (self.cfg["voice"].update(
                            barge_in=self.var_barge.get()), self.save_cfg())).pack(side="left")
        self.var_followup = tk.BooleanVar(value=self.cfg["voice"].get("listen_after_question", True))
        ttk.Checkbutton(row, text="Listen for my answer after a question", variable=self.var_followup,
                        style="Panel.TCheckbutton", command=lambda: (self.cfg["voice"].update(
                            listen_after_question=self.var_followup.get()), self.save_cfg())).pack(side="left", padx=16)
        self.var_observe = tk.BooleanVar(value=self.cfg["learning"]["observe_enabled"])
        ttk.Checkbutton(c2, text="Learn my habits (passive observation)", variable=self.var_observe,
                        style="Panel.TCheckbutton", command=self._toggle_observe).pack(anchor="w", pady=4)
        self.var_online = tk.BooleanVar(value=self.cfg["permissions"].get("online_lookups", True))
        ttk.Checkbutton(c2, text="Allow online lookups (weather + Wikipedia answers — the only things that go online)",
                        variable=self.var_online, style="Panel.TCheckbutton",
                        command=lambda: (self.cfg["permissions"].update(online_lookups=self.var_online.get()),
                                         self.save_cfg())).pack(anchor="w")
        self.var_startup = tk.BooleanVar(value=self.cfg["startup_with_windows"])
        ttk.Checkbutton(c2, text="Start with Windows", variable=self.var_startup, style="Panel.TCheckbutton",
                        command=self._toggle_startup).pack(anchor="w")

        c3 = ttk.Frame(f, style="Panel.TFrame", padding=16)
        c3.pack(fill="x")
        ttk.Label(c3, text="Memory", style="PanelH2.TLabel").pack(anchor="w")
        self.lbl_facts = ttk.Label(c3, text="", style="PanelMuted.TLabel", justify="left", wraplength=700)
        self.lbl_facts.pack(anchor="w", pady=6)
        row = ttk.Frame(c3, style="Panel.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="Clear conversation", command=self._clear_chat).pack(side="left")
        ttk.Button(row, text="Forget everything", style="Danger.TButton", command=self._forget_all).pack(side="left", padx=6)
        ttk.Button(row, text="Open data folder", command=lambda: __import__("os").startfile(config.DATA_DIR)).pack(side="left")

        bottom = ttk.Frame(f, padding=(0, 14))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Run full setup again…", command=self._rerun_setup).pack(side="left")
        ttk.Label(bottom, text=f"{APP_NAME} v{__version__} — {COMPANY}", style="Muted.TLabel").pack(side="right")
        self._refresh_facts()

    # ================================================================== chat
    def add_line(self, kind, text, speaker=None):
        self.chat.configure(state="normal")
        if kind == "user":
            self.chat.insert("end", f"{self.cfg['user_name']}\n", "user_name")
            self.chat.insert("end", text + "\n\n", "user")
        elif kind == "bot":
            self.chat.insert("end", f"{self.name_}\n", "bot_name")
            self.chat.insert("end", text, "bot")
        else:
            self.chat.insert("end", text + "\n", kind)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _append_stream(self, text):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, "bot")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _on_enter(self, e):
        if not (e.state & 0x1):  # shift+enter = newline
            self.send()
            return "break"

    def send(self, text=None):
        text = (text or self.entry.get("1.0", "end")).strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        if self.busy:
            self.add_line("system", "One moment — still working on the last request.")
            return
        if self.speaker:
            self.speaker.stop()
        if self._handle_local_command(text):
            return
        self.add_line("user", text)
        self.busy = True
        self.set_state("thinking")
        threading.Thread(target=self._think, args=(text,), daemon=True).start()

    def _handle_local_command(self, text):
        """Routine phrases ('start my chrome workspace') run instantly without the LLM."""
        low = text.lower().strip(" .!?")
        for r in patterns.load_routines().get("routines", []):
            if r.get("enabled") and r.get("phrase") and r["phrase"].lower() in low:
                self.add_line("user", text)
                for t in r.get("targets", []):
                    tools.launch(t)
                self.add_line("bot", "Opening your workspace.\n\n")
                self.say("Opening your workspace.")
                return True
        return False

    def _think(self, text):
        self._barged = False
        self._followup_pending, self._followup_until = False, 0.0
        first = [True]
        pending = [""]   # streamed text not yet spoken

        def on_token(t):
            def w():
                if first[0]:
                    first[0] = False
                    self.add_line("bot", "")
                self._append_stream(t)
            self._ui_q.put(w)
            # talk while the reply is still being written: speak each finished sentence right away
            pending[0] += t
            cut = max(pending[0].rfind(". "), pending[0].rfind("! "), pending[0].rfind("? "), pending[0].rfind("\n"))
            if cut >= 18:
                self.say(pending[0][:cut + 1])
                pending[0] = pending[0][cut + 1:]

        def on_tool(name, args, result):
            arg = ", ".join(f"{k}={v!r}" for k, v in args.items())
            self._ui_q.put(lambda: self.add_line("tool", f"⚙ {name}({arg[:120]}) → {result[:200]}"))

        # In a game, Johnny looks at the screen with every question.
        images, extra, model = None, "", None
        look_hwnd = self.game_info["hwnd"] if self.game_info else (self._look_hwnd if self._attach_screen else None)
        self._attach_screen = False
        if look_hwnd:
            img, black = game.capture(look_hwnd)
            if img and not black:
                images = [img]
                extra = game.ASK_CONTEXT.format(user=self.cfg["user_name"],
                                                context=game.profile_context(self.game_profile)
                                                if self.game_info else "(Not in a game - this is their screen.)")
                model = self.coach.model
        if self.hud and self.hud.winfo_ismapped():
            self._ui_q.put(lambda: self.hud and self.hud.set_tip(f"You: {text[:70]}", "you"))
        try:
            reply = self.brain.ask(text, on_token=on_token, on_tool=on_tool, images=images,
                                   extra_context=extra, model=model)
            if first[0] and reply:
                self._ui_q.put(lambda: self.add_line("bot", reply))
            self._ui_q.put(lambda: self._append_stream("\n\n"))
            if reply and self.hud and self.hud.winfo_ismapped():
                self._ui_q.put(lambda: self.hud and self.hud.set_tip(reply[:220]))
            self.say(pending[0] if not first[0] else reply)   # the rest of the stream, or the whole reply
            if (reply and reply.rstrip().rstrip("\"')").endswith("?") and not self._barged
                    and self.cfg["voice"].get("listen_after_question", True)):
                self._followup_pending = True
        except OllamaError as e:
            self._ui_q.put(lambda e=e: self.add_line("error", f"AI error: {e}"))
        except Exception as e:
            self._ui_q.put(lambda e=e: self.add_line("error", f"Error: {e!r}"))
        finally:
            self.busy = False
            self._ui_q.put(lambda: (self.set_state("idle") if self.mode == "thinking" else None,
                                    self.refresh_skills(), self._refresh_facts(), self.refresh_habits()))

    def say(self, text):
        if self._barged and self.busy:   # the user cut in: drop the rest of the interrupted reply
            return
        if self.speaker and text and self.cfg["voice"]["tts_enabled"]:
            self._recent_speech = (self._recent_speech + [text])[-6:]
            self._ui_q.put(lambda: self.set_state("speaking"))
            self.speaker.say(text)

    # ================================================================== voice in
    def talk(self, from_wake=False):
        if not self.listener or not self.listener.available:
            messagebox.showinfo("Voice input", "Voice input is off. Turn it on in Settings.")
            return
        if self.busy or self.mode == "listening":
            return
        if self.speaker:
            self.speaker.stop()
        self.set_state("listening")
        self.listener.interrupt()  # free the mic if the wake listener holds it
        self._banner("🎙  Listening…")
        if self.hud:
            self.hud.set_tip("🎙 Listening…", "you")
        threading.Thread(target=self._listen_once, args=(from_wake,), daemon=True).start()

    def _listen_once(self, from_wake=False):
        if not from_wake:
            voice.chime()
        try:
            text = self.listener.listen(on_level=lambda l: setattr(self, "level", l))
        except Exception as e:
            self._ui_q.put(lambda e=e: (self.add_line("error", f"Microphone error: {e}"), self.set_state("idle"),
                                        self._banner(None)))
            return
        self.level = 0
        self._ui_q.put(lambda: self.set_state("idle"))
        if text:
            self._ui_q.put(lambda: (self._banner(f"💬  {text[:60]}"), self.send(text)))
        else:
            self._ui_q.put(lambda: (self._banner(None), self.hud and self.hud.set_tip("Didn't catch that.", "you")))

    # -- "Hey Johnny" hands-free wake phrase
    def _toggle_wake(self):
        on = self.wake_loop_on.get()
        self.cfg["voice"]["wake_enabled"] = on
        config.save(self.cfg)
        self._set_wake(on)

    def _set_wake(self, on):
        if on and not (self.listener and self.listener.available):
            self.wake_loop_on.set(False)
            return
        self.wake_active = on
        if not on and self.listener:
            self.listener.interrupt()
        self._ensure_voice_loop()
        self.set_state(self.mode)

    # ------------------------------------------------------------------ always-on conversation listener
    def _ensure_voice_loop(self):
        if self.listener and self.listener.available and not self._voice_loop_running:
            self._voice_loop_running = True
            threading.Thread(target=self._voice_loop, daemon=True, name="voice-loop").start()

    def _speaking(self):
        sp = self.speaker
        return bool(sp and (sp.speaking or not sp._q.empty()))

    def _echo_state(self):
        """(Johnny is talking, how loud his audio output is right now) - for barge-in echo rejection."""
        talking = self._speaking()
        level = getattr(self.speaker, "playback_level", None)
        return talking, (level() if (talking and level) else None)

    def _voice_mode(self):
        """What the mic should be doing right now:
        'barge'    Johnny is talking - any real speech from the user cuts him off and becomes the new request
        'followup' Johnny just asked a question - the next thing said is the answer (no wake word)
        'wake'     idle - wait for "Hey Johnny"
        None       mic released (push-to-talk, teaching, replaying a task, or hands-free off)"""
        v = self.cfg["voice"]
        if not (self.listener and self.listener.available) or self.mode in ("listening", "recording", "acting"):
            return None
        if self._speaking():
            return "barge" if v.get("barge_in", True) else None
        if self._followup_pending and not self.busy:
            # Johnny finished asking his question: open the floor for the answer
            self._followup_pending = False
            self._followup_until = time.time() + v.get("followup_seconds", 8)
            voice.chime(soft=True)
            self._ignore_speech_until = time.time() + 0.35
            self._ui_q.put(lambda: (self._banner("🎙  Listening for your answer…"),
                                    self.hud and self.hud.set_tip("🎙 Listening for your answer…", "you"),
                                    self.lbl_state.configure(text="Listening for your answer…")))
        if time.time() < self._followup_until:
            return "followup"
        if self.busy:
            return None
        return "wake" if self.wake_active else None

    def _on_user_speech_start(self):
        """Fires the instant the user starts talking. While Johnny is speaking, that means: stop and listen."""
        if time.time() < self._ignore_speech_until:   # our own chime, not the user
            return
        self._utt_mode = self._voice_mode() or "wake"
        if self._utt_mode == "barge":
            self._barged = True
            self.brain.cancelled = True           # stop writing the rest of the reply
            if self.speaker:
                self.speaker.stop()               # cut the voice off mid-word
            self._ui_q.put(lambda: (self.set_state("listening"), self._banner("🎙  Listening…"),
                                    self.hud and self.hud.set_tip("🎙 Listening…", "you")))
        elif self._utt_mode == "followup":
            self._followup_until = time.time() + 12    # keep the window open while they're mid-answer

    def _is_echo(self, heard):
        """Did the mic just pick up Johnny's own voice from the speakers?"""
        import difflib
        h = re.sub(r"[^a-z0-9 ]", "", heard.lower()).split()
        if not h:
            return True
        for said in self._recent_speech[-3:]:
            s = re.sub(r"[^a-z0-9 ]", "", said.lower()).split()
            if s and difflib.SequenceMatcher(None, h, s).ratio() > 0.5 or \
                    (len(h) >= 3 and " ".join(h) in " ".join(s)):
                return True
        return False

    def _handle_heard(self, heard, mode):
        phrase = self.cfg["voice"].get("wake_word") or f"hey {self.name_.lower()}"
        if mode == "barge":
            if self._is_echo(heard):
                self._log_voice(f"[ignored echo] {heard}")
                self._ui_q.put(lambda: self.set_state("idle"))
                return
            cmd = voice.match_wake(heard, phrase)
            text = cmd if cmd else heard          # "Hey Johnny, stop" or just "stop"/"actually, open Chrome"
            if re.fullmatch(r"(ok(ay)?\s*)?(stop|shh+|quiet|be quiet|shut up|enough|that's enough|never ?mind)[.!]*",
                            text.strip().lower()):
                self._ui_q.put(lambda: (self.set_state("idle"), self._banner(None)))
                return
            self._send_when_free(text)
        elif mode == "followup":
            self._followup_until = 0
            self._send_when_free(voice.match_wake(heard, phrase) or heard)
        else:
            cmd = voice.match_wake(heard, phrase)
            if cmd is None or not self.wake_active:
                return
            voice.chime()
            if len(cmd) >= 2:      # "Hey Johnny, open Spotify" -> straight to work
                self._send_when_free(cmd)
            else:                  # just "Hey Johnny" -> listen for the request
                self._ui_q.put(lambda: self.talk(from_wake=True))
                time.sleep(0.8)

    def _send_when_free(self, text):
        """Send once the interrupted reply has finished cancelling."""
        for _ in range(50):
            if not self.busy:
                break
            time.sleep(0.1)
        self._ui_q.put(lambda: (self._banner(f"💬  {text[:60]}"), self.send(text)))

    def _voice_loop(self):
        """One mic stream for wake phrase, barge-in and follow-up answers."""
        warned = False
        while True:
            if self._voice_mode() is None:
                time.sleep(0.15)
                continue
            try:
                for audio in self.listener.utterances(
                        should_pause=lambda: self._voice_mode() is None, max_seconds=12, silence_seconds=0.7,
                        on_dead_mic=None if warned else self._dead_mic,
                        echo_fn=self._echo_state, on_speech_start=self._on_user_speech_start):
                    warned = True
                    mode = self._utt_mode
                    heard = self.listener.transcribe(audio)
                    self._log_voice(f"[{mode}] {heard}" if heard else "")
                    if heard:
                        self._handle_heard(heard, mode)
                    elif mode == "barge":
                        self._ui_q.put(lambda: self.set_state("idle"))
                    if self._voice_mode() is None:
                        break
                warned = True
            except Exception as e:
                self._log_voice(f"[mic error] {e!r}")
                time.sleep(3)

    def _dead_mic(self):
        name = self.cfg["voice"].get("mic_device") or "your default microphone"
        self._ui_q.put(lambda: self.add_line(
            "error", f"⚠ {name} is sending complete silence (muted, switched off, or blocked in Windows "
                     f"Privacy › Microphone). Pick a different mic in Settings or the game HUD ⚙."))

    def _log_voice(self, text):
        """Keep a short log of what the wake listener heard - invaluable when 'Hey Johnny' seems ignored."""
        if not text:
            return
        try:
            p = config.LOG_DIR / "voice.log"
            if p.exists() and p.stat().st_size > 200_000:
                p.write_text("", encoding="utf-8")
            with p.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')}  {text}\n")
        except OSError:
            pass

    def set_mic(self, name):
        self.cfg["voice"]["mic_device"] = name
        self.save_cfg()
        if self.listener:
            self.listener.device_name = name
            self.listener.dead_mic = False
            self.listener.interrupt()          # the wake loop reopens the new device
        self.add_line("system", f"🎙 Microphone set to {name or 'Windows default'}.")

    # ================================================================== game mode
    def save_cfg(self):
        config.save(self.cfg)

    def _on_game_start(self, info):
        self._ui_q.put(lambda: self._game_started(info))

    def _on_game_end(self, info):
        self._ui_q.put(lambda: self._game_ended(info))

    def _game_started(self, info):
        if not self.cfg["game"]["enabled"]:
            return
        self.game_info = info
        self.game_profile = game.load_profile(info["exe"])
        name = self.game_profile.get("name") or info["title"] or info["exe"]
        if not self.hud:
            self.hud = HUD(self)
        self.hud.set_status(f"· {name}")
        self.hud.set_tip(f"Game mode on. Say \"Hey {self.name_}\" or tap Ask — I can see your screen.")
        if not self.hud_hidden:
            if self.hud.winfo_ismapped():
                self.hud.place_window(info["hwnd"])
            self.hud.show_for(info["hwnd"])
        self.add_line("system", f"🎮 Game mode: {name}")
        threading.Thread(target=self._game_warmup, args=(info,), daemon=True).start()

    def _game_warmup(self, info):
        """Check the model can see, and figure out which game this is the first time we meet it."""
        caps = Ollama(self.cfg["ollama_host"]).capabilities(self.coach.model)
        if caps and "vision" not in caps:
            self._ui_q.put(lambda: self.hud and self.hud.set_tip(
                f"⚠ {self.coach.model} can't see images. Pick a vision model (e.g. gemma3, qwen2.5vl) "
                f"in Settings › Game mode.", "warn"))
            return
        time.sleep(4)
        if game.exclusive_fullscreen() and info["exe"] not in self._warned_exclusive:
            self._warned_exclusive.add(info["exe"])
            msg = ("This game is running in exclusive fullscreen, so Windows won't let me draw the HUD over it or see "
                   "the screen. Switch it to Borderless, or to Windowed and say 'make it borderless'.")
            self._ui_q.put(lambda: self.add_line("error", "🎮 " + msg))
            self.say(msg)
            return
        img, black = game.capture(info["hwnd"])
        if black:
            self._ui_q.put(lambda: self.hud and self.hud.set_tip(
                "⚠ The game screen captures as black — switch the game to Borderless/Windowed fullscreen "
                "so I can see it.", "warn"))
            return
        prof = self.game_profile
        if img and prof is not None and not prof.get("name"):
            name = self.coach.identify(info, img)
            if name:
                prof["name"] = name
                game.save_profile(prof)
                self._ui_q.put(lambda: (self.hud and self.hud.set_status(f"· {name}"),
                                        self.add_line("system", f"🎮 Looks like you're playing {name}.")))

    def _game_ended(self, info):
        if self.game_learning:
            self.game_toggle_learn()
        self.game_info = None
        if self.hud:
            self.hud.set_status("· no game")
        self.hud_refresh_visibility()
        self.add_line("system", "🎮 Game mode off.")

    def hud_refresh_visibility(self):
        """Show the HUD while gaming, or always if pinned - unless hidden with Ctrl+Alt+H."""
        want = (self.game_info is not None and self.cfg["game"]["enabled"]) or self.cfg["game"].get("hud_always")
        if want and not self.hud_hidden:
            if not self.hud:
                self.hud = HUD(self)
                self.hud.set_status("· " + (self.game_profile.get("name") or self.game_info["exe"])
                                    if self.game_info and self.game_profile else "· ready")
            if not self.hud.winfo_ismapped():
                self.hud.show_for(self.game_info["hwnd"] if self.game_info else None)
        elif self.hud and self.hud.winfo_ismapped():
            self.hud.withdraw()

    def game_make_borderless(self):
        hwnd = self.game_info["hwnd"] if self.game_info else None
        msg = game.make_borderless(hwnd)
        if self.hud:
            self.hud.set_tip(msg, "warn" if not msg.startswith("Done") else "tip")
            if self.game_info:
                self.after(800, lambda: self.hud.show_for(self.game_info["hwnd"]) if self.game_info else None)
        return msg

    def hud_hide(self):
        self.hud_hidden = True
        if self.hud:
            self.hud.withdraw()

    def hud_toggle(self):
        if self.hud and self.hud.winfo_ismapped():
            self.hud_hide()
        else:
            self.hud_hidden = False
            if not self.hud:
                self.hud = HUD(self)
            if self.hud.collapsed:
                self.hud.expand()
            self.hud.show_for(self.game_info["hwnd"] if self.game_info else None)

    def set_game_mode(self, mode):
        self.cfg["game"]["mode"] = mode
        self.save_cfg()
        self.coach.last_call = ""
        self._next_coach = 0
        if self.hud:
            self.hud.set_tip({"off": "Coaching off — I'll only answer when asked.",
                              "coach": "Coach mode: I'll chip in with tips when something matters.",
                              "steer": "Steer mode: you drive, I'll call out where to go and what to do."}[mode])

    def game_ask_voice(self):
        self.talk()

    def game_look(self):
        if self.busy:
            return
        if not self.game_info:  # pinned HUD outside a game: look at whatever is in front
            self._attach_screen, self._look_hwnd = True, game.user32.GetForegroundWindow()
        self.send("Look at my screen — what should I do right now?")

    def game_mark_not_game(self):
        if not self.game_info:
            return
        exe = self.game_info["exe"]
        self.cfg["game"]["never"].append(exe)
        self.save_cfg()
        self._game_ended(self.game_info)
        self.detector.current = None
        self.add_line("system", f"OK — I won't treat {exe} as a game.")

    def game_toggle_learn(self):
        if not self.game_info and not self.game_learning:
            return
        if not self.game_learning:
            info = self.game_info
            self.game_learning = game.PlaySession(lambda: info["hwnd"])
            self.game_learning.start()
            self.hud and self.hud.set_learning(True)
            self.hud and self.hud.set_tip("Learning by watching — just play. Tap Stop when you're done.")
            self.say("Watching and learning. Just play.")
            return
        sess, self.game_learning = self.game_learning, None
        minutes = sess.stop()
        self.hud and self.hud.set_learning(False)
        prof = self.game_profile
        if not sess.images:
            self.hud and self.hud.set_tip("I couldn't capture the screen, so there's nothing to learn from.", "warn")
            return
        self.hud and self.hud.set_tip(f"Studying {minutes:.0f} min of gameplay…")

        def study():
            try:
                notes = self.coach.learn(prof, sess.images, sess.controls, minutes)
            except Exception as e:
                self._ui_q.put(lambda e=e: self.hud and self.hud.set_tip(f"Couldn't study that session: {e}", "warn"))
                return
            game.merge_notes(prof, notes, sess.controls, minutes)
            msg = f"Learned {len(notes)} new things about {prof.get('name') or prof['exe']}."
            self._ui_q.put(lambda: (self.hud and self.hud.set_tip(msg + " " + (notes[0] if notes else "")),
                                    self.add_line("system", "🎮 " + msg + "\n   • " + "\n   • ".join(notes)),
                                    self.refresh_games()))
            self.say(msg)
        threading.Thread(target=study, daemon=True).start()

    def _coach_loop(self):
        """Proactive coaching / steering while a game is running."""
        while True:
            time.sleep(0.5)
            g = self.cfg["game"]
            info = self.game_info
            if not info or g["mode"] == "off" or time.time() < self._next_coach:
                continue
            if self.busy or self.mode in ("listening", "thinking", "recording") or \
                    (self.speaker and (self.speaker.speaking or not self.speaker._q.empty())):
                continue
            interval = g["steer_interval"] if g["mode"] == "steer" else g["coach_interval"]
            self._next_coach = time.time() + interval
            img, black = game.capture(info["hwnd"])
            if not img or black or not self.game_profile:
                continue
            try:
                tip = self.coach.tip(self.game_profile, img, g["mode"])
            except Exception:
                self._next_coach = time.time() + 30
                continue
            if tip and self.game_info:
                kind = "steer" if g["mode"] == "steer" else "tip"
                self._ui_q.put(lambda t=tip, k=kind: self.hud and self.hud.set_tip(("🧭 " if k == "steer" else "💡 ") + t, k))
                if g["speak_tips"]:
                    self.say(tip)

    def _banner(self, text):
        """Floating status pill so voice use works while the window is hidden in the tray."""
        old = getattr(self, "_voice_banner", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
            self._voice_banner = None
        if text and self.state() in ("withdrawn", "iconic"):
            self._voice_banner = Overlay(self, text, bg=T.ACCENT2, fg="#ffffff")
            self.after(6000 if text.startswith("💬") else 12000, lambda b=self._voice_banner: self._close_banner(b))

    def _close_banner(self, b):
        if getattr(self, "_voice_banner", None) is b:
            self._banner(None)

    # ================================================================== teach mode
    def teach(self, name=None):
        if self.recorder and self.recorder.recording:
            self.recorder.stop()
            return
        if not name:
            name = simpledialog.askstring("Teach a task", "What should this task be called?\n"
                                          "(e.g. \"check my email\", \"export report\")", parent=self)
            if not name:
                return
        self._begin_recording(name)

    def _start_teaching_from_tool(self, name):
        self._ui_q.put(lambda: self._begin_recording(name))
        return (f"Teaching mode starting for '{name}'. The user will perform the task now and press "
                f"Ctrl+Alt+S when done.")

    def _begin_recording(self, name):
        try:
            self.recorder = watcher.Recorder(self.cfg["learning"]["privacy_keywords"],
                                             on_stop=lambda steps: self._recording_done(name, steps))
        except RuntimeError as e:
            messagebox.showerror("Teach mode", str(e))
            return
        self.set_state("recording")
        self.say(f"Watching. Show me how to {name}. Press control alt S when you're done.")
        self.overlay = Overlay(self, f"●  Recording \"{name}\"  —  Ctrl+Alt+S to stop")
        self.after(1800, lambda: (self.iconify(), self.after(400, self.recorder.start)))

    def _recording_done(self, name, steps):
        self._ui_q.put(lambda: (getattr(self, "overlay", None) and self.overlay.destroy(), self.deiconify(),
                                self.lift(), self.set_state("thinking")))
        meaningful = [s for s in steps if s["type"] not in ("move", "window")]
        if len(meaningful) < 2:
            self._ui_q.put(lambda: (self.add_line("system", "That recording was empty, so I didn't save it."),
                                    self.set_state("idle")))
            return
        info = self.brain.describe_recording(name, steps)
        self._ui_q.put(lambda: self._confirm_skill(info, steps))

    def _confirm_skill(self, info, steps):
        self.set_state("idle")
        dlg = SkillDialog(self, info, steps)
        self.wait_window(dlg)
        if not dlg.result:
            self.add_line("system", "Recording discarded.")
            return
        skill = dlg.result
        watcher.save_skill(skill)
        self.refresh_skills()
        msg = f"Learned \"{skill['name']}\". Just ask me to {skill['name']} whenever you like."
        self.add_line("system", f"✓ {msg}")
        self.say(msg)

    # ================================================================== skills
    def refresh_skills(self):
        self.skill_tree.delete(*self.skill_tree.get_children())
        for s in watcher.load_skills():
            self.skill_tree.insert("", "end", iid=s["_path"], values=(
                s["name"], s.get("description", ""), len(s.get("steps", [])), ", ".join(s.get("variables", []))))

    def _selected_skill(self):
        sel = self.skill_tree.selection()
        if not sel:
            return None
        return next((s for s in watcher.load_skills() if s["_path"] == sel[0]), None)

    def _skill_action(self, act):
        s = self._selected_skill()
        if not s:
            return
        if act == "run":
            variables = {}
            for v in s.get("variables", []):
                val = simpledialog.askstring("Skill input", f"Value for '{v}':", parent=self)
                if val is None:
                    return
                variables[v] = val
            self._run_skill(s, variables)
        elif act == "rename":
            new = simpledialog.askstring("Rename", "New name:", initialvalue=s["name"], parent=self)
            if new:
                import os
                os.remove(s["_path"])
                s["name"] = new
                s.pop("_path")
                watcher.save_skill(s)
        elif act == "speed":
            sp = simpledialog.askfloat("Replay speed", "Speed multiplier (1 = as recorded, 2 = twice as fast):",
                                       initialvalue=s.get("speed", 1.0), minvalue=0.25, maxvalue=10, parent=self)
            if sp:
                s["speed"] = sp
                watcher.save_skill({k: v for k, v in s.items() if k != "_path"})
        elif act == "delete" and messagebox.askyesno("Delete", f"Delete the skill '{s['name']}'?"):
            watcher.delete_skill(s["name"])
        self.refresh_skills()

    def _run_skill(self, skill, variables, done=None):
        self.set_state("acting")
        self.add_line("system", f"▶ Running \"{skill['name']}\" — press Esc to abort.")
        self.iconify()

        def go():
            time.sleep(0.8)
            ok = False
            try:
                ok = watcher.Replayer(skill.get("speed", 1.0)).run(skill["steps"], variables)
            except Exception as e:
                self._ui_q.put(lambda e=e: self.add_line("error", f"Replay error: {e}"))
            self._ui_q.put(lambda: (self.deiconify(), self.set_state("idle"),
                                    self.add_line("system", "✓ Done." if ok else "✗ Aborted.")))
            if done:
                done(ok)
        threading.Thread(target=go, daemon=True).start()

    def _run_skill_from_tool(self, skill, variables):
        ev, res = threading.Event(), {}
        self._ui_q.put(lambda: self._run_skill(skill, variables, done=lambda ok: (res.update(ok=ok), ev.set())))
        ev.wait(timeout=600)
        return f"Skill '{skill['name']}' " + ("finished successfully." if res.get("ok") else "was aborted or failed.")

    # ================================================================== habits
    def refresh_habits(self):
        for w in self.sugg_frame.winfo_children():
            w.destroy()
        if not self.suggestions:
            ttk.Label(self.sugg_frame, text="No suggestions yet — I need a few days of watching to spot habits.",
                      style="Muted.TLabel").pack(anchor="w")
        for s in self.suggestions:
            c = ttk.Frame(self.sugg_frame, style="Panel.TFrame", padding=12)
            c.pack(fill="x", pady=3)
            ttk.Label(c, text="💡 " + s["text"], style="Panel.TLabel", wraplength=600).pack(side="left")
            ttk.Button(c, text="No thanks", command=lambda x=s: self._sugg(x, False)).pack(side="right")
            ttk.Button(c, text="Yes", style="Accent.TButton", command=lambda x=s: self._sugg(x, True)).pack(side="right", padx=6)
        self.routine_tree.delete(*self.routine_tree.get_children())
        for r in patterns.load_routines().get("routines", []):
            what = f"Open {r.get('target') or ' + '.join(r.get('targets', []))}"
            when = f"{r['at']} ({r.get('days', 'daily')})" if r.get("at") else f"Say \"{r.get('phrase', '')}\""
            self.routine_tree.insert("", "end", iid=r["id"], values=(what, when, "On" if r.get("enabled") else "Off"))
        self.lbl_usage.configure(text=patterns.usage_report(self.memory, 86400))

    def _sugg(self, s, accept):
        (patterns.accept if accept else patterns.dismiss)(s)
        self.suggestions.remove(s)
        if accept:
            self.add_line("system", "✓ Routine added.")
        self.refresh_habits()

    def _toggle_routine(self):
        sel = self.routine_tree.selection()
        if sel:
            data = patterns.load_routines()
            for r in data["routines"]:
                if r["id"] == sel[0]:
                    r["enabled"] = not r.get("enabled")
            patterns.save_routines(data)
            self.refresh_habits()

    def _delete_routine(self):
        sel = self.routine_tree.selection()
        if sel:
            data = patterns.load_routines()
            data["routines"] = [r for r in data["routines"] if r["id"] != sel[0]]
            patterns.save_routines(data)
            self.refresh_habits()

    def check_habits(self):
        if not self.cfg["learning"]["suggest_automations"]:
            return
        new = patterns.find_habits(self.memory)
        known = {s["id"] for s in self.suggestions}
        fresh = [s for s in new if s["id"] not in known]
        self.suggestions.extend(fresh)
        if fresh:
            self.add_line("system", f"💡 I noticed {len(fresh)} habit(s) I could automate — see Habits & routines.")
        self.refresh_habits()

    # ================================================================== background
    def _start_background(self):
        if self.cfg["learning"]["observe_enabled"]:
            self.observer = watcher.Observer(self.memory, self.cfg["learning"]["privacy_keywords"])
            self.observer.start()
        else:
            self.observer = None
        if pkb:
            try:
                self.hotkeys = pkb.GlobalHotKeys({
                    self.cfg["voice"]["push_to_talk_hotkey"]: lambda: self._ui_q.put(self.talk),
                    self.cfg["learning"]["record_hotkey"]: lambda: self._ui_q.put(
                        lambda: None if (self.recorder and self.recorder.recording) else self.teach()),
                    "<ctrl>+<alt>+h": lambda: self._ui_q.put(self.hud_toggle),
                })
                self.hotkeys.start()
            except Exception:
                pass
        self.after(15000, self._tick)
        self._setup_tray()
        self.after(1500, self.hud_refresh_visibility)
        self.detector = game.GameDetector(self.cfg, self._on_game_start, self._on_game_end)
        self.detector.start()
        threading.Thread(target=self._coach_loop, daemon=True, name="coach").start()

    def _tick(self):
        now = time.time()
        for due, label in [t for t in self.timers if t[0] <= now]:
            self.timers.remove((due, label))
            self.add_line("system", f"⏰ Reminder: {label}")
            self.say(f"Pardon the interruption, {self.cfg['user_name']}. Reminder: {label}")
            self.deiconify()
            self.lift()
        for r in patterns.due_routines():
            patterns.mark_ran(r["id"])
            if r["type"] == "open_app":
                tools.launch(r["target"])
                self.add_line("system", f"⚙ Routine: opened {r['target']}")
        if int(now) % 1800 < 30:
            self.check_habits()
        self.after(30000, self._tick)

    def _set_timer(self, secs, label):
        self.timers.append((time.time() + secs, label))

    def _boot(self):
        ol = Ollama(self.cfg["ollama_host"])
        if not ol.ensure_running():
            self._ui_q.put(lambda: (self.add_line("error", "Ollama isn't running. Start Ollama, then try again."),
                                    self.set_state("idle")))
            return
        models = ol.list_models()
        # v1.4: move heavy installs to the light model once (it leaves the GPU free for games)
        if not self.cfg.get("light_model_offered") and self.cfg["model"] == "gemma4:26b" and "qwen3.5:4b" in models:
            self.cfg.update(model="qwen3.5:4b", light_model_offered=True)
            self.save_cfg()
            self._ui_q.put(lambda: (self.lbl_model.configure(text="Model: qwen3.5:4b"), self.var_model.set("qwen3.5:4b"),
                                    self.add_line("system", "⚡ Switched to the lighter qwen3.5:4b model (4 GB instead "
                                                            "of 10 GB of GPU memory). Change it any time in Settings.")))
        self._ui_q.put(lambda: (self.cmb_model.configure(values=models),
                                self.cmb_vision.configure(values=["(same as main model)"] + models)))
        if not ol.has_model(self.cfg["model"]):
            self._ui_q.put(lambda: self.add_line("error", f"Model '{self.cfg['model']}' isn't installed. "
                                                          f"Pick one in Settings or re-run setup."))
            self._ui_q.put(lambda: self.set_state("idle"))
            return
        self._ui_q.put(lambda: self.lbl_state.configure(text="Warming up the AI…"))
        ol.warm(self.cfg["model"])
        hello = self.brain.greeting()
        self._ui_q.put(lambda: (self.add_line("bot", hello + "\n\n"), self.set_state("idle"), self.check_habits(),
                                self._set_wake(True) if self.wake_loop_on.get() else self._ensure_voice_loop()))
        self.say(hello)

    # ================================================================== state / animation
    def set_state(self, s):
        self.mode = s
        idle = f"Say \"Hey {self.name_}\" or type below" if self.wake_active else "Online · awaiting instructions"
        labels = {"idle": idle, "listening": "Listening…", "thinking": "Thinking…",
                  "speaking": "Speaking…", "recording": "Watching & learning…", "acting": "Performing task…"}
        self.lbl_state.configure(text=labels.get(s, s),
                                 foreground=T.BAD if s == "recording" else T.ACCENT if s != "idle" else T.MUTED)

    def _animate(self):
        speed = {"thinking": 0.12, "listening": 0.05, "speaking": 0.07, "recording": 0.03, "acting": 0.15}.get(self.mode, 0.015)
        self.phase += speed
        lvl = self.level
        if self.mode == "speaking":
            if self.speaker and not self.speaker.speaking and self.speaker._q.empty():
                self.set_state("idle")
            lvl = 0.35 + 0.3 * abs(math.sin(self.phase * 5))
        elif self.mode == "thinking":
            lvl = 0.15 + 0.15 * math.sin(self.phase * 2)
        elif self.mode == "recording":
            lvl = 0.3 + 0.3 * abs(math.sin(self.phase * 3))
        T.reactor(self.orb, 130, 120, 70, self.phase, lvl)
        if int(self.phase * 100) % 3 == 0:
            self._update_mic_meter()
        self.after(40, self._animate)

    def _drain_ui(self):
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._drain_ui)

    # ================================================================== permissions
    def _call_ui(self, fn):
        """Run fn on the Tk thread and return its result (for tools running on the brain thread)."""
        ev, res = threading.Event(), {}
        self._ui_q.put(lambda: (res.update(v=fn()), ev.set()))
        ev.wait(timeout=30)
        return res.get("v", "Timed out.")

    def _confirm_threadsafe(self, question):
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(self.name_, question, parent=self)
        ev, res = threading.Event(), {}

        def ask():
            self.deiconify()
            self.lift()
            res["ok"] = messagebox.askyesno(self.name_, question, parent=self)
            ev.set()
        self._ui_q.put(ask)
        ev.wait(timeout=300)
        return res.get("ok", False)

    # ================================================================== settings handlers
    def _set(self, key, value):
        self.cfg[key] = value
        config.save(self.cfg)

    def _apply_model(self):
        m = self.var_model.get().strip()
        if not Ollama(self.cfg["ollama_host"]).has_model(m):
            if not messagebox.askyesno("Model", f"'{m}' isn't downloaded. Download it now? (may take a while)"):
                return
            self.add_line("system", f"Downloading {m}…")
            threading.Thread(target=self._pull_model, args=(m,), daemon=True).start()
            return
        self._set("model", m)
        self.brain.supports_tools = self.brain.supports_think_flag = True
        self.lbl_model.configure(text=f"Model: {m}")
        self.add_line("system", f"Switched to {m}.")

    def _pull_model(self, m):
        try:
            last = -1
            for status, frac in Ollama(self.cfg["ollama_host"]).pull(m):
                if frac is not None and int(frac * 10) != last:
                    last = int(frac * 10)
                    self._ui_q.put(lambda f=frac: self.lbl_state.configure(text=f"Downloading {m}: {f * 100:.0f}%"))
            self._ui_q.put(self._apply_model)
        except OllamaError as e:
            self._ui_q.put(lambda e=e: self.add_line("error", f"Download failed: {e}"))

    def _build_voice_settings(self, parent):
        vc = self.cfg["voice"]
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Voice", style="Panel.TLabel", width=12).pack(side="left")
        self._voice_names = {name: vid for vid, (name, _) in neural_voice.VOICES.items()}
        self._voice_names["Windows voice (classic)"] = "windows"
        cur = "Windows voice (classic)" if vc.get("engine") == "windows" else             neural_voice.VOICES.get(vc.get("neural_voice", "af_heart"), neural_voice.VOICES["af_heart"])[0]
        self.var_voice = tk.StringVar(value=cur)
        cb = ttk.Combobox(row, textvariable=self.var_voice, values=list(self._voice_names), state="readonly", width=44)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._voice_changed())
        ttk.Label(row, text="Speed", style="Panel.TLabel").pack(side="left", padx=(12, 4))
        self.var_vspeed = tk.DoubleVar(value=vc.get("neural_speed", 1.0))
        ttk.Scale(row, from_=0.7, to=1.4, variable=self.var_vspeed, length=110,
                  command=lambda v: self._voice_changed(save_only=True)).pack(side="left")
        ttk.Button(row, text="▶ Test", command=self._test_voice).pack(side="left", padx=8)
        self.lbl_voice_status = ttk.Label(parent, text=self._voice_status_text(), style="PanelMuted.TLabel")
        self.lbl_voice_status.pack(anchor="w")

    def _voice_status_text(self):
        if self.cfg["voice"].get("engine") == "windows":
            return "Using the classic Windows voice."
        if not neural_voice.available():
            return "Natural voice isn't included in this build — using the Windows voice."
        return "Natural voice ready (offline)." if neural_voice.model_ready() else             "Natural voice will download on first use (~350 MB, one time)."

    def _voice_changed(self, save_only=False):
        vc = self.cfg["voice"]
        choice = self._voice_names.get(self.var_voice.get(), "af_heart")
        vc["neural_speed"] = round(float(self.var_vspeed.get()), 2)
        engine = "windows" if choice == "windows" else "neural"
        switched = engine != vc.get("engine", "neural")
        vc["engine"] = engine
        if engine == "neural":
            vc["neural_voice"] = choice
        config.save(self.cfg)
        if isinstance(self.speaker, neural_voice.NeuralSpeaker) and engine == "neural":
            self.speaker.configure(vc["neural_voice"], vc["neural_speed"])
        elif not save_only and switched and vc["tts_enabled"]:
            if self.speaker:
                self.speaker.stop()
            self.speaker = self._make_speaker()
        self.lbl_voice_status.configure(text=self._voice_status_text())

    def _test_voice(self):
        if not self.speaker:
            messagebox.showinfo("Voice", "Turn on 'Speak replies' first.")
            return
        self.speaker.stop()
        self.say(f"Hey {self.cfg['user_name']}, this is {self.name_}. Chrome is open, and it's a beautiful day. "
                 f"What can I do for you?")

    def _make_speaker(self):
        """Natural Kokoro voice when installed, otherwise the classic Windows voice."""
        vc = self.cfg["voice"]
        if vc.get("engine", "neural") == "neural" and neural_voice.available():
            if neural_voice.model_ready():
                sp = neural_voice.NeuralSpeaker(vc.get("neural_voice", "af_heart"), vc.get("neural_speed", 1.0),
                                                clean=voice._clean_for_speech)
                sp.warm()
                return sp
            if not getattr(self, "_voice_downloading", False):
                self._voice_downloading = True
                threading.Thread(target=self._download_voice, daemon=True).start()
        return voice.Speaker(vc["tts_voice_id"], vc["tts_rate"])

    def _download_voice(self):
        """First run with the natural voice: fetch the model in the background, then switch over."""
        self._ui_q.put(lambda: self.add_line("system", "🔊 Downloading the natural voice (~350 MB, one time)…"))
        last = [-1]

        def prog(f, msg):
            if int(f * 10) != last[0]:
                last[0] = int(f * 10)
                self._ui_q.put(lambda m=msg: self.lbl_voice_status.configure(text=m)
                               if getattr(self, "lbl_voice_status", None) else None)
        try:
            neural_voice.download_model(prog)
        except Exception as e:
            self._ui_q.put(lambda e=e: self.add_line("error", f"Natural voice download failed ({e}). Using the "
                                                                  f"Windows voice for now."))
            return
        finally:
            self._voice_downloading = False
        self._ui_q.put(self._switch_to_neural)

    def _switch_to_neural(self):
        if self.speaker:
            self.speaker.stop()
        self.speaker = self._make_speaker() if self.cfg["voice"]["tts_enabled"] else None
        if getattr(self, "lbl_voice_status", None):
            self.lbl_voice_status.configure(text="Natural voice ready.")
        self.add_line("system", "🔊 Natural voice installed.")
        self.say(f"Natural voice online. How do I sound, {self.cfg['user_name']}?")

    def _toggle_tts(self):
        self.cfg["voice"]["tts_enabled"] = self.var_tts.get()
        config.save(self.cfg)
        if self.var_tts.get() and not self.speaker:
            self.speaker = self._make_speaker()
        elif not self.var_tts.get() and self.speaker:
            self.speaker.stop()

    def _toggle_stt(self):
        on = self.var_stt.get()
        if on and not self.listener:
            self.listener = voice.Listener(self.cfg["voice"]["stt_model"], self.cfg["voice"].get("mic_device", ""))
        if on and not self.listener.available:
            self.var_stt.set(False)
            messagebox.showinfo("Voice input", "Speech recognition isn't included in this install.")
            return
        self.cfg["voice"]["stt_enabled"] = on
        config.save(self.cfg)
        if on:
            self.listener.preload()
            self._ensure_voice_loop()
            self.chk_wake.state(["!disabled"])
            if not self.wake_loop_on.get():
                self.wake_loop_on.set(True)
                self._toggle_wake()
            self.add_line("system", f"🎙 Voice input on. Say \"Hey {self.name_}\" any time.")
        else:
            self.wake_loop_on.set(False)
            self._toggle_wake()
            self.chk_wake.state(["disabled"])

    def _toggle_observe(self):
        self.cfg["learning"]["observe_enabled"] = self.var_observe.get()
        config.save(self.cfg)
        if self.observer:
            self.observer.paused = not self.var_observe.get()
        elif self.var_observe.get():
            self.observer = watcher.Observer(self.memory, self.cfg["learning"]["privacy_keywords"])
            self.observer.start()

    def _toggle_startup(self):
        self._set("startup_with_windows", self.var_startup.get())
        winintegration.set_startup(self.var_startup.get())

    def _refresh_facts(self):
        facts = self.memory.facts(15)
        self.lbl_facts.configure(text=("Things I remember about you:\n• " + "\n• ".join(facts)) if facts
                                 else "I haven't memorised anything about you yet.")

    def _clear_chat(self):
        self.memory.clear_messages()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def _forget_all(self):
        if messagebox.askyesno("Forget everything", "Erase conversation history, remembered facts and observed "
                                                    "activity? Learned skills are kept."):
            self.memory.clear_messages()
            self.memory.forget("")
            self.memory.clear_activity()
            self._clear_chat()
            self._refresh_facts()
            self.refresh_habits()

    def _rerun_setup(self):
        if messagebox.askyesno("Setup", f"Close {self.name_} and run setup again?"):
            self.cfg["setup_complete"] = False
            config.save(self.cfg)
            self.restart_into_setup = True
            self.quit_app()

    def stop_all(self):
        if self.speaker:
            self.speaker.stop()
        self.brain.cancelled = True
        if self.recorder and self.recorder.recording:
            self.recorder.stop()
        self._banner(None)
        self.set_state("idle")

    # ================================================================== tray
    def _setup_tray(self):
        self.tray = None
        if not pystray:
            return
        try:
            import sys
            from pathlib import Path
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            img = Image.open(base / "assets" / "logo.png").resize((64, 64))
        except Exception:
            img = Image.new("RGBA", (64, 64), (156, 43, 158, 255))
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {self.name_}", lambda: self._ui_q.put(self.show_window), default=True),
            pystray.MenuItem("Talk", lambda: self._ui_q.put(self.talk)),
            pystray.MenuItem("Teach a task", lambda: self._ui_q.put(self.teach)),
            pystray.MenuItem("Quit", lambda: self._ui_q.put(self.quit_app)))
        self.tray = pystray.Icon("johnny", img, self.name_, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def hide_to_tray(self):
        if self.tray:
            self.withdraw()
        else:
            self.iconify()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self):
        self.wake_active = False
        self.detector.running = False
        if self.game_learning:
            self.game_learning.stop()
        if self.listener:
            self.listener.interrupt()
        try:
            if self.tray:
                self.tray.stop()
            if self.observer:
                self.observer.running = False
        except Exception:
            pass
        self.destroy()


class Overlay(tk.Toplevel):
    """Small always-on-top banner (recording, or voice status while hidden in the tray)."""

    def __init__(self, master, text, bg="#3a0d14", fg="#ffd7dc"):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.92)
        self.configure(bg=bg)
        tk.Label(self, text=text, bg=bg, fg=fg, font=("Segoe UI Semibold", 11),
                 padx=18, pady=8).pack()
        self.update_idletasks()
        w = self.winfo_width()
        self.geometry(f"+{(self.winfo_screenwidth() - w) // 2}+12")


class SkillDialog(tk.Toplevel):
    """Review a freshly recorded skill: name, description, and which typed text becomes a variable."""

    def __init__(self, master, info, steps):
        super().__init__(master)
        self.title("New skill learned")
        self.configure(bg=T.BG)
        self.geometry("640x600")
        self.transient(master)
        self.grab_set()
        self.result = None
        self.steps = steps
        f = ttk.Frame(self, padding=20)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="I watched you — here's what I learned", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="Name", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        self.var_name = tk.StringVar(value=info["name"])
        ttk.Entry(f, textvariable=self.var_name).pack(fill="x")
        ttk.Label(f, text="Description", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        self.var_desc = tk.StringVar(value=info["description"])
        ttk.Entry(f, textvariable=self.var_desc).pack(fill="x")

        typed = [s for s in steps if s["type"] == "type"]
        self.var_templates = []
        if typed:
            ttk.Label(f, text="Typed text — wrap anything that changes each time in {braces} to make it a variable,\n"
                              "e.g.  weather in {city}.  I've suggested some:", style="Muted.TLabel").pack(anchor="w", pady=(12, 4))
            box = ttk.Frame(f, style="Panel.TFrame", padding=8)
            box.pack(fill="x")
            suggested = info.get("variables", {})
            for s in typed[:8]:
                tpl = s["text"]
                for sub, var in suggested.items():
                    if sub and sub in tpl:
                        tpl = tpl.replace(sub, "{" + watcher.slug(var) + "}", 1)
                v = tk.StringVar(value=tpl)
                ttk.Entry(box, textvariable=v).pack(fill="x", pady=2)
                self.var_templates.append((s, v))

        ttk.Label(f, text="Steps", style="Muted.TLabel").pack(anchor="w", pady=(12, 4))
        txt = tk.Text(f, height=8, bg=T.PANEL, fg=T.MUTED, relief="flat", font=T.MONO)
        txt.insert("1.0", watcher.describe_steps(steps, 40))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        b = ttk.Frame(f, padding=(0, 12, 0, 0))
        b.pack(fill="x")
        ttk.Button(b, text="Save skill", style="Accent.TButton", command=self.ok).pack(side="right")
        ttk.Button(b, text="Discard", command=self.destroy).pack(side="right", padx=8)

    def ok(self):
        variables = []
        for s, v in self.var_templates:
            names = watcher.template_vars(v.get())
            if names:
                s["template"] = v.get()
                variables += [n for n in names if n not in variables]
            else:
                s.pop("template", None)
                s["text"] = v.get()
        self.result = {"name": self.var_name.get().strip() or "unnamed task", "description": self.var_desc.get().strip(),
                       "variables": variables, "speed": 1.0, "created": time.strftime("%Y-%m-%d %H:%M"),
                       "steps": self.steps}
        self.destroy()


def run(start_minimized=False):
    app = JohnnyApp(start_minimized)
    app.mainloop()
    return getattr(app, "restart_into_setup", False)
