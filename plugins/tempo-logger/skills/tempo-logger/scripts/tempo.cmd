@echo off
rem forces UTF-8 from outside the interpreter, because PYTHONUTF8 set inside a script
rem is read too late to take effect and the JSON would be decoded as cp1252 on a Windows machine.
setlocal EnableDelayedExpansion

set "HERE=%~dp0"
set "SCRIPT=%~1"
if "%SCRIPT%"=="" (
  echo usage: tempo.cmd ^<script-name^> [args...] 1>&2
  exit /b 2
)

set "TARGET=%HERE%%SCRIPT%.py"
rem a missing script must say so plainly:
rem python's own error for a missing path reads like a broken install.
if not exist "%TARGET%" (
  echo tempo: no such script: %SCRIPT%.py ^(check the name^) 1>&2
  exit /b 2
)

rem batch `shift` does not affect %*, so rebuild the argument list
rem instead of passing %* and sending the script name through as an argument.
shift
set "ARGS="
:collect
if "%~1"=="" goto run
set ARGS=!ARGS! "%~1"
shift
goto collect

:run
rem the launcher being installed does not mean it can find a Python 3, and its own
rem failure code would otherwise be returned as if a script had run and exited with it
where py >nul 2>&1 && (
  py -3 -X utf8 -c "" >nul 2>&1 && (
    py -3 -X utf8 "%TARGET%"!ARGS!
    exit /b !ERRORLEVEL!
  )
)
python -X utf8 -c "" >nul 2>&1 || (
  echo tempo: no python interpreter found on PATH ^(tried py, python^) 1>&2
  exit /b 2
)
python -X utf8 "%TARGET%"!ARGS!
exit /b !ERRORLEVEL!
