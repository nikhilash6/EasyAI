@echo off
REM ===================================================================
REM  EasyAI Studio - the publishing tools
REM
REM  For whoever publishes EasyAI, not for viewers. Adds new workflows,
REM  rebuilds the catalogue, checks the download links, and keeps the
REM  translations up to date.
REM
REM  Most jobs in here need ComfyUI running.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title EasyAI Studio

REM --- find Python ----------------------------------------------------
set "PY="
set "PYW="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

where pyw >nul 2>nul && set "PYW=pyw"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW set "PYW=%PY%"

REM --- the libraries --------------------------------------------------
%PY% -c "import PySide6, requests" >nul 2>nul
if errorlevel 1 goto :install

:run
echo Starting EasyAI Studio...
start "EasyAI Studio" %PYW% "%~dp0EasyAIStudio.py"
exit /b 0

REM -------------------------------------------------------------------
:install
echo.
echo ===================================================================
echo   First time setup
echo ===================================================================
echo.
echo A few Python libraries are needed. This happens once.
echo.
pause
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :install_failed
goto :run

REM -------------------------------------------------------------------
:install_failed
echo.
echo Could not install the libraries needed.
echo Try opening a Command Prompt here and running:
echo     %PY% -m pip install -r requirements.txt
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
echo EasyAI Studio needs Python 3.10 or newer.
echo.
echo 1. Go to  https://www.python.org/downloads/
echo 2. Download and run the installer.
echo 3. IMPORTANT: tick "Add python.exe to PATH" on the first screen.
echo 4. When it finishes, double-click EasyAI Studio.bat again.
echo.
pause
exit /b 1
