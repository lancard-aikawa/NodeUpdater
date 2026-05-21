@echo off
REM Build both NodeUpdater.exe and PypkgUpdater.exe (PkgUpdater umbrella build).
REM Prerequisite: py -m pip install --user pyinstaller

setlocal
cd /d "%~dp0"

call node\build.cmd
if errorlevel 1 (
  echo.
  echo Node build failed.
  exit /b 1
)

call py\build.cmd
if errorlevel 1 (
  echo.
  echo Py build failed.
  exit /b 1
)

echo.
echo Both built: dist\NodeUpdater.exe + dist\PypkgUpdater.exe
endlocal
