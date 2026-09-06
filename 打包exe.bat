@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
py -3.11 build_exe.py
endlocal
pause
