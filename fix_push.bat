@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === LCBot: diagnose and fix an empty-looking GitHub repo ===
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

echo --- Current branch ---
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set CURBRANCH=%%b
echo You're on branch: %CURBRANCH%
echo.

echo --- Commits so far ---
git log --oneline -5
if errorlevel 1 (
    echo No commits found yet -- something went wrong earlier. Try running
    echo setup_github.bat again.
    pause
    exit /b 1
)
echo.

echo --- Remote ---
git remote -v
echo.

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    echo No "origin" remote is set -- run setup_github.bat again and make
    echo sure you paste the repo URL when it asks.
    pause
    exit /b 1
)

if not "%CURBRANCH%"=="main" (
    echo Renaming local branch "%CURBRANCH%" to "main" to match GitHub's default...
    git branch -M main
    set CURBRANCH=main
)

echo.
echo Pushing "%CURBRANCH%" to GitHub now -- watch below for any error
echo ^(a browser sign-in window may pop up first -- sign in there if it does^)...
echo.
git push -u origin %CURBRANCH%
if errorlevel 1 (
    echo.
    echo === Push failed -- see the error message above ===
    echo Common causes:
    echo  - You weren't signed in, or closed/cancelled the sign-in popup
    echo  - You don't have write access to this repo under this GitHub account
    echo  - The repo URL saved as "origin" is wrong ^(check the "git remote -v"
    echo    output above -- it should end in /LCBot.git under your username^)
    echo.
    echo Take a screenshot of this whole window and send it over if you're
    echo not sure which of these it is.
    pause
    exit /b 1
)

echo.
echo === Done -- refresh the repo page on github.com. You should now see ===
echo === your files listed, and the "no branches" message should be gone. ===
pause
