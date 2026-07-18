# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('fonts', 'fonts'), ('icons', 'icons'), ('image', 'image')]
binaries = []
hiddenimports = ['keyring.backends.Windows']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BeFree',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icons\\befree.ico'],
)

# --onedir : les fichiers restent dans un dossier installé plutôt que d'être
# ré-extraits dans %TEMP% à chaque lancement — évite qu'un antivirus supprime
# un fichier extrait (ex. themes/blue.json de customtkinter) entre l'extraction
# et sa lecture, cause du crash "FileNotFoundError ... blue.json" en onefile.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BeFree',
)
