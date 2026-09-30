@echo off
cd /d "%~dp0"
where py >nul 2>&1 && (py -3 m75.py & goto :eof)
python -c "" >nul 2>&1 && (python m75.py & goto :eof)
echo Python 3 is required: https://www.python.org/downloads/
pause
