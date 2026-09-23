@echo off
REM Double-click this. pythonw runs the window with no console behind it.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "earshot_setup.py"
) else (
  start "" pythonw "earshot_setup.py"
)
