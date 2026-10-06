"""Text-to-speech (Windows SAPI via pyttsx3) and optional offline speech-to-text (faster-whisper)."""
import difflib
import queue
import re
import threading
import time

try:
    import pyttsx3
except ImportError:  # optional
    pyttsx3 = None

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    np = sd = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None


def list_voices():
    """[(id, name)] of installed SAPI voices."""
    if not pyttsx3:
        return []
    try:
        eng = pyttsx3.init()
        vs = [(v.id, v.name) for v in eng.getProperty("voices")]
        eng.stop()
        return vs
    except Exception:
        return []


def _clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " I've put the code on screen. ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"https?://\S+", "the link", text)
    # say numbers and units the way a person would
    text = re.sub(r"\s*\((-?\d+(?:\.\d+)?)\s*°\s*C\)", "", text)          # drop "(26°C)" after a °F value
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*F\b", r"\1 degrees", text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*C\b", r"\1 degrees Celsius", text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°", r"\1 degrees", text)
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    text = re.sub(r"(\d)\s*mph\b", r"\1 miles per hour", text)
    text = re.sub(r"(\d)\s*km/h\b", r"\1 kilometers per hour", text)
    text = re.sub(r"\s*[—–]\s*", ", ", text)                             # em dashes -> natural pause
    text = text.replace("&", " and ").replace("e.g.", "for example").replace("i.e.", "that is")
    return re.sub(r"\s{2,}", " ", text).strip()


class Speaker:
    """Speaks on a dedicated thread so the UI never blocks."""

    def __init__(self, voice_id="", rate=185):
        self.voice_id, self.rate = voice_id, rate
        self.enabled = pyttsx3 is not None
        self._q: queue.Queue = queue.Queue()
        self._engine = None
        self.speaking = False
        if self.enabled:
            threading.Thread(target=self._run, daemon=True, name="tts").start()

    def _run(self):
        try:
            import pythoncom  # pyttsx3's SAPI driver needs COM on this thread
            pythoncom.CoInitialize()
        except ImportError:
            pass
        self._engine = pyttsx3.init()
        self._apply()
        while True:
            text = self._q.get()
            if text is None:
                break
            if isinstance(text, tuple):
                self._apply()
                continue
            self.speaking = True
            try:
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                pass
            self.speaking = False

    def _apply(self):
        if self._engine:
            if self.voice_id:
                self._engine.setProperty("voice", self.voice_id)
            self._engine.setProperty("rate", self.rate)

    def configure(self, voice_id, rate):
        self.voice_id, self.rate = voice_id, rate
        self._q.put(("configure",))  # applied on the tts thread, where the engine lives

    def say(self, text):
        if self.enabled and text:
            self._q.put(_clean_for_speech(text))

    def stop(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass


GREETINGS = {"hey", "hi", "hay", "hei", "heh", "a", "ok", "okay", "yo", "hello", "say", "eh", "hej"}


def _similar(word: str, name: str) -> bool:
    if word == name:
        return True
    # Whisper spells names loosely: Johnny / Jonny / Johnnie / Johny / Joni ...
    squash = lambda w: re.sub(r"(.)\1+", r"\1", w.replace("h", ""))  # noqa: E731
    if squash(word) == squash(name):
        return True
    return difflib.SequenceMatcher(None, word, name).ratio() >= 0.75


def match_wake(text: str, wake_phrase: str):
    """Return the command spoken after the wake phrase ('' if just the phrase), or None if it wasn't said.

    'Hey Johnny, open Spotify' -> 'open Spotify';  'Hey Johnny.' -> '';  'tell Johnny later' -> None
    """
    name = wake_phrase.lower().split()[-1] if wake_phrase.strip() else "johnny"
    words = list(re.finditer(r"[A-Za-z']+", text))
    for i, m in enumerate(words[:6]):  # the wake phrase has to open the utterance
        w = m.group(0).lower()
        hit = _similar(w, name) and (i == 0 or words[i - 1].group(0).lower() in GREETINGS)
        if not hit and w.startswith(("hey", "hi")):  # "Heyjohnny"
            rest = w[3:] if w.startswith("hey") else w[2:]
            hit = len(rest) >= 3 and _similar(rest, name)
        if hit:
            return text[m.end():].strip(" ,.!?;:-").strip()
    return None


def chime(soft=False):
    """Short rising two-tone 'I'm listening' cue (soft=True: a gentle sine blip for 'your turn')."""
    try:
        if soft and np is not None and sd is not None:
            sr = 24000
            t = np.linspace(0, 0.16, int(sr * 0.16), False)
            tone = np.concatenate([np.sin(2 * np.pi * 660 * t[:len(t) // 2]), np.sin(2 * np.pi * 990 * t[len(t) // 2:])])
            fade = np.minimum(1, np.minimum(np.arange(len(t)), np.arange(len(t))[::-1]) / 600)
            sd.play((tone * fade * 0.12).astype("float32"), sr)
            sd.wait()
            return
        import winsound
        winsound.Beep(880, 90)
        winsound.Beep(1320, 110)
    except Exception:
        pass


def input_devices():
    """[(name, is_default)] for microphones. Uses the MME host API, which every Windows mic appears under."""
    if not sd:
        return []
    try:
        default = sd.default.device[0]
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["hostapi"] == 0 and "Sound Mapper" not in d["name"]:
                out.append((d["name"], i == default))
        return out
    except Exception:
        return []


def _device_index(name):
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["name"] == name:
            return i
    return None  # device unplugged -> fall back to the Windows default


class Listener:
    """Streams the microphone, cuts speech into utterances, and transcribes them locally with Whisper."""

    SAMPLE_RATE = 16000
    BLOCK = 0.1  # seconds per audio block

    def __init__(self, model_size="base.en", device_name=""):
        self.available = all((np, sd, WhisperModel))
        self.model_size = model_size
        self.device_name = device_name
        self._model = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._stop = False
        self.level = 0.0            # live input level 0..1 for meters
        self.last_heard = ""        # last transcript, for the settings/debug readout
        self.dead_mic = False       # selected mic sends pure digital silence (muted / off)
        self.in_speech = False
        self.debug = None           # set to [] to record (level, threshold, talking, playback, coupling) per block

    def interrupt(self):
        """Make an in-progress listen()/utterances() return early (e.g. push-to-talk taking over)."""
        self._stop = True

    # ---------------------------------------------------------------- model
    def _load(self):
        with self._load_lock:
            if self._model is None:
                try:
                    m = WhisperModel(self.model_size, device="cuda", compute_type="float16")
                    # CUDA libraries (cuBLAS/cuDNN) only load on first use, so prove the GPU path works now
                    list(m.transcribe(np.zeros(self.SAMPLE_RATE, dtype="float32"), language="en")[0])
                    self._model = m
                except Exception:
                    self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            return self._model

    def preload(self):
        if self.available:
            threading.Thread(target=self._load, daemon=True).start()

    def transcribe(self, audio) -> str:
        segs, _ = self._load().transcribe(audio, language="en", vad_filter=True, beam_size=1)
        # drop segments Whisper itself thinks are noise, so background sound can't fake a wake phrase
        text = " ".join(s.text.strip() for s in segs if s.no_speech_prob < 0.6).strip()
        if text:
            self.last_heard = text
        return text

    # ---------------------------------------------------------------- capture
    def _stream(self):
        """Open the chosen mic at its native rate (resampled to 16 kHz ourselves - more reliable than asking
        the driver for 16 kHz)."""
        dev = _device_index(self.device_name)
        info = sd.query_devices(dev if dev is not None else sd.default.device[0], "input")
        rate = int(info["default_samplerate"]) or 44100
        st = sd.InputStream(device=dev, samplerate=rate, channels=1, dtype="float32", blocksize=int(rate * self.BLOCK))
        return st, rate

    def _to16k(self, chunks, rate):
        audio = np.concatenate(chunks).flatten()
        if rate == self.SAMPLE_RATE:
            return audio.astype("float32")
        n = int(len(audio) * self.SAMPLE_RATE / rate)
        return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")

    def utterances(self, should_pause=lambda: False, max_seconds=10.0, silence_seconds=0.7, on_dead_mic=None,
                   echo_fn=None, on_speech_start=None):
        """Yield 16 kHz audio for each stretch of speech heard, until interrupted or should_pause() is true.

        Uses an adaptive noise floor so quiet headset mics and noisy rooms both work.
        echo_fn() -> (talking, playback_level): while Johnny talks the mic also hears him through the speakers.
        With the live playback level we learn how much of his voice leaks into the mic (≈0 on a headset, more on
        speakers) and only count sound that is clearly LOUDER than that expected echo - the user's voice -
        sustained for 0.3 s. Without a playback level we fall back to riding above the measured echo.
        on_speech_start() fires the moment the user starts talking - used to cut Johnny off mid-sentence.
        """
        if not self.available:
            raise RuntimeError("Voice input isn't installed.")
        with self._lock:
            self._stop = False
            st, rate = self._stream()
            with st:
                noise, pre, speech, silent, peak_seen = 0.002, [], None, 0.0, 0.0
                blocks, warned, echo, loud_run, voiced = 0, False, 0.0, 0, 0
                coupling, coupling_n = 0.3, 0   # mic level per unit of playback level (learned)
                recent = []
                while not self._stop and (speech is not None or not should_pause()):
                    data, _ = st.read(int(rate * self.BLOCK))
                    data = data.copy()
                    lvl = float(np.sqrt(np.mean(data ** 2)))
                    peak_seen = max(peak_seen, float(np.abs(data).max()))
                    self.level = min(1.0, lvl * 15)
                    blocks += 1
                    if not warned and blocks * self.BLOCK >= 3:
                        self.dead_mic = peak_seen == 0.0
                        if self.dead_mic and on_dead_mic:
                            on_dead_mic()
                        warned = True
                    e = echo_fn() if echo_fn else (False, None)
                    talking, play = e if isinstance(e, tuple) else (bool(e), None)
                    base = max(0.0035, noise * 3.0)
                    if talking and play is not None:
                        if play > 0.01 and speech is None:
                            r = lvl / play
                            if coupling_n < 5 or r < coupling * 3:   # skip outliers (that's the user talking)
                                a = 0.5 if coupling_n < 5 else 0.15      # settle fast, then track slowly
                                coupling = (1 - a) * coupling + a * r
                                coupling_n += 1
                        thr = max(base, coupling * play * 1.5 + noise * 2)
                    else:
                        if talking and speech is None:
                            echo = max(lvl, echo * 0.93)  # no reference: ride above the measured echo
                        elif not talking:
                            echo *= 0.8                   # echo tail fades fast once he stops
                        thr = max(base, echo * 1.8)
                    need = 2 if talking else 1            # blocks of voice before it counts (0.2 s over Johnny)
                    self.in_speech = speech is not None
                    if self.debug is not None:
                        self.debug.append((round(lvl, 4), round(thr, 4), talking, round(play or 0, 4), round(coupling, 3)))
                    if speech is None:
                        pre = (pre + [data])[-5:]          # 0.5 s pre-roll so "Hey" isn't clipped
                        recent = (recent + [lvl > thr])[-5:]
                        loud_run = sum(recent) if talking else (loud_run + 1 if lvl > thr else 0)
                        if loud_run >= need:   # talking over Johnny: 0.2 s of voice within any 0.5 s window
                            # over Johnny the pre-roll is mostly his own echo - keep only the triggering audio
                            speech = list(pre[-(need + 1):]) if talking else list(pre)
                            silent, voiced = 0.0, loud_run
                            loud_run, recent = 0, []
                            self.in_speech = True
                            if on_speech_start:
                                on_speech_start()
                        elif not talking and lvl <= thr:  # learn the room's noise floor while quiet
                            noise = 0.95 * noise + 0.05 * lvl
                    else:
                        speech.append(data)
                        quiet = lvl <= max(0.0035, noise * 3.0)
                        silent = silent + self.BLOCK if quiet else 0.0
                        voiced += 0 if quiet else 1
                        if silent >= silence_seconds or len(speech) * self.BLOCK >= max_seconds:
                            if voiced * self.BLOCK >= 0.3:  # real speech, not a click / tap / chime
                                yield self._to16k(speech, rate)
                            speech, pre = None, []
                self.level = 0.0
                self.in_speech = False

    def listen(self, max_seconds=15, silence_seconds=1.2, on_level=None, wait_seconds=6.0) -> str:
        """Push-to-talk: capture one utterance (waiting up to wait_seconds for speech to start)."""
        deadline = time.time() + wait_seconds

        def pause():
            if on_level:
                on_level(self.level)
            return time.time() > deadline and not self.in_speech

        for audio in self.utterances(should_pause=pause, max_seconds=max_seconds, silence_seconds=silence_seconds):
            self._stop = True
            return self.transcribe(audio)
        return ""
