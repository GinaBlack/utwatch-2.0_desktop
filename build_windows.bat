@echo off
rem Builds a Windows folder with UTWatch.exe using PyInstaller. Not yet tested on Windows: expect to adjust.
cd /d "%~dp0"
call .venv\Scripts\activate
pip install pyinstaller
pyinstaller --noconfirm --windowed --name UTWatch --collect-all PySide6 run.py
echo Built: dist\UTWatch\UTWatch.exe
echo Test it on a clean Windows user account before relying on it.
pause
