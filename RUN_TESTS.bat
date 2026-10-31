@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE where python3 >nul 2>&1 && set "PYEXE=python3"

if not defined PYEXE (
  echo [ERROR] Python 3 is not installed or not available in PATH.
  pause
  exit /b 1
)

%PYEXE% -m unittest discover -s tests -v
echo.
pause
