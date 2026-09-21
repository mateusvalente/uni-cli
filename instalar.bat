@echo off
setlocal enabledelayedexpansion
title Instalador do Uni CLI

echo ==============================================
echo       Instalador Automatico - Uni CLI
echo ==============================================
echo.

:: 1. Verifica se o Python esta disponivel
echo [*] Verificando instalacao do Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [AVISO] O Python nao foi encontrado no seu computador ou nao esta no PATH.
    echo.
    echo O Uni CLI precisa do Python para funcionar. 
    echo Vou abrir a pagina oficial de download para voce.
    echo.
    echo IMPORTANTE: Durante a instalacao, lembre-se de marcar a caixa na primeira tela:
    echo "[x] Add python.exe to PATH"
    echo.
    pause
    start https://www.python.org/downloads/windows/
    echo.
    echo Apos terminar a instalacao do Python, feche esta janela e rode o instalar.bat novamente!
    pause
    exit /b
) else (
    echo [OK] Python encontrado!
)

echo.

:: 2. Pega a pasta atual (onde este .bat esta rodando)
set "UNI_DIR=%~dp0"
:: Remove a barra final do caminho
if "%UNI_DIR:~-1%"=="\" set "UNI_DIR=%UNI_DIR:~0,-1%"

:: 3. Busca o PATH atual apenas do Usuario (para nao misturar com o do Sistema)
set "USER_PATH="
for /f "usebackq tokens=2,*" %%A in (`reg query HKCU\Environment /v PATH 2^>nul`) do (
    set "USER_PATH=%%B"
)

:: 4. Verifica se a pasta ja esta no PATH
echo [*] Verificando Variaveis de Ambiente...
echo %USER_PATH% | find /I "%UNI_DIR%" >nul
if %errorlevel% equ 0 (
    echo [OK] A pasta do Uni CLI ja esta no seu PATH.
) else (
    echo [*] Adicionando %UNI_DIR% ao PATH do usuario...
    if "%USER_PATH%"=="" (
        setx PATH "%UNI_DIR%" >nul
    ) else (
        setx PATH "%USER_PATH%;%UNI_DIR%" >nul
    )
    echo [OK] Pasta adicionada com sucesso!
)

echo.
echo ==============================================
echo    Instalacao concluida com sucesso!
echo ==============================================
echo.
echo O comando "uni" ja esta configurado.
echo.
echo ATENCAO: Feche TODAS as suas janelas abertas do Terminal (CMD ou PowerShell) 
echo e abra um novo terminal para que as configuracoes entrem em vigor.
echo.
pause
