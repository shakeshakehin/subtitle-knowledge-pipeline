@echo off
title Start Subtitle Pipeline and Langfuse

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\langfuse-self-host\Manage-Langfuse.ps1" workspace

if errorlevel 1 (
  echo.
  echo Startup failed. Keep this window open and send the error to Codex.
  pause
)
