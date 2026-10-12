"""PyInstaller entry point (a package can't be frozen via `-m`)."""
import sys

from johnny.__main__ import run

if __name__ == "__main__":
    if '--build-smoke-test' in sys.argv:
        # Exercise frozen imports without opening windows or starting services.
        try:
            from johnny import app, setup_wizard
            import ctranslate2
            import faster_whisper
            import kokoro_onnx
            import onnxruntime
        except Exception:
            from pathlib import Path
            import traceback

            Path('build-smoke-test.log').write_text(traceback.format_exc(), encoding='utf-8')
            sys.exit(1)
    else:
        run()
