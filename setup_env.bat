@echo off
REM ---------------------------------------------------------------------------
REM  Finds -- or on first run builds -- the Python that the launchers use.
REM  run_frogger.bat CALLs this; you do not need to run it yourself.
REM  It sets PYEXE (console Python) and PYWEXE (windowed Python, no console).
REM
REM  Order:
REM    1. a conda environment named spotting, if you made one (used as is)
REM    2. this program's own environment in
REM           %USERPROFILE%\.python-envs\yeast-spotting-quantification
REM       created on first run from any Python 3.11+ on this computer,
REM       and updated automatically whenever requirements.txt changes.
REM
REM  To start over, delete that folder; the next launch rebuilds it.
REM  (Not under AppData: the Microsoft Store Python silently redirects folders
REM  it creates there, so the environment would not be where this looks.)
REM ---------------------------------------------------------------------------

set "PYEXE="
set "PYWEXE="
set "_SE_ROOT=%~dp0"

REM -- 1. Developer's conda environment, if present -----------------------------
if "spotting"=="" goto :venv
for %%P in ("%USERPROFILE%\miniconda3\envs\spotting" "%LOCALAPPDATA%\miniconda3\envs\spotting" "%USERPROFILE%\anaconda3\envs\spotting" "%ProgramData%\miniconda3\envs\spotting") do (
    if not defined PYEXE if exist "%%~P\python.exe" (
        "%%~P\python.exe" -c "import sys" >nul 2>nul && set "PYEXE=%%~P\python.exe" && set "PYWEXE=%%~P\pythonw.exe"
    )
)
if defined PYEXE exit /b 0

REM -- 2. The program's own virtual environment ---------------------------------
:venv
set "_SE_VENV=%USERPROFILE%\.python-envs\yeast-spotting-quantification"
set "_SE_PY=%_SE_VENV%\Scripts\python.exe"
set "_SE_STAMP=%_SE_VENV%\requirements.installed.txt"

if exist "%_SE_PY%" (
    "%_SE_PY%" -c "import sys" >nul 2>nul
    if errorlevel 1 (
        echo   The environment in "%_SE_VENV%" no longer works; rebuilding it.
        rmdir /s /q "%_SE_VENV%"
    )
)
if exist "%_SE_PY%" goto :sync

call :find_base
if not defined _SE_BASE (
    echo.
    echo   ERROR: Python 3.11 or newer was not found on this computer.
    echo.
    echo   Install it from https://www.python.org/downloads/windows/
    echo   ^(tick "Add python.exe to PATH" in the installer^), then run this again.
    echo   Anaconda / Miniconda also works.
    echo.
    exit /b 1
)
echo.
echo   First run on this computer: setting up the Python environment.
echo   This needs an internet connection and takes a few minutes, once.
echo.
%_SE_BASE% -m venv "%_SE_VENV%"
if errorlevel 1 (
    echo   ERROR: could not create "%_SE_VENV%".
    exit /b 1
)

:sync
REM Reinstall only when requirements.txt differs from what was last installed.
fc /b "%_SE_ROOT%requirements.txt" "%_SE_STAMP%" >nul 2>nul
if not errorlevel 1 goto :ready
echo   Installing the required Python packages...
"%_SE_PY%" -m pip install -r "%_SE_ROOT%requirements.txt" --disable-pip-version-check --no-warn-script-location
if errorlevel 1 (
    echo.
    echo   ERROR: installing the packages in requirements.txt failed ^(see above^).
    echo   Check the internet connection and run this again.
    exit /b 1
)
copy /y "%_SE_ROOT%requirements.txt" "%_SE_STAMP%" >nul
echo   Done.
echo.

:ready
set "PYEXE=%_SE_PY%"
set "PYWEXE=%_SE_VENV%\Scripts\pythonw.exe"
exit /b 0

REM ---------------------------------------------------------------------------
REM Any Python new enough to build the environment from. Each candidate has to
REM actually run: Windows ships a python.exe stub that only opens the Store.
:find_base
set "_SE_BASE="
set "_SE_CHECK=import sys; sys.exit(sys.version_info[:2] < (3,11))"
py -3 -c "%_SE_CHECK%" >nul 2>nul && set "_SE_BASE=py -3" && goto :eof
python -c "%_SE_CHECK%" >nul 2>nul && set "_SE_BASE=python" && goto :eof
for %%P in ("%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\miniconda3" "%USERPROFILE%\anaconda3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3") do (
    if not defined _SE_BASE if exist "%%~P\python.exe" (
        "%%~P\python.exe" -c "%_SE_CHECK%" >nul 2>nul && set _SE_BASE="%%~P\python.exe"
    )
)
goto :eof
