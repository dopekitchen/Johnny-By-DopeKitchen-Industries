"""First-run setup wizard: hardware check, Ollama, model choice + download, personality, voice, learning, startup."""
import importlib.util
import json
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, COMPANY, __version__, config, hardware, models_catalog, neural_voice, theme, voice, winintegration
from .ollama_client import Ollama, OllamaError
from .proc import clean_spawn

T = theme


class Wizard(tk.Tk):
    def __init__(self, reconfigure=False):
        super().__init__()
        self.title(f"{APP_NAME} Setup — {COMPANY}")
        self.geometry("980x660")
        self.minsize(820, 600)
        T.apply(self)
        T.set_window_icon(self)
        self.cfg = config.load()
        self.reconfigure = reconfigure
        self.hw = None
        self.ollama = Ollama(self.cfg["ollama_host"])
        self.finished = False
        self._voice_map = {}
        self._q = queue.Queue()  # worker threads hand UI work to the main thread through this
        self._poll_id = self.after(50, self._poll)

        # state vars
        v = self.cfg
        self.var_model = tk.StringVar(value=v["model"])
        self.var_user = tk.StringVar(value=v["user_name"])
        self.var_name = tk.StringVar(value=v["assistant_name"])
        self.var_persona = tk.StringVar(value=v["personality"])
        self.var_tts = tk.BooleanVar(value=v["voice"]["tts_enabled"])
        self.var_voice = tk.StringVar()
        self.var_rate = tk.IntVar(value=v["voice"]["tts_rate"])
        self.var_speed = tk.DoubleVar(value=v["voice"].get("neural_speed", 1.0))
        self.var_stt = tk.BooleanVar(value=v["voice"]["stt_enabled"])
        self.var_stt_model = tk.StringVar(value=v["voice"]["stt_model"])
        self.var_wake_on = tk.BooleanVar(value=v["voice"].get("wake_enabled", True))
        self.var_mic = tk.StringVar(value=v["voice"].get("mic_device") or "Windows default")
        self.var_observe = tk.BooleanVar(value=v["learning"]["observe_enabled"])
        self.var_suggest = tk.BooleanVar(value=v["learning"]["suggest_automations"])
        self.var_privacy = tk.StringVar(value=", ".join(v["learning"]["privacy_keywords"]))
        self.var_shell = tk.StringVar(value=v["permissions"]["run_shell"])
        self.var_input = tk.StringVar(value=v["permissions"]["control_input"])
        self.var_files = tk.BooleanVar(value=v["permissions"]["file_access"])
        self.var_startup = tk.BooleanVar(value=v["startup_with_windows"])
        self.var_minimized = tk.BooleanVar(value=v["start_minimized"])
        self.var_desktop = tk.BooleanVar(value=not reconfigure)
        self.var_startmenu = tk.BooleanVar(value=not reconfigure)
        self.var_models_dir = tk.StringVar(value=v.get("models_dir", ""))

        self._build_chrome()
        self.pages = [self.page_welcome, self.page_system, self.page_model, self.page_persona,
                      self.page_voice, self.page_learning, self.page_startup, self.page_install, self.page_done]
        self.step_names = ["Welcome", "System check", "AI model", "Personality", "Voice",
                           "Learning & privacy", "Startup", "Install", "Finish"]
        self.idx = 0
        self.show(0)

    # ------------------------------------------------------------------ frame
    def _build_chrome(self):
        side = ttk.Frame(self, style="Panel.TFrame", width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        logo = T.logo(120)
        if logo:
            tk.Label(side, image=logo, bg=T.PANEL).pack(pady=(26, 10))
        ttk.Label(side, text=APP_NAME.upper(), style="PanelH2.TLabel", foreground=T.ACCENT,
                  font=("Segoe UI Semibold", 18)).pack()
        ttk.Label(side, text=f"by {COMPANY}", style="PanelMuted.TLabel").pack(pady=(0, 18))
        self.step_labels = []
        for _ in range(9):
            lbl = ttk.Label(side, text="", style="PanelMuted.TLabel", padding=(24, 4))
            lbl.pack(anchor="w")
            self.step_labels.append(lbl)
        ttk.Label(side, text=f"v{__version__}", style="PanelMuted.TLabel").pack(side="bottom", pady=10)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(right, padding=(34, 28, 34, 10))
        self.body.pack(fill="both", expand=True)
        nav = ttk.Frame(right, padding=(34, 10, 34, 20))
        nav.pack(fill="x")
        self.btn_back = ttk.Button(nav, text="Back", command=self.back)
        self.btn_back.pack(side="left")
        self.btn_next = ttk.Button(nav, text="Next", style="Accent.TButton", command=self.next)
        self.btn_next.pack(side="right")
        ttk.Button(nav, text="Cancel", command=self.cancel).pack(side="right", padx=8)

    def show(self, i):
        self.idx = i
        for w in self.body.winfo_children():
            w.destroy()
        for n, lbl in enumerate(self.step_labels):
            mark = "●" if n == i else ("✓" if n < i else "○")
            lbl.configure(text=f"{mark}  {self.step_names[n]}",
                          foreground=T.ACCENT if n == i else (T.GOOD if n < i else T.MUTED))
        self.btn_back.configure(state="normal" if 0 < i < 7 else "disabled")
        self.btn_next.configure(state="normal", text="Next")
        self.pages[i]()

    def next(self):
        if not self.validate():
            return
        if self.idx < len(self.pages) - 1:
            self.show(self.idx + 1)
        else:
            self.finished = True
            self.destroy()

    def back(self):
        self.show(max(0, self.idx - 1))

    def cancel(self):
        if self.idx >= 8 or messagebox.askyesno("Cancel setup", "Exit setup? Your choices won't be saved."):
            self.destroy()

    def h(self, title, sub=""):
        ttk.Label(self.body, text=title, style="H1.TLabel").pack(anchor="w")
        if sub:
            ttk.Label(self.body, text=sub, style="Muted.TLabel", wraplength=600, justify="left").pack(anchor="w", pady=(4, 16))

    def card(self, parent=None):
        f = ttk.Frame(parent or self.body, style="Panel.TFrame", padding=16)
        f.pack(fill="x", pady=6)
        return f

    # ------------------------------------------------------------------ pages
    def page_welcome(self):
        self.h(f"Welcome to {APP_NAME}", "Your private AI butler — it runs 100% on this PC. No cloud, no subscription.")
        for icon, title, text in [
            ("🧠", "Local AI brain", "Powered by Ollama and an open model you choose for your hardware."),
            ("🎙", "Talks with you", "Natural voice replies and optional offline speech recognition."),
            ("👁", "Learns by watching", "Show it a task once in Teach mode and it can repeat it for you on command."),
            ("⚙", "Controls your PC", "Open apps, search, manage files, reminders, media, routines and more."),
        ]:
            c = self.card()
            ttk.Label(c, text=icon, style="Panel.TLabel", font=("Segoe UI Emoji", 20)).pack(side="left", padx=(0, 14))
            col = ttk.Frame(c, style="Panel.TFrame")
            col.pack(side="left", fill="x")
            ttk.Label(col, text=title, style="PanelH2.TLabel").pack(anchor="w")
            ttk.Label(col, text=text, style="PanelMuted.TLabel").pack(anchor="w")
        self.btn_next.configure(text="Get started")

    def page_system(self):
        self.h("System check", "Checking your hardware and the Ollama AI runtime.")
        hwcard = self.card()
        self.lbl_hw = ttk.Label(hwcard, text="Scanning hardware…", style="Panel.TLabel", justify="left")
        self.lbl_hw.pack(anchor="w")
        oc = self.card()
        self.lbl_ollama = ttk.Label(oc, text="Looking for Ollama…", style="Panel.TLabel", justify="left")
        self.lbl_ollama.pack(anchor="w")
        self.ollama_btns = ttk.Frame(oc, style="Panel.TFrame")
        self.ollama_btns.pack(anchor="w", pady=(10, 0))
        self.btn_next.configure(state="disabled")
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        if self.hw is None:
            self.hw = hardware.summary()
        hw = self.hw
        gpu = "\n".join(f"   GPU: {g['name']} ({g['vram_gb']} GB VRAM)" for g in hw["gpus"]) or "   GPU: none detected"
        txt = (f"💻  {hw['os']}\n   CPU: {hw['cpu']} ({hw['cores']} threads)\n   RAM: {hw['ram_gb']} GB\n{gpu}\n"
               f"   Free disk: {hw['disk_free_gb']} GB")
        installed = Ollama.installed()
        running = self.ollama.ensure_running() if installed else False
        self._ui(self._scan_done, txt, installed, running)

    def _scan_done(self, txt, installed, running):
        if not self.winfo_exists() or self.idx != 1:
            return
        self.lbl_hw.configure(text=txt)
        for w in self.ollama_btns.winfo_children():
            w.destroy()
        if running:
            self.lbl_ollama.configure(text=f"✅  Ollama {self.ollama.version()} is installed and running.",
                                      foreground=T.GOOD)
            self.btn_next.configure(state="normal")
        else:
            msg = ("⚠  Ollama is installed but not responding. Start it from the Start menu, then re-check."
                   if installed else
                   "⚠  Ollama is required. Download and install it (free), then click Re-check.")
            self.lbl_ollama.configure(text=msg, foreground=T.WARN)
            if not installed:
                ttk.Button(self.ollama_btns, text="Download Ollama", style="Accent.TButton",
                           command=lambda: clean_spawn(webbrowser.open)("https://ollama.com/download/windows")).pack(side="left")
            ttk.Button(self.ollama_btns, text="Re-check",
                       command=lambda: (self.lbl_ollama.configure(text="Checking…", foreground=T.FG),
                                        threading.Thread(target=self._scan, daemon=True).start())
                       ).pack(side="left", padx=8)

    def page_model(self):
        self.h("Choose your AI brain", "Bigger models are smarter but need more memory. We've picked the best fit for this PC.")
        hw = self.hw or {"best_vram_gb": 0, "ram_gb": 8}
        installed = set(self.ollama.list_models())
        rec = models_catalog.recommend(hw)
        if not self.var_model.get():
            self.var_model.set(rec)

        cols = ("model", "size", "fit", "notes")
        tree = ttk.Treeview(self.body, columns=cols, show="headings", height=9, selectmode="browse")
        for c, w, t in (("model", 190, "Model"), ("size", 80, "Download"), ("fit", 150, "On this PC"), ("notes", 300, "")):
            tree.heading(c, text=t, anchor="w")
            tree.column(c, width=w, anchor="w", stretch=c == "notes")
        rows = {}
        for m in models_catalog.CATALOG:
            label = m["name"] + ("  ★" if m["tag"] == rec else "")
            have = m["tag"] in installed
            iid = tree.insert("", "end", values=(label, "installed" if have else f"{m['size']} GB",
                                                 models_catalog.fit_label(m, hw), m["notes"]))
            rows[iid] = m["tag"]
        for tag in sorted(installed - {m["tag"] for m in models_catalog.CATALOG}):
            iid = tree.insert("", "end", values=(tag, "installed", "Already on this PC", "One of your existing Ollama models."))
            rows[iid] = tag
        tree.pack(fill="x")
        for iid, tag in rows.items():
            if tag == self.var_model.get():
                tree.selection_set(iid)
                tree.see(iid)
        tree.bind("<<TreeviewSelect>>", lambda e: tree.selection() and self.var_model.set(rows[tree.selection()[0]]))

        c = self.card()
        ttk.Label(c, text="Or type any Ollama model tag:", style="Panel.TLabel").pack(side="left")
        ttk.Entry(c, textvariable=self.var_model, width=30).pack(side="left", padx=10)
        ttk.Label(c, text="★ = recommended", style="PanelMuted.TLabel").pack(side="right")
        ttk.Label(self.body, text="Tip: models that support tool calling (Qwen 3, Llama 3.1+, Mistral) work best — "
                                  "that's how Johnny controls your PC.", style="Muted.TLabel", wraplength=620).pack(anchor="w", pady=8)

    def page_persona(self):
        self.h("Personality", "How should your assistant address you and behave?")
        c = self.card()
        g = ttk.Frame(c, style="Panel.TFrame")
        g.pack(fill="x")
        ttk.Label(g, text="What should I call you?", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(g, textvariable=self.var_user, width=28).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Label(g, text="Assistant's name", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(g, textvariable=self.var_name, width=28).grid(row=1, column=1, sticky="w", padx=12)
        ttk.Label(g, text="(you'll say \"Hey <name>\")", style="PanelMuted.TLabel").grid(row=1, column=2, sticky="w")
        c2 = self.card()
        ttk.Label(c2, text="Style", style="PanelH2.TLabel").pack(anchor="w", pady=(0, 6))
        for key, title, desc in [("butler", "Classic Butler", "Polished, loyal and lightly witty. \"Right away, sir.\""),
                                 ("friendly", "Friendly", "Warm and casual, like a helpful friend."),
                                 ("concise", "Concise", "Straight to the point, minimal chatter."),
                                 ("witty", "Witty", "Dry humour and sarcasm, still helpful.")]:
            row = ttk.Frame(c2, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(row, text=title, value=key, variable=self.var_persona, style="Panel.TRadiobutton",
                            width=16).pack(side="left")
            ttk.Label(row, text=desc, style="PanelMuted.TLabel").pack(side="left")

    def page_voice(self):
        self.h("Voice", "Johnny can speak replies and listen to you — all offline.")
        c = self.card()
        ttk.Checkbutton(c, text="Speak replies out loud", variable=self.var_tts, style="Panel.TCheckbutton").pack(anchor="w")
        row = ttk.Frame(c, style="Panel.TFrame")
        row.pack(fill="x", pady=8)
        # natural (Kokoro) voices first, classic Windows voices after
        self._voice_map = {}
        if neural_voice.available():
            for vid, (name, _) in neural_voice.VOICES.items():
                self._voice_map["★ " + name] = ("neural", vid)
        for vid, name in voice.list_voices():
            self._voice_map["Windows: " + name] = ("windows", vid)
        names = list(self._voice_map) or ["(default voice)"]
        vc = self.cfg["voice"]
        want = ("windows", vc["tts_voice_id"]) if vc.get("engine") == "windows" else \
            ("neural", vc.get("neural_voice", "af_heart"))
        cur = next((n for n, v in self._voice_map.items() if v == want), names[0])
        self.var_voice.set(cur)
        ttk.Label(row, text="Voice", style="Panel.TLabel", width=8).pack(side="left")
        ttk.Combobox(row, textvariable=self.var_voice, values=names, state="readonly", width=48).pack(side="left")
        ttk.Button(row, text="▶ Test", command=self._test_voice).pack(side="left", padx=8)
        row2 = ttk.Frame(c, style="Panel.TFrame")
        row2.pack(fill="x")
        ttk.Label(row2, text="Speed", style="Panel.TLabel", width=8).pack(side="left")
        ttk.Scale(row2, from_=0.7, to=1.4, variable=self.var_speed, length=300).pack(side="left")
        self.lbl_voice_note = ttk.Label(c, style="PanelMuted.TLabel", wraplength=600, justify="left",
                                        text="★ = natural neural voices (Siri-like, offline). The first one downloads "
                                             "once (~350 MB) during install.")
        self.lbl_voice_note.pack(anchor="w", pady=(8, 0))

        c2 = self.card()
        have = all(importlib.util.find_spec(m) for m in ("faster_whisper", "sounddevice"))
        ttk.Checkbutton(c2, text="Voice input — talk to Johnny with your microphone", variable=self.var_stt,
                        style="Panel.TCheckbutton").pack(anchor="w")
        ttk.Label(c2, text=("Installed." if have else "Downloads speech recognition (~150 MB) during install.")
                  + "  Push-to-talk: Ctrl+Alt+J.", style="PanelMuted.TLabel", wraplength=560).pack(anchor="w", pady=4)
        ttk.Checkbutton(c2, text=f"Hands-free: start listening when I say \"Hey {self.var_name.get() or 'Johnny'}\"",
                        variable=self.var_wake_on, style="Panel.TCheckbutton").pack(anchor="w", pady=(2, 4))
        row3 = ttk.Frame(c2, style="Panel.TFrame")
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="Accuracy", style="Panel.TLabel", width=10).pack(side="left")
        ttk.Combobox(row3, textvariable=self.var_stt_model, state="readonly", width=34,
                     values=["tiny.en", "base.en", "small.en", "medium.en"]).pack(side="left")
        ttk.Label(row3, text="  (bigger = more accurate, slower)", style="PanelMuted.TLabel").pack(side="left")
        row4 = ttk.Frame(c2, style="Panel.TFrame")
        row4.pack(fill="x", pady=4)
        ttk.Label(row4, text="Microphone", style="Panel.TLabel", width=10).pack(side="left")
        ttk.Combobox(row4, textvariable=self.var_mic, state="readonly", width=34,
                     values=["Windows default"] + [n for n, _ in voice.input_devices()]).pack(side="left")
        ttk.Label(row4, text="  (pick the mic you actually talk into)", style="PanelMuted.TLabel").pack(side="left")

    def _test_voice(self):
        kind, vid = self._voice_map.get(self.var_voice.get(), ("windows", ""))
        line = f"Hey {self.var_user.get()}, I'm {self.var_name.get() or 'Johnny'}. Chrome is open, and it's a " \
               f"beautiful day. What can I do for you?"

        def run():
            try:
                if kind == "neural":
                    if not neural_voice.model_ready():
                        neural_voice.download_model(lambda f, m: self._ui(self.lbl_voice_note.configure, {"text": m}))
                    from kokoro_onnx import Kokoro
                    import sounddevice as sd
                    k = Kokoro(str(neural_voice.MODEL_DIR / "kokoro-v1.0.onnx"),
                               str(neural_voice.MODEL_DIR / "voices-v1.0.bin"))
                    audio, sr = k.create(line, voice=vid, speed=float(self.var_speed.get()),
                                         lang=neural_voice.VOICES[vid][1])
                    self._ui(self.lbl_voice_note.configure, {"text": "Playing…"})
                    sd.play(audio, sr)
                    sd.wait()
                    self._ui(self.lbl_voice_note.configure, {"text": "Natural voice installed."})
                else:
                    import pyttsx3
                    e = pyttsx3.init()
                    if vid:
                        e.setProperty("voice", vid)
                    e.setProperty("rate", int(185 * float(self.var_speed.get())))
                    e.say(line)
                    e.runAndWait()
            except Exception as ex:
                self._ui(messagebox.showinfo, "Voice", f"Voice test unavailable: {ex}")
        threading.Thread(target=run, daemon=True).start()

    def _browse_models_dir(self):
        chosen = filedialog.askdirectory(title="Select Ollama models folder",
                                         initialdir=self.var_models_dir.get() or None)
        if chosen:
            self.var_models_dir.set(chosen)

    def page_learning(self):
        self.h("Learning & privacy", "Everything Johnny learns stays in your user folder on this PC.")
        c = self.card()
        ttk.Checkbutton(c, text="Notice my habits (which apps I use and when)", variable=self.var_observe,
                        style="Panel.TCheckbutton").pack(anchor="w")
        ttk.Checkbutton(c, text="Suggest automations based on my habits", variable=self.var_suggest,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=4)
        ttk.Label(c, text="Teach mode (Ctrl+Alt+R to start, Ctrl+Alt+S to stop) records your mouse and keyboard only "
                          "while you're teaching a task.", style="PanelMuted.TLabel", wraplength=600).pack(anchor="w", pady=4)
        ttk.Label(c, text="Never record typing in windows containing:", style="Panel.TLabel").pack(anchor="w", pady=(8, 2))
        ttk.Entry(c, textvariable=self.var_privacy).pack(fill="x")

        c2 = self.card()
        ttk.Label(c2, text="Permissions", style="PanelH2.TLabel").pack(anchor="w", pady=(0, 6))
        for text, var in [("Run PowerShell commands", self.var_shell), ("Control mouse/keyboard (skills, typing)", self.var_input)]:
            row = ttk.Frame(c2, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=text, style="Panel.TLabel", width=38).pack(side="left")
            for val, lab in (("ask", "Ask me first"), ("always", "Always allow"), ("never", "Never")):
                ttk.Radiobutton(row, text=lab, value=val, variable=var, style="Panel.TRadiobutton").pack(side="left", padx=6)
        ttk.Checkbutton(c2, text="Allow reading and listing my files", variable=self.var_files,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=(6, 0))

    def page_startup(self):
        self.h("Startup & shortcuts")
        c = self.card()
        ttk.Checkbutton(c, text="Start Johnny when I sign in to Windows", variable=self.var_startup,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=2)
        ttk.Checkbutton(c, text="Start minimized to the system tray", variable=self.var_minimized,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=2)
        ttk.Checkbutton(c, text="Create a desktop shortcut", variable=self.var_desktop,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=2)
        ttk.Checkbutton(c, text="Add to the Start menu", variable=self.var_startmenu,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=2)

        # ---- Ollama models directory ----------------------------------------
        c3 = self.card()
        ttk.Label(c3, text="Ollama models location", style="PanelH2.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(c3, text="Models can be several gigabytes — point to a drive with plenty of space.\n"
                          "Leave blank to use Ollama's default location.",
                  style="PanelMuted.TLabel", wraplength=600).pack(anchor="w")
        row_m = ttk.Frame(c3, style="Panel.TFrame")
        row_m.pack(fill="x", pady=(6, 0))
        self._models_entry = ttk.Entry(row_m, textvariable=self.var_models_dir, width=50)
        self._models_entry.pack(side="left")
        ttk.Button(row_m, text="Browse…", command=self._browse_models_dir).pack(side="left", padx=6)
        ttk.Button(row_m, text="Clear (use default)",
                   command=lambda: self.var_models_dir.set("")).pack(side="left")
        c2 = self.card()
        ttk.Label(c2, text="Summary", style="PanelH2.TLabel").pack(anchor="w")
        ttk.Label(c2, style="PanelMuted.TLabel", justify="left", text=(
            f"Model: {self.var_model.get()}\nAssistant: {self.var_name.get()} ({self.var_persona.get()}), calling you "
            f"\"{self.var_user.get()}\"\nVoice output: {'on' if self.var_tts.get() else 'off'}   "
            f"Voice input: {'on' if self.var_stt.get() else 'off'}\n"
            f"Habit learning: {'on' if self.var_observe.get() else 'off'}\n"
            f"Models dir: {self.var_models_dir.get() or 'Ollama default'}")).pack(anchor="w", pady=4)
        self.btn_next.configure(text="Install")

    def page_install(self):
        self.h("Installing", "This can take a while the first time — models are several gigabytes.")
        self.lbl_status = ttk.Label(self.body, text="Preparing…", style="H2.TLabel")
        self.lbl_status.pack(anchor="w", pady=(10, 6))
        self.prog = ttk.Progressbar(self.body, mode="determinate", maximum=1.0, length=600)
        self.prog.pack(fill="x")
        self.lbl_detail = ttk.Label(self.body, text="", style="Muted.TLabel")
        self.lbl_detail.pack(anchor="w", pady=4)
        self.log = tk.Text(self.body, height=14, bg=T.PANEL, fg=T.MUTED, relief="flat", font=T.MONO, wrap="word")
        self.log.pack(fill="both", expand=True, pady=10)
        self.btn_next.configure(state="disabled")
        self.btn_back.configure(state="disabled")
        threading.Thread(target=self._install, daemon=True).start()

    def _ui(self, fn, *a):
        self._q.put((fn, a))

    def _poll(self):
        try:
            while True:
                fn, a = self._q.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._poll_id = self.after(50, self._poll)

    def destroy(self):
        try:
            self.after_cancel(self._poll_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()

    def _log(self, line):
        def w():
            self.log.insert("end", line + "\n")
            self.log.see("end")
        self._ui(w)

    def _status(self, text, frac=None, detail=""):
        def w():
            self.lbl_status.configure(text=text)
            self.lbl_detail.configure(text=detail)
            if frac is None:
                self.prog.configure(mode="indeterminate")
                self.prog.start(12)
            else:
                self.prog.stop()
                self.prog.configure(mode="determinate", value=frac)
        self._ui(w)

    def _install(self):
        try:
            # 1. optional python packages
            needed = []
            if not importlib.util.find_spec("pyttsx3"):
                needed += ["pyttsx3", "pywin32"]
            if not importlib.util.find_spec("pynput"):
                needed += ["pynput"]
            if not importlib.util.find_spec("pystray"):
                needed += ["pystray", "pillow"]
            if self.var_stt.get() and not all(importlib.util.find_spec(m) for m in ("faster_whisper", "sounddevice")):
                needed += ["faster-whisper", "sounddevice", "numpy"]
            if needed and getattr(__import__("sys"), "frozen", False):
                self._log("Note: this build doesn't include: " + ", ".join(needed))
            elif needed:
                self._status("Installing components…", None, " ".join(needed))
                ok = winintegration.pip_install(needed, self._log)
                self._log("Components installed." if ok else "Some components failed to install — see above.")

            # 2. model
            tag = self.var_model.get().strip()
            if self.ollama.has_model(tag):
                self._log(f"Model {tag} already installed.")
            else:
                self._status(f"Downloading {tag}…", 0)
                last = ""
                for status, frac in self.ollama.pull(tag):
                    if frac is not None:
                        self._status(f"Downloading {tag}…", frac, f"{frac * 100:.1f}%  —  {status}")
                    if status != last:
                        self._log(status)
                        last = status
            self._status(f"Loading {tag} into memory…", None)
            self.ollama.warm(tag)

            # 3. natural voice
            kind, _vid = self._voice_map.get(self.var_voice.get(), ("windows", ""))
            if kind == "neural" and self.var_tts.get() and not neural_voice.model_ready():
                self._status("Downloading the natural voice…", 0)
                try:
                    neural_voice.download_model(lambda f, m: self._status("Downloading the natural voice…", f, m))
                    self._log("Natural voice ready.")
                except Exception as e:
                    self._log(f"Natural voice download failed ({e}) - Johnny will retry on first launch.")

            # 3b. speech model warmup
            if self.var_stt.get():
                self._status("Downloading speech recognition model…", None)
                try:
                    from faster_whisper import WhisperModel
                    WhisperModel(self.var_stt_model.get(), device="cpu", compute_type="int8")
                    self._log("Speech model ready.")
                except Exception as e:
                    self._log(f"Speech model will download on first use ({e}).")

            # 4. config + shortcuts
            self._status("Saving settings…", 0.95)
            self._save_config()
            try:
                winintegration.set_startup(self.var_startup.get())
                if self.var_desktop.get():
                    winintegration.desktop_shortcut()
                if self.var_startmenu.get():
                    winintegration.start_menu_shortcut()
            except Exception as e:
                self._log(f"Shortcut step skipped: {e}")
            self._status("All set!", 1.0)
            self._ui(lambda: self.show(8))
        except OllamaError as e:
            self._status("Download failed", 0, str(e))
            self._log(f"ERROR: {e}")
            self._ui(lambda: (self.btn_back.configure(state="normal"), self.btn_next.configure(state="disabled")))
        except Exception as e:
            self._status("Something went wrong", 0, str(e))
            self._log(f"ERROR: {e!r}")
            self._ui(lambda: self.btn_back.configure(state="normal"))

    def _save_config(self):
        c = self.cfg
        models_dir = self.var_models_dir.get().strip()
        c.update(model=self.var_model.get().strip(), user_name=self.var_user.get().strip() or "Sir",
                 assistant_name=self.var_name.get().strip() or "Johnny", personality=self.var_persona.get(),
                 startup_with_windows=self.var_startup.get(), start_minimized=self.var_minimized.get(),
                 models_dir=models_dir, setup_complete=True)
        kind, vid = self._voice_map.get(self.var_voice.get(), ("neural", "af_heart"))
        c["voice"].update(tts_enabled=self.var_tts.get(), engine=kind,
                          tts_voice_id=vid if kind == "windows" else c["voice"]["tts_voice_id"],
                          neural_voice=vid if kind == "neural" else c["voice"].get("neural_voice", "af_heart"),
                          neural_speed=round(float(self.var_speed.get()), 2),
                          tts_rate=int(185 * float(self.var_speed.get())), stt_enabled=self.var_stt.get(),
                          stt_model=self.var_stt_model.get(), wake_word=f"hey {(self.var_name.get().strip() or 'johnny').lower()}",
                          wake_enabled=self.var_wake_on.get(),
                          mic_device="" if self.var_mic.get() == "Windows default" else self.var_mic.get())
        c["learning"].update(observe_enabled=self.var_observe.get(), suggest_automations=self.var_suggest.get(),
                             privacy_keywords=[k.strip() for k in self.var_privacy.get().split(",") if k.strip()])
        c["permissions"].update(run_shell=self.var_shell.get(), control_input=self.var_input.get(),
                                file_access=self.var_files.get())
        config.save(c)
        # Also update install_meta.json so the env-var is applied on the next launch.
        if getattr(__import__("sys"), "frozen", False):
            meta_path = Path(__import__("sys").executable).parent / "install_meta.json"
        else:
            meta_path = Path(__file__).resolve().parent.parent / "install_meta.json"
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            existing["models_dir"] = models_dir
            meta_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except OSError:
            pass  # non-fatal — the env-var was already set for this session

    def page_done(self):
        self.h(f"{self.var_name.get()} is ready", "Here's how to get going:")
        for k, t in [("Type or talk", "Ask anything, or give commands like \"open Spotify\" or \"remind me in 10 minutes\"."),
                     (f"\"Hey {self.var_name.get()}\"", "Say it from anywhere, then your request — e.g. \"Hey Johnny, open Spotify\"."),
                     ("Ctrl+Alt+J", "Push-to-talk from anywhere (if voice input is enabled)."),
                     ("Ctrl+Alt+R", "Start Teach mode — do the task, then press Ctrl+Alt+S to stop."),
                     ("\"Do <skill>\"", "Ask Johnny to repeat anything it has learned. Press Esc to abort a replay.")]:
            c = self.card()
            ttk.Label(c, text=k, style="PanelH2.TLabel", foreground=T.ACCENT, width=14).pack(side="left")
            ttk.Label(c, text=t, style="PanelMuted.TLabel", wraplength=480).pack(side="left")
        self.btn_next.configure(text=f"Launch {self.var_name.get()}")
        self.btn_back.configure(state="disabled")

    # ------------------------------------------------------------------ validation
    def validate(self):
        if self.idx == 2 and not self.var_model.get().strip():
            messagebox.showwarning("Model", "Pick a model or type an Ollama model tag.")
            return False
        if self.idx == 3 and not self.var_name.get().strip():
            messagebox.showwarning("Name", "Give your assistant a name.")
            return False
        return True


def run(reconfigure=False) -> bool:
    w = Wizard(reconfigure)
    w.mainloop()
    return w.finished
