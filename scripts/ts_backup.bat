@echo off
rem ==========================================================================
rem  ts_backup.bat
rem  Cyclic backup of PycharmProjects subfolders, sent over Tailscale Taildrop.
rem  One project folder per run. Falls back through a list of target devices.
rem
rem  ASCII ONLY. Do not put non-ASCII characters in this file: the console
rem  code page and the file encoding will disagree and corrupt the log.
rem ==========================================================================

setlocal enabledelayedexpansion

rem ---------------------------- CONFIG --------------------------------------
rem Root folder holding the project folders (one is backed up per run).
set "BASE_DIR=C:\Users\DiCiA\PycharmProjects"

rem Remembers which project folder was handled last.
set "STATE_FILE=C:\Users\DiCiA\backup_state.txt"

rem Working directory: archives are built here, log lives here.
set "WORK_DIR=C:\TempBackup"

rem Target devices, in fallback order. Space separated, no trailing colon.
set "TARGETS=wisenesco-23031302 laptop-7gmpubqc desktop-dvj3pqk desktop-0g92n63"

rem Retention for archives that could not be sent to ANY target.
set "PENDING_KEEP_DAYS=3"
set "PENDING_KEEP_PER_PROJECT=1"

rem Log rotates once it grows past this many megabytes (one .1 backup kept).
set "LOG_MAX_MB=5"

rem Folders to leave out of the archive. Empty = archive everything
rem (.git and .env are preserved by design).
rem Example:
rem   set "TAR_EXCLUDES=--exclude=*/venv/* --exclude=*/__pycache__/* --exclude=*/node_modules/*"
set "TAR_EXCLUDES="

rem 1 = build the archive but skip the actual transfer (for testing).
set "DRY_RUN=0"
rem --------------------------------------------------------------------------

set "PENDING_DIR=%WORK_DIR%\pending"
set "LOG_FILE=%WORK_DIR%\backup.log"

rem Exit code reported to Task Scheduler as "Last Result":
rem   0 = archived and delivered (or nothing to do)
rem   1 = fatal, no usable archive was produced
rem   2 = archive was produced but no target accepted it (parked in pending)
set "RC=0"

if not exist "%WORK_DIR%"    mkdir "%WORK_DIR%"
if not exist "%PENDING_DIR%" mkdir "%PENDING_DIR%"

call :RotateLog
call :Log "=== run start ==="

rem Archives left behind by a previous interrupted run (pending/ is untouched).
del /f /q "%WORK_DIR%\*.zip" >nul 2>&1

rem ---------------------------------------------------------------- timestamp
set "DATETIME="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyy_MM_dd_HH_mm"`) do set "DATETIME=%%I"
if not defined DATETIME (
    call :Log "FATAL: could not obtain timestamp from PowerShell"
    set "RC=1"
    goto :End
)

rem ------------------------------------------------- retry previous failures
call :FlushPending
call :PrunePending

rem ------------------------------------------------------ pick target folder
set "LAST_FOLDER="
if exist "%STATE_FILE%" (
    for /f "usebackq tokens=* delims= " %%A in ("%STATE_FILE%") do (
        if not defined LAST_FOLDER set "LAST_FOLDER=%%A"
    )
)

:TrimTrailingSpace
if defined LAST_FOLDER (
    if "!LAST_FOLDER:~-1!"==" " (
        set "LAST_FOLDER=!LAST_FOLDER:~0,-1!"
        goto :TrimTrailingSpace
    )
)

rem Dot folders (.idea, .git, ...) sitting directly under BASE_DIR are tooling
rem state, not projects. Including them would waste a whole turn of the cycle.
set "TARGET_FOLDER="
set "FIRST_FOLDER="
set "TAKE_NEXT=0"
for /d %%F in ("%BASE_DIR%\*") do (
    set "NAME="
    set "NAME=%%~nxF"
    if not "!NAME:~0,1!"=="." (
        if not defined FIRST_FOLDER set "FIRST_FOLDER=!NAME!"
        if "!TAKE_NEXT!"=="1" (
            if not defined TARGET_FOLDER set "TARGET_FOLDER=!NAME!"
        )
        if /i "!NAME!"=="!LAST_FOLDER!" set "TAKE_NEXT=1"
    )
)

rem No entry recorded, or the recorded folder is gone / was the last one:
rem wrap around to the first folder.
if not defined TARGET_FOLDER set "TARGET_FOLDER=%FIRST_FOLDER%"

if not defined TARGET_FOLDER (
    call :Log "no project folders found under %BASE_DIR% - nothing to do"
    goto :End
)

call :Log "target folder: !TARGET_FOLDER! (previous: !LAST_FOLDER!)"

rem Advance the cursor NOW, not after a successful transfer. If a folder
rem keeps failing, the remaining projects must still get their turn; failed
rem archives are the responsibility of pending/.
>"%STATE_FILE%" echo !TARGET_FOLDER!

rem ------------------------------------------------------------------ archive
set "ZIP_FILE=%WORK_DIR%\!TARGET_FOLDER!_!DATETIME!.zip"
call :Log "creating archive: !ZIP_FILE!"

tar -a -c -f "!ZIP_FILE!" %TAR_EXCLUDES% -C "%BASE_DIR%" "!TARGET_FOLDER!" >>"%LOG_FILE%" 2>&1
set "TAR_RC=%ERRORLEVEL%"

rem bsdtar returns 1 for warnings (locked files such as a git index or an
rem open sqlite db) and still produces a usable archive. 2 and above is fatal.
if !TAR_RC! geq 2 (
    call :Log "FATAL: tar failed with exit code !TAR_RC!"
    del /f /q "!ZIP_FILE!" >nul 2>&1
    set "RC=1"
    goto :End
)
if !TAR_RC! equ 1 call :Log "WARN: tar reported warnings (some files may have been locked)"

if not exist "!ZIP_FILE!" (
    call :Log "FATAL: archive was not created"
    set "RC=1"
    goto :End
)
set "ZIP_SIZE=0"
for %%A in ("!ZIP_FILE!") do set "ZIP_SIZE=%%~zA"
if !ZIP_SIZE! leq 0 (
    call :Log "FATAL: archive is empty"
    del /f /q "!ZIP_FILE!" >nul 2>&1
    set "RC=1"
    goto :End
)
call :Log "archive ready, !ZIP_SIZE! bytes"

rem ----------------------------------------------------------------- transfer
call :TrySend "!ZIP_FILE!"
if not errorlevel 1 (
    del /f /q "!ZIP_FILE!" >nul 2>&1
    call :Log "sent and removed local archive"
) else (
    move /y "!ZIP_FILE!" "%PENDING_DIR%\" >nul 2>&1
    call :Log "all targets failed - archive moved to pending, will retry next run"
    call :PrunePending
    set "RC=2"
)

:End
call :Log "=== run end (exit !RC!) ==="
endlocal & exit /b %RC%


rem ==========================================================================
rem  Subroutines
rem ==========================================================================

rem --------------------------------------------------------------------------
rem :TrySend <quoted-path>
rem Walks TARGETS in order and stops at the first device that accepts the file.
rem Returns 0 on success, 1 when every target failed.
rem
rem NOTE: "if not errorlevel 1" is used instead of "if %ERRORLEVEL% equ 0"
rem because %ERRORLEVEL% inside a parenthesised block is expanded when the
rem block is parsed, not when it runs, and would always read the stale value.
rem --------------------------------------------------------------------------
:TrySend
if "%DRY_RUN%"=="1" (
    call :Log "DRY_RUN=1 - skipping transfer of %~nx1"
    exit /b 0
)
for %%D in (%TARGETS%) do (
    tailscale file cp %1 %%D: >>"%LOG_FILE%" 2>&1
    if not errorlevel 1 (
        call :Log "sent %~nx1 -> %%D"
        exit /b 0
    )
    call :Log "failed  %~nx1 -> %%D"
)
exit /b 1

rem --------------------------------------------------------------------------
rem :FlushPending
rem Retries archives from earlier runs, oldest first, before creating a new one.
rem --------------------------------------------------------------------------
:FlushPending
for /f "usebackq delims=" %%P in (`dir /b /a:-d /o:d "%PENDING_DIR%\*.zip" 2^>nul`) do (
    call :TrySend "%PENDING_DIR%\%%P"
    if not errorlevel 1 (
        del /f /q "%PENDING_DIR%\%%P" >nul 2>&1
        call :Log "pending flushed: %%P"
    ) else (
        call :Log "pending still stuck: %%P"
    )
)
exit /b 0

rem --------------------------------------------------------------------------
rem :PrunePending
rem Retention for pending/: drop anything older than PENDING_KEEP_DAYS, then
rem keep at most PENDING_KEEP_PER_PROJECT archives per project folder.
rem The project name is recovered by stripping the _yyyy_MM_dd_HH_mm suffix.
rem Date arithmetic is done in PowerShell - cmd cannot do it reliably.
rem --------------------------------------------------------------------------
:PrunePending
powershell -NoProfile -Command "$d='%PENDING_DIR%'; if (Test-Path -LiteralPath $d) { Get-ChildItem -LiteralPath $d -Filter *.zip -File | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-%PENDING_KEEP_DAYS%) } | Remove-Item -Force -ErrorAction SilentlyContinue; Get-ChildItem -LiteralPath $d -Filter *.zip -File | Group-Object { $_.BaseName -replace '_\d{4}(_\d{2}){4}$','' } | ForEach-Object { $_.Group | Sort-Object LastWriteTime -Descending | Select-Object -Skip %PENDING_KEEP_PER_PROJECT% | Remove-Item -Force -ErrorAction SilentlyContinue } }" >>"%LOG_FILE%" 2>&1
exit /b 0

rem --------------------------------------------------------------------------
rem :RotateLog
rem --------------------------------------------------------------------------
:RotateLog
if not exist "%LOG_FILE%" exit /b 0
set /a LOG_MAX_BYTES=%LOG_MAX_MB%*1048576
set "LOG_SIZE=0"
for %%A in ("%LOG_FILE%") do set "LOG_SIZE=%%~zA"
if !LOG_SIZE! gtr !LOG_MAX_BYTES! (
    if exist "%LOG_FILE%.1" del /f /q "%LOG_FILE%.1" >nul 2>&1
    move /y "%LOG_FILE%" "%LOG_FILE%.1" >nul 2>&1
)
exit /b 0

rem --------------------------------------------------------------------------
rem :Log <quoted-message>
rem
rem Redirection comes first so no trailing space is written.
rem
rem The message is parked in a variable and emitted with delayed expansion.
rem Writing "echo ... %~1" directly would substitute the text while the line
rem is still being parsed, so a ">" inside the message (as in "sent x -> host")
rem becomes a real redirection operator and the entry silently lands in a file
rem named after whatever followed it instead of in the log. Delayed expansion
rem happens after redirection has already been resolved, so !MSG! is inert.
rem --------------------------------------------------------------------------
:Log
setlocal
set "MSG=%~1"
>>"%LOG_FILE%" echo([%DATE% %TIME%] !MSG!
endlocal & exit /b 0
