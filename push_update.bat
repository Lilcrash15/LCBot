@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === LCBot: push your latest changes to GitHub ===
echo Folder: %cd%
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo Git isn't installed, or isn't on PATH.
    pause
    exit /b 1
)

if not exist .git (
    echo There's no git repo here yet -- run setup_github.bat first.
    pause
    exit /b 1
)

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    echo No GitHub remote is set up yet -- run setup_github.bat first.
    pause
    exit /b 1
)

echo --- What's changed since your last push ---
git status --short
echo.

git diff --quiet && git diff --cached --quiet
if not errorlevel 1 (
    git log origin/main..HEAD --oneline | findstr . >nul
    if errorlevel 1 (
        echo Nothing to push -- everything's already up to date.
        pause
        exit /b 0
    )
)

set /p COMMITMSG="Commit message (describe what you changed): "
if "%COMMITMSG%"=="" (
    echo A commit message is required -- stopping.
    pause
    exit /b 1
)

git add -A
git commit -m "%COMMITMSG%"
if errorlevel 1 (
    echo.
    echo Nothing new to commit -- checking if there are unpushed commits
    echo from before...
)

echo.
echo Pushing to GitHub now ^(a browser sign-in window may pop up first^)...
git push origin main
if errorlevel 1 (
    echo.
    echo === Push failed -- see the error above ===
    echo Common causes: not signed in, cancelled the sign-in popup, or no
    echo write access to this repo under this GitHub account.
    pause
    exit /b 1
)

echo.
echo === Done -- your changes are on GitHub ===
echo ^(This just pushes code. If you want to publish a numbered release
echo  with the compiled exe attached, run release.bat instead/after.^)
pause
