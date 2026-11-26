@echo off
rem One-time setup on Windows: builds the environment, then downloads and
rem verifies the pinned model files. Safe to run again.
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required: https://docs.astral.sh/uv/getting-started/installation/
  exit /b 1
)
echo Building the environment (.venv)...
uv sync --frozen --no-dev
if errorlevel 1 exit /b 1
echo Fetching model files...
".venv\Scripts\python.exe" fetch_models.py
if errorlevel 1 exit /b 1
echo.
echo Setup complete. Start Dogspeak with dogspeak.cmd
echo Optional: .venv\Scripts\python.exe windows_install.py   (adds Dogspeak to the Start menu)
