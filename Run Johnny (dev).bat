@echo off
rem Runs Johnny straight from this folder using your system Python (for development).
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
start "" pythonw -m johnny %*
