@echo off
chcp 65001 >nul
cd /d "%~dp0"

title Hussein Ahmed Graduation Project - Offline

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE where python3 >nul 2>&1 && set "PYEXE=python3"

if not defined PYEXE (
  echo.
  echo [ERROR] Python 3 is not installed or not available in PATH.
  echo Install Python 3, then run this file again.
  echo.
  pause
  exit /b 1
)

echo Starting Hussein Ahmed Graduation Project locally...
start "" "http://127.0.0.1:8000"
%PYEXE% server.py

echo.
echo The server has stopped.
pause
