@echo off
REM ===================================================================
REM  Builds EasyAI.exe and EasyAI Setup.exe into the dist\ folder.
REM
REM  Each is a single self-contained .exe - no Python needed on the
REM  machine that runs it. Give someone dist\EasyAI.exe and it works.
REM
REM  Run this whenever the code, the workflows or the translations
REM  change. It takes a couple of minutes.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Building EasyAI

REM  "Build EXE.bat nopause" skips the Press-any-key at the end, so this can
REM  be run from a script without leaving a window waiting for someone.
set "HOLD=pause"
if /i "%~1"=="nopause" set "HOLD=rem"

REM --- find Python ----------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

echo.
echo ===================================================================
echo   Building EasyAI
echo ===================================================================
echo.

REM --- make sure the build tool is there ------------------------------
%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller, this happens once...
    %PY% -m pip install pyinstaller
    if errorlevel 1 goto :pyinstaller_failed
)

REM --- warn if the translations are behind the code -------------------
%PY% -X utf8 "%~dp0tools\make_lang.py" 2>nul | findstr /C:"missing:" >nul
if not errorlevel 1 (
    echo.
    echo   NOTE: some text is not translated yet. The build will still
    echo   work - untranslated text simply stays in English.
    echo   Run:  %PY% tools\make_lang.py --write
    echo.
)

REM --- clean the previous build ---------------------------------------
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM -------------------------------------------------------------------
REM  Each build is driven by its .spec file, which lists the files to put
REM  inside the .exe and the libraries to leave out. The data layout must
REM  match what the code expects, because app\paths.py resolves it relative
REM  to the unpacked bundle; what is excluded, and why, is in
REM  build_common.py.
REM -------------------------------------------------------------------
echo [1/3] Building EasyAI.exe ...
%PY% -m PyInstaller --noconfirm --clean "EasyAI.spec"
if errorlevel 1 goto :build_failed

echo.
echo [2/3] Building EasyAI Setup.exe ...
%PY% -m PyInstaller --noconfirm --clean "EasyAI Setup.spec"
if errorlevel 1 goto :build_failed

REM -------------------------------------------------------------------
REM  Studio is the publishing tool, not for viewers. It carries tools\ and
REM  the workflows because it reads and rewrites them.
REM -------------------------------------------------------------------
echo.
echo [3/3] Building EasyAI Studio.exe ...
%PY% -m PyInstaller --noconfirm --clean "EasyAI Studio.spec"
if errorlevel 1 goto :build_failed

REM --- tidy up --------------------------------------------------------
REM  Only the working folder. The .spec files are part of the source now -
REM  they say what goes in each build and what is left out - so deleting
REM  them, as this used to when PyInstaller generated them, would break
REM  the next run.
if exist "build" rmdir /s /q "build"

echo.
echo ===================================================================
echo   Done
echo ===================================================================
echo.
dir /b "dist\*.exe"
echo.
echo Both are in:  %~dp0dist
echo.
echo These need nothing installed - not even Python. Copy the whole
echo dist folder, or just the one .exe you want to share.
echo.
echo Give viewers  EasyAI.exe  and  EasyAI Setup.exe  only.
echo EasyAI Studio.exe is the publishing tool and is for you.
echo.
echo Note: settings, workflows and results are kept next to the .exe,
echo so put it in a folder you can write to - not Program Files.
echo.
%HOLD%
exit /b 0

REM -------------------------------------------------------------------
:build_failed
echo.
echo Build failed. The messages above say why.
echo.
echo The usual causes:
echo   - antivirus holding a file open. Try again, or exclude this folder.
echo   - a previous EasyAI.exe still running. Close it and retry.
echo.
%HOLD%
exit /b 1

REM -------------------------------------------------------------------
:pyinstaller_failed
echo.
echo Could not install PyInstaller.
echo Try opening a Command Prompt here and running:
echo     %PY% -m pip install pyinstaller
echo.
%HOLD%
exit /b 1

REM -------------------------------------------------------------------
:no_python
echo.
echo Python is not installed, so nothing can be built.
echo Get it from  https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup.
echo.
%HOLD%
exit /b 1
