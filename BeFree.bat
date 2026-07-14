@echo off
rem Lance BeFree. Verifie/installe les dependances manquantes automatiquement
rem (ecran de chargement), puis demarre l'application. Necessite Python
rem dans le PATH (https://www.python.org/downloads/ - coche "Add python.exe
rem to PATH" pendant l'installation).
cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 (
    echo.
    echo  Python est introuvable sur ce PC.
    echo  Installe-le depuis https://www.python.org/downloads/
    echo  puis coche "Add python.exe to PATH" pendant l'installation.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

start "" pythonw launcher.pyw
