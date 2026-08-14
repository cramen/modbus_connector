@echo off
rem Build a standalone application (PyInstaller).
rem Output: dist\ModbusConnector\ModbusConnector.exe
setlocal
cd /d "%~dp0"

set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo error: .venv not found, create it first: python -m venv .venv 1>&2
    exit /b 1
)

"%PY%" -m pip install -q -e ".[build]"
if errorlevel 1 exit /b 1

"%PY%" -m PyInstaller ^
    --noconfirm --clean ^
    --windowed ^
    --name ModbusConnector ^
    --icon assets\icon.ico ^
    --paths src ^
    src\modbus_connector\__main__.py
if errorlevel 1 exit /b 1

rem PyInstaller intermediate directory, not runnable by itself
if exist build rmdir /s /q build

echo.
echo Build finished. Artifact:
echo   dist\ModbusConnector\ModbusConnector.exe
echo Copy the whole dist\ModbusConnector folder to another machine.
