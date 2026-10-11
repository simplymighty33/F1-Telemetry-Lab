"""Real business views. No example telemetry, fabricated map or derived scoring."""
import json
import math

from PySide6 import QtCore, QtWidgets
from decoder.display import lap_display, session_label, tyre_label
from decoder.tyres import tyre_name

from . import theme
from .adapters import lap_time, number, QUALITY_LABELS
from .charts import ChartStack
from .tables import Table, LapModel, LapDelegate


def button(text, callback=None):
    result = QtWidgets.QPushButton(text)
    theme.apply_type(result, 'body_strong')
    if callback:
        result.clicked.connect(callback)
    return result


def combo_entries(combo, entries, selected=None):
    blocker = QtCore.QSignalBlocker(combo)
    combo.clear()
    for text, value in entries:
        combo.addItem(text, value)
        combo.setItemData(combo.count() - 1, text, QtCore.Qt.ItemDataRole.ToolTipRole)
    index = combo.findData(selected)
    if index >= 0:
        combo.setCurrentIndex(index)
    del blocker


class TimingCard(theme.Panel):
    def __init__(self, title, color):
        super().__init__()
        layout = theme.vertical(self, 14, 5)
        self.title = theme.label(title, 'body_strong')
        self.value = theme.label('—', 'timing_compact')
        self.value.setStyleSheet(f'color: {color}')
        self.detail = theme.label('尚未选择圈次', 'caption', muted=True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addStretch()
        self.setMinimumWidth(140)

    def compact(self, small):
        self.setFixedHeight(118 if small else 145)
        theme.apply_type(self.value, 'timing_compact' if small else 'timing_display')


class CapturePage(QtWidgets.QWidget):
    connect_requested = QtCore.Signal()
    analysis_requested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        layout = theme.vertical(self, 0, 10)
        panel = theme.Panel()
        body = theme.vertical(panel, 16, 8)
        self.page_title = theme.label('实时采集', 'page_title')
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.page_title, 1)
        actions.addWidget(button('连接与存储设置', self.connect_requested.emit))
        actions.addWidget(button('分析当前录制', self.analysis_requested.emit))
        body.addLayout(actions)
        self.context = theme.label('等待可信游戏会话', 'context')
        self.context.setWordWrap(True)
        body.addWidget(self.context)
        self.raw_status = theme.label('Raw：未启动')
        self.analysis_status = theme.label('分析：未启动', muted=True)
        self.raw_status.setWordWrap(True)
        self.analysis_status.setWordWrap(True)
        self.warning = theme.label('', muted=True)
        self.warning.setWordWrap(True)
        self.warning.hide()
        statuses = QtWidgets.QHBoxLayout()
        statuses.setSpacing(16)
        statuses.addWidget(self.raw_status, 1)
        statuses.addWidget(self.analysis_status, 1)
        body.addLayout(statuses)
        body.addWidget(self.warning)
        layout.addWidget(panel)
        self.heading = theme.label('当前会话 · 逐圈记录（最新在上）', 'section')
        layout.addWidget(self.heading)
        self.laps = Table(['圈号', '轮胎 · 磨损', '圈速', 'S1', 'S2', 'S3', '有效性'])
        layout.addWidget(self.laps, 1)
        self.path = theme.label('尚未创建录制档案', 'caption', muted=True)
        self.path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path)
        self.context_key = None

    def update_snapshot(self, snapshot):
        key = snapshot.session_uid, snapshot.player_car_index, snapshot.packet_format
        if key != self.context_key:
            self.context_key = key
            self.laps.replace([])
        context = session_label(snapshot.track_name, snapshot.session_type)
        self.context.setText(f'{snapshot.game_name} · {context} · {snapshot.game_mode}')
        self.heading.setText(f'{context} · 逐圈记录（最新在上）')
        self.raw_status.setText(f'Raw · {snapshot.message}' +
                               (f'\n写入错误：{snapshot.error}' if snapshot.error else ''))
        self.raw_status.setWordWrap(True)
        self.analysis_status.setText(f'基础数据：{snapshot.foundation_state} ｜ 分析：{snapshot.analysis_state}')
        issues = [*snapshot.health_warnings, snapshot.foundation_error, snapshot.analysis_error, snapshot.display_error]
        if snapshot.foundation_decode_errors:
            issues.append(f'有 {snapshot.foundation_decode_errors:,} 个数据包未能解析；这不等于 Raw 丢失。'
                          '请保留 Raw，停止录制后升级并独立重建。')
        self.warning.setText('\n'.join(str(v) for v in issues if v))
        self.warning.setVisible(bool(self.warning.text()))
        path = str(snapshot.session_directory) if snapshot.session_directory else '等待首个数据包创建档案'
        self.path.setText('录制档案：' + (path if len(path) < 90 else '…' + path[-86:]))
        self.path.setToolTip(path)

    def update_laps(self, update):
        if self.context_key is None or update.session_uid != self.context_key[0]:
            return
        valid = [lap for lap in update.laps if lap.lap_valid and lap.lap_time_ms > 0]
        best = min((lap.lap_time_ms for lap in valid), default=None)
        self.laps.replace([(f'第{lap.lap_number}圈', tyre_label(lap.tyre), lap_time(lap.lap_time_ms),
                            lap_time(lap.sector1_ms), lap_time(lap.sector2_ms), lap_time(lap.sector3_ms),
                            '有效 · 会话最快' if lap.lap_valid and lap.lap_time_ms == best else '有效' if lap.lap_valid else '无效')
                           for lap in reversed(update.laps)], follow_newest=True)


class AnalysisPage(QtWidgets.QWidget):
    scope_changed = QtCore.Signal(object, object)
    pair_changed = QtCore.Signal(object, object)
    practice_requested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.model = None
        self.quality = ()
        self.regions = ()
        self.lap_list = QtWidgets.QListView()
        self.lap_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lap_list.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.lap_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lap_model = LapModel(self.lap_list)
        self.lap_list.setModel(self.lap_model)
        self.lap_list.setItemDelegate(LapDelegate(self.lap_list))
        self.lap_list.selectionModel().currentChanged.connect(self.choose_list_lap)
        self.lap_list.setToolTip('上下键选择对比圈；基准圈通过上方选择器设置')
        layout = theme.vertical(self, 0, 10)
        self.scope = theme.label('单圈分析 · 未选择来源', 'page_title')
        self.scope.setWordWrap(True)
        header = theme.Panel()
        header_layout = theme.vertical(header, 12, 8)
        header_layout.addWidget(self.scope)
        controls = QtWidgets.QHBoxLayout()
        self.session = QtWidgets.QComboBox()
        self.session.setMinimumWidth(220)
        self.segment = QtWidgets.QComboBox()
        self.segment.setMinimumWidth(150)
        self.session.currentIndexChanged.connect(self.change_session)
        self.segment.currentIndexChanged.connect(self.change_segment)
        controls.addWidget(theme.label('会话'))
        controls.addWidget(self.session, 3)
        controls.addWidget(theme.label('驾驶段'))
        controls.addWidget(self.segment, 2)
        controls.addWidget(button('练习报告', self.practice_requested.emit))
        self.details_button = button('区间 / 质量面板', self.toggle_details)
        controls.addWidget(self.details_button)
        header_layout.addLayout(controls)
        pairs = QtWidgets.QHBoxLayout()
        self.reference = QtWidgets.QComboBox()
        self.comparison = QtWidgets.QComboBox()
        for name, combo in [('基准', self.reference), ('对比', self.comparison)]:
            pairs.addWidget(theme.label(name))
            pairs.addWidget(combo, 1)
            combo.setMinimumWidth(140)
            combo.currentIndexChanged.connect(self.change_pair)
        header_layout.addLayout(pairs)
        layout.addWidget(header)
        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(14)
        central = QtWidgets.QWidget()
        self.center_column = central
        center = theme.vertical(central, 0, 10)
        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(10)
        self.cards = [TimingCard('基准圈', theme.BLUE), TimingCard('对比圈', theme.AMBER),
                      TimingCard('官方圈差 · 对比 − 基准', theme.TEXT)]
        for card in self.cards:
            cards.addWidget(card, 1)
            card.compact(True)
        center.addLayout(cards)
        self.state = theme.label('打开分析数据库，或从 Session 构建分析。', 'body', muted=True)
        self.state.setWordWrap(True)
        center.addWidget(self.state)
        self.charts = ChartStack()
        self.charts.inspected.connect(self.show_cursor)
        center.addWidget(self.charts, 1)
        center.addWidget(theme.label('蓝色实线：基准   琥珀虚线：对比  ｜  悬停查值 · ←/→ 原采样步进 · Ctrl+0 整圈', 'caption', muted=True))
        columns.addWidget(central, 1)
        self.right = QtWidgets.QScrollArea()
        self.right.setWidgetResizable(True)
        self.right.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side = QtWidgets.QWidget()
        sidebar = theme.vertical(side, 0, 10)
        capability = theme.Panel()
        cap_layout = theme.vertical(capability)
        toggle = QtWidgets.QToolButton()
        theme.apply_type(toggle, 'body_strong')
        toggle.setText('地图 / ERS 能力状态')
        toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setCheckable(True)
        toggle.setChecked(True)
        cap_layout.addWidget(toggle)
        reason = theme.label('尚未接入：当前比较模型无世界坐标和 ERS 曲线。\n四图和区间分析仍可正常使用。', 'caption', muted=True)
        reason.setWordWrap(True)
        toggle.toggled.connect(reason.setVisible)
        cap_layout.addWidget(reason)
        sidebar.addWidget(capability)
        self.timing = theme.label('官方圈差 / 可归属 / 未归属：—', 'body')
        self.timing.setWordWrap(True)
        sidebar.addWidget(self.timing)
        sidebar.addWidget(theme.label('共享距离游标', 'section'))
        self.cursor_heading = theme.label('悬停图线查看原始采样', 'caption', muted=True)
        sidebar.addWidget(self.cursor_heading)
        self.cursor = Table(['通道', '基准', '对比'])
        self.cursor.setFixedHeight(179)
        sidebar.addWidget(self.cursor)
        tabs = QtWidgets.QTabWidget()
        self.regions_table = Table(['距离 km', '时间差 s', '区间'])
        self.regions_table.setMinimumHeight(230)
        self.regions_table.selectionModel().currentChanged.connect(self.region_selected)
        tabs.addTab(self.regions_table, '时间区间')
        self.metrics = Table(['指标', '基准', '对比'])
        tabs.addTab(self.metrics, '整圈指标')
        self.quality_table = Table(['圈号', '覆盖', '质量'])
        self.quality_table.selectionModel().currentChanged.connect(self.quality_selected)
        tabs.addTab(self.quality_table, '数据质量')
        sidebar.addWidget(tabs)
        self.details = QtWidgets.QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(140)
        sidebar.addWidget(self.details)
        sidebar.addWidget(button('恢复整圈视图', self.charts.restore))
        sidebar.addStretch()
        self.right.setWidget(side)
        self.right.setFixedWidth(360)
        columns.addWidget(self.right)
        layout.addLayout(columns, 1)
        self.narrow = False
        self.details_active = False
        self.sidebar_width = 360
        self.reset_values()

    def compact(self, small):
        self.sidebar_width = 360 if small else 448
        self.responsive_columns()
        for card in self.cards:
            card.compact(small)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'sidebar_width'):
            self.responsive_columns()

    def responsive_columns(self):
        self.narrow = self.width() < 1100
        self.details_button.setVisible(self.narrow)
        self.center_column.setVisible(not self.narrow or not self.details_active)
        self.right.setVisible(not self.narrow or self.details_active)
        self.right.setFixedWidth(max(330, self.width()) if self.narrow else self.sidebar_width)
        self.details_button.setText('返回图表' if self.narrow and self.details_active else '区间 / 质量面板')

    def toggle_details(self):
        self.details_active = not self.details_active
        self.responsive_columns()

    def clear(self, message='正在加载…', clear_catalog=False):
        self.model = None
        self.regions = ()
        self.charts.clear()
        self.state.setText(message)
        self.regions_table.replace([])
        self.metrics.replace([])
        self.details.clear()
        self.timing.setText('官方圈差 / 可归属 / 未归属：—')
        self.reset_values()
        if clear_catalog:
            for combo in (self.session, self.segment, self.reference, self.comparison):
                combo_entries(combo, [])
            self.lap_model.replace([], None, None)
            self.quality = ()
            self.quality_table.replace([])

    def reset_values(self):
        for card in self.cards:
            card.value.setText('—')
            card.detail.setText('尚未选择圈次')

    def set_catalog(self, sessions, uid, segments, segment_id, laps, quality):
        combo_entries(self.session, [(s.label, s.session_uid) for s in sessions], uid)
        combo_entries(self.segment, [('全部驾驶段', None)] +
                      [(f'驾驶段 {s["id"]} · {s["lap_count"]} 圈', s['id']) for s in segments], segment_id)
        old_ref, old_cmp = self.reference.currentData(), self.comparison.currentData()
        default_ref = min(laps, key=lambda lap: lap.lap_time_ms).lap_number if laps else None
        default_cmp = next((p.lap_number for p in reversed(laps) if p.lap_number != default_ref), default_ref)
        ids = {p.lap_number for p in laps}
        old_ref = old_ref if old_ref in ids else default_ref
        old_cmp = old_cmp if old_cmp in ids else default_cmp
        entries = [(lap_display(p.track, p.session_type, p.lap_number, p.tyre, lap_time(p.lap_time_ms)),
                    p.lap_number) for p in laps]
        combo_entries(self.reference, entries, old_ref)
        combo_entries(self.comparison, entries, old_cmp)
        self.update_lap_list(laps, old_ref, old_cmp)
        self.quality = quality
        self.quality_table.replace([(f'第{q["lap_number"]}圈', number(q['coverage_ratio'] * 100 if q['coverage_ratio'] is not None else None, '%'),
                                    QUALITY_LABELS.get(q['quality_status'], q['quality_status'])) for q in quality])
        choice = next((s for s in sessions if s.session_uid == uid), None)
        self.scope.setText('单圈分析 · ' + (choice.label if choice else '此来源尚无已提交圈次'))

    def update_lap_list(self, laps, ref, cmp):
        scroll = self.lap_list.verticalScrollBar().value()
        blocker = QtCore.QSignalBlocker(self.lap_list.selectionModel())
        self.lap_model.replace(laps, ref, cmp)
        row = next((i for i, lap in enumerate(self.lap_model.laps) if lap.lap_number == cmp), -1)
        if row >= 0:
            self.lap_list.setCurrentIndex(self.lap_model.index(row))
        self.lap_list.verticalScrollBar().setValue(scroll)
        del blocker

    def change_session(self):
        self.scope_changed.emit(self.session.currentData(), None)

    def change_segment(self):
        self.scope_changed.emit(self.session.currentData(), self.segment.currentData())

    def change_pair(self):
        self.pair_changed.emit(self.reference.currentData(), self.comparison.currentData())

    def choose_list_lap(self, current, previous):
        if current.isValid():
            lap = current.data(QtCore.Qt.ItemDataRole.UserRole)
            self.comparison.setCurrentIndex(self.comparison.findData(lap.lap_number))

    def set_comparison(self, model, identity):
        self.model = model
        self.charts.set_model(model, identity)
        for card, lap in zip(self.cards, (model.reference, model.comparison)):
            card.value.setText(lap_time(lap.lap_time_ms))
            card.detail.setText(f'第{lap.lap_number}圈 · {tyre_label(lap.tyre)}')
            card.detail.setToolTip(lap_display(lap.track, lap.session_type, lap.lap_number, lap.tyre, lap_time(lap.lap_time_ms)))
        delta = model.comparison.lap_time_ms - model.reference.lap_time_ms
        self.cards[2].value.setText(f'{delta / 1000:+.3f}')
        self.cards[2].value.setStyleSheet(f'color: {theme.GREEN if delta < 0 else theme.TEXT}')
        self.cards[2].detail.setText('秒 · 官方整圈计时，不拉齐曲线')
        self.timing.setText(f'官方：{delta / 1000:+.3f} s\n可归属：{model.observed_delta_ms / 1000:+.3f} s\n'
                            f'未归属：{model.unattributed_delta_ms / 1000:+.3f} s')
        self.state.setText(' · '.join(model.warnings) or '已加载真实圈次；缺口处不连接。')
        self.regions = model.regions
        region_blocker = QtCore.QSignalBlocker(self.regions_table.selectionModel())
        self.regions_table.replace([(f'{r.start_m / 1000:.3f}–{r.end_m / 1000:.3f}',
                                     f'{r.delta_ms / 1000:+.3f}', r.kind) for r in model.regions])
        del region_blocker
        metric_fields = [('S1', 'sector1_ms', lap_time), ('S2', 'sector2_ms', lap_time), ('S3', 'sector3_ms', lap_time),
                         ('轮胎 / 磨损', 'tyre', tyre_label), ('相对个人最佳', 'delta_to_best_ms', lambda v: number(v / 1000, ' s', 3)),
                         ('全油门', 'full_throttle_percent', lambda v: number(v, '%')),
                         ('制动', 'braking_percent', lambda v: number(v, '%')),
                         ('滑行', 'coasting_percent', lambda v: number(v, '%')),
                         ('油刹重叠', 'overlap_percent', lambda v: number(v, '%')),
                         ('极速', 'maximum_speed_kph', lambda v: number(v, ' km/h')),
                         ('制动事件', 'braking_event_count', str), ('油门事件', 'throttle_event_count', str),
                         ('升档', 'upshift_count', str), ('降档', 'downshift_count', str),
                         ('转向修正', 'steering_correction_count', str),
                         ('转向变化/km', 'steering_variation_per_km', number)]
        self.metrics.replace([(name, fmt(getattr(model.reference, field)), fmt(getattr(model.comparison, field)))
                              for name, field, fmt in metric_fields])
        residual = '\n'.join(f'{name}: {value / 1000:+.6f} s' for name, value in model.residual_components)
        self.details.setPlainText('未归属差额组成（不作驾驶原因推断）\n' + (residual or '无额外组成记录') +
                                  '\n\n圈条件证据\n' + json.dumps(model.conditions, ensure_ascii=False, indent=2))

    def show_cursor(self, point, distance):
        if point is None:
            self.cursor_heading.setText('该距离无可靠样本' if math.isfinite(distance) else '悬停图线查看原始采样')
            self.cursor.replace([(name, '—', '—') for name in ('速度 km/h', '油门 %', '刹车 %', '档位')])
            return
        self.cursor_heading.setText(f'{point.distance_m / 1000:.6f} km · Δ {number(point.delta_ms / 1000, " s", 6)}')
        self.cursor.replace([(name, number(getattr(point, 'reference_' + field) * factor if getattr(point, 'reference_' + field) is not None else None, '', digits),
                              number(getattr(point, 'comparison_' + field) * factor if getattr(point, 'comparison_' + field) is not None else None, '', digits))
                             for name, field, factor, digits in [('速度 km/h', 'speed_kph', 1, 3), ('油门 %', 'throttle', 100, 3),
                                                                 ('刹车 %', 'brake', 100, 3), ('档位', 'gear', 1, 0)]])

    def region_selected(self, current, previous):
        if not current.isValid() or current.row() >= len(self.regions):
            return
        region = self.regions[current.row()]
        self.charts.focus_region(region.start_m, region.end_m)
        lines = [f'{region.start_m:.1f}–{region.end_m:.1f} m · {region.kind}',
                 f'区间差：{region.delta_ms / 1000:+.6f} s（观察差异，不代表原因）']
        for name, operation in [('基准', region.reference), ('对比', region.comparison)]:
            lines.append(f'\n{name}：进入 {number(operation.entry_speed_kph)} / 最低 {number(operation.minimum_speed_kph)} / '
                         f'离开 {number(operation.exit_speed_kph)} km/h\n制动起点 {number(operation.brake_start_m, " m")} / '
                         f'全油门 {number(operation.full_throttle_m, " m")} / 最低档 {number(operation.minimum_gear, precision=0)}')
        self.details.setPlainText('\n'.join(lines))

    def quality_selected(self, current, previous):
        if current.isValid() and current.row() < len(self.quality):
            details = self.quality[current.row()].get('details')
            self.details.setPlainText(json.dumps(details, ensure_ascii=False, indent=2) if details else
                                      '此旧数据库没有逐圈质量详情；保持只读，可另建分析补齐。')


class PracticePage(QtWidgets.QWidget):
    jump_requested = QtCore.Signal(int, int, float, float)

    def __init__(self):
        super().__init__()
        self.report = None
        layout = theme.vertical(self, 0, 12)
        self.heading = theme.label('练习分析 · 尚未选择会话', 'page_title')
        self.heading.setWordWrap(True)
        header = theme.Panel()
        header_layout = theme.vertical(header, 12, 8)
        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(self.heading, 1)
        self.refresh = button('生成 / 刷新练习报告')
        actions.addWidget(self.refresh)
        header_layout.addLayout(actions)
        self.status = theme.label('在单圈页选择会话与驾驶段，再生成练习报告。', muted=True)
        self.status.setWordWrap(True)
        header_layout.addWidget(self.status)
        layout.addWidget(header)
        self.tabs = QtWidgets.QTabWidget()
        overview = QtWidgets.QWidget()
        overview_layout = theme.vertical(overview)
        self.stats = theme.label('可分析圈：— ｜ 最快 / 中位 / 标准差：—', 'summary')
        self.stats.setWordWrap(True)
        overview_layout.addWidget(self.stats)
        self.sector_stats = theme.label('三个计时段：—', 'caption', muted=True)
        self.sector_stats.setWordWrap(True)
        overview_layout.addWidget(self.sector_stats)
        self.laps = Table(['圈号', '圈速', 'S1 / S2 / S3', '轮胎 / 磨损', '资格 / 原因'])
        overview_layout.addWidget(self.laps, 1)
        self.tabs.addTab(overview, '概览与圈次')
        conditions = QtWidgets.QWidget()
        conditions_layout = theme.vertical(conditions)
        self.groups = Table(['驾驶段 / 胎组', '圈次', '中位圈速', '标准差'])
        self.groups.setMaximumHeight(230)
        conditions_layout.addWidget(self.groups)
        self.conditions = Table(['圈号', '起始燃油 kg', '胎温中位 °C', 'ERS 中位 MJ', '已核验样本'])
        conditions_layout.addWidget(self.conditions, 1)
        self.conditions_note = theme.label('', 'caption', muted=True)
        self.conditions_note.setWordWrap(True)
        conditions_layout.addWidget(self.conditions_note)
        self.tabs.addTab(conditions, '胎组与条件')
        repeated = QtWidgets.QWidget()
        repeated_layout = theme.vertical(repeated)
        self.repeat_note = theme.label('重复损失区间不是弯道编号，也不是自动驾驶建议。', muted=True)
        self.repeat_note.setWordWrap(True)
        repeated_layout.addWidget(self.repeat_note)
        self.repeated = Table(['区间 m', '中位差 s', '损失圈 / 候选圈', '支持 / 圈次'])
        self.repeated.doubleClicked.connect(self.jump)
        repeated_layout.addWidget(self.repeated, 1)
        self.tabs.addTab(repeated, '重复损失与跳转')
        help_view = QtWidgets.QTextEdit()
        help_view.setReadOnly(True)
        help_view.setPlainText('练习报告固定于生成时的来源、Session 与驾驶段。切换来源会清除旧结果。\n'
                               '缺口、无效圈、条件不充分会排除或标注；未知值显示 —。\n'
                               '双击支持的重复损失区间，返回单圈分析并聚焦。\n'
                               '工具菜单可运行离线 Raw 审计；审计不能证明网络从未丢包。\n'
                               '真实游戏长测与不同 DPI 显示器切换仍需在游戏电脑验证。')
        self.tabs.addTab(help_view, '方法与审计')
        layout.addWidget(self.tabs, 1)

    def clear(self, message='来源已改变，请重新生成报告。'):
        self.report = None
        self.status.setText(message)
        self.stats.setText('可分析圈：— ｜ 最快 / 中位 / 标准差：—')
        self.sector_stats.setText('三个计时段：—')
        for table in (self.laps, self.groups, self.conditions, self.repeated):
            table.replace([])

    def set_report(self, report, context):
        self.report = report
        self.heading.setText('练习分析 · ' + context)
        stats = report['statistics']
        self.stats.setText(f'可分析圈 {stats["count"]} ｜ 最快 {lap_time(stats["best_ms"])} ｜ '
                           f'中位 {lap_time(stats["median_ms"])} ｜ 波动 {number(stats["spread_ms"], " ms")} ｜ '
                           f'标准差 {number(stats["stddev_ms"], " ms")}')
        self.sector_stats.setText('\n'.join(f'S{i + 1} · {s["count"]} 圈：最快 {lap_time(s["best_ms"])} / '
                                            f'中位 {lap_time(s["median_ms"])} / 标准差 {number(s["stddev_ms"], " ms")}'
                                           for i, s in enumerate(report['sectors'])))
        self.status.setText(f'基准第{report["reference_lap"]}圈 ｜ 隐藏较早圈次 {report["omitted_laps"]} ｜ '
                            f'排除原因：{json.dumps(report["excluded_reasons"], ensure_ascii=False)}')
        self.laps.replace([(r['lap'], lap_time(r['time_ms']), ' / '.join(lap_time(v) for v in r['sector_ms']),
                            f'{tyre_name(r["visual_compound"], r["compound"])} · {number(r["wear_percent"], "%")}',
                            '可用' if r['eligible'] else ' / '.join(r['reasons'])) for r in reversed(report['rows'])])
        self.groups.replace([(f'{g["segment_id"]} / {g["tyre_set"]}', str(g['laps']),
                              lap_time(g['statistics']['median_ms']), number(g['statistics']['stddev_ms'], ' ms'))
                             for g in report['groups']])
        self.conditions.replace([(r['lap'], number(r['conditions'].get('fuel_start_kg')),
                                  number(r['conditions'].get('tyre_inner_median_c')),
                                  number(r['conditions'].get('ers_median_mj'), precision=3),
                                  f'{r["conditions"].get("status_verified_samples", 0)} / {r["conditions"].get("source_samples", 0)}')
                                 for r in reversed(report['rows'])])
        self.conditions_note.setText(json.dumps(report['condition_warnings'], ensure_ascii=False))
        self.repeat_note.setText(report['repeatability_note'] + ' 双击有支持且有损失圈的行可跳转。')
        self.repeated.replace([(f'{r["start_m"]:.0f}–{r["end_m"]:.0f}', number(r['median_delta_ms'] / 1000 if r['median_delta_ms'] is not None else None, precision=3),
                                f'{r["loss_count"]} / {r["candidate_count"]}',
                                f'{"支持" if r["supported"] else "不足"} · {r["loss_laps"]}') for r in report['repeatability']])

    def jump(self, index):
        if self.report and index.isValid():
            region = self.report['repeatability'][index.row()]
            reference = self.report['reference_lap']
            if region['supported'] and region['loss_laps'] and reference is not None:
                self.jump_requested.emit(reference, region['loss_laps'][0], region['start_m'], region['end_m'])
