"""Build the real Qt application with process-local native DLL search isolation."""
import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join((str(Path(sys.executable).parent), str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']))
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(out / 'cache')
    versions = {name: metadata.version(name) for name in ('PySide6', 'pyqtgraph', 'numpy', 'pyinstaller')}
    (out / 'build-environment.json').write_text(json.dumps({'python': sys.version, 'versions': versions,
                        'dll_search': os.environ['PATH'].split(os.pathsep), 'scope': 'builder process only'}, indent=2), encoding='utf-8')
    try:
        from collector.qt_ui.branding import EXE_NAME, ICON_PATH
        from PyInstaller.__main__ import run
        arguments = ['--noconfirm', '--onedir', '--windowed', '--noupx', '--name', EXE_NAME,
                     '--icon', str(ICON_PATH),
                     '--add-data', str(ICON_PATH) + os.pathsep + 'collector/qt_ui/assets',
                     '--manifest', str(root / 'packaging' / 'qt_app.manifest'),
                     '--version-file', str(root / 'packaging' / 'windows_version_info.txt'),
                     '--distpath', str(out / 'dist'), '--workpath', str(out / 'build'), '--specpath', str(out),
                     '--paths', str(root), '--additional-hooks-dir', str(root / 'packaging' / 'hooks')]
        # No Tk loop, web browser, 3D/Graphs GPL modules, or other Qt bindings.
        for module in ('tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6.QtWebEngineCore',
                       'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'PySide6.QtQml',
                       'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtGraphs', 'PySide6.Qt3DCore',
                       'pyqtgraph.opengl', 'OpenGL', 'matplotlib', 'scipy', 'pandas', 'PIL', 'psutil', 'pytest', 'yaml'):
            arguments += ['--exclude-module', module]
        arguments.append(str(root / 'packaging' / 'qt_entry.py'))
        run(arguments)
        destination = out / 'dist' / EXE_NAME
        for name in ('README.md', 'docs/qt-command-center-guide.md', 'docs/qt-third-party-notices.md'):
            shutil.copy2(root / name, destination / Path(name).name)
        licenses = destination / 'licenses'
        upstream = root / 'packaging' / 'qt-license-texts'
        if upstream.is_dir():
            shutil.copytree(upstream, licenses / 'Qt-upstream', dirs_exist_ok=True)
        python_license = Path(sys.base_prefix) / 'LICENSE.txt'
        if python_license.is_file():
            (licenses / 'Python').mkdir(parents=True, exist_ok=True)
            shutil.copy2(python_license, licenses / 'Python' / 'LICENSE.txt')
        license_manifest = []
        for package in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'numpy', 'pyqtgraph', 'pyinstaller'):
            distribution = metadata.distribution(package)
            for file in distribution.files or ():
                if ('licenses' in file.parts or file.name.upper().startswith(('LICENSE', 'COPYING'))):
                    source = distribution.locate_file(file)
                    if source.is_file():
                        target = licenses / package / str(file).replace('/', '_').replace('\\', '_')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                        license_manifest.append({'package': package, 'version': distribution.version, 'file': str(target.relative_to(destination))})
        (destination / 'license-inventory.json').write_text(json.dumps(license_manifest, indent=2), encoding='utf-8')
        binaries = [p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file() and p.suffix.lower() in ('.dll', '.pyd')]
        forbidden = [name for name in binaries if any(token in name.lower() for token in ('virtualkeyboard', 'qt6graphs', 'webengine'))]
        if forbidden:
            raise RuntimeError(f'Unexpected optional/GPL-only module payload: {forbidden}')
        (destination / 'native-library-inventory.json').write_text(json.dumps(binaries, indent=2), encoding='utf-8')
    finally:
        os.environ['PATH'] = previous


if __name__ == '__main__':
    main()
