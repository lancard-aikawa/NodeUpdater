@echo off
REM PkgUpdater 一括ビルド: NodeUpdater.exe と PypkgUpdater.exe を順に生成。
REM 事前に: py -m pip install --user pyinstaller

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
