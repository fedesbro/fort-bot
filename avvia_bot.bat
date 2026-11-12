@echo off
cd /d "%~dp0"

rem Nome con cui comparira' il processo nella lista (Gestione attivita'):
rem cambialo qui se vuoi un altro nome. Niente di nascosto, serve solo a
rem farlo riconoscere al volo invece di vederlo come "python.exe".
set "NOMEPROCESSO=TestGioco.exe"

rem Crea una copia del Python del progetto con quel nome (solo la prima volta
rem o se Python e' stato aggiornato).
set "RUNNER=%~dp0.venv\runner\%NOMEPROCESSO%"
if not exist "%~dp0.venv\runner" mkdir "%~dp0.venv\runner"
if not exist "%RUNNER%" (
    copy /y ".venv\Scripts\python.exe" "%RUNNER%" >nul
)
rem se Python e' piu' recente della copia, aggiorna la copia
for %%A in (".venv\Scripts\python.exe") do set "SRC=%%~tA"
for %%B in ("%RUNNER%") do set "DST=%%~tB"

"%RUNNER%" bot.py %*
pause
