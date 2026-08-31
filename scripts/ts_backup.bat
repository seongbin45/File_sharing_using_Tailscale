@echo off
rem ==========================================================================
rem  ts_backup.bat
rem  Archives the whole project root in one piece and sends it over Tailscale
rem  Taildrop, falling back through a list of target devices.
rem
rem  Every run produces a complete, self-consistent snapshot. Run it once a
rem  day: at roughly 6 GB and ~7 MB/s of Taildrop throughput a run takes on
rem  the order of twenty minutes, so anything more frequent is not sensible.
rem
rem  ASCII ONLY. Do not put non-ASCII characters in this file: the console
rem  code page and the file encoding will disagree and corrupt the log.
rem ==========================================================================

setlocal enabledelayedexpansion

rem ---------------------------- CONFIG --------------------------------------
rem The directory that gets archived, whole, on every run.
set "BASE_DIR=C:\Users\DiCiA\PycharmProjects"

rem Working directory: the archive is built here, the log lives here.
rem Needs room for a full archive of BASE_DIR.
set "WORK_DIR=C:\TempBackup"

rem Refuse to start unless the work volume has at least this many megabytes
rem free. A half-written archive that fills the disk is worse than a skipped
rem run.
set "MIN_FREE_MB=10000"

rem Target devices, in fallback order. Space separated, no trailing colon.
set "TARGETS=wisenesco-23031302 laptop-7gmpubqc desktop-dvj3pqk desktop-0g92n63"

rem Retention for archives that could not be sent to ANY target.
set "PENDING_KEEP_DAYS=3"
set "PENDING_KEEP_COUNT=1"

rem Log rotates once it grows past this many megabytes (one .1 backup kept).
set "LOG_MAX_MB=5"

rem Paths to leave out of the archive. Empty = archive everything, which is
rem the intent here: .git history and .env files must survive.
rem Example, if the archive ever has to be trimmed:
rem   set "TAR_EXCLUDES=--exclude=*/venv/* --exclude=*/__pycache__/*"
set "TAR_EXCLUDES="

rem 1 = build the archive but skip the actual transfer (for testing).
set "DRY_RUN=0"
rem --------------------------------------------------------------------------

set "PENDING_DIR=%WORK_DIR%\pending"
set "LOG_FILE=%WORK_DIR%\backup.log"

rem Exit code reported to Task Scheduler as "Last Result":
rem   0 = archived and delivered
rem   1 = fatal, no usable archive was produced
rem   2 = archive was produced but no target accepted it (parked in pending)
set "RC=0"

rem Split BASE_DIR into its parent and its own name. tar is then pointed at
rem the parent and told to archive the one directory, so the archive unpacks
rem into a folder of the same name instead of scattering its contents.
for %%A in ("%BASE_DIR%") do set "BASE_NAME=%%~nxA"
for %%A in ("%BASE_DIR%") do set "BASE_PARENT=%%~dpA"
if "!BASE_PARENT:~-1!"=="\" set "BASE_PARENT=!BASE_PARENT:~0,-1!"

if not exist "%WORK_DIR%"    mkdir "%WORK_DIR%"
if not exist "%PENDING_DIR%" mkdir "%PENDING_DIR%"

call :RotateLog
call :Log "=== run start ==="

rem Archives left behind by a previous interrupted run (pending/ is untouched).
del /f /q "%WORK_DIR%\*.zip" >nul 2>&1

if not exist "%BASE_DIR%\" (
    call :Log "FATAL: BASE_DIR does not exist: %BASE_DIR%"
    set "RC=1"
    goto :End
)

rem ---------------------------------------------------------------- timestamp
set "DATETIME="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyy_MM_dd_HH_mm"`) do set "DATETIME=%%I"
if not defined DATETIME (
    call :Log "FATAL: could not obtain timestamp from PowerShell"
    set "RC=1"
    goto :End
)

rem --------------------------------------------------------------- disk check
set "FREE_MB="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[int]((Get-Item -LiteralPath '%WORK_DIR%').PSDrive.Free / 1MB)"`) do set "FREE_MB=%%I"
if defined FREE_MB (
    call :Log "free space on work volume: !FREE_MB! MB"
    if !FREE_MB! lss %MIN_FREE_MB% (
        call :Log "FATAL: need at least %MIN_FREE_MB% MB free, refusing to start"
        set "RC=1"
        goto :End
    )
) else (
    call :Log "WARN: could not determine free space, continuing"
)

rem ------------------------------------------------- retry previous failures
call :FlushPending
call :PrunePending

rem ------------------------------------------------------------------ archive
set "ZIP_FILE=%WORK_DIR%\!BASE_NAME!_!DATETIME!.zip"
call :Log "creating archive: !ZIP_FILE!"

tar -a -c -f "!ZIP_FILE!" %TAR_EXCLUDES% -C "!BASE_PARENT!" "!BASE_NAME!" >>"%LOG_FILE%" 2>&1
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
rem keep at most PENDING_KEEP_COUNT archives. Each one is a full snapshot, so
rem holding more than the newest is rarely worth the disk.
rem Date arithmetic is done in PowerShell - cmd cannot do it reliably.
rem --------------------------------------------------------------------------
:PrunePending
powershell -NoProfile -Command "$d='%PENDING_DIR%'; if (Test-Path -LiteralPath $d) { Get-ChildItem -LiteralPath $d -Filter *.zip -File | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-%PENDING_KEEP_DAYS%) } | Remove-Item -Force -ErrorAction SilentlyContinue; Get-ChildItem -LiteralPath $d -Filter *.zip -File | Sort-Object LastWriteTime -Descending | Select-Object -Skip %PENDING_KEEP_COUNT% | Remove-Item -Force -ErrorAction SilentlyContinue }" >>"%LOG_FILE%" 2>&1
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
