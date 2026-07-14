' Lance BeFree sans fenêtre de console (Python doit être dans le PATH).
' Le lanceur verifie/installe les dependances manquantes automatiquement.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run "pythonw launcher.pyw", 0, False
Set WshShell = Nothing
