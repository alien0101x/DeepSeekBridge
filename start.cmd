@echo off
REM Copyright (c) 2026 alien0101x - DeepSeekBridge
REM github.com/alien0101x/DeepSeekBridge - MIT License
cd /d "%~dp0"

REM Kill old bridge if running
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8084" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

REM Start bridge (always log to bridge.log for debugging)
python main.py > "%~dp0bridge.log" 2>&1
