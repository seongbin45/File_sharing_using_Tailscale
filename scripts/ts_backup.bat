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

rem Full path to 7z.exe. Deliberately NOT looked up on PATH: the 7-Zip
rem installer does not register itself there, and a scheduled task runs with a
rem plain environment rather than whatever shell the command was tested in.
set "SEVENZIP=C:\Program Files\7-Zip\7z.exe"

rem LZMA2 compression level. Measured on this data against Windows tar's zip:
rem   -mx=1  10% smaller and twice as fast   (deflate is single threaded)
rem   -mx=5  21% smaller, same total run time once transfer is counted
rem   -mx=9  a few percent more for several times the time and memory
rem 5 is the choice: the run is unattended at 04:00 with hours of headroom,
rem so bytes matter and minutes do not.
set "SEVENZIP_LEVEL=5"

rem Retention for archives that could not be sent to ANY target.
set "PENDING_KEEP_DAYS=3"
set "PENDING_KEEP_COUNT=1"

rem Log rotates once it grows past this many megabytes (one .1 backup kept).
set "LOG_MAX_MB=5"

rem Optional path to a text file listing names to leave out of the archive,
rem one per line, for example:
rem     venv
rem     .venv
rem     __pycache__
rem     node_modules
rem Empty = archive everything, which is the intent here: .git history and
rem .env files must survive.
rem
rem A list file rather than 7-Zip's own -xr!name switches: "!" is consumed by
rem delayed expansion, so those switches would be silently mangled here.
rem The path must not contain spaces.
set "EXCLUDE_LIST="

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

rem 7-Zip given a directory stores it relative to its parent, so the archive
rem unpacks into a folder of the same name instead of scattering its contents.
for %%A in ("%BASE_DIR%") do set "BASE_NAME=%%~nxA"

if not exist "%WORK_DIR%"    mkdir "%WORK_DIR%"
if not exist "%PENDING_DIR%" mkdir "%PENDING_DIR%"

call :RotateLog
call :Log "=== run start ==="

rem Archives left behind by a previous interrupted run (pending/ is untouched).
del /f /q "%WORK_DIR%\*.7z" >nul 2>&1

if not exist "%BASE_DIR%\" (
    call :Log "FATAL: BASE_DIR does not exist: %BASE_DIR%"
    set "RC=1"
    goto :End
)
if not exist "%SEVENZIP%" (
    call :Log "FATAL: 7-Zip not found at %SEVENZIP%"
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
set "ARCHIVE=%WORK_DIR%\!BASE_NAME!_!DATETIME!.7z"
call :Log "creating archive: !ARCHIVE! (LZMA2 -mx=%SEVENZIP_LEVEL%)"

set "EXCLUDE_ARG="
if defined EXCLUDE_LIST (
    if exist "!EXCLUDE_LIST!" (
        set "EXCLUDE_ARG=-xr@!EXCLUDE_LIST!"
        call :Log "excluding names listed in !EXCLUDE_LIST!"
    ) else (
        call :Log "WARN: EXCLUDE_LIST not found, archiving everything: !EXCLUDE_LIST!"
    )
)

"%SEVENZIP%" a -t7z -mx=%SEVENZIP_LEVEL% -mmt=on -bso0 -bsp0 !EXCLUDE_ARG! "!ARCHIVE!" "%BASE_DIR%" >>"%LOG_FILE%" 2>&1
set "SZ_RC=%ERRORLEVEL%"

rem 7-Zip returns 1 for warnings (a file it could not open, such as one held
rem open by another process) and still produces a usable archive.
rem 2 = fatal, 7 = bad command line, 8 = out of memory, 255 = user abort.
if !SZ_RC! geq 2 (
    call :Log "FATAL: 7-Zip failed with exit code !SZ_RC!"
    del /f /q "!ARCHIVE!" >nul 2>&1
    set "RC=1"
    goto :End
)
if !SZ_RC! equ 1 call :Log "WARN: 7-Zip reported warnings (some files may have been locked)"

if not exist "!ARCHIVE!" (
    call :Log "FATAL: archive was not created"
    set "RC=1"
    goto :End
)
set "ARCHIVE_SIZE=0"
for %%A in ("!ARCHIVE!") do set "ARCHIVE_SIZE=%%~zA"
if !ARCHIVE_SIZE! leq 0 (
    call :Log "FATAL: archive is empty"
    del /f /q "!ARCHIVE!" >nul 2>&1
    set "RC=1"
    goto :End
)
call :Log "archive ready, !ARCHIVE_SIZE! bytes"

rem ----------------------------------------------------------------- transfer
call :TrySend "!ARCHIVE!"
if not errorlevel 1 (
    del /f /q "!ARCHIVE!" >nul 2>&1
    call :Log "sent and removed local archive"
) else (
    move /y "!ARCHIVE!" "%PENDING_DIR%\" >nul 2>&1
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
for /f "usebackq delims=" %%P in (`dir /b /a:-d /o:d "%PENDING_DIR%\*.7z" 2^>nul`) do (
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
powershell -NoProfile -Command "$d='%PENDING_DIR%'; if (Test-Path -LiteralPath $d) { Get-ChildItem -LiteralPath $d -Filter *.7z -File | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-%PENDING_KEEP_DAYS%) } | Remove-Item -Force -ErrorAction SilentlyContinue; Get-ChildItem -LiteralPath $d -Filter *.7z -File | Sort-Object LastWriteTime -Descending | Select-Object -Skip %PENDING_KEEP_COUNT% | Remove-Item -Force -ErrorAction SilentlyContinue }" >>"%LOG_FILE%" 2>&1
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
