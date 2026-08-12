@echo off
setlocal
cd /d "%~dp0"

if not exist venv (
    echo [ERROR] Primero debes ejecutar Instalar.bat una vez.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

start "" cmd /c "timeout /t 3 >nul & start http://localhost:5000"

echo Iniciando Certificados y Cotizaciones...
echo No cierres esta ventana mientras uses la aplicacion.
echo.

python app.py

echo.
echo La aplicacion se detuvo.
pause
