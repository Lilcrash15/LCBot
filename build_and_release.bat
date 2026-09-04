@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === LCBot: one-click build + release ===
echo Folder: %cd%
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Could not find "python" on PATH. Install Python 3.10+ from
    echo python.org ^(check "Add python.exe to PATH" during install^).
    pause
    exit /b 1
)
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

rem -- figure out the current version, and offer standardized bumps -----
for /f "usebackq delims=" %%v in (`python _ship_helpers.py current`) do set CURRENT_VERSION=%%v
if not defined CURRENT_VERSION (
    echo Couldn't read the current version from chatbot\__init__.py -- stopping.
    pause
    exit /b 1
)

echo Current version: v%CURRENT_VERSION%
echo.
echo How should the version change? All options produce a plain
echo MAJOR.MINOR.PATCH version -- no more stray tags like v0.1.1.2.
echo   1^) Patch  -- bug fixes / small tweaks
echo   2^) Minor  -- new features, nothing breaks
echo   3^) Major  -- big/breaking changes
echo   4^) Type an exact version myself
set /p BUMP_CHOICE="Choice (1-4): "

set BUMP_KIND=
if "%BUMP_CHOICE%"=="1" set BUMP_KIND=patch
if "%BUMP_CHOICE%"=="2" set BUMP_KIND=minor
if "%BUMP_CHOICE%"=="3" set BUMP_KIND=major

set NEW_VERSION=
if "%BUMP_CHOICE%"=="4" (
    set /p TYPED_VERSION="Enter version, no 'v' prefix, e.g. 1.2.0: "
    python _ship_helpers.py validate !TYPED_VERSION!
    if errorlevel 1 (
        pause
        exit /b 1
    )
    set NEW_VERSION=!TYPED_VERSION!
) else (
    if not defined BUMP_KIND (
        echo Not a valid choice -- stopping.
        pause
        exit /b 1
    )
    for /f "usebackq delims=" %%v in (`python _ship_helpers.py next !BUMP_KIND!`) do set NEW_VERSION=%%v
)

if not defined NEW_VERSION (
    echo Couldn't work out a new version -- stopping.
    pause
    exit /b 1
)

echo.
echo This will build and release v%NEW_VERSION% ^(currently v%CURRENT_VERSION%^):
echo   1. Bump chatbot\__init__.py and roll CHANGELOG.md's Unreleased section
echo   2. Build dist\TwitchChatBotV2.exe
echo   3. Commit, push, tag v%NEW_VERSION%, and push the tag
echo   4. Create the GitHub release and attach the exe ^(if gh is installed^)
set /p CONFIRM="Continue? (y/n): "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled -- nothing was changed.
    pause
    exit /b 0
)

rem -- bump version + changelog -------------------------------------------
python _ship_helpers.py apply %NEW_VERSION%
if errorlevel 1 (
    echo.
    echo === Updating the version/changelog failed -- see the error above ===
    pause
    exit /b 1
)
echo.
echo chatbot\__init__.py and CHANGELOG.md are updated for v%NEW_VERSION%.
echo If you want to add more detail to CHANGELOG.md by hand, do it now --
echo this script will use it as written once you continue.
pause

rem -- build --------------------------------------------------------------
echo.
echo Building the exe...
call build_exe.bat
if not exist dist\TwitchChatBotV2.exe (
    echo.
    echo Build didn't produce dist\TwitchChatBotV2.exe -- stopping here.
    echo Nothing has been committed or pushed yet, so it's safe to fix the
    echo build and just run this script again.
    pause
    exit /b 1
)

rem -- commit + push the version bump --------------------------------------
echo.
echo Committing the version bump...
git add chatbot\__init__.py CHANGELOG.md
git commit -m "Release v%NEW_VERSION%"
echo.
echo Pushing to GitHub...
git push origin main
if errorlevel 1 (
    echo.
    echo === Push failed -- see the error above ===
    echo Common causes: not signed in, cancelled the sign-in popup, or no
    echo write access to this repo under this GitHub account. The version
    echo bump is committed locally -- fix whatever's wrong and just run
    echo this script again, it'll pick up from here.
    pause
    exit /b 1
)

rem -- tag + push the tag ---------------------------------------------------
git rev-parse "v%NEW_VERSION%" >nul 2>nul
if errorlevel 1 (
    git tag v%NEW_VERSION%
    if errorlevel 1 (
        echo Tagging failed -- see the error above.
        pause
        exit /b 1
    )
)
git push origin v%NEW_VERSION%
if errorlevel 1 (
    echo.
    echo === Pushing the tag failed -- see the error above ===
    pause
    exit /b 1
)

rem -- create the GitHub release --------------------------------------------
where gh >nul 2>nul
if errorlevel 1 (
    echo.
    echo === Code and tag v%NEW_VERSION% are pushed ===
    echo GitHub CLI ^(gh^) isn't installed, so you'll need to draft the
    echo release by hand: go to your repo's Releases page on github.com,
    echo click "Create a new release", pick tag v%NEW_VERSION%, attach
    echo dist\TwitchChatBotV2.exe, and publish it.
    echo ^(Installing gh from https://cli.github.com/ lets this script do
    echo  that last step for you automatically next time.^)
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

gh release view v%NEW_VERSION% >nul 2>nul
if not errorlevel 1 (
    echo.
    echo A release for v%NEW_VERSION% already exists on GitHub -- nothing
    echo more to do there.
    pause
    exit /b 0
)

echo Creating GitHub release v%NEW_VERSION% and attaching the exe...
gh release create v%NEW_VERSION% dist\TwitchChatBotV2.exe --title "v%NEW_VERSION%" --generate-notes
if errorlevel 1 (
    echo.
    echo === Creating the release failed -- see the error above ===
    echo Code and the tag are still pushed fine -- you can draft the
    echo release by hand on github.com if this keeps failing.
    pause
    exit /b 1
)

echo.
echo === Done -- v%NEW_VERSION% is built, committed, tagged, pushed, and released ===
pause
