@echo off
rem Creates a virtual environment on first run, installs the requirements, then starts UTWatch.
cd /d "%~dp0"
if not exist .venv (
  py -3.11 -m venv .venv || (echo Python 3.11 was not found. Install it from python.org and tick "Add to PATH". & pause & exit /b 1)
  call .venv\Scripts\activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
python run.py
if errorlevel 1 pause
