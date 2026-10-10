@echo off
REM Manual/fallback only. Do NOT point a shortcut or Task at this .bat -
REM cmd.exe can still flash. Prefer Naru_update_manager_hidden.vbs.
set "DIR=%~dp0"
if not exist "%DIR%Naru_update_manager_hidden.vbs" exit /b 1
REM Re-enter via VBS (window style 0) even if someone double-clicks this .bat.
wscript //B //Nologo "%DIR%Naru_update_manager_hidden.vbs" %*
