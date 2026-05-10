@echo off
setlocal EnableExtensions

REM ===============================
REM GifDataTool — install
REM ===============================

cd /d "%~dp0" || exit /b 1

echo Installing pip packages in:
cd
echo.

where pip >nul 2>&1 || (
  echo [ERROR] pip not found. Install pip from https://pypi.org/project/pip/ then re-run INSTALL.bat
  pause
  exit /b 1
)

call pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)

echo Done.
pause
exit /b 0
