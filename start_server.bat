@echo off
setlocal enabledelayedexpansion
title TCA Triage Dashboard Server

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║       TCA QA Triage Dashboard — Local Server     ║
echo  ╚══════════════════════════════════════════════════╝
echo.

:: ─────────────────────────────────────────────────────────
:: Load credentials from .env file
:: ─────────────────────────────────────────────────────────
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        set "line=%%A"
        if not "!line:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
    )
    echo  [OK]   Credentials loaded from .env
) else (
    echo  [WARN] No .env file found - Cosmos refresh will fail
    echo         Copy .env.example to .env and fill in your values
    echo.
)

:: ─────────────────────────────────────────────────────────
:: Start the server from the Mock Screens root
:: ─────────────────────────────────────────────────────────
cd /d "%~dp0.."
echo  [INFO] Starting server on http://localhost:5500
echo  [INFO] Dashboard: http://localhost:5500/Triaging-Dashboard/qa_triage_dashboard.html
echo.
echo  Press Ctrl+C to stop the server
echo.

python "Triaging-Dashboard\server.py" 5500

:: If server exits unexpectedly, pause so you can read the error
echo.
echo  [!] Server stopped.
pause
