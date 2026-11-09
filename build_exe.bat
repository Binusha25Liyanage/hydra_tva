@echo off
REM Builds dist\Hydra TvA\Hydra TvA.exe (folder build; give users the whole folder, e.g. zipped).
REM Before building: in your copy, SFA Connection -> Admin -> "Save current profiles as defaults"
REM (writes tva\sfa_defaults.json, which is bundled so users get your login URL and report profiles).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
pip install pyinstaller
set EXTRA=
if exist tva\sfa_defaults.json set EXTRA=--add-data "tva\sfa_defaults.json;tva"
pyinstaller --noconfirm --windowed --name "Hydra TvA" --add-data "ui;ui" %EXTRA% --collect-all playwright main.py
echo.
echo Done: dist\Hydra TvA\Hydra TvA.exe
echo Users need Microsoft Edge (already on Windows 10/11) or Google Chrome. No other install needed.
echo To give a PC admin rights (save defaults), put an empty file named admin.flag next to the exe.
pause
