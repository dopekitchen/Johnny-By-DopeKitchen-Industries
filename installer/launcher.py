"""PyInstaller entry point (a package can't be frozen via `-m`)."""
from johnny.__main__ import run

if __name__ == "__main__":
    run()
