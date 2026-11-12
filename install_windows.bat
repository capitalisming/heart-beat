@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title PPG Respiratory Estimator - Windows setup

echo.
echo ==============================================================
echo  PPG Respiratory Estimator - Windows setup
echo  Folder: %CD%
echo ==============================================================
echo.

rem ---- find Python 3.9 or newer -------------------------------------------
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 goto :oldpython

echo Using this Python:
%PY% --version
echo.

rem ---- virtual environment --------------------------------------------------
if exist ".venv\Scripts\python.exe" goto :have_venv
echo Creating the virtual environment .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :fail
goto :install

:have_venv
echo The virtual environment .venv already exists - reusing it.

:install
echo.
echo Installing the packages - an internet connection is needed ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto :fail

rem ---- tests ----------------------------------------------------------------
echo.
echo Running the test suite ...
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto :tests_failed
echo Tests passed.
goto :demo

:tests_failed
echo.
echo WARNING: some tests failed - see the output above. The program may still work.
echo If you report the problem, include the text of the failures.

rem ---- demo on a simulated recording -----------------------------------------
:demo
echo.
echo Demo on a SIMULATED recording - not a human measurement:
".venv\Scripts\python.exe" scripts\run_estimation.py data\sample\SYNTHETIC_rr12.csv --reference markers --no-figures

rem ---- serial ports ---------------------------------------------------------
echo.
echo Serial ports found on this PC - the board shows up here once it is connected:
".venv\Scripts\python.exe" scripts\record_serial.py --list

echo.
echo ==============================================================
echo  Setup finished.
echo.
echo  Run any script with the helper ppg.bat, for example:
echo    ppg scripts\record_serial.py --list
echo    ppg scripts\record_serial.py --port COM5 --duration 180 --participant P01 --analyze
echo    ppg scripts\run_estimation.py data\sample\SYNTHETIC_rr12.csv --reference markers
echo.
echo  In PowerShell write .\ppg instead of ppg.
echo  The Arduino sketch and its guide are described in README.md and
echo  docs\arduino_guide_ru.md.
echo ==============================================================
echo.
pause
exit /b 0

:nopython
echo ERROR: Python was not found.
echo Install Python 3.12 or 3.13 from https://www.python.org/downloads/windows/
echo and tick "Add python.exe to PATH" on the first screen of the installer.
echo Then run this file again.
goto :end_error

:oldpython
echo ERROR: Python 3.9 or newer is required.
%PY% --version
echo Install a newer version from https://www.python.org/downloads/windows/
echo and run this file again.
goto :end_error

:fail
echo.
echo ERROR: setup did not finish - see the messages above.
echo Common causes: no internet connection, or a proxy or antivirus blocking pip.

:end_error
echo.
pause
exit /b 1
