@echo off
title Stop Ares
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Stop
pause
