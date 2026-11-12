@echo off
rem Runs Python from the project's virtual environment: ppg scripts\run_estimation.py ...
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo The project is not installed yet - run install_windows.bat first.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" %*
