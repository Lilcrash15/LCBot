@echo off
setlocal
cd /d "%~dp0"

echo === Twitch Chat Bot: build .exe ===
echo Folder: %cd%
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Could not find "python" on PATH. Install Python 3.10+ from python.org
    echo and make sure "Add python.exe to PATH" was checked during setup.
    pause
    exit /b 1
)

echo Installing/upgrading PyInstaller...
python -m pip install --upgrade pip >nul
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo pip install pyinstaller failed -- see the error above.
    pause
    exit /b 1
)

echo.
echo Cleaning previous build output...
echo (Reusing an old "build" folder across a PyInstaller upgrade is a
echo  known cause of "ordinal ### could not be located" errors -- it
echo  mixes cached files from the old PyInstaller version with the new
echo  one's bootloader. Wiping it every time avoids that entirely.)
if exist build rmdir /s /q build
if exist dist\TwitchChatBotV2.exe del /f /q dist\TwitchChatBotV2.exe
if exist dist\TwitchChatBotV2.exe (
    echo Couldn't delete the old dist\TwitchChatBotV2.exe -- it's probably
    echo still running. Close it from Task Manager, then run this again.
    pause
    exit /b 1
)

echo.
echo Building TwitchChatBotV2.exe (this can take a minute or two)...
echo (Named V2 so this doesn't collide with an old TwitchChatBot.exe
echo  that might still be running/locked from a previous test.)
set ICON_FLAG=
if exist assets\icon.ico (
    echo Using assets\icon.ico as the app icon.
    rem PyInstaller 6.x resolves a *relative* --icon path against its
    rem workpath ("build"), not the project folder, which is a known
    rem quirk -- with --workpath set to "build" it went looking for
    rem "build\assets\icon.ico" and crashed with FileNotFoundError
    rem *after* already copying the bare bootloader into dist\, which
    rem is exactly the broken half-built exe that was throwing
    rem "ordinal not found" on launch. Using an absolute path (%~dp0
    rem is this .bat's own folder, with a trailing backslash) sidesteps
    rem that resolution bug entirely.
    set ICON_FLAG=--icon=%~dp0assets\icon.ico
)
python -m PyInstaller --onefile --windowed --name TwitchChatBotV2 --distpath dist --workpath build --specpath build %ICON_FLAG% run_bot.py
if errorlevel 1 (
    echo Build failed -- see the error above.
    pause
    exit /b 1
)

echo Copying the overlay folder next to the exe...
xcopy /E /I /Y overlay dist\overlay >nul

echo Copying the assets folder next to the exe...
rem The --icon= flag above only bakes the icon into the exe's own file
rem icon (what Explorer shows) -- the app also sets its window/taskbar
rem icon at runtime by loading assets\icon.ico from next to wherever
rem it's running from, so that folder needs to actually be there next
rem to the exe, not just at build time in the project folder.
if exist assets xcopy /E /I /Y assets dist\assets >nul

echo.
echo === Done ===
echo Your exe is at: %cd%\dist\TwitchChatBotV2.exe
echo The "dist" folder also has a copy of "overlay" next to it -- keep
echo them together (that's what OBS/Streamlabs OBS should point at).
echo.
echo If an old TwitchChatBot.exe window is still open, close it (it's
echo the outdated light-themed version) and just use TwitchChatBotV2.exe
echo from now on. It writes its own config.json and chatbot.db next to
echo itself on first run.
echo.
pause
