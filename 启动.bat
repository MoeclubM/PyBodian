@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动.ps1" -Action gui
endlocal
