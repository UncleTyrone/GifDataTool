@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ===============================
REM GifDataTool — launcher
REM ===============================

set "ROOT=%~dp0"

:run
echo Running:
echo   streamlit "run" "!ROOT!app.py" !EXTRA!
REM Run node attached to this console so output streams live (do not capture via PowerShell).
where streamlit >nul 2>&1 || (
  echo [ERROR] streamlit is not on PATH.
  set "EC=9009"
  goto after_run
)

if defined EXTRA (
  streamlit "run" "!ROOT!app.py" !EXTRA!
) else (
  streamlit "run" "!ROOT!app.py"
)
