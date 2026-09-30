@echo off
rem Builds dist\m75.exe (single file, no Python needed to run it).
cd /d %~dp0
py -3 -m pip install --user -q pyinstaller || exit /b 1
py -3 -m PyInstaller --onefile --console --name m75 --clean --noconfirm --log-level WARN m75.py
