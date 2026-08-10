@echo off
rem Mailigence - stop backend + frontend (optionally: stop.bat -StopDb)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
echo.
pause
