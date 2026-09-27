' TsBackup update manager - hidden launcher for the Startup-folder shortcut
' and the one-time post-install [Run] line.
' WScript.Shell Run window-style 0 = fully hidden (no black console flash).
Option Explicit

Dim sh, fso, dir, exe, args, i, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
exe = dir & "\TsBackup_update_manager.exe"
' Do not fall back to .bat here - bat re-enters this VBS (loop risk).

args = ""
If WScript.Arguments.Count > 0 Then
  For i = 0 To WScript.Arguments.Count - 1
    args = args & " " & QuoteArg(WScript.Arguments(i))
  Next
End If

If fso.FileExists(exe) Then
  cmd = QuoteArg(exe) & args
  sh.Run cmd, 0, False
Else
  WScript.Quit 1
End If

Function QuoteArg(s)
  QuoteArg = """" & Replace(CStr(s), """", """""") & """"
End Function
