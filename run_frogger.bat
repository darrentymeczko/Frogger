@echo off
REM ---------------------------------------------------------------------------
REM  Frogger -- double-click to run.
REM
REM  The whole program in one window: plate templates, experiments (where the
REM  photos are measured) and the review of their results, as tabs, with Home
REM  to start from. It runs spotting_app.py with no stage, i.e.
REM      spotting_app.py workbench
REM
REM  Optionally pass files or result folders to open, e.g.
REM      run_frogger.bat "Experiment Designs\Set09.spotexp.json"
REM
REM  A single tool or console pipeline runs with  py spotting_app.py <stage>
REM  (see spotting_app.py).
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM Find this program's Python. setup_env.bat uses a conda environment named
REM "spotting" if there is one; otherwise, on the first run on a computer, it
REM builds the program's own environment and installs requirements.txt.
call "%~dp0setup_env.bat"
if errorlevel 1 (
    pause
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
chcp 65001 >nul

set "ARGS="
:collect
if "%~1"=="" goto run
set "ARGS=!ARGS! "%~1""
shift
goto collect

:run
REM Hand the window to pythonw so no console is left sitting behind it. The
REM program reports its own problems: a crash at startup is written to
REM spotting_error.log and shown in a dialog.
if exist "%PYWEXE%" (
    start "" "%PYWEXE%" "%~dp0spotting_app.py" workbench %ARGS%
    endlocal
    exit /b 0
)

"%PYEXE%" "%~dp0spotting_app.py" workbench %ARGS%
set "RC=%ERRORLEVEL%"

REM Only hold the console open when something went wrong.
if not "%RC%"=="0" (
    echo.
    echo   *** Finished with errors (exit code %RC%^) ***
    echo.
    pause
)
endlocal
exit /b %RC%
