@echo off
setlocal enabledelayedexpansion
title Comparador de Fotos com IA
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no PATH do Windows.
    echo Certifique-se de que o Python 3.10+ esta instalado e adicionado ao PATH.
    pause
    exit /b 1
)

if not "%~1"=="" (
    set "PASTA=%~1"
    set "PASTA=!PASTA:"=!"
    if "!PASTA:~-1!"=="\" (
        if not "!PASTA:~1,2!"==":\" (
            set "PASTA=!PASTA:~0,-1!"
        )
    )
    echo Iniciando Comparador de Fotos em: "!PASTA!"
    python comparador_fotos.py "!PASTA!"
) else (
    echo Iniciando Comparador de Fotos com IA...
    python comparador_fotos.py
)

if errorlevel 1 (
    echo.
    echo Ocorreu um erro durante a execucao.
    pause
)
