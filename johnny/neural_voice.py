"""Natural, Siri-like offline speech with Kokoro (82M-parameter neural TTS, runs ~6x realtime on CPU).

Text is split into sentences: the next sentence is synthesized while the current one plays, so speech starts
within a fraction of a second and long replies flow without gaps.
"""
import queue
import re
import threading
import time
import urllib.request

from . import config

try:
    import sounddevice as sd
    from kokoro_onnx import Kokoro
except Exception:  # optional dependency
    Kokoro = None

MODEL_DIR = config.DATA_DIR / "voices"
BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
FILES = {"kokoro-v1.0.onnx": 325_505_369, "voices-v1.0.bin": 28_214_398}

# voice id -> (friendly name, language)
VOICES = {
    "af_heart": ("Heart — warm American female (most Siri-like)", "en-us"),
    "af_bella": ("Bella — bright American female", "en-us"),
    "af_nicole": ("Nicole — soft, calm American female", "en-us"),
    "af_sky": ("Sky — light American female", "en-us"),
    "am_michael": ("Michael — friendly American male", "en-us"),
    "am_fenrir": ("Fenrir — deep American male", "en-us"),
    "am_puck": ("Puck — upbeat American male", "en-us"),
    "am_echo": ("Echo — smooth American male", "en-us"),
    "bm_george": ("George — British male (classic butler)", "en-gb"),
    "bm_lewis": ("Lewis — deep British male", "en-gb"),
    "bm_fable": ("Fable — storyteller British male", "en-gb"),
    "bf_emma": ("Emma — British female", "en-gb"),
    "bf_isabella": ("Isabella — warm British female", "en-gb"),
}


def available() -> bool:
    return Kokoro is not None


def model_ready() -> bool:
    return all((MODEL_DIR / f).exists() and (MODEL_DIR / f).stat().st_size == size for f, size in FILES.items())


def download_model(on_progress=None):
    """Fetch the voice model (~350 MB, once). on_progress(fraction, text)."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    total = sum(FILES.values())
    done = 0
    for name, size in FILES.items():
        dest = MODEL_DIR / name
        if dest.exists() and dest.stat().st_size == size:
            done += size
            continue
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(BASE + name, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done / total, f"Downloading natural voice… {done / 1e6:.0f} / {total / 1e6:.0f} MB")
        tmp.replace(dest)
    if on_progress:
        on_progress(1.0, "Natural voice ready.")


_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_sentences(text):
    parts, buf = [], ""
    for s in _SENTENCE.split(text):
        s = s.strip()
        if not s:
            continue
        buf = f"{buf} {s}".strip() if buf else s
        if len(buf) >= 18:          # merge tiny fragments ("Yes." "Done.") for natural rhythm
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    # break very long sentences at a comma so the first audio starts sooner
    out = []
    for p in parts:
        while len(p) > 140:
            cut = p.rfind(", ", 40, 120)
            if cut < 0:
                break
            out.append(p[:cut + 1])
            p = p[cut + 2:]
        out.append(p)
    return out


class NeuralSpeaker:
    """Same interface as voice.Speaker: say(), stop(), configure(), .speaking, ._q"""

    def __init__(self, voice="af_heart", speed=1.0, clean=None):
        self.voice, self.speed = voice if voice in VOICES else "af_heart", speed
        self.clean = clean or (lambda t: t)
        self.enabled = available() and model_ready()
        self._q: queue.Queue = queue.Queue()        # text waiting to be synthesized
        self._audio: queue.Queue = queue.Queue()    # synthesized audio waiting to play
        self._gen = 0                               # bumps on stop() so stale audio is dropped
        self._synth_busy = self._play_busy = False
        self._now_playing = None
        self._synth_gen = self._play_gen = -1
        self._k = None
        self.error = ""
        if self.enabled:
            threading.Thread(target=self._synth_loop, daemon=True, name="tts-synth").start()
            threading.Thread(target=self._play_loop, daemon=True, name="tts-play").start()

    @property
    def speaking(self):
        """True only for speech that is still wanted - work left over from before stop() doesn't count."""
        g = self._gen
        return ((self._synth_busy and self._synth_gen == g) or (self._play_busy and self._play_gen == g)
                or any(item[0] == g for item in list(self._q.queue))
                or any(item[0] == g for item in list(self._audio.queue)))

    def _model(self):
        if self._k is None:
            self._k = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
        return self._k

    def _synth_loop(self):
        while True:
            gen, text = self._q.get()
            self._synth_busy, self._synth_gen = True, gen
            try:
                if gen == self._gen:
                    lang = VOICES.get(self.voice, ("", "en-us"))[1]
                    for sentence in split_sentences(text):
                        if gen != self._gen:
                            break
                        samples, sr = self._model().create(sentence, voice=self.voice, speed=self.speed, lang=lang)
                        if gen != self._gen:
                            break
                        self._audio.put((gen, samples, sr))
            except Exception as e:
                self.error = repr(e)
            finally:
                self._synth_busy = not self._q.empty()

    def _play_loop(self):
        while True:
            gen, samples, sr = self._audio.get()
            self._play_busy, self._play_gen = True, gen
            if gen != self._gen:
                self._play_busy = False
                continue
            try:
                self._now_playing = (samples, sr, time.time())
                sd.play(samples, sr)
                sd.wait()
            except Exception as e:
                self.error = repr(e)
            finally:
                self._now_playing = None
                self._play_busy = False

    def playback_level(self, window=0.3):
        """Loudness of what Johnny is outputting right now (max RMS over the last `window` seconds).
        The listener compares the mic against this to tell the user's voice apart from Johnny's own echo."""
        np_ = self._now_playing
        if not np_:
            return 0.0
        samples, sr, t0 = np_
        end = int((time.time() - t0) * sr)
        start = max(0, end - int(window * sr))
        seg = samples[start:max(end, start + 1)]
        if len(seg) == 0:
            return 0.0
        hop = max(1, int(0.05 * sr))
        return float(max((float((seg[i:i + hop] ** 2).mean()) ** 0.5 for i in range(0, len(seg), hop)), default=0.0))

    def say(self, text):
        if self.enabled and text:
            text = self.clean(text)
            if text:
                self._q.put((self._gen, text))

    def stop(self):
        self._gen += 1
        for q in (self._q, self._audio):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        try:
            sd.stop()
        except Exception:
            pass

    def configure(self, voice=None, speed=None):
        if voice in VOICES:
            self.voice = voice
        if speed:
            self.speed = float(speed)

    def warm(self):
        """Load the model and run a tiny synthesis so the first real reply is instant."""
        if self.enabled:
            def w():
                try:
                    self._model().create("Ready.", voice=self.voice, lang=VOICES[self.voice][1])
                except Exception as e:
                    self.error = repr(e)
            threading.Thread(target=w, daemon=True).start()
