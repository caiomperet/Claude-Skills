@echo off
setlocal EnableExtensions
title Instalacao do netmon
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\netmon"
set "PYVER=3.12.10"

echo.
echo  netmon - monitor de qualidade da internet
echo  =========================================
echo.

rem 1) Procura um Python com Tkinter. Se nao houver, baixa o instalador oficial e instala sem perguntas.
call :find_python
if not defined PYW (
  echo  Python nao encontrado. Baixando o instalador oficial %PYVER% de python.org...
  set "PYSETUP=%TEMP%\python-%PYVER%-amd64.exe"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe' -OutFile '%TEMP%\python-%PYVER%-amd64.exe'"
  if errorlevel 1 goto :fail_download
  echo  Instalando o Python ^(um ou dois minutos, sem janelas^)...
  "%TEMP%\python-%PYVER%-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0 Include_doc=0 Include_tcltk=1
  if errorlevel 1 goto :fail_python
  call :find_python
  if not defined PYW goto :fail_python
)
echo  Python encontrado: %PYW%

rem 2) Copia o programa para a pasta do usuario (nao precisa de administrador).
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%SRC%netmon.py" "%DEST%\" >nul
copy /Y "%SRC%netmon_gui.py" "%DEST%\" >nul
copy /Y "%SRC%config.example.json" "%DEST%\" >nul
copy /Y "%SRC%README.md" "%DEST%\" >nul
if errorlevel 1 goto :fail_copy

rem 3) Cria atalhos, liga o inicio automatico, inicia o monitor e abre a interface.
start "" "%PYW%" "%DEST%\netmon_gui.py" --install
echo.
echo  Pronto. O monitor ja esta rodando em segundo plano e a interface vai abrir em instantes.
echo  Um atalho "netmon" foi criado na Area de Trabalho e no Menu Iniciar.
echo  Dados e relatorios ficam em %DEST%
echo.
echo  Esta janela fecha sozinha em 15 segundos.
timeout /t 15 >nul
exit /b 0

:find_python
set "PYW="
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" "%ProgramFiles%\Python313\pythonw.exe" "%ProgramFiles%\Python312\pythonw.exe" "%ProgramFiles%\Python311\pythonw.exe") do (
  if not defined PYW if exist %%P set "PYW=%%~P"
)
if not defined PYW for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW exit /b 0
set "PYX=%PYW:pythonw.exe=python.exe%"
"%PYX%" -c "import tkinter" >/dev/null 2>&1
if errorlevel 1 set "PYW="
exit /b 0

:fail_download
echo.
echo  Nao foi possivel baixar o Python. Verifique a conexao ou instale manualmente em
echo  https://www.python.org/downloads/windows/  (marque "Add python.exe to PATH") e rode este arquivo de novo.
pause
exit /b 1

:fail_python
echo.
echo  A instalacao do Python nao concluiu. Instale manualmente em https://www.python.org/downloads/windows/
echo  (marque "Add python.exe to PATH") e rode este arquivo de novo.
pause
exit /b 1

:fail_copy
echo.
echo  Nao foi possivel copiar os arquivos para %DEST%.
pause
exit /b 1
