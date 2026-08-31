@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Fehler: Die virtuelle Umgebung .venv fehlt.
  echo Bitte zuerst die Einrichtung aus README.md ausfuehren.
  exit /b 1
)
".venv\Scripts\python.exe" -m job_finder.review
