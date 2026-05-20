@echo off
REM PyInstaller で PypkgUpdater.exe を単一ファイルにビルド。
REM 事前に: py -m pip install --user pyinstaller
REM
REM リポジトリルートを cwd にしてビルドする (shared/ など他パッケージを解決するため)。

setlocal
cd /d "%~dp0\.."

py -m PyInstaller ^
  --onefile ^
  --noconsole ^
  --name PypkgUpdater ^
  --paths . ^
  --collect-submodules tkinter ^
  --collect-submodules shared ^
  --collect-submodules py ^
  py\main.py

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Output: dist\PypkgUpdater.exe
endlocal
