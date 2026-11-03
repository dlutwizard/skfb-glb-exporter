@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if exist "D:\Anaconda\pythonw.exe" (
    start "" "D:\Anaconda\pythonw.exe" "%~dp0sketchfab_gui.py"
) else if exist "D:\Anaconda\python.exe" (
    "D:\Anaconda\python.exe" "%~dp0sketchfab_gui.py"
) else (
    pythonw "%~dp0sketchfab_gui.py" 2>nul || python "%~dp0sketchfab_gui.py"
)
