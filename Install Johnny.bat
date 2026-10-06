@echo off
title Johnny Setup - DopeKitchen Industries
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1"
if errorlevel 1 pause
