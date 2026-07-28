@echo off
cd /d "%~dp0"

REM Kill old monitor instances
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart.ps1"
exit /b %ERRORLEVEL%
