@echo off
cd /d "%~dp0"

echo ======================================
echo    DeepSeekBridge - First Time Setup
echo ======================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
pip install -r requirements.txt -q

echo [2/3] Installing Playwright browser...
python -m playwright install chromium

echo [3/3] Creating browser profile and shortcut...
if not exist "browser_profile" mkdir browser_profile
powershell -Command "$shell = New-Object -ComObject WScript.Shell; $shortcut = $shell.CreateShortcut('%USERPROFILE%\Desktop\DeepSeekBridge.lnk'); $shortcut.TargetPath = 'cmd.exe'; $shortcut.Arguments = '/k cd /d ""%~dp0"" && python main.py'; $shortcut.WorkingDirectory = '%~dp0'; $shortcut.Save()"

echo.
echo ======================================
echo    Setup Complete!
echo ======================================
echo.
echo NEXT STEPS:
echo 1. Double-click "DeepSeekBridge" on your desktop
echo 2. Chrome will open - login to chat.deepseek.com
echo 3. After login, the bridge is ready to use
echo 4. Open OpenCode - you can now use deepseek/deepseek-chat
echo.
echo Your login is saved - you only need to login once!
echo.
pause
