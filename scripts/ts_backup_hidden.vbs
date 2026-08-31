' =========================================================================
'  ts_backup_hidden.vbs
'  Launches ts_backup.bat with a hidden window so no console flashes on the
'  desktop when the scheduled task fires while the user is logged on.
'
'  ASCII ONLY.
' =========================================================================

Option Explicit

Dim shell, fso, scriptDir, batPath

Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' Resolve the .bat next to this .vbs, so the pair can live in any folder.
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath   = fso.BuildPath(scriptDir, "ts_backup.bat")

If Not fso.FileExists(batPath) Then
    ' Nothing to run. Exit non-zero so the task's Last Result shows the problem.
    WScript.Quit 2
End If

' 0 = hidden window, True = wait for the batch to finish so the scheduled
' task's Last Result reflects the real outcome instead of always being 0.
WScript.Quit shell.Run("""" & batPath & """", 0, True)
