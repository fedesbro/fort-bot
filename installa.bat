@echo off
cd /d "%~dp0"
echo Creo l'ambiente Python...
python -m venv .venv || goto :errore
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :errore
if not exist templates mkdir templates
echo.
echo Installazione completata.
pause
exit /b 0
:errore
echo.
echo ERRORE durante l'installazione.
pause
exit /b 1
