"""Single logical-pixel font and drawing policy; Qt supplies screen scaling."""
from dataclasses import dataclass
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
from .branding import APP_NAME, app_icon

BG, PANEL, TEXT, MUTED = '#101216', '#1A1E24', '#F4F7FB', '#A4AFBD'
BORDER, BLUE, AMBER, GREEN = '#35404C', '#60AEFF', '#FFB454', '#55DF84'
ACCENT, CONTROL, SELECTED, SEPARATOR = BLUE, '#18242F', '#203B51', '#2C3946'
DANGER, DANGER_BORDER = '#351C23', '#A84C57'


@dataclass(frozen=True)
class TypeRole:
    size: int
    weight: QtGui.QFont.Weight
    families: tuple[str, ...]
    tabular: bool = False


UI_FAMILIES = ('Microsoft YaHei UI', 'Segoe UI')
LATIN_FAMILIES = ('Segoe UI', 'Microsoft YaHei UI')
MONO_FAMILIES = ('Consolas', 'Segoe UI', 'Microsoft YaHei UI')
TYPE = {
    'caption': TypeRole(12, QtGui.QFont.Weight.Normal, UI_FAMILIES),
    'axis': TypeRole(12, QtGui.QFont.Weight.Normal, LATIN_FAMILIES, True),
    'body': TypeRole(14, QtGui.QFont.Weight.Normal, UI_FAMILIES),
    'body_strong': TypeRole(14, QtGui.QFont.Weight.DemiBold, UI_FAMILIES),
    'summary': TypeRole(16, QtGui.QFont.Weight.Normal, UI_FAMILIES),
    'section': TypeRole(16, QtGui.QFont.Weight.DemiBold, UI_FAMILIES),
    'context': TypeRole(18, QtGui.QFont.Weight.DemiBold, UI_FAMILIES),
    'page_title': TypeRole(20, QtGui.QFont.Weight.DemiBold, UI_FAMILIES),
    'brand': TypeRole(20, QtGui.QFont.Weight.DemiBold, LATIN_FAMILIES),
    'lap_value': TypeRole(24, QtGui.QFont.Weight.DemiBold, LATIN_FAMILIES, True),
    'timing_compact': TypeRole(50, QtGui.QFont.Weight.DemiBold, LATIN_FAMILIES, True),
    'timing_display': TypeRole(68, QtGui.QFont.Weight.DemiBold, LATIN_FAMILIES, True),
    'log': TypeRole(12, QtGui.QFont.Weight.Normal, MONO_FAMILIES),
}


def font(role='body'):
    token = TYPE[role]
    result = QtGui.QFont()
    result.setFamilies(list(token.families))
    result.setPixelSize(token.size)
    result.setWeight(token.weight)
    result.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
    if token.tabular:
        result.setFeature(QtGui.QFont.Tag.fromString('tnum'), 1)
    return result


def curve_pen(plot, color, dashed=False, width=1.6):
    pen = pg.mkPen(color, width=width * plot.viewport().devicePixelRatioF(), cosmetic=True)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap if dashed else QtCore.Qt.PenCapStyle.RoundCap)
    if dashed:
        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
    return pen


def install(app):
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setFont(font('body'))
    pg.setConfigOptions(antialias=True, useOpenGL=False)
    # Bounded per-process raster cache. Data, transforms and DPR invalidate Qt's
    # device-coordinate entries; the cursor is deliberately not cached.
    QtGui.QPixmapCache.setCacheLimit(32 * 1024)
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(BG))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(PANEL))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(SELECTED))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(TEXT))
    app.setPalette(palette)
    app.setStyle('Fusion')
    app.setStyleSheet(f'''
    QWidget {{ color: {TEXT}; }}
    QMainWindow, QDialog {{ background: {BG}; }}
    QLabel[muted="true"] {{ color: {MUTED}; }}
    QPushButton, QToolButton {{ background: {CONTROL}; border: 1px solid {BORDER};
        border-radius: 5px; padding: 7px 12px; min-height: 20px; }}
    QPushButton:hover, QToolButton:hover {{ background: #223341; border-color: #526778; }}
    QPushButton:pressed, QToolButton:pressed {{ background: #14212C; border-color: {BLUE}; }}
    QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus,
    QTableView:focus, QListView:focus {{ border-color: {BLUE}; }}
    QPushButton:checked {{ background: {SELECTED}; border-color: {BLUE}; }}
    QPushButton[primary="true"] {{ background: {DANGER}; border-color: {DANGER_BORDER}; }}
    QPushButton[primary="true"]:hover {{ background: #50252F; }}
    QPushButton[primary="true"]:pressed {{ background: #29151C; }}
    QPushButton[primary="true"]:focus {{ border-color: {BLUE}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: #76808D; background: #1A1E24; }}
    QComboBox, QLineEdit, QSpinBox {{ background: {CONTROL}; border: 1px solid {BORDER};
        border-radius: 4px; padding: 6px; min-height: 20px; }}
    QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {SELECTED}; }}
    QTableView, QListView, QTextEdit {{ background: {PANEL}; alternate-background-color: #1D232B; border: 1px solid {BORDER};
        border-radius: 5px; gridline-color: {SEPARATOR}; }}
    QHeaderView::section {{ background: #202A35; padding: 7px; border: 0; border-bottom: 1px solid {BORDER}; }}
    QTableView::item {{ padding: 5px; }}
    QTableView::item:selected, QListView::item:selected {{ background: {SELECTED}; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QScrollBar:vertical {{ background: #151920; width: 12px; }}
    QScrollBar::handle:vertical {{ background: #45515F; min-height: 30px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: #647487; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; }}
    QTabBar::tab {{ background: {CONTROL}; padding: 9px 14px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:hover {{ background: #223341; }}
    QTabBar::tab:selected {{ background: {SELECTED}; border-bottom-color: {BLUE}; }}
    QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
    QMenu::item:selected {{ background: {SELECTED}; }}
    QToolTip {{ background: #263240; color: {TEXT}; border: 1px solid {BLUE}; }}
    ''')


class Panel(QtWidgets.QWidget):
    """AA panel background has one owner, rather than overlapping QSS fills."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.save()
        painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing |
                               QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        painter.setBrush(QtGui.QColor(PANEL))
        painter.setPen(QtGui.QPen(QtGui.QColor(BORDER), 1.0))
        painter.drawRoundedRect(rect, 5., 5.)
        painter.restore()


def apply_type(widget, role):
    widget.setFont(font(role))
    widget.setProperty('typeRole', role)
    return widget


def label(text='', role='body', muted=False):
    widget = QtWidgets.QLabel(text)
    widget.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    apply_type(widget, role)
    widget.setProperty('muted', muted)
    return widget


def vertical(widget, margins=12, spacing=10):
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return layout
