@echo off
setlocal
cd /d "%~dp0"
echo Building a console (non-windowed) debug exe so we can see errors...
python -m PyInstaller --onefile --name TwitchChatBot_debug --distpath dist_debug --workpath build_debug --specpath build_debug run_bot.py
echo.
echo Build done. Launching it now -- leave this window open to read any error:
echo.
dist_debug\TwitchChatBot_debug.exe
echo.
echo === exited with code %errorlevel% ===
pause
