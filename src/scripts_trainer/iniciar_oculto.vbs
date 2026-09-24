Set shell = CreateObject("WScript.Shell")
script = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\servidor.py"

' pythonw.exe ejecuta el servidor sin ventana de consola.
shell.Run "pythonw.exe """ & script & """", 0, False
