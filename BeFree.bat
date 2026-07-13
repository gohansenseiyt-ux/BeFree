@echo off
rem Lance BeFree sans fenetre de console.
rem Necessite Python dans le PATH (https://www.python.org/downloads/).
cd /d "%~dp0"
start "" pythonw main.py
