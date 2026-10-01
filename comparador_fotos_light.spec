# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs
import os
import sys
from pathlib import Path

block_cipher = None

# Coleta dados para Pillow, decodificador HEIC, certificados e utilitários do pip
datas = [('icon.ico', '.'), ('icon.png', '.')]
datas += collect_data_files('pillow_heif')
datas += collect_data_files('pip')
datas += collect_data_files('certifi')

# Submódulos essenciais do Comparador e do Pip para download dinâmico
hiddenimports = [
    'src',
    'src.config',
    'src.hardware',
    'src.scanner',
    'src.hasher',
    'src.embeddings',
    'src.clustering',
    'src.web_photo_detector',
    'src.shadow_remover',
    'src.auto_orienter',
    'src.ignored_pairs',
    'src.dependency_installer',
    'src.updater',
    'src.photo_engine',
    'src.ui',
    'src.ui.styles',
    'src.ui.menus',
    'src.ui.actions',
    'src.ui.main_window',
    'src.ui.components.photo_card',
    'src.ui.views.grid_view',
    'src.ui.views.fullscreen_view',
    'src.ui.dialogs.timed_confirm_dialog',
    'src.ui.dialogs.folder_rules_dialog',
    'src.ui.dialogs.folder_selector_dialog',
    'src.ui.dialogs.excluded_folders_dialog',
    'src.ui.dialogs.deletion_progress_dialog',
    'src.ui.dialogs.performance_dialog',
    'src.ui.dialogs.before_after_compare_dialog',
    'src.ui.dialogs.update_dialog',
    'pip',
    'pip._internal',
    'pip._internal.cli.main',
]

hiddenimports += collect_submodules('imagehash')
hiddenimports += collect_submodules('PIL')
hiddenimports += collect_submodules('pillow_heif')
hiddenimports += collect_submodules('send2trash')
hiddenimports += collect_submodules('requests')
hiddenimports += collect_submodules('numpy')
hiddenimports += collect_submodules('urllib3')
hiddenimports += collect_submodules('pip')

binaries = []
binaries += collect_dynamic_libs('pillow_heif')

a = Analysis(
    ['comparador_fotos.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'scipy', 'timm', 'open_clip',
        'matplotlib', 'pandas', 'playwright', 'pytest', 'IPython', 'jupyter'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ComparadorFotos_Launcher',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
