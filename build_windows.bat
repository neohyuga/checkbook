@echo off
rem Build Checkbook.exe on a Windows computer (needs Python 3.11+ from python.org).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv || exit /b 1
)
.venv\Scripts\python -m pip install -r requirements-dev.txt || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name Checkbook ^
  Checkbook.pyw || exit /b 1
echo.
echo Done:  dist\Checkbook.exe
