' Lance BeFree sans fenêtre de console (Python doit être dans le PATH).
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run "pythonw main.py", 0, False
Set WshShell = Nothing
