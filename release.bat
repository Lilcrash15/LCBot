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

git rev-parse "%VERSION%" >nul 2>nul
if not errorlevel 1 (
    echo.
    echo Tag %VERSION% already exists locally -- skipping the "git tag" step.
    goto push_tag
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
    echo Tagging failed -- see the error above.
    pause
    exit /b 1
)

:push_tag
rem Always push, even if the tag already existed locally -- it may exist
rem locally but not (or no longer) on GitHub, and pushing an already-synced
rem tag is a harmless no-op ("Everything up-to-date").
echo.
echo Making sure %VERSION% is pushed to GitHub...
git push origin %VERSION%
if errorlevel 1 (
    echo.
    echo === Pushing the tag failed -- see the error above ===
    echo Common causes: not signed in, cancelled the sign-in popup, or no
    echo write access to this repo under this GitHub account.
    pause
    exit /b 1
)
where gh >nul 2>nul
if errorlevel 1 (
    echo.
    echo GitHub CLI ^(gh^) not found -- the tag is pushed, but you'll need to
    echo draft the release by hand: go to your repo's Releases page on
    echo github.com, click "Create a new release", pick tag %VERSION%, and
    echo publish it. If dist\TwitchChatBotV2.exe exists, attach it there too.
    echo.
    echo ^(Installing gh from https://cli.github.com/ lets this script do
    echo  all of that for you automatically next time.^)
    pause
    exit /b 0
)

echo.
echo Checking gh sign-in...
gh auth status >nul 2>nul
if errorlevel 1 (
    echo Not signed in yet. Opening browser sign-in via GitHub CLI...
    echo ^(This is between you and GitHub -- nothing is typed here.^)
    gh auth login --web -h github.com
)

gh release view %VERSION% >nul 2>nul
if not errorlevel 1 (
    echo.
    echo A release for %VERSION% already exists on GitHub -- nothing more
    echo to do. Go check github.com/^<you^>/LCBot/releases if you want to
    echo edit it or attach a different file.
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
if errorlevel 1 (
    echo.
    echo === Creating the release failed -- see the error above ===
    pause
    exit /b 1
)

echo.
echo === Done -- check github.com/^<you^>/LCBot/releases ===
pause
