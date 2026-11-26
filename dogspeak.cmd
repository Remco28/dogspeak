@echo off
rem Starts Dogspeak, or hands the command to the one already running.
rem   dogspeak.cmd [--toggle-recording] [--import-audio FILE]
rem No cd: a relative FILE belongs to the folder this was started from.
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Run setup.cmd first.
  exit /b 1
)
set "PYTHONPATH=%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m dogspeak_app.qt_app %*
