"""Collect Qt GUI without optional PDF / GPL-only VirtualKeyboard plugins."""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# Filtering BEFORE bindepend's transitive dependency scan prevents unused
# plugins from pulling their QML / PDF / GPL-only module trees into the app.
binaries = [(source, destination) for source, destination in binaries
            if not source.lower().endswith(('qtvirtualkeyboardplugin.dll', 'qpdf.dll'))]
