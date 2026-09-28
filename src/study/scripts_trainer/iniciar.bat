@echo off
cd /d "%~dp0"

rem Ejecuta el servidor sin mostrar ninguna consola.
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0iniciar_oculto.vbs"
exit /b
