"""Command center shell: capture stays alive independently of the visible page."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import time

from PySide6 import QtCore, QtGui, QtWidgets

from analysis.incremental import analysis_path, update_analysis, rebuild_analysis
from analysis.practice import practice_report
from collector import DISPLAY_VERSION
from collector.audit_session import audit_session
from collector.logging_setup import close_logging
from collector.service import CollectorService
from collector.settings import parse_user_udp_port, save_udp_port
from storage.archive_tools import convert_archive
from storage.lease import lease_active
from storage.raw_archive import ArchiveReadError

from . import theme
from .branding import APP_NAME, BrandMark, app_icon
from .analysis_controller import AnalysisController, read_revision, SnapshotChanged
from .pages import CapturePage, AnalysisPage, PracticePage, button
from .tasks import Operation


def query_practice(repository, uid, segment, ref, revision, cancelled):
    if read_revision(repository.database) != revision:
        raise SnapshotChanged('练习来源已推进，请刷新当前范围')
    result = practice_report(repository, uid, segment, ref, cancelled)
    if read_revision(repository.database) != revision:
        raise SnapshotChanged('练习查询期间来源已推进，请刷新当前范围')
    return result


class ClosingDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('正在安全关闭')
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowCloseButtonHint, False)
        layout = theme.vertical(self, 22)
        layout.addWidget(theme.label('请等待数据保存和后台任务结束', 'context'))
        self.state = theme.label('正在请求停止…')
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.resize(460, 150)

    def reject(self):
        # Escape must not bypass draining. The dialog closes with its owner.
        pass


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, service, logger, data_directory, config_path, database=None):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} {DISPLAY_VERSION}')
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1200, 700)
        self.resize(1366, 768)
        self.service, self.logger = service, logger
        self.data_directory, self.config_path = Path(data_directory), Path(config_path)
        self.closing = self.allow_close = self.restarting = False
        self.live = service is not None
        self.live_source = None
        self.pending_restart = None
        self.stop_only = False
        self.last_refresh = 0.
        self.focus_after_compare = None
        self.operation, self.practice_operation = Operation(), Operation()
        self.operation_context = None
        self.practice_context = None
        self.controller = AnalysisController(self)
        self.capture_page, self.analysis_page, self.practice_page = CapturePage(), AnalysisPage(), PracticePage()
        self._build_shell()
        self.controller.cleared.connect(self.analysis_page.clear)
        self.controller.scope_invalidated.connect(self.invalidate_practice)
        self.controller.catalog_ready.connect(self.catalog_ready)
        self.controller.comparison_ready.connect(self.comparison_ready)
        self.controller.failed.connect(lambda message: self.footer_message.setText('分析来源：' + message))
        self.analysis_page.scope_changed.connect(self.controller.set_scope)
        self.analysis_page.pair_changed.connect(self.request_pair)
        self.analysis_page.practice_requested.connect(self.show_practice)
        self.practice_page.refresh.clicked.connect(self.generate_practice)
        self.practice_page.jump_requested.connect(self.jump_practice)
        self.capture_page.connect_requested.connect(self.connection_settings)
        self.capture_page.analysis_requested.connect(self.follow_live)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        if database:
            self.open_database(database)
        else:
            self.set_page(0 if service else 1)
        if service:
            service.start()

    def _build_shell(self):
        root = QtWidgets.QWidget()
        layout = theme.vertical(root, 0, 0)
        header = QtWidgets.QWidget()
        header.setFixedHeight(58)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(16, 7, 16, 7)
        header_layout.addWidget(BrandMark())
        self.brand = theme.label(APP_NAME, 'brand')
        header_layout.addWidget(self.brand)
        header_layout.addWidget(theme.label('F1 23 / 24 / 25', 'caption', muted=True))
        header_layout.addStretch()
        self.source_label = theme.label('实时录制' if self.live else '历史分析', 'caption')
        self.source_label.setMinimumWidth(150)
        self.source_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.source_label)
        tools = QtWidgets.QToolButton()
        theme.apply_type(tools, 'body_strong')
        tools.setText('来源与工具')
        tools.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(tools)
        for text, callback in [('打开分析数据库（只读）', self.choose_database), ('分析当前录制', self.follow_live),
                               ('从 Session 构建 / 续建分析', self.choose_build), ('独立重建分析（保留原结果）', self.choose_rebuild),
                               ('压缩 Raw（校验并保留原文件）', self.choose_compress), ('离线 Raw 审计', self.choose_audit),
                               ('连接与存储设置', self.connection_settings), ('停止采集（保留分析窗口）', self.stop_capture)]:
            action = menu.addAction(text)
            action.triggered.connect(callback)
        tools.setMenu(menu)
        self.tools = tools
        header_layout.addWidget(tools)
        layout.addWidget(header)
        content = QtWidgets.QHBoxLayout()
        content.setContentsMargins(14, 0, 14, 0)
        content.setSpacing(14)
        self.rail = theme.Panel()
        self.rail.setFixedWidth(170)
        rail = theme.vertical(self.rail, 10)
        workspace = theme.label('工作空间', 'caption', muted=True)
        workspace.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        workspace.setFixedHeight(24)
        rail.addWidget(workspace)
        self.nav = []
        for index, text in enumerate(('实时采集', '单圈比较', '练习分析')):
            item = button(text, lambda checked=False, i=index: self.set_page(i))
            item.setCheckable(True)
            item.setMinimumHeight(34)
            item.setAccessibleName(text)
            self.nav.append(item)
            rail.addWidget(item)
        self.rail_heading = theme.label('圈次 · 基准 / 对比', 'caption', muted=True)
        rail.addWidget(self.rail_heading)
        rail.addWidget(self.analysis_page.lap_list, 1)
        # When the lap list is hidden, absorb spare height here, not in labels.
        self.rail_spacer = QtWidgets.QWidget()
        self.rail_spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                      QtWidgets.QSizePolicy.Policy.Expanding)
        rail.addWidget(self.rail_spacer, 1)
        self.rail_help = theme.label('Raw 是原始记录。\n切换页面不会中断采集。\n\n历史来源不会随游戏\n会话切换。', 'caption', muted=True)
        self.rail_help.setWordWrap(True)
        self.rail_help.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                    QtWidgets.QSizePolicy.Policy.Minimum)
        rail.addWidget(self.rail_help)
        content.addWidget(self.rail)
        self.stack = QtWidgets.QStackedWidget()
        for page in (self.capture_page, self.analysis_page, self.practice_page):
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)
        layout.addLayout(content, 1)
        footer = QtWidgets.QWidget()
        footer.setFixedHeight(52)
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 6, 16, 6)
        self.footer_message = theme.label('准备就绪', 'caption', muted=True)
        self.footer_message.setMinimumWidth(80)
        self.footer_message.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        footer_layout.addWidget(self.footer_message, 1)
        self.cancel_button = button('取消后台任务', self.cancel_operations)
        self.cancel_button.setEnabled(False)
        footer_layout.addWidget(self.cancel_button)
        self.stop_button = button('安全停止并关闭', self.close)
        self.stop_button.setProperty('primary', True)
        self.stop_button.setToolTip('停止接收并等待 Raw 和后台任务安全保存后关闭')
        footer_layout.addWidget(self.stop_button)
        layout.addWidget(footer)
        self.setCentralWidget(root)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'rail'):
            small = self.width() < 1650
            self.rail.setFixedWidth(170 if small else 208)
            self.analysis_page.compact(small)

    def set_page(self, index):
        self.stack.setCurrentIndex(index)
        for i, item in enumerate(self.nav):
            item.setChecked(i == index)
        self.analysis_page.lap_list.setVisible(index == 1)
        self.rail_heading.setVisible(index == 1)
        self.rail_spacer.setVisible(index != 1)
        self.rail_help.setVisible(index != 1)

    def allowed(self):
        return not self.closing and not self.restarting

    def inform(self, title, text):
        if not self.closing:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(text)
            box.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            box.exec()

    def confirm(self, title, text):
        if not self.allowed():
            return False
        answer = QtWidgets.QMessageBox.question(self, title, text)
        return answer == QtWidgets.QMessageBox.StandardButton.Yes and self.allowed()

    def catalog_ready(self, catalog):
        if self.closing:
            return
        self.analysis_page.set_catalog(catalog.sessions, catalog.uid, catalog.segments,
                                       catalog.segment_id, catalog.laps, catalog.quality)
        if self.practice_context and self.practice_context[-1] != catalog.revision:
            self.practice_page.status.setText('分析已更新；当前练习报告是先前快照，刷新后才能跳转比较。')
        self.request_pair()

    def request_pair(self, *args):
        if self.allowed():
            self.controller.set_pair(self.analysis_page.reference.currentData(), self.analysis_page.comparison.currentData())

    def comparison_ready(self, model, identity):
        if self.closing:
            return
        self.analysis_page.set_comparison(model, identity)
        self.analysis_page.update_lap_list(self.controller.catalog.laps, model.reference.lap_number, model.comparison.lap_number)
        if self.focus_after_compare and self.focus_after_compare[:2] == self.controller.pair:
            self.analysis_page.charts.focus_region(*self.focus_after_compare[2:])
            self.focus_after_compare = None

    def invalidate_practice(self):
        self.practice_operation.cancel()
        self.practice_context = None
        self.practice_page.clear()
        self.focus_after_compare = None

    def open_database(self, path):
        if not self.allowed():
            return
        self.live = False
        self.live_source = None
        path = Path(path).resolve()
        self.controller.set_source(path)
        self.source_label.setText('历史 · ' + path.parent.name[-22:])
        self.source_label.setToolTip(str(path))
        self.set_page(1)

    def choose_database(self):
        if not self.allowed():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '打开分析数据库（只读）', str(self.data_directory), 'SQLite (*.db);;所有文件 (*)')
        if path and self.allowed():
            self.open_database(path)

    def follow_live(self):
        if not self.allowed():
            return
        self.live = True
        self.live_source = None
        self.controller.set_source(None)
        self.source_label.setText('实时 · 等待已提交分析')
        self.set_page(1)
        self.last_refresh = 0

    def stop_capture(self):
        if not self.allowed() or not self.service:
            return
        if self.confirm('停止采集', '保存并排空当前录制，分析窗口仍保持打开。需要再次采集可通过连接设置重启。'):
            self.stop_only = True
            self.service.stop()

    def connection_settings(self):
        if not self.allowed() or self.service is None:
            self.inform('连接设置', '当前是独立离线模式。启动采集入口后可编辑 UDP 与输出目录。')
            return
        old = self.service
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle('连接与存储设置')
        layout = theme.vertical(dialog, 20)
        form = QtWidgets.QFormLayout()
        port = QtWidgets.QLineEdit(str(old.port))
        directory = QtWidgets.QLineEdit(str(self.data_directory))
        form.addRow('UDP 端口（1–65535）', port)
        form.addRow('录制输出目录', directory)
        layout.addLayout(form)
        notice = theme.label('确认后保存配置，排空旧监听，再启用新设置。\n游戏中仍需启用 UDP 遥测；游戏版本自动识别。', 'caption', muted=True)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        for item in buttons.buttons():
            theme.apply_type(item, 'body_strong')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(600, 220)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted or not self.allowed():
            return
        self.apply_connection(port.text(), directory.text())

    def apply_connection(self, port_text, directory_text):
        """Confirmed connection edit; publish settings only while the app is open."""
        if not self.allowed() or self.service is None:
            return
        old = self.service
        try:
            new_port = parse_user_udp_port(port_text)
            target = Path(directory_text).expanduser()
            if not target.is_absolute():
                raise ValueError('输出目录请填写完整路径。')
            target = target.resolve()
            if not self.confirm('切换连接', f'UDP {old.port} → {new_port}\n输出目录：{target}\n保存当前档案后重新监听？') or not self.allowed():
                return
            target.mkdir(parents=True, exist_ok=True)
            # Save with the existing settings parser and preserve other fields.
            self._save_connection(new_port, target)
            settings = replace(old.settings, udp_port=new_port, data_directory=str(target))
        except Exception as exc:
            self.inform('设置未应用', f'{type(exc).__name__}: {exc}')
            return
        if not self.allowed():
            return
        self.pending_restart = settings, target, old.host, new_port
        self.restarting = True
        self.tools.setEnabled(False)
        old.stop()

    def _save_connection(self, port, target):
        # Preserve all existing fields. Port + folder publish atomically, so a
        # failed folder save cannot leave only the port unexpectedly changed.
        temporary = self.config_path.with_suffix('.qt-tmp')
        try:
            shutil.copy2(self.config_path, temporary)
            save_udp_port(temporary, port)
            settings = json.loads(temporary.read_text(encoding='utf-8'))
            settings['data_directory'] = str(target)
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(self.config_path)
        finally:
            temporary.unlink(missing_ok=True)

    def choose_session(self, title):
        if not self.allowed():
            return None
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, title, str(self.data_directory))
        return Path(folder) if folder and self.allowed() else None

    def choose_build(self):
        source = self.choose_session('选择包含 Raw 的 Session 目录')
        if source:
            self.start_build(source)

    def choose_rebuild(self):
        source = self.choose_session('选择 Session，独立重建（不覆盖旧分析）')
        if source and self.confirm('独立重建', '从权威 Raw 另建缓存；旧分析不覆盖。请先停止此档案的采集。'):
            self.start_build(source, rebuild=True)

    def start_operation(self, kind, source, producer):
        if not self.allowed() or not self.operation.ready():
            self.inform('后台任务忙', '请等待当前构建 / 压缩 / 审计结束，或先取消。')
            return False
        key = (kind, self.controller.generation, str(source), time.monotonic_ns())
        self.operation_context = key
        self.footer_message.setText(f'{kind} · 正在准备')
        self.cancel_button.setEnabled(True)
        return self.operation.start(key, producer)

    def start_build(self, source, rebuild=False, recover=False):
        builder = rebuild_analysis if rebuild else update_analysis
        self.start_operation('独立重建' if rebuild else '构建分析', source,
                             lambda control, cancelled: builder(source, progress_every=0, control=control, recover_tail=recover))

    def choose_compress(self):
        if not self.allowed():
            return
        source, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择停止录制的 Raw 文件', str(self.data_directory), 'Raw (*.bin);;所有文件 (*)')
        if not source or not self.allowed():
            return
        source = Path(source)
        if lease_active(source.parent / 'capture.lock'):
            self.inform('正在录制', '请先停止当前档案的采集，再进行压缩。')
            return
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, '另存压缩副本（原文件保留）', str(source.with_name(source.stem + '.compressed.bin')), 'Raw (*.bin)')
        if target and self.allowed():
            self.start_operation('压缩 Raw', source, lambda control, cancelled: convert_archive(source, Path(target), control=control))

    def choose_audit(self):
        source = self.choose_session('选择停止录制的 Session，进行只读审计')
        if source:
            self.start_operation('只读审计', source, lambda control, cancelled: audit_session(source, cancelled=cancelled.is_set))

    def cancel_operations(self):
        self.operation.cancel()
        self.practice_operation.cancel()
        self.footer_message.setText('正在取消；等待安全检查点，原始档案保留。')

    def show_practice(self):
        self.set_page(2)
        self.generate_practice()

    def generate_practice(self):
        catalog = self.controller.catalog
        if not self.allowed() or not catalog or not catalog.uid or not self.practice_operation.ready():
            self.practice_page.status.setText('请先选取可用会话；当前报告任务忙时可在底栏取消。')
            return
        repository, uid, segment = catalog.repository, catalog.uid, catalog.segment_id
        ref = self.analysis_page.reference.currentData()
        key = (self.controller.generation, str(repository.database), uid, segment, ref, catalog.revision)
        self.practice_context = key
        self.practice_page.status.setText('正在查询圈条件与重复损失…')
        self.practice_operation.start(key, lambda control, cancelled: query_practice(repository, uid, segment, ref, catalog.revision, cancelled.is_set))
        self.cancel_button.setEnabled(True)

    def jump_practice(self, reference, comparison, start, end):
        key = self.practice_context
        if not key or key[:4] != (self.controller.generation, str(self.controller.database), self.controller.uid, self.controller.segment_id):
            return
        if not self.controller.catalog or key[-1] != self.controller.catalog.revision:
            self.practice_page.status.setText('分析 revision 已改变，请刷新报告，避免跳转到另一有效分支。')
            return
        self.focus_after_compare = reference, comparison, start, end
        for combo, value in ((self.analysis_page.reference, reference), (self.analysis_page.comparison, comparison)):
            blocker = QtCore.QSignalBlocker(combo)
            combo.setCurrentIndex(combo.findData(value))
            del blocker
        self.set_page(1)
        self.request_pair()
        if self.analysis_page.model and self.controller.pair == (reference, comparison):
            model = self.analysis_page.model
            if (model.reference.lap_number, model.comparison.lap_number) == (reference, comparison):
                self.analysis_page.charts.focus_region(start, end)

    def poll(self):
        if self.closing:
            self.poll_close()
            return
        if self.service:
            updates = self.service.drain_lap_updates()
            snapshot = self.service.snapshot()
            self.capture_page.update_snapshot(snapshot)
            for update in updates:
                self.capture_page.update_laps(update)
            if self.restarting and self.service.wait(0):
                settings, target, host, port = self.pending_restart
                self.pending_restart = None
                self.service = CollectorService(settings, target, self.logger, host=host, port=port)
                self.data_directory = target
                self.restarting = False
                self.stop_only = False
                self.tools.setEnabled(True)
                self.service.start()
                self.live_source = None
            if self.live and not self.restarting:
                source = (snapshot.session_uid, snapshot.player_car_index, snapshot.packet_format,
                          str(snapshot.session_directory))
                if source != self.live_source:
                    self.live_source = source
                    path = analysis_path(snapshot.session_directory) if snapshot.session_directory else None
                    self.controller.set_source(path)
                state = f'实时 · {snapshot.analysis_state}'
                self.source_label.setText(state)
                self.source_label.setToolTip(str(snapshot.session_directory or '等待录制档案'))
        now = time.monotonic()
        if now - self.last_refresh >= 1.5:
            self.last_refresh = now
            if self.controller.database:
                self.controller.refresh()
        self.controller.poll()
        self.poll_operations()

    def poll_operations(self):
        busy = not self.operation.ready() or not self.practice_operation.ready()
        self.cancel_button.setEnabled(busy)
        if not self.operation.ready():
            stage, completed, total = self.operation.control.snapshot()
            self.footer_message.setText(f'{stage} · {completed:,}' + (f' / {total:,}' if total else ''))
        result = self.practice_operation.take()
        if result and result[0] == self.practice_context:
            key, value, error = result
            if error:
                self.practice_page.clear(f'{type(error).__name__}: {error}')
            else:
                self.practice_page.set_report(value, value['session'] + ' · ' + value['scope'])
        result = self.operation.take()
        if not result or result[0] != self.operation_context:
            return
        key, value, error = result
        kind, generation, source, _ = key
        if error:
            self.footer_message.setText(f'{kind} · {type(error).__name__}: {error}')
            if isinstance(error, InterruptedError):
                return
            if generation != self.controller.generation:
                self.show_result(kind + '（先前来源）', {'source': source, 'error': str(error),
                                                         'note': '来源已切换；不会自动在当前来源重试。'})
                return
            if kind in ('构建分析', '独立重建') and isinstance(error, ArchiveReadError) and error.truncated:
                if self.confirm('检测到不完整尾部', f'{error}\n只恢复已核验完整前缀？原始 Raw 不改写，CRC 错误不能忽略。'):
                    self.start_build(Path(source), rebuild=kind == '独立重建', recover=True)
            elif kind == '构建分析' and isinstance(error, ValueError):
                if self.confirm('无法续用现有分析', f'{error}\n是否从 Raw 独立重建？现有结果保留；录制中不可重建。'):
                    self.start_build(Path(source), rebuild=True)
            else:
                self.inform(kind + '失败', f'{type(error).__name__}: {error}')
        else:
            self.footer_message.setText(kind + ' · 已完成')
            if kind in ('构建分析', '独立重建') and generation == self.controller.generation and self.allowed():
                self.open_database(Path(value['analysis_database']))
            self.show_result(kind, value)

    def show_result(self, kind, result):
        if self.closing:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(kind + '结果')
        layout = theme.vertical(dialog, 16)
        layout.addWidget(theme.label('任务结果', 'section'))
        summary = theme.label('', 'body')
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        if kind == '只读审计':
            summary.setText(f'Raw CRC：{"通过" if result.get("raw_crc_valid") else "未通过 / 未核验"}\n'
                            f'已遍历 {result.get("raw_packets", "—")} 包 ｜ Session {result.get("session_count", "—")}\n'
                            f'Header 重编码差异：{result.get("header_roundtrip_mismatches", "—")}\n'
                            f'录制索引差异：{result.get("index_mismatches", "—")} ｜ 基础缓存差异：{result.get("foundation_mismatches", "—")}\n'
                            '提示：' + ('；'.join(result.get('warnings', [])) or '档案检查完成；这不证明游戏 UDP 从未丢包。'))
        elif kind == '压缩 Raw':
            summary.setText(f'原文件保留；已校验 {result.get("verified_packet_count", "—")} 包\n'
                            f'节省 {result.get("saved_percent", "—")}% ｜ 副本：{result.get("target", "—")}')
        else:
            summary.setText(f'状态：{result.get("status", "见详细结果")}\n'
                            f'分析产物：{result.get("analysis_database", "—")}\n'
                            '历史 / 活动快照、尾部恢复与来源信息见下方详细记录。')
        layout.addWidget(summary)
        layout.addWidget(theme.label('详细记录与产物路径（可选择复制）', 'caption', muted=True))
        text = QtWidgets.QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        layout.addWidget(text, 1)
        layout.addWidget(button('关闭', dialog.accept))
        dialog.resize(720, 520)
        dialog.exec()

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.pending_restart = None
        self.controller.close()
        self.operation.close()
        self.practice_operation.close()
        self.centralWidget().setEnabled(False)
        if self.service:
            self.service.stop()
        self.closing_dialog = ClosingDialog(self)
        self.closing_dialog.show()
        self.poll_close()

    def poll_close(self):
        service_ready = self.service is None or self.service.wait(0)
        tasks_ready = self.controller.ready() and self.operation.ready() and self.practice_operation.ready()
        stages = []
        if not service_ready:
            stages.append('保存 Raw、排空采集与派生数据')
        if not tasks_ready:
            stages.append('等待分析 / 压缩 / 审计任务安全结束')
        self.closing_dialog.state.setText('\n'.join(stages) or '保存完成，正在关闭窗口…')
        if service_ready and tasks_ready:
            self.timer.stop()
            if self.logger:
                close_logging(self.logger)
            self.allow_close = True
            self.closing_dialog.accept()
            self.close()
