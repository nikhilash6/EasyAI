@echo off
REM ===================================================================
REM  EasyAI Setup launcher
REM  Double-click this file to install ComfyUI and the models EasyAI
REM  needs. This does not change EasyAI itself.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title EasyAI Setup

REM --- find a Python we can use --------------------------------------
set "PY="
set "PYW="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

where pyw >nul 2>nul && set "PYW=pyw"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW set "PYW=%PY%"

REM --- check the version ---------------------------------------------
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

REM --- check the libraries -------------------------------------------
%PY% -c "import PySide6, requests" >nul 2>nul
if errorlevel 1 goto :install

REM --- git is needed for the custom nodes -----------------------------
where git >nul 2>nul
if errorlevel 1 goto :no_git

:run
echo Starting EasyAI Setup...
start "EasyAI Setup" %PYW% "%~dp0EasyAISetup.py"
exit /b 0

REM -------------------------------------------------------------------
:install
echo.
echo ===================================================================
echo   First time setup
echo ===================================================================
echo.
echo A few Python libraries are needed. This happens once and takes a
echo couple of minutes. Leave this window open.
echo.
pause
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :install_failed
echo.
echo Setup finished.
echo.
goto :run

REM -------------------------------------------------------------------
:install_failed
echo.
echo Could not install the libraries needed.
echo.
echo Try opening a Command Prompt in this folder and running:
echo     %PY% -m pip install -r requirements.txt
echo.
pause
exit /b 1

REM -------------------------------------------------------------------
:no_git
echo.
echo ===================================================================
echo   Git is not installed
echo ===================================================================
echo.
echo The installer uses Git to fetch the ComfyUI add-ons at the exact
echo versions EasyAI was built against.
echo.
echo 1. Go to  https://git-scm.com/download/win
echo 2. Download and run the installer. The default options are fine.
echo 3. When it finishes, double-click EasyAISetup.bat again.
echo.
pause
exit /b 1

REM -------------------------------------------------------------------
:no_python
echo.
echo ===================================================================
echo   Python is not installed
echo ===================================================================
echo.
echo EasyAI Setup needs Python 3.10 or newer.
echo.
echo 1. Go to  https://www.python.org/downloads/
echo 2. Download and run the installer.
echo 3. IMPORTANT: tick "Add python.exe to PATH" on the first screen.
echo 4. When it finishes, double-click EasyAISetup.bat again.
echo.
pause
exit /b 1

REM -------------------------------------------------------------------
:old_python
echo.
echo ===================================================================
echo   Python is too old
echo ===================================================================
echo.
for /f "delims=" %%V in ('%PY% -c "import sys;print(sys.version.split()[0])"') do echo You have Python %%V, but 3.10 or newer is needed.
echo.
echo Install a newer version from  https://www.python.org/downloads/
echo and remember to tick "Add python.exe to PATH".
echo.
pause
exit /b 1
