@echo off
setlocal enableextensions
set "APP=%~dp0keyclicker.py"

if not exist "%APP%" (
    echo [KeyClicker] keyclicker.py not found next to this script.
    pause
    exit /b 1
)

set "PY=%LocalAppData%\Programs\Python\Python314\python.exe"
if exist "%PY%" goto run

for /f "delims=" %%P in ('where python 2^>nul') do (
    set "PY=%%P"
    goto run
)

echo [KeyClicker] python.exe not found.
pause
exit /b 1

:run
echo ============================================
echo  KeyClicker - debug mode (console visible)
echo  Python: %PY%
echo ============================================
"%PY%" "%APP%"
echo.
echo App exited. error.log (if any) is in this folder.
pause
