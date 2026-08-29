@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === LCBot: publish to GitHub (private repo) ===
echo Folder: %cd%
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo Git isn't installed, or isn't on PATH.
    echo Install it from https://git-scm.com/download/win ^(defaults are fine^),
    echo then run this script again.
    pause
    exit /b 1
)

where gh >nul 2>nul
if errorlevel 1 (
    set HAVE_GH=0
) else (
    set HAVE_GH=1
)

if not exist .git (
    echo Initializing git repo...
    git init
    git branch -M main
) else (
    echo Git repo already exists here -- reusing it.
)

echo.
echo Staging files ^(config.json, chatbot.db, build\, dist\, etc. are
echo excluded via .gitignore -- your tokens/points database never get
echo committed^)...
git add -A
git status --short

echo.
set /p COMMITMSG="Commit message (press Enter for 'Initial commit'): "
if "%COMMITMSG%"=="" set COMMITMSG=Initial commit
git commit -m "%COMMITMSG%"
if errorlevel 1 (
    echo ^(Nothing new to commit -- that's fine if you've already run this before.^)
)

if %HAVE_GH%==1 (
    echo.
    echo GitHub CLI found -- checking sign-in...
    gh auth status >nul 2>nul
    if errorlevel 1 (
        echo Not signed in yet. Opening browser sign-in via GitHub CLI...
        echo ^(This is between you and GitHub -- nothing is typed here.^)
        gh auth login --web -h github.com
    )
    git remote get-url origin >nul 2>nul
    if errorlevel 1 (
        echo Creating private GitHub repo "LCBot" and pushing...
        gh repo create LCBot --private --source=. --remote=origin --push
    ) else (
        echo Remote "origin" is already set -- pushing...
        git push -u origin main
    )
) else (
    echo.
    echo GitHub CLI ^(gh^) not found -- falling back to manual steps.
    echo ^(Optional: installing it from https://cli.github.com/ lets this
    echo  script create the repo and future releases for you automatically.^)
    echo.
    echo  1. Go to https://github.com/new in your browser.
    echo  2. Repository name: LCBot
    echo  3. Set visibility to Private.
    echo  4. Leave "Add a README file", ".gitignore", and "license" UNCHECKED
    echo     ^(this project already has all three -- checking them creates
    echo     conflicting files on GitHub's side^).
    echo  5. Click "Create repository".
    echo  6. On the next page, copy the repository URL ^(the one starting
    echo     with https://github.com/... and ending in .git^).
    echo.
    set /p REPOURL="Paste that URL here and press Enter: "
    if "!REPOURL!"=="" (
        echo No URL entered -- stopping. Run this script again when you're ready.
        pause
        exit /b 1
    )
    git remote remove origin >nul 2>nul
    git remote add origin "!REPOURL!"
    echo Pushing... this may open a browser window to sign in to GitHub
    echo the first time ^(Git Credential Manager handles that, not this script^).
    git push -u origin main
)

echo.
echo === Done -- LCBot is now on GitHub as a private repository ===
echo Next: run release.bat whenever you want to tag and publish a version.
echo.
pause
