@echo off
setlocal enableextensions
set "APP=%~dp0keyclicker.py"

if not exist "%APP%" (
    echo [KeyClicker] keyclicker.py not found next to this script.
    pause
    exit /b 1
)

set "PYW=%LocalAppData%\Programs\Python\Python314\pythonw.exe"
if exist "%PYW%" goto run

for /f "delims=" %%P in ('where pythonw 2^>nul') do (
    set "PYW=%%P"
    goto run
)

echo [KeyClicker] pythonw.exe not found.
echo Install Python 3 for Windows (with tkinter) and try again.
pause
exit /b 1

:run
start "" "%PYW%" "%APP%"
exit /b 0
