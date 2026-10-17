@echo off
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
pushd "%ROOT%" || exit /b 1

set "INPUT=%~1"
if not defined INPUT set "INPUT=complex_test.luau"
if not exist "%INPUT%" (
    echo [ERROR] Input file not found: "%INPUT%"
    popd
    exit /b 1
)

set "OUTPUT=%~2"
if not defined OUTPUT (
    for %%F in ("%INPUT%") do set "OUTPUT=dist\%%~nF_obf%%~xF"
)

set "VM_FLAG=--enable-vm"
if /I "%~3"=="--standard" set "VM_FLAG="

where py >nul 2>nul
if errorlevel 1 (
    python src\main.py "%INPUT%" -o "%OUTPUT%" %VM_FLAG%
) else (
    py -3 src\main.py "%INPUT%" -o "%OUTPUT%" %VM_FLAG%
)
set "RESULT=%ERRORLEVEL%"

popd
exit /b %RESULT%
