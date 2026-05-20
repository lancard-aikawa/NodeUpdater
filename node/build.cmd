@echo off
REM PyInstaller で NodeUpdater.exe を単一ファイルにビルド。
REM 事前に: py -m pip install --user pyinstaller
REM
REM リポジトリルートを cwd にしてビルドする (shared/ など他パッケージを解決するため)。

setlocal
cd /d "%~dp0\.."

py -m PyInstaller ^
  --onefile ^
  --noconsole ^
  --name NodeUpdater ^
  --paths . ^
  --collect-submodules tkinter ^
  --collect-submodules shared ^
  --collect-submodules node ^
  node\main.py

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Output: dist\NodeUpdater.exe
endlocal
