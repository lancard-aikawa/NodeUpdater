@echo off
REM PyInstaller で NodeUpdater.exe を単一ファイルにビルド。
REM 事前に: py -m pip install --user pyinstaller

setlocal
cd /d "%~dp0"

py -m PyInstaller ^
  --onefile ^
  --noconsole ^
  --name NodeUpdater ^
  --collect-submodules tkinter ^
  main.py

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Output: dist\NodeUpdater.exe
endlocal
