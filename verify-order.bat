@echo off
REM Usage: verify-order.bat <order-id> [options]
REM Example: verify-order.bat 1313394568122470400
REM Example: verify-order.bat 1313394568122470400 -e prod
REM Example: verify-order.bat 1313394568122470400 -e prod -p -v

setlocal enabledelayedexpansion

set ORDER_ID=
set ENV=stg
set SHOW_PARTNER=
set TAX_VIEW=
set PRECISION=
set VERBOSE=

REM Parse arguments
:parse_args
if "%~1"=="" goto :done_parsing

REM Check if first positional argument (order ID)
if "%ORDER_ID%"=="" (
    set "FIRST_CHAR=%~1"
    set "FIRST_CHAR=!FIRST_CHAR:~0,1!"
    if not "!FIRST_CHAR!"=="-" (
        set "ORDER_ID=%~1"
        shift
        goto :parse_args
    )
)

if /i "%~1"=="-e" (
    set "ENV=%~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="--env" (
    set "ENV=%~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="-p" (
    set "SHOW_PARTNER=--show-partner-config"
    shift
    goto :parse_args
)
if /i "%~1"=="--show-partner-config" (
    set "SHOW_PARTNER=--show-partner-config"
    shift
    goto :parse_args
)
if /i "%~1"=="-t" (
    set "TAX_VIEW=--tax-view %~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="--tax-view" (
    set "TAX_VIEW=--tax-view %~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="--precision" (
    set "PRECISION=--precision %~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="-v" (
    set "VERBOSE=--verbose"
    shift
    goto :parse_args
)
if /i "%~1"=="--verbose" (
    set "VERBOSE=--verbose"
    shift
    goto :parse_args
)

REM Unknown argument, skip
shift
goto :parse_args

:done_parsing

if "%ORDER_ID%"=="" (
    echo Usage: verify-order.bat ^<order-id^> [options]
    echo.
    echo Arguments:
    echo   order-id                    Required - The order ID to verify
    echo.
    echo Options:
    echo   -e, --env ^<stg^|prod^>        Environment ^(default: stg^)
    echo   -p, --show-partner-config   Display partner configuration
    echo   -t, --tax-view ^<level^>      Tax detail: basic, full, or failures
    echo       --precision ^<2-8^>       Decimal precision ^(default: 5^)
    echo   -v, --verbose               Enable verbose logging
    echo.
    echo Examples:
    echo   verify-order.bat 1313394568122470400
    echo   verify-order.bat 1313394568122470400 -e prod
    echo   verify-order.bat 1313394568122470400 -e prod -p
    echo   verify-order.bat 1313394568122470400 -e stg -t full -v
    exit /b 1
)

cd /d "%~dp0"
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id %ORDER_ID% --env %ENV% %SHOW_PARTNER% %TAX_VIEW% %PRECISION% %VERBOSE%

endlocal
