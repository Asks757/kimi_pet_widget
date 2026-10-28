@echo off
setlocal
cd /d "%~dp0"

py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
    goto :python_found
)
python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=python"
    goto :python_found
)
goto :missing_python

:python_found

if not exist ".venv\Scripts\pythonw.exe" (
    %PYTHON% -m venv .venv
    if errorlevel 1 goto :setup_failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :setup_failed
)

start "KimiWhaleWidget" ".venv\Scripts\pythonw.exe" whale_widget.py
exit /b 0

:missing_python
echo Python 3 is required. Install it from https://www.python.org/downloads/
echo During installation, enable "Add Python to PATH".
pause
exit /b 1

:setup_failed
echo Failed to create the Python environment or install PySide6.
pause
exit /b 1
