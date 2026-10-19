@echo off
REM Sets up a virtual environment the first time, then starts SituSim.
cd /d "%~dp0"
if not exist .venv (
  echo First run: creating .venv and installing packages...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python main.py
pause
