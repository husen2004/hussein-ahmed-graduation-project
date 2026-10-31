@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE where python3 >nul 2>&1 && set "PYEXE=python3"
if not defined PYEXE (
  echo Python 3 is required.
  pause
  exit /b 1
)
%PYEXE% -c "import sqlite3,datetime,os; os.makedirs(r'database\\backups',exist_ok=True); src=sqlite3.connect(r'database\\students.db'); fn=r'database\\backups\\students_manual_'+datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S')+'.db'; dst=sqlite3.connect(fn); src.backup(dst); dst.close(); src.close(); print('Backup created:',fn)"
pause
