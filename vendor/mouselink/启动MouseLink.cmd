@echo off
chcp 65001 >nul
if exist "%~dp0release\1.0.3\MouseLink\MouseLink.exe" (
    start "" "%~dp0release\1.0.3\MouseLink\MouseLink.exe"
) else (
    echo 未找到打包应用，请先运行 tools\Build-Desktop.ps1。
    pause
)
