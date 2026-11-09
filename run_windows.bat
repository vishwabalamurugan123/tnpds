@echo off
cd /d "%~dp0"
echo Installing required packages (first run only)...
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check your internet connection and Python installation.
  pause
  exit /b 1
)
echo Starting TNPDS - PANRUTI TALUK...
start "" http://127.0.0.1:5000
py app.py
pause
