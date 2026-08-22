@echo off
cd /d D:\OpenCode\DeepSeekBridge

REM Kill old bridge if running
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8084" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

REM Start bridge
python main.py
