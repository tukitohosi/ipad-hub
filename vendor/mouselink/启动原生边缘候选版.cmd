@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "%~dp0runtime\build-venv\Scripts\pythonw.exe" "%~dp0open_bridge\desktop_app.py" --native-edges --trial-seconds 180
