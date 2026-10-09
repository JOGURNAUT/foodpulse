@echo off
REM In PowerShell these need the .\ prefix:  .\run all
setlocal
cd /d "%~dp0"
if "%~1"=="" goto :help
if /i "%~1"=="all"      goto :all
if /i "%~1"=="generate" goto :generate
if /i "%~1"=="load"     goto :load
if /i "%~1"=="models"   goto :models
if /i "%~1"=="report"   goto :report
if /i "%~1"=="test"     goto :test
if /i "%~1"=="dash"     goto :dash
if /i "%~1"=="fresh"    goto :fresh
goto :help

:all
python -m foodpulse.generate || exit /b 1
python -m foodpulse.load     || exit /b 1
python scripts/build_models.py || exit /b 1
python scripts/build_report.py || exit /b 1
python -m pytest tests/ -q
exit /b %errorlevel%

:generate
python -m foodpulse.generate
exit /b %errorlevel%
:load
python -m foodpulse.load
exit /b %errorlevel%
:models
python scripts/build_models.py
exit /b %errorlevel%
:report
python scripts/build_report.py
exit /b %errorlevel%
:test
python -m pytest tests/ -q
exit /b %errorlevel%

:help
echo.
echo   In PowerShell: .\run all
echo.
echo   all        generate, load, build models, run tests, write the report
echo   generate   synthetic orders with defects injected
echo   load       raw CSVs into the warehouse
echo   models     build the dbt models and run all 35 model tests
echo   report     write docs\index.html
echo   test       the pytest suite
echo   dash       the Streamlit dashboard
echo   fresh      rebuild incremental models from scratch
echo.
exit /b 0
