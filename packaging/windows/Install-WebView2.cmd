@echo off
setlocal
set "BOOTSTRAPPER=%~dp0MicrosoftEdgeWebview2Setup.exe"
if not exist "%BOOTSTRAPPER%" (
  echo MicrosoftEdgeWebview2Setup.exe was not found beside this script.
  echo Download WebView2 Runtime from:
  echo https://developer.microsoft.com/microsoft-edge/webview2/
  pause
  exit /b 2
)
"%BOOTSTRAPPER%" /silent /install
set "INSTALL_EXIT=%ERRORLEVEL%"
if "%INSTALL_EXIT%"=="0" goto installed
if "%INSTALL_EXIT%"=="3010" goto installed
echo WebView2 Runtime installation failed with code %INSTALL_EXIT%.
pause
exit /b %INSTALL_EXIT%

:installed
if "%INSTALL_EXIT%"=="3010" (
  echo WebView2 Runtime is installed. Windows should be restarted.
  pause
  exit /b 0
)
echo WebView2 Runtime is installed.
pause
