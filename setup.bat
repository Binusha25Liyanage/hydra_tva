@echo off
REM One-time setup: creates .venv with Python 3.12 and installs requirements.
cd /d "%~dp0"
py -3.12 -m venv .venv || (echo Python 3.12 not found. Install it from python.org & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup complete. Start the app with run.bat
pause
