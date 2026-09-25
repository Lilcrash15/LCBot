@echo off
setlocal enabledelayedexpansion

echo === LCBot: force-rebuild Windows' taskbar/icon cache ===
echo.
echo Use this ONLY if lcbot.log shows every icon-related line succeeding
echo (AppUserModelID set, both "applied ... app icon" lines) and the
echo taskbar icon is STILL wrong -- that combination means the app is
echo doing everything right and this is Windows' own icon cache being
echo stale, not a bug in LCBot. This script clears that cache and makes
echo Windows rebuild it from scratch.
echo.
echo What this does:
echo  1. Closes Explorer (your desktop/taskbar disappear for a few
echo     seconds -- this is normal, nothing else closes or is affected).
echo  2. Deletes Windows' icon cache files (safe -- Windows just
echo     regenerates them from the real icons the next time it needs to).
echo  3. Restarts Explorer.
echo.
echo Close TwitchChatBotV2 first if it's running. Press Ctrl+C now to
echo cancel, or...
pause

echo.
echo Closing Explorer...
taskkill /IM explorer.exe /F >nul 2>nul

echo Deleting icon cache files...
del /a /q "%localappdata%\IconCache.db" >nul 2>nul
del /a /f /q "%localappdata%\Microsoft\Windows\Explorer\iconcache*.db" >nul 2>nul

echo Restarting Explorer...
start explorer.exe

echo.
echo === Done ===
echo Give the taskbar a few seconds to come back, then launch
echo TwitchChatBotV2.exe again and check the icon.
echo.
echo If it's STILL wrong after this: check whether TwitchChatBotV2.exe is
echo PINNED to your taskbar. A pinned icon can be cached separately from
echo the running app's own icon -- unpin it now, run this script again,
echo then launch the app fresh and re-pin it from the running window
echo (right-click the taskbar icon while it's running -> Pin to taskbar)
echo rather than pinning the .exe file itself.
echo.
pause
