"""Read-only Qt models, with selection identity independent of row position."""
from PySide6 import QtCore, QtGui, QtWidgets
from decoder.display import tyre_label
from .adapters import lap_time
from .theme import BLUE, AMBER, BORDER, TEXT, MUTED, PANEL, SELECTED, font, apply_type


class TableModel(QtCore.QAbstractTableModel):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.headers, self.rows = headers, []

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            return str(self.rows[index.row()][index.column()])

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if role == QtCore.Qt.ItemDataRole.DisplayRole and orientation == QtCore.Qt.Orientation.Horizontal:
            return self.headers[section]

    def replace(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()


class TableHeader(QtWidgets.QHeaderView):
    """Keep the header role through Qt parent-font/stylesheet propagation."""
    def event(self, event):
        result = super().event(event)
        if event.type() in (QtCore.QEvent.Type.FontChange, QtCore.QEvent.Type.StyleChange,
                            QtCore.QEvent.Type.Polish, QtCore.QEvent.Type.ParentChange):
            role = self.property('typeRole')
            if role and self.font() != font(role):
                apply_type(self, role)
        return result


class Table(QtWidgets.QTableView):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.rows_model = TableModel(headers, self)
        self.setModel(self.rows_model)
        self.setHorizontalHeader(TableHeader(QtCore.Qt.Orientation.Horizontal, self))
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        apply_type(self.horizontalHeader(), 'body_strong')
        self.setAlternatingRowColors(True)

    def replace(self, rows, key_column=0, follow_newest=False):
        rows = list(rows)
        current = self.currentIndex()
        identity = (self.rows_model.rows[current.row()][key_column]
                    if current.isValid() and current.row() < len(self.rows_model.rows) else None)
        scroll = self.verticalScrollBar().value()
        was_top = scroll == 0
        self.rows_model.replace(rows)
        if identity is not None:
            for i, row in enumerate(rows):
                if row[key_column] == identity:
                    self.selectRow(i)
                    break
        self.verticalScrollBar().setValue(0 if follow_newest and was_top else scroll)


class LapModel(QtCore.QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.laps = []
        self.reference = self.comparison = None

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.laps)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        lap = self.laps[index.row()]
        if role == QtCore.Qt.ItemDataRole.UserRole:
            return lap
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            return f'第{lap.lap_number}圈 · {tyre_label(lap.tyre)} · {lap_time(lap.lap_time_ms)}'

    def replace(self, laps, reference, comparison):
        self.beginResetModel()
        self.laps = list(reversed(laps))
        self.reference, self.comparison = reference, comparison
        self.endResetModel()


class LapDelegate(QtWidgets.QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QtCore.QSize(160, 86)

    def paint(self, painter, option, index):
        lap = index.data(QtCore.Qt.ItemDataRole.UserRole)
        model = index.model()
        painter.save()
        painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing |
                               QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = QtCore.QRectF(option.rect).adjusted(3., 3., -3., -3.)
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        focused = bool(option.state & QtWidgets.QStyle.StateFlag.State_HasFocus)
        painter.setBrush(QtGui.QColor(SELECTED if selected else PANEL))
        painter.setPen(QtGui.QPen(QtGui.QColor(BLUE if focused else BORDER), 1.0))
        painter.drawRoundedRect(rect, 5., 5.)
        flags = []
        if lap.lap_number == model.reference:
            flags.append('基准')
        if lap.lap_number == model.comparison:
            flags.append('对比')
        painter.setPen(QtGui.QColor(TEXT))
        painter.setFont(font('body_strong'))
        title = f'第{lap.lap_number}圈  {" / ".join(flags)}'
        title = QtGui.QFontMetricsF(painter.font()).elidedText(
            title, QtCore.Qt.TextElideMode.ElideRight, rect.width() - 20)
        painter.drawText(rect.adjusted(10., 5., -8., -5.), QtCore.Qt.AlignmentFlag.AlignTop,
                         title)
        painter.setFont(font('lap_value'))
        painter.setPen(QtGui.QColor(AMBER if lap.lap_number == model.comparison else BLUE))
        painter.drawText(rect.adjusted(10., 27., -8., -5.), QtCore.Qt.AlignmentFlag.AlignTop, lap_time(lap.lap_time_ms))
        painter.setFont(font('caption'))
        painter.setPen(QtGui.QColor(MUTED))
        wear = tyre_label(lap.tyre)
        text = QtGui.QFontMetricsF(painter.font()).elidedText(wear, QtCore.Qt.TextElideMode.ElideRight, rect.width() - 20)
        painter.drawText(rect.adjusted(10., 59., -8., -4.), QtCore.Qt.AlignmentFlag.AlignTop, text)
        painter.restore()
