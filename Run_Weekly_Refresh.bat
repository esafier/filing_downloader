@echo off
REM ============================================================
REM Filing Downloader - Weekly Refresh (for Windows Task Scheduler)
REM ============================================================
REM Runs "refresh --all": every ticker folder under OneDrive\Tickers
REM gets any NEW filings added, files of filings that fell outside
REM the caps removed, and its financials + index regenerated.
REM
REM No "pause" at the end: Task Scheduler runs this unattended.
REM Console output is appended to refresh_console.log next to this
REM file; the one-line-per-ticker summary goes to
REM OneDrive\Tickers\_refresh_log.txt.
REM
REM Register it (see README.md, "Weekly refresh"):
REM   schtasks /Create /TN "Filing Downloader Weekly Refresh" ^
REM     /TR "\"C:\Users\ely\Code\Filing_downloader\Run_Weekly_Refresh.bat\"" ^
REM     /SC WEEKLY /D SAT /ST 07:00
REM ============================================================

title Filing Downloader - weekly refresh
cd /d "%~dp0"

REM Same real-Python path as Run_Filing_Downloader.bat (the WindowsApps
REM python.exe stub only opens the Microsoft Store).
set "PYTHON_EXE=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"

if not exist "%PYTHON_EXE%" (
    echo %DATE% %TIME% ERROR: Python not found at "%PYTHON_EXE%" >> "%~dp0refresh_console.log"
    exit /b 1
)

echo. >> "%~dp0refresh_console.log"
echo ===== %DATE% %TIME% weekly refresh start ===== >> "%~dp0refresh_console.log"
"%PYTHON_EXE%" downloader.py refresh --all >> "%~dp0refresh_console.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo ===== %DATE% %TIME% weekly refresh end (exit %EXIT_CODE%) ===== >> "%~dp0refresh_console.log"
exit /b %EXIT_CODE%
