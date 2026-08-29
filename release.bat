@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === LCBot: tag and publish a release ===
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo Git isn't installed -- see setup_github.bat for the install link.
    pause
    exit /b 1
)

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    echo No GitHub remote is set up yet -- run setup_github.bat first.
    pause
    exit /b 1
)

set /p VERSION="Version to release (e.g. v0.1.0 -- include the 'v'): "
if "%VERSION%"=="" (
    echo No version entered -- stopping.
    pause
    exit /b 1
)

echo.
echo Reminder: bump __version__ in chatbot\__init__.py and add a
echo CHANGELOG.md entry for %VERSION% BEFORE running this, if you
echo haven't already -- this script tags whatever is currently
echo committed, it doesn't edit those files for you.
echo.
set /p CONFIRM="Continue tagging %VERSION%? (y/n): "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

git tag %VERSION%
if errorlevel 1 (
    echo Tagging failed -- does that tag already exist? ^(git tag -l to check^)
    pause
    exit /b 1
)
git push origin %VERSION%

where gh >nul 2>nul
if errorlevel 1 (
    echo.
    echo GitHub CLI ^(gh^) not found -- the tag is pushed, but you'll need to
    echo draft the release by hand: go to your repo's Releases page on
    echo github.com, click "Draft a new release", pick tag %VERSION%, and
    echo publish it. If dist\TwitchChatBotV2.exe exists, attach it there too.
    pause
    exit /b 0
)

echo.
if exist dist\TwitchChatBotV2.exe (
    echo Creating GitHub release %VERSION% and attaching dist\TwitchChatBotV2.exe...
    gh release create %VERSION% dist\TwitchChatBotV2.exe --title "%VERSION%" --generate-notes
) else (
    echo dist\TwitchChatBotV2.exe not found -- run build_exe.bat first if you
    echo want the compiled exe attached to this release. Creating the
    echo release without it for now...
    gh release create %VERSION% --title "%VERSION%" --generate-notes
)

echo.
echo === Done ===
pause
