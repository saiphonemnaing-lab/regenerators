@echo off
cd /d "%~dp0"
set "ORCHARD_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%ORCHARD_PY%" goto bundled
where py >nul 2>nul
if not errorlevel 1 goto launcher
where python >nul 2>nul
if not errorlevel 1 goto systempython
echo Python is required to serve the dashboard. Install Python or use the Codex bundled runtime.
pause
exit /b 1
:bundled
echo Open http://127.0.0.1:8765 in your browser. Press Ctrl+C here to stop.
"%ORCHARD_PY%" -m http.server 8765 --bind 127.0.0.1 --directory dashboard/dist
goto done
:launcher
py -3 -m http.server 8765 --bind 127.0.0.1 --directory dashboard/dist
goto done
:systempython
python -m http.server 8765 --bind 127.0.0.1 --directory dashboard/dist
:done
pause
