@echo off
REM Show/hide the DeepSeek bridge Chrome window
REM Usage: chrome.cmd show   |   chrome.cmd hide
cd /d "%~dp0"
if /i "%1"=="hide" (
    powershell -NoProfile -ExecutionPolicy Bypass -File chrome_move.ps1 -32000 -32000
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File chrome_move.ps1 60 60
)
pause
