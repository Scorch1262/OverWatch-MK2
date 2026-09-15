# -*- mode: python ; coding: utf-8 -*-
# OverWatchMK2 – PyInstaller Build Spec
#
# Baut je nach Plattform, auf der PyInstaller ausgeführt wird:
#   Windows -> OverWatchMK2.exe (onefile)
#   macOS   -> OverWatchMK2.app (Bundle) UND ein onefile-Unix-Binary
#              als Nebenprodukt (wird von der macOS-BUNDLE()-Stufe
#              unten mit eingepackt)
#   Linux   -> einfaches onefile-Binary (Entwicklung/Test)
#
# WICHTIG: config.yaml wird ABSICHTLICH NICHT in die Datenbank
# gebündelt/mitgegeben -- main.py schreibt/liest sie ausschließlich
# NEBEN der exe/.app (siehe external_path() in main.py). Die
# config.yaml im Projektordner dient nur als Vorlage, die das
# GitHub-Actions-Workflow separat neben die fertige exe/.app in das
# Release-Zip kopiert (siehe .github/workflows/build.yml).

import sys
from pathlib import Path

block_cipher = None
HERE = Path(SPECPATH).resolve()

_required = {
    'templates': HERE / 'templates',
    'templates/index.html': HERE / 'templates' / 'index.html',
}
for name, path in _required.items():
    if not path.exists():
        raise FileNotFoundError(
            f"\n\n  FEHLER: '{name}' nicht gefunden: {path}\n"
            f"  Stelle sicher dass alle Dateien im Repository eingecheckt sind.\n"
        )

datas = [(str(HERE / 'templates'), 'templates')]
_static = HERE / 'static'
if _static.exists() and any(_static.iterdir()):
    datas.append((str(_static), 'static'))

hiddenimports = [
    'flask', 'flask.templating', 'flask.json', 'flask.helpers',
    'flask_socketio', 'socketio', 'socketio.async_drivers',
    'engineio', 'engineio.async_drivers', 'engineio.async_drivers.threading',
    'werkzeug', 'werkzeug.serving', 'werkzeug.routing',
    'werkzeug.middleware', 'werkzeug.middleware.proxy_fix',
    'jinja2', 'jinja2.ext', 'markupsafe',
    'requests', 'requests.adapters', 'requests.packages',
    'urllib3', 'urllib3.util', 'urllib3.util.retry',
    'urllib3.contrib', 'urllib3.packages',
    'certifi', 'charset_normalizer', 'idna',
    'email', 'email.mime', 'email.mime.text', 'email.mime.multipart',
    'email.mime.base', 'email.encoders', 'email.utils',
    'email.header', 'email.message', 'email.generator',
    'yaml',
    'logging', 'logging.handlers', 'socket', 'struct', 'select',
    'threading', 'queue', 'platform', 'subprocess', 'argparse',
    'datetime', 'collections', 'json', 'traceback', 'sqlite3',
    'click', 'simple_websocket', 'wsproto', 'h11', 'bidict',
    # ── Satelliten-Bahnberechnung (sgp4, Starlink-Tracker) ────────
    'sgp4', 'sgp4.api', 'sgp4.model', 'sgp4.propagation',
    'sgp4.conveniences', 'sgp4.functions', 'sgp4.exporter', 'sgp4.io',
    # ── SMS-Ortung (pyserial) ──────────────────────────────────────
    'serial', 'serial.tools', 'serial.tools.list_ports',
]

a = Analysis(
    [str(HERE / 'main.py')],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas',
        'PIL', 'cv2', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'gtk', 'test', 'unittest', 'xmlrpc',
        'ftplib', 'imaplib', 'poplib', 'smtplib', 'telnetlib',
        'distutils', 'setuptools',
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
    name='OverWatchMK2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# WICHTIG (seit 1.0.3, siehe CHANGELOG.md): Auf macOS wird HIER bewusst
# KEIN automatisches BUNDLE()-.app mehr erzeugt. Ein von PyInstallers
# BUNDLE() gebautes .app startet beim Finder-Doppelklick OHNE jedes
# sichtbare Fenster/Terminal (macOS hängt GUI-gestarteten Prozessen
# grundsätzlich kein Terminal an) -- das Programm lief dann zwar
# korrekt im Hintergrund, ließ sich aber nur über die Aktivitätsanzeige
# wieder beenden (Nutzeranforderung: "mit einem Terminal laufen, damit
# man es einfacher wieder beenden kann"). Der GitHub-Actions-Workflow
# (.github/workflows/build.yml, Job build-macos) baut das .app-Bundle
# deshalb selbst, um ein kleines Start-Skript als CFBundleExecutable
# einzusetzen, das beim Doppelklick ein sichtbares Terminal-Fenster
# öffnet und darin DIESES hier erzeugte onefile-Binary ausführt --
# Strg+C oder Schließen des Fensters beendet das Programm dann wie
# gewohnt. Unter Windows/Linux ist 'exe' oben bereits das fertige
# Endprodukt, keine weitere Verpackung nötig.
