@echo off
REM Starts the Bard Service on port 8090 (syncs .venv with uv on each run).
REM LABEL: Bard Service
REM DESCRIPTION: Text to speech MCP tool server (Piper or Paradee) for Citadel and Ember.

cd /d "%~dp0"

uv sync
if errorlevel 1 (
    echo uv sync failed. Install uv: winget install astral-sh.uv
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

python -m src.run
pause
