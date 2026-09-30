@echo off
chcp 65001 >nul
setlocal
set "MOUSELINK_PYTHON=%~dp0runtime\build-venv\Scripts\pythonw.exe"
if not exist "%MOUSELINK_PYTHON%" set "MOUSELINK_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%MOUSELINK_PYTHON%" (
    echo Python runtime not found. See README.zh-CN.md for development setup.
    pause
    exit /b 1
)
set "MOUSELINK_DEV_DATA=%~dp0test-results\placement-1.0.3\app-data"
if not exist "%MOUSELINK_DEV_DATA%" mkdir "%MOUSELINK_DEV_DATA%"
if not exist "%MOUSELINK_DEV_DATA%\settings.json" if exist "%LOCALAPPDATA%\MouseLink\settings.json" copy /y "%LOCALAPPDATA%\MouseLink\settings.json" "%MOUSELINK_DEV_DATA%\settings.json" >nul
set "QT_QPA_PLATFORM=windows"
start "" "%MOUSELINK_PYTHON%" "%~dp0open_bridge\desktop_app.py" --data-dir "%MOUSELINK_DEV_DATA%"
