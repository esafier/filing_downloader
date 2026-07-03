@echo off
REM ============================================================
REM Filing Downloader - One-Click Launcher
REM ============================================================
REM Double-click this file (or the desktop shortcut that points to
REM it) and the SEC Filing & Earnings Transcript Downloader opens
REM straight to its first prompt. No VS Code, no typing commands.
REM ============================================================

REM Set the window title so it's easy to spot in the taskbar
title Filing Downloader

REM Jump into the folder this .bat file lives in so .env and
REM downloader.py are found. %~dp0 means "the folder this .bat
REM is sitting in", so the launcher still works if the project
REM folder gets moved.
cd /d "%~dp0"

REM ------------------------------------------------------------
REM Locate the REAL Python.
REM ------------------------------------------------------------
REM Windows has a fake "stub" python.exe at
REM   C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\python.exe
REM that just opens the Microsoft Store instead of running Python.
REM Your real Python 3.13 is installed in the sibling folder
REM   ...\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\
REM so we point directly at that exe to skip the broken stub.
REM ------------------------------------------------------------
set "PYTHON_EXE=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"

if not exist "%PYTHON_EXE%" (
    echo.
    echo   ERROR: Could not find Python at:
    echo     %PYTHON_EXE%
    echo.
    echo   Python may have been updated or uninstalled.
    echo   Ask Claude to re-point this launcher to the new path.
    echo.
    pause
    exit /b 1
)

REM Run the downloader using the real Python
"%PYTHON_EXE%" downloader.py

REM Keep the window open after the script finishes so you can read
REM the "DONE!" summary (or any error message) before it closes.
pause
