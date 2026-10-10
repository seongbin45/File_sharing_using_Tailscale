; Inno Setup script for 나루 (Naru).
;
;   iscc /DMyAppVersion=0.1.1 build\naru.iss
;
; Run from desktop\ (same convention as the PyInstaller specs) so the
; relative "..\dist\..." paths below resolve. Produces
; desktop\dist\Naru-Setup.exe. MyAppVersion is passed in by the release
; workflow from the git tag; it defaults to 0.0.0 for a local/manual build
; with no tag context.
;
; Wraps the already-built Naru.exe (see tsbackup.spec) - this script does
; not touch how that exe itself is built.
;
; File/path-facing names (exe names, install folder, Start Menu group's
; underlying registry string) use the ASCII "Naru" transliteration; only
; MyAppName - which drives the Korean display text shown in the wizard,
; Start Menu, and Add/Remove Programs - is the real Korean name "나루".

#define MyAppName "나루"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "Naru.exe"

[Setup]
AppId={{8F2C4A61-9E3B-4C7A-BD5E-1A6F3D9E2B47}}
; ^ Fixed forever - generated once for this app. Regenerating it would make
; every future version look like an unrelated app to Windows (no in-place
; upgrade, duplicate Start Menu/uninstall entries). Never change this -
; not even for this rename: it's what lets this release upgrade in place
; over a pre-rename "TsBackup"-named install rather than installing
; side by side as an unrelated second app.
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Naru
DefaultGroupName={#MyAppName}
; Per-machine (Program Files, UAC prompt) is the default - most users just
; want "install it," and it also means the firewall rule below applies
; with no extra prompt. A per-user install (no admin needed) remains
; available as an explicit opt-out via the wizard's dialog or an explicit
; /CURRENTUSER switch - the same switches a future winget/Chocolatey
; manifest would use. {autopf} below resolves to the right Program Files
; variant for whichever was chosen.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=Naru-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; build/make_icon.py renders this from the same code (app/icons.py) that
; draws the app's own window/tray icon, so the wizard/Add-Remove-Programs
; icon and the running app's icon are always the same source of truth.
SetupIconFile=..\dist\naru.ico

[Dirs]
; Lets the auto-update manager - which runs unelevated by design (a
; per-user Startup-folder/login launch, never itself UAC-elevated) -
; write into {app} even on a per-machine (admin, Program Files) install:
; both creating {app}\UpdateManager\{status,pending} and atomically
; replacing {app}\Naru.exe itself (NTFS's "Modify" permission set
; includes FILE_DELETE_CHILD on the directory, which authorizes replacing
; any child file regardless of that file's own ACL - the existing
; Naru.exe doesn't need its own ACL touched). No-op on a per-user
; install (already owned outright by the installing user). Same technique
; other self-updating per-machine Windows apps use for the same reason
; (e.g. Chrome) - the accepted tradeoff is that any local non-admin
; account can also write here, acceptable given this app's one-operator-
; per-PC framing (see README.md).
Name: "{app}"; Permissions: users-modify

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Independent update manager - lives in {app} alongside Naru.exe itself
; (not a separate ProgramData/LocalAppData folder like CloneUp uses): the
; apply step only ever replaces the single named file Naru.exe and never
; wipes the folder, so there's nothing here for it to collide with. See
; update_manager/paths.py's module docstring.
Source: "..\dist\Naru_update_manager.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\update_manager\launchers\Naru_update_manager.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\update_manager\launchers\Naru_update_manager_hidden.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\VERSION"; DestDir: "{app}"; Flags: ignoreversion
; Build-time .ico (see SetupIconFile above) - also bundled into {app} so
; a from-source/dev run can reuse it if ever needed; the running app's
; own window/tray icon stays code-drawn regardless (app/icons.py).
Source: "..\dist\naru.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; {userstartup}, not {commonstartup}, regardless of per-user/per-machine
; install: autostart is a per-login concern, not a per-install-scope one -
; only the installing user gets it, which is the least surprising default
; either way (a per-machine install shouldn't silently autostart the app
; for every other account on a shared PC).
;
; Name is the literal ASCII "Naru", not {#MyAppName} ("나루"): nobody
; browses their own Startup folder, so there's no display-text reason for
; this one shortcut to be Korean, and app/startup.py's LINK_NAME /
; update_manager/process_win.py's is_tray_autostart_registered() both do
; an exact-string filename check against it - keeping it ASCII avoids any
; Unicode-normalization/locale-codepage edge case in that comparison.
Name: "{userstartup}\Naru"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon
; Update manager's ongoing autostart. Filename/Parameters here is the
; exact same command line as the one-time [Run] launch below, and the
; one CloneUp's schtasks /TR uses - wscript.exe (GUI-subsystem, no
; console) running the hidden VBS (which itself launches the exe with
; window style 0) is what actually suppresses any window; neither a
; Scheduled Task nor a Startup shortcut being the launch vehicle changes
; that, so no schtasks/SYSTEM-task complexity is needed on either the
; admin or per-user install mode.
; Same ASCII-name reasoning as the shortcut above.
Name: "{userstartup}\Naru Update Check"; Filename: "{sys}\wscript.exe"; Parameters: "//B //Nologo ""{app}\Naru_update_manager_hidden.vbs"""; Tasks: autoupdatemanager

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
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,나루}"; Flags: nowait postinstall skipifsilent
; Start the update manager once now too - the Startup-folder shortcut
; above only fires on the *next* login, and hidden/skipifsilent/unchecked
; keeps this off any wizard summary page.
Filename: "{sys}\wscript.exe"; Parameters: "//B //Nologo ""{app}\Naru_update_manager_hidden.vbs"""; Description: "자동 업데이트 확인 시작"; Flags: nowait postinstall skipifsilent unchecked; Tasks: autoupdatemanager
; Scoped inbound allow for the pairing listener (tsbackup/pairing.py's
; PAIRING_PORT - keep these in sync) - restricted to Tailscale's own CGNAT
; range and this app's exe, never a blanket allow. Needs admin rights to
; manage firewall rules at all, so this only runs - and only eliminates
; the first-run Windows Firewall prompt - on a per-machine (admin) install,
; which is now the default (see PrivilegesRequired above). A per-user
; install (still available via the wizard dialog or /CURRENTUSER) still
; shows that prompt on first bind; that's an unavoidable Windows
; constraint, not a bug in this rule.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Naru Pairing"" dir=in action=allow protocol=TCP localport=8781 remoteip=100.64.0.0/10 program=""{app}\{#MyAppExeName}"" enable=yes"; Flags: runhidden; Check: IsAdminInstallMode
; Cleanup for an upgrade from a pre-rename (TsBackup-named) install: netsh's
; delete matches by rule name only, never by the program= path, so the old
; uninstaller's own [UninstallRun] delete step (which only ever runs on an
; explicit uninstall, never during this kind of in-place upgrade) would
; otherwise never remove it - "TsBackup Pairing" would linger forever,
; orphaned, pointing at a program path that no longer exists. Safe to run
; unconditionally: deleting a rule that isn't there is a silent no-op.
; Remove this line in a future release once no such installs remain.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""TsBackup Pairing"""; Flags: runhidden; Check: IsAdminInstallMode

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Naru Pairing"""; Flags: runhidden; Check: IsAdminInstallMode

[InstallDelete]
; Cleanup for an upgrade from a pre-rename (TsBackup-named) install.
; Inno's in-place-upgrade behaviour (same AppId as above) does not delete
; files/icons that existed in an old version's [Files]/[Icons] but are
; absent or renamed here - without this, a pre-rename install's old
; TsBackup.exe and its Startup-folder shortcuts would be left running
; alongside the new Naru.exe ones after an upgrade: two copies of the app
; and two update-manager loops competing for the same receiver port and
; scheduled-backup slot. Safe on a fresh install too - deleting a path
; that isn't there is a silent no-op. Remove this section in a future
; release once no pre-rename installs remain.
Type: files; Name: "{app}\TsBackup.exe"
Type: files; Name: "{app}\TsBackup_update_manager.exe"
Type: files; Name: "{app}\TsBackup_update_manager.bat"
Type: files; Name: "{app}\TsBackup_update_manager_hidden.vbs"
Type: files; Name: "{userstartup}\TsBackup.lnk"
Type: files; Name: "{userstartup}\TsBackup Update Check.lnk"

; Deliberately no [UninstallDelete]: uninstall only removes what's listed
; under [Files]/[Icons] above. The user's %LOCALAPPDATA%\Naru\
; config.json and log are left alone on purpose - config persists across
; reinstall/upgrade, matching how config.py already reasons about keeping
; install location and user data separate.
