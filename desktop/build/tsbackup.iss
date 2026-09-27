; Inno Setup script for TsBackup.
;
;   iscc /DMyAppVersion=0.1.1 build\tsbackup.iss
;
; Run from desktop\ (same convention as tsbackup.spec) so the relative
; "..\dist\..." paths below resolve. Produces desktop\dist\TsBackup-Setup.exe.
; MyAppVersion is passed in by the release workflow from the git tag; it
; defaults to 0.0.0 for a local/manual build with no tag context.
;
; Wraps the already-built TsBackup.exe (see tsbackup.spec) - this script
; does not touch how that exe itself is built.

#define MyAppName "TsBackup"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "TsBackup.exe"

[Setup]
AppId={{8F2C4A61-9E3B-4C7A-BD5E-1A6F3D9E2B47}}
; ^ Fixed forever - generated once for this app. Regenerating it would make
; every future version look like an unrelated app to Windows (no in-place
; upgrade, duplicate Start Menu/uninstall entries). Never change this.
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; Let the person installing choose per-user (no admin needed) vs
; per-machine (Program Files, UAC prompt) at runtime, and let a
; scripted/silent install pick via /CURRENTUSER or /ALLUSERS - the same
; switch a future winget/Chocolatey manifest would use. {autopf} below
; resolves to the right Program Files variant for whichever was chosen.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=TsBackup-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; No .ico bundled here - same reasoning as tsbackup.spec: the app paints
; its own icon in code (app/icons.py), so the wizard just uses Inno's
; default and there's no icon file to keep in sync.

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Independent update manager - lives in {app} alongside TsBackup.exe
; itself (not a separate ProgramData/LocalAppData folder like CloneUp
; uses): the apply step only ever replaces the single named file
; TsBackup.exe and never wipes the folder, so there's nothing here for
; it to collide with. See update_manager/paths.py's module docstring.
Source: "..\dist\TsBackup_update_manager.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\update_manager\launchers\TsBackup_update_manager.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\update_manager\launchers\TsBackup_update_manager_hidden.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\VERSION"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; {userstartup}, not {commonstartup}, regardless of per-user/per-machine
; install: autostart is a per-login concern, not a per-install-scope one -
; only the installing user gets it, which is the least surprising default
; either way (a per-machine install shouldn't silently autostart the app
; for every other account on a shared PC).
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon
; Update manager's ongoing autostart. Filename/Parameters here is the
; exact same command line as the one-time [Run] launch below, and the
; one CloneUp's schtasks /TR uses - wscript.exe (GUI-subsystem, no
; console) running the hidden VBS (which itself launches the exe with
; window style 0) is what actually suppresses any window; neither a
; Scheduled Task nor a Startup shortcut being the launch vehicle changes
; that, so no schtasks/SYSTEM-task complexity is needed on either the
; admin or per-user install mode.
Name: "{userstartup}\{#MyAppName} Update Check"; Filename: "{sys}\wscript.exe"; Parameters: "//B //Nologo ""{app}\TsBackup_update_manager_hidden.vbs"""; Tasks: autoupdatemanager

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 아이콘:"; Flags: unchecked
; Checked by default - this is what makes "automation" work without any
; external Task Scheduler entry: the app has its own in-process QTimer
; scheduler (tsbackup/engine.py), so as long as it's running in the tray
; it keeps backing up on schedule. No flag here means checked by default.
Name: "startupicon"; Description: "Windows 시작 시 자동 실행 (트레이에 상주하며 예약된 백업을 계속 수행합니다)"; GroupDescription: "시작 옵션:"
; Default (no Flags) = initially checked, and stays checked across an
; upgrade. Do not add "checkedonce" - CloneUp's own release history
; found that flag unchecks the task on upgrade whenever a previous
; version predating the task existed, which silently turned auto-update
; off on every existing install the moment it shipped.
Name: "autoupdatemanager"; Description: "로그인 시 자동 업데이트 확인 실행"; GroupDescription: "시작 옵션:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,TsBackup}"; Flags: nowait postinstall skipifsilent
; Start the update manager once now too - the Startup-folder shortcut
; above only fires on the *next* login, and hidden/skipifsilent/unchecked
; keeps this off any wizard summary page.
Filename: "{sys}\wscript.exe"; Parameters: "//B //Nologo ""{app}\TsBackup_update_manager_hidden.vbs"""; Description: "자동 업데이트 확인 시작"; Flags: nowait postinstall skipifsilent unchecked; Tasks: autoupdatemanager
; Scoped inbound allow for the pairing listener (tsbackup/pairing.py's
; PAIRING_PORT - keep these in sync) - restricted to Tailscale's own CGNAT
; range and this app's exe, never a blanket allow. Needs admin rights to
; manage firewall rules at all, so this only runs - and only eliminates
; the first-run Windows Firewall prompt - on a per-machine (admin) install.
; A per-user install (this installer's other supported mode, since
; PrivilegesRequired is "lowest") still shows that prompt on first bind;
; that's an unavoidable Windows constraint, not a bug in this rule.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""TsBackup Pairing"" dir=in action=allow protocol=TCP localport=8781 remoteip=100.64.0.0/10 program=""{app}\{#MyAppExeName}"" enable=yes"; Flags: runhidden; Check: IsAdminInstallMode

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""TsBackup Pairing"""; Flags: runhidden; Check: IsAdminInstallMode

; Deliberately no [UninstallDelete]: uninstall only removes what's listed
; under [Files]/[Icons] above. The user's %LOCALAPPDATA%\TsBackup\
; config.json and log are left alone on purpose - config persists across
; reinstall/upgrade, matching how config.py already reasons about keeping
; install location and user data separate.
