"""Minimal dependency-free Ollama REST client."""
import http.client
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from .proc import clean_spawn

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class OllamaError(RuntimeError):
    pass


# Every request must use the same context size and keep-alive: Ollama reloads the whole model whenever num_ctx
# changes, which made chat <-> game-coach switches (and the first reply after warm-up) take 20+ seconds.
NUM_CTX = 8192
KEEP_ALIVE = "30m"


class Ollama:
    def __init__(self, host: str = "http://127.0.0.1:11434"):
        self.host = host.rstrip("/")

    # ---- low level -------------------------------------------------------
    def _req(self, path, payload=None, method=None, timeout=30):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def _stream(self, path, payload, timeout=600):
        """Yield JSON lines. If Ollama is down (closed, updating, crashed) before any data arrives, restart it
        and retry once instead of failing the user's request."""
        for attempt in (1, 2):
            got_data = False
            try:
                with self._req(path, payload, timeout=timeout) as resp:
                    for raw in resp:
                        line = raw.strip()
                        if line:
                            obj = json.loads(line)
                            if "error" in obj:
                                raise OllamaError(obj["error"])
                            got_data = True
                            yield obj
                return
            except urllib.error.HTTPError as e:
                raise OllamaError(e.read().decode(errors="ignore") or str(e)) from e
            except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as e:
                if attempt == 1 and not got_data and self.ensure_running():
                    continue
                reason = getattr(e, "reason", e)
                raise OllamaError(f"Cannot reach Ollama at {self.host}: {reason}") from e

    # ---- service ---------------------------------------------------------
    def is_running(self) -> bool:
        try:
            with self._req("/api/version", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def version(self) -> str:
        try:
            with self._req("/api/version", timeout=3) as r:
                return json.loads(r.read()).get("version", "?")
        except Exception:
            return ""

    @staticmethod
    def installed() -> bool:
        if shutil.which("ollama"):
            return True
        local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        return os.path.exists(local)

    @staticmethod
    def exe() -> str:
        return shutil.which("ollama") or os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")

    @clean_spawn
    def ensure_running(self, wait=20) -> bool:
        if self.is_running():
            return True
        if not self.installed():
            return False
        tray = os.path.join(os.path.dirname(self.exe()), "ollama app.exe")
        try:
            if os.path.exists(tray):
                subprocess.Popen([tray], creationflags=_NO_WINDOW)
            else:
                subprocess.Popen([self.exe(), "serve"], creationflags=_NO_WINDOW,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        for _ in range(wait * 2):
            if self.is_running():
                return True
            time.sleep(0.5)
        return False

    # ---- models ----------------------------------------------------------
    def list_models(self) -> list[str]:
        try:
            with self._req("/api/tags", timeout=5) as r:
                return [m["name"] for m in json.loads(r.read()).get("models", [])]
        except Exception:
            return []

    def has_model(self, tag: str) -> bool:
        names = self.list_models()
        return tag in names or (":" not in tag and f"{tag}:latest" in names)

    def capabilities(self, tag: str) -> list:
        """e.g. ['completion', 'vision', 'tools']; [] if unknown (older Ollama)."""
        try:
            with self._req("/api/show", {"model": tag}, timeout=10) as r:
                return json.loads(r.read()).get("capabilities") or []
        except Exception:
            return []

    def pull(self, tag: str):
        """Yields (status_text, fraction_or_None)."""
        for obj in self._stream("/api/pull", {"model": tag, "stream": True}, timeout=7200):
            total, done = obj.get("total"), obj.get("completed")
            frac = (done / total) if total and done is not None else None
            yield obj.get("status", ""), frac

    # ---- inference -------------------------------------------------------
    def chat(self, model, messages, tools=None, options=None, stream=True, think=None):
        payload = {"model": model, "messages": messages, "stream": stream, "keep_alive": KEEP_ALIVE,
                   "options": {**(options or {}), "num_ctx": NUM_CTX}}
        if tools:
            payload["tools"] = tools
        if think is not None:
            payload["think"] = think
        if stream:
            return self._stream("/api/chat", payload)
        for attempt in (1, 2):
            try:
                with self._req("/api/chat", payload, timeout=600) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                raise OllamaError(e.read().decode(errors="ignore") or str(e)) from e
            except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as e:
                if attempt == 1 and self.ensure_running():
                    continue
                raise OllamaError(f"Cannot reach Ollama at {self.host}: {getattr(e, 'reason', e)}") from e

    def warm(self, model):
        """Load the model into memory so the first reply is quick."""
        try:
            with self._req("/api/generate", {"model": model, "prompt": "", "keep_alive": KEEP_ALIVE,
                                             "options": {"num_ctx": NUM_CTX}}, timeout=300):
                pass
        except Exception:
            pass
