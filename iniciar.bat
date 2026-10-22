@echo off
title Musica Downloader
echo ==================================================
echo   Iniciando Musica Downloader...
echo ==================================================
echo.
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Hubo un error al iniciar la aplicacion.
    pause
)
