"""Four synchronized real-data plots; cursor is an original-sample inspection."""
import math

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from . import theme
from .adapters import channel_data, SampleIndex


class Plot(pg.PlotWidget):
    hovered = QtCore.Signal(float)
    left = QtCore.Signal()
    stepped = QtCore.Signal(int)
    restored = QtCore.Signal()

    def __init__(self, title, unit, height, parent=None):
        super().__init__(parent, background=theme.PANEL)
        self.setMinimumHeight(height)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing |
                            QtGui.QPainter.RenderHint.TextAntialiasing)
        self.setMenuEnabled(False)
        self.hideButtons()
        self.showGrid(x=True, y=True, alpha=.20)
        self.setLabel('left', unit, color=theme.MUTED, **{'font-size': '12px'})
        self.setLabel('bottom', '距离', units='km', color=theme.MUTED, **{'font-size': '12px'})
        for position in ('left', 'bottom'):
            axis = self.getAxis(position)
            axis.setTickFont(theme.font('axis'))
            axis.setPen(theme.BORDER)
            # AxisItem.setPen also rewrites its label color; text color must follow.
            axis.setTextPen(theme.MUTED)
            axis.setStyle(autoExpandTextSpace=False)
        self.getAxis('left').setWidth(78)
        self.getAxis('bottom').setHeight(32)
        self.setAccessibleName(title)
        self.view = self.getViewBox()
        self.scene().sigMouseMoved.connect(self.scene_mouse_move)
        self.view.setDefaultPadding(.01)
        self.view.setMouseEnabled(x=True, y=False)
        self.curves = []
        self.cursor = pg.InfiniteLine(angle=90, movable=False, pen=theme.curve_pen(self, theme.ACCENT, width=1))
        self.addItem(self.cursor, ignoreBounds=True)
        self.cursor.hide()
        self.region = pg.LinearRegionItem((0, 0), movable=False,
                                         brush=pg.mkBrush(96, 174, 255, 24),
                                         pen=theme.curve_pen(self, theme.ACCENT, width=1))
        self.addItem(self.region, ignoreBounds=True)
        self.region.hide()

    def add_curve(self, color, dashed=False):
        curve = pg.PlotDataItem(pen=theme.curve_pen(self, color, dashed), antialias=True,
                               skipFiniteCheck=False, autoDownsample=False)
        # Device-coordinate caches regenerate on transform/DPR changes. They cache
        # AA pixels at the actual viewport resolution, not a scaled low-res image.
        curve.curve.setCacheMode(QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.addItem(curve)
        self.curves.append((curve, color, dashed))
        return curve

    def refresh_pens(self):
        for curve, color, dashed in self.curves:
            curve.setPen(theme.curve_pen(self, color, dashed))
        self.cursor.setPen(theme.curve_pen(self, theme.ACCENT, width=1))
        for line in self.region.lines:
            line.setPen(theme.curve_pen(self, theme.ACCENT, width=1))

    def event(self, event):
        if event.type() in (QtCore.QEvent.Type.DevicePixelRatioChange, QtCore.QEvent.Type.ScreenChangeInternal):
            QtCore.QTimer.singleShot(0, self.refresh_pens)
        return super().event(event)

    def scene_mouse_move(self, position):
        if self.view.sceneBoundingRect().contains(position):
            self.hovered.emit(self.view.mapSceneToView(position).x() * 1000)
        else:
            self.left.emit()

    def leaveEvent(self, event):
        self.left.emit()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
            self.stepped.emit(-1 if event.key() == QtCore.Qt.Key.Key_Left else 1)
        elif event.key() == QtCore.Qt.Key.Key_Escape:
            self.left.emit()
        elif event.key() == QtCore.Qt.Key.Key_0 and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.restored.emit()
        else:
            return super().keyPressEvent(event)
        event.accept()


class ChartStack(QtWidgets.QScrollArea):
    inspected = QtCore.Signal(object, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        layout = theme.vertical(body, margins=0, spacing=10)
        self.plots = []
        self.trace = ()
        self.sample_index = SampleIndex(self.trace)
        self.distance = None
        self.model = None
        self.identity = None
        for title, unit, height in [('时间差 · 对比 − 基准', 's', 168),
                                    ('速度', 'km/h', 152), ('油门', '%', 152), ('刹车', '%', 152)]:
            plot = Plot(title, unit, height)
            plot.hovered.connect(self.inspect)
            plot.left.connect(self.clear_cursor)
            plot.stepped.connect(self.step)
            plot.restored.connect(self.restore)
            self.plots.append(plot)
            panel = theme.Panel()
            panel_layout = theme.vertical(panel, 10, 6)
            heading = QtWidgets.QHBoxLayout()
            heading.addWidget(theme.label(title, 'section'))
            heading.addStretch()
            legends = [('对比 − 基准', theme.AMBER, False)] if len(self.plots) == 1 else [
                ('基准', theme.BLUE, False), ('对比', theme.AMBER, True)]
            for text, color, dashed in legends:
                key = QtWidgets.QFrame()
                key.setFixedSize(18, 2)
                key.setStyleSheet(f'border-top: 2px {"dashed" if dashed else "solid"} {color};')
                heading.addWidget(key)
                legend = theme.label(text, 'caption')
                legend.setStyleSheet(f'color: {color}')
                heading.addWidget(legend)
            panel_layout.addLayout(heading)
            panel_layout.addWidget(plot)
            layout.addWidget(panel)
        for plot in self.plots[1:]:
            plot.setXLink(self.plots[0])
        self.items = [(self.plots[0], self.plots[0].add_curve(theme.AMBER), 'delta_ms')]
        for plot, name in zip(self.plots[1:], ('speed_kph', 'throttle', 'brake')):
            self.items.extend([(plot, plot.add_curve(theme.BLUE), 'reference_' + name),
                               (plot, plot.add_curve(theme.AMBER, True), 'comparison_' + name)])
        self.markers = []
        for plot, curve, field in self.items:
            marker = pg.ScatterPlotItem(size=6, pxMode=True, antialias=True,
                                        brush=pg.mkBrush(0, 0, 0, 0),
                                        pen=theme.curve_pen(plot, theme.TEXT, width=.8))
            # ScatterPlotItem owns its DPR-aware symbol atlas; size=6 is logical
            # viewport size. Do not double-multiply size by DPR.
            plot.addItem(marker, ignoreBounds=True)
            self.markers.append((plot, marker, field))
        layout.addStretch(1)
        self.setWidget(body)
        self.setMinimumWidth(330)

    def clear(self):
        self.model = None
        self.trace = ()
        self.sample_index = SampleIndex(self.trace)
        self.identity = None
        for plot, curve, field in self.items:
            curve.setData([], [])
            plot.region.hide()
        self.clear_cursor()

    def set_model(self, model, identity):
        preserve = identity == self.identity
        view = self.plots[0].view.viewRange()[0] if preserve else None
        self.model, self.trace, self.identity = model, model.trace, identity
        self.sample_index = SampleIndex(self.trace)
        for plot, curve, field in self.items:
            x, y, connect = channel_data(model.trace, field)
            curve.setData(x, y, connect=connect, antialias=True, skipFiniteCheck=False)
            plot.refresh_pens()
        for plot in self.plots[1:]:
            plot.enableAutoRange(axis='y')
        for plot in self.plots[2:]:
            plot.setYRange(0, 100, padding=.04)
        if view:
            self.plots[0].setXRange(*view, padding=0)
        else:
            self.restore()
        if self.distance is not None:
            self.inspect(self.distance)

    def restore(self):
        finite = [p.distance_m for p in self.trace if math.isfinite(p.distance_m)]
        if finite:
            self.plots[0].setXRange(min(finite) / 1000, max(finite) / 1000 or .001, padding=.01)
        self.plots[0].enableAutoRange(axis='y')
        for plot in self.plots:
            plot.region.hide()

    def focus_region(self, start, end):
        if end <= start:
            return
        self.plots[0].setXRange(start / 1000, end / 1000, padding=.06)
        for plot in self.plots:
            plot.region.setRegion((start / 1000, end / 1000))
            plot.region.show()

    def inspect(self, distance):
        self.distance = distance
        if self.sample_index.trace is not self.trace:
            self.sample_index = SampleIndex(self.trace)
        point = self.sample_index.nearest(distance)
        for plot in self.plots:
            if point is None:
                plot.cursor.hide()
            else:
                plot.cursor.setValue(point.distance_m / 1000)
                plot.cursor.show()
        self.inspected.emit(point, distance)
        for plot, marker, field in self.markers:
            value = getattr(point, field) if point else None
            if value is not None and math.isfinite(value):
                factor = .001 if field == 'delta_ms' else 100 if field.endswith(('throttle', 'brake')) else 1
                marker.setData([point.distance_m / 1000], [value * factor])
                marker.setPen(theme.curve_pen(plot, theme.TEXT, width=.8))
            else:
                marker.setData([], [])

    def clear_cursor(self):
        self.distance = None
        for plot in self.plots:
            plot.cursor.hide()
        for plot, marker, field in self.markers:
            marker.setData([], [])
        self.inspected.emit(None, float('nan'))

    def step(self, direction):
        lo, hi = self.plots[0].view.viewRange()[0]
        visible = [p for p in self.trace if math.isfinite(p.distance_m) and lo <= p.distance_m / 1000 <= hi]
        if not visible:
            return
        if self.distance is None:
            index = 0 if direction > 0 else len(visible) - 1
        else:
            index = min(range(len(visible)), key=lambda i: abs(visible[i].distance_m - self.distance))
            index = max(0, min(len(visible) - 1, index + direction))
        self.inspect(visible[index].distance_m)
