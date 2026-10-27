# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for BYTT Sonar Viewer
# Build from the project root on Windows:
#   pip install pyinstaller
#   pyinstaller bytt.spec
#
# Output: dist/BYTT_Sonar_Viewer/BYTT_Sonar_Viewer.exe  (one-folder)
#      or dist/BYTT_Sonar_Viewer.exe                     (one-file, see below)

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# ── Collect PySide6 data / plugins Qt needs at runtime ────────────────────────
pyside6_datas   = collect_data_files('PySide6')
pyqtgraph_datas = collect_data_files('pyqtgraph')

block_cipher = None

a = Analysis(
    # Entry-point script
    ['bytt/app.py'],

    pathex=['.'],

    # Runtime dependencies PyInstaller cannot auto-detect via import scanning
    binaries=collect_dynamic_libs('PySide6'),

    datas=[
        # PySide6 Qt plugins (platforms, imageformats, styles, …)
        *pyside6_datas,
        # pyqtgraph colour maps and other data assets
        *pyqtgraph_datas,
        # Ship the default config example so first-run works without a home dir
        ('bsf_viewer_config.json', '.'),
    ],

    # Hidden imports that PyInstaller's static analysis misses
    hiddenimports=[
        # PySide6 sub-modules loaded at runtime via Qt plugin system
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtPrintSupport',
        # pyqtgraph internals
        'pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyqt6',
        'pyqtgraph.graphicsItems.PlotItem.plotConfigTemplate_pyqt6',
        'pyqtgraph.imageview.ImageViewTemplate_pyqt6',
        # opencv (cv2) is a compiled extension — name the exact import
        'cv2',
        # numpy internals that PyInstaller sometimes misses
        'numpy.core._methods',
        'numpy.lib.format',
        # All bytt sub-packages (ensures they're walked even if not imported at top level)
        'bytt',
        'bytt.app',
        'bytt.config',
        'bytt.bsf.file_io',
        'bytt.gui.main_window',
        'bytt.gui.controls_panel',
        'bytt.gui.waterfall_view',
        'bytt.gui.gps_panel',
        'bytt.gui.sonar_control_panel',
        'bytt.gui.connection_indicators',
        'bytt.nav.gps_track',
        'bytt.net.live_client',
        'bytt.net.playback_source',
        'bytt.net.command_client',
        'bytt.net.recorder',
        'bytt.processing.enhancement',
        'bytt.processing.colormap',
        'bytt.processing.nadir_detection',
        'bytt.protocol.packets',
        'bytt.protocol.commands',
        'bytt.protocol.constants',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],

    # Modules to exclude to keep the bundle smaller
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'IPython',
        'jupyter',
        'PIL',          # Pillow — not used
        'wx',
        'PyQt5',
        'PyQt6',
        'test',
        'unittest',
        'pytest',
    ],

    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── ONE-FOLDER build (recommended — faster startup, easier to debug) ───────────
exe = EXE(
    pyz,
    a.scripts,
    [],                         # no scripts merged into exe for one-folder mode
    exclude_binaries=True,      # True = one-folder, False = one-file
    name='BYTT_Sonar_Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                   # compress with UPX if installed; safe to set False
    console=False,              # False = no black terminal window on launch
    # icon='assets/bytt.ico',   # uncomment and supply a .ico file to set the exe icon
    # version='assets/version_info.txt',  # uncomment for Windows version metadata
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BYTT_Sonar_Viewer',   # creates dist/BYTT_Sonar_Viewer/ folder
)

# ── ONE-FILE build alternative ─────────────────────────────────────────────────
# Replace the EXE + COLLECT block above with this to get a single .exe.
# Startup is slower (it unpacks to %TEMP% every run) but easier to distribute.
#
# exe = EXE(
#     pyz,
#     a.scripts,
#     a.binaries,
#     a.zipfiles,
#     a.datas,
#     [],
#     name='BYTT_Sonar_Viewer',
#     debug=False,
#     bootloader_ignore_signals=False,
#     strip=False,
#     upx=True,
#     upx_exclude=[],
#     runtime_tmpdir=None,
#     console=False,
#     icon='assets/bytt.ico',
# )
