' =========================================================================
'  ts_receive_hidden.vbs
'  Runs ts_receive.ps1 with a hidden window so nothing flashes on the
'  desktop when the scheduled task fires while the user is logged on.
'
'  -ExecutionPolicy Bypass is passed on the command line: it applies to
'  this one process only, needs no administrator rights, and does not
'  change the machine's policy.
'
'  ASCII ONLY.
' =========================================================================

Option Explicit

Dim shell, fso, scriptDir, ps1Path, command

Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' Resolve the .ps1 next to this .vbs, so the pair can live in any folder.
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path   = fso.BuildPath(scriptDir, "ts_receive.ps1")

If Not fso.FileExists(ps1Path) Then
    ' Exit non-zero so the task's Last Result shows the problem.
    WScript.Quit 2
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1Path & """"

' 0 = hidden window, True = wait, so the script's exit code reaches the
' scheduler instead of always being 0.
WScript.Quit shell.Run(command, 0, True)
