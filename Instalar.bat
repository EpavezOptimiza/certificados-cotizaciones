@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Instalador - Certificados y Cotizaciones
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No se encontro Python en este PC.
    echo.
    echo Instala Python 3.11 o superior desde:
    echo   https://www.python.org/downloads/
    echo IMPORTANTE: al instalar, marca la casilla "Add python.exe to PATH"
    echo.
    echo Luego vuelve a ejecutar este Instalar.bat
    pause
    exit /b 1
)

echo Python encontrado:
python --version
echo.

if not exist venv (
    echo Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo Entorno virtual ya existe, se reutiliza.
)

call venv\Scripts\activate.bat

echo.
echo Instalando dependencias de Python... ^(puede tardar varios minutos^)
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

echo.
echo Instalando navegador Chromium para los bots de PreviRed/DICOM...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de Chromium.
    pause
    exit /b 1
)

if not exist adjuntos mkdir adjuntos

echo.
echo ============================================
echo   Instalacion completa.
echo   Usa Iniciar.bat para abrir la aplicacion.
echo ============================================
pause
