@echo off
setlocal

echo ================================================
echo  BuildTime - Windows .exe builder
echo  Run this ONCE, on a Windows PC, whenever 
echo  a new version is released. It produces one file -
echo  dist\BuildTime.exe
echo ================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/windows/
    echo and check "Add python.exe to PATH" during setup, then run this again.
    pause
    exit /b 1
)

if exist .build_venv rmdir /s /q .build_venv

echo [1/4] Creating a throwaway build environment ...
python -m venv .build_venv
if errorlevel 1 goto :error
call .build_venv\Scripts\activate.bat

echo [2/4] Installing build tools and app dependencies ...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 goto :error
pip install pyinstaller
if errorlevel 1 goto :error

echo [3/4] Building BuildTime.exe (this can take a minute) ...
REM --onefile  : ship ONE file, nothing to unzip or install
REM --windowed : no black console window behind the app
REM --name     : controls the .exe's name
REM Want a custom icon later? Add:  --icon=path\to\icon.ico
pyinstaller --noconfirm --onefile --windowed --name BuildTime main.py
if errorlevel 1 goto :error

echo [4/4] Cleaning up build leftovers ...
call .build_venv\Scripts\deactivate.bat
rmdir /s /q build >nul 2>nul
rmdir /s /q .build_venv >nul 2>nul
del /q BuildTime.spec >nul 2>nul

echo.
echo ================================================
echo  Done. Your app is at:  dist\BuildTime.exe
echo  No install needed to run it.
echo ================================================
pause
exit /b 0

:error
echo.
echo ================================================
echo  Build failed - see the error above.
echo ================================================
pause
exit /b 1
