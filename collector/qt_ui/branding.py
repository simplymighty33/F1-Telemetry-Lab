"""Display identity only; recording paths and protocol identities are unchanged."""
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from collector import APP_NAME
EXE_NAME = 'RaceTraceLab'
ICON_PATH = Path(__file__).with_name('assets') / 'app.ico'


def app_icon():
    return QtGui.QIcon(str(ICON_PATH))


class BrandMark(QtWidgets.QWidget):
    """Paint the approved multiresolution icon at the current device scale."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setAccessibleName(APP_NAME)
        self.icon = app_icon()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        self.icon.paint(painter, self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter)
