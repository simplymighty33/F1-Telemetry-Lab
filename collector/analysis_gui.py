"""Tk desktop viewer for offline lap comparison and driving metrics."""

from __future__ import annotations

import argparse
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from analysis.incremental import update_analysis as build_analysis, analysis_path, read_summary
from storage.raw_archive import ArchiveReadError
from storage.archive_tools import convert_archive
from analysis.comparison import (
    AnalysisDatabaseError,
    AnalysisRepository,
    LapComparison,
    LapSummary,
    find_latest_analysis_database,
)
from collector.runtime import application_root
from collector import APP_NAME, DISPLAY_VERSION
from collector.shutdown import ShutdownDialog


BACKGROUND = "#10141c"
PANEL = "#19212d"
PANEL_ALT = "#252f3e"
TEXT = "#f4f7fb"
MUTED = "#9cabc0"
ACCENT = "#e10600"
GREEN = "#35c46a"
CYAN = "#49b8ff"
AMBER = "#f5b942"
GRID = "#334052"


def format_lap_time(milliseconds: int | float) -> str:
    value = int(round(milliseconds))
    minutes, remainder = divmod(value, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_delta(milliseconds: int | float) -> str:
    return f"{milliseconds / 1000:+.3f} 秒"


def lap_label(lap: LapSummary) -> str:
    if lap.tyre.tyre_lap_number is not None:
        return (f"{lap.tyre.name} 第{lap.tyre.tyre_lap_number}圈（{format_lap_time(lap.lap_time_ms)}）"
                f" · 第{lap.tyre.stint_number}套/总第{lap.lap_number}圈")
    suffix = " · 最佳" if lap.delta_to_best_ms == 0 else f" · +{lap.delta_to_best_ms / 1000:.3f}"
    return f"第 {lap.lap_number} 圈 · {format_lap_time(lap.lap_time_ms)}{suffix}"


class ComparisonChart(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=PANEL, highlightthickness=0, height=430)
        self.comparison: LapComparison | None = None
        self.hover_var = tk.StringVar(value="将鼠标移到曲线上查看该距离的数值")
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _event: self.hover_var.set("将鼠标移到曲线上查看该距离的数值"))

    def set_comparison(self, comparison: LapComparison | None) -> None:
        self.comparison = comparison
        self.redraw()

    def _geometry(self) -> tuple[float, float, float, list[tuple[float, float]]]:
        width = max(300, self.winfo_width())
        height = max(130, self.winfo_height())
        left, right, top, bottom, gap = 58.0, width - 18.0, 22.0, height - 24.0, 18.0
        available = bottom - top - gap * 2
        delta_height = available * 0.24
        speed_height = available * 0.43
        panels = [
            (top, top + delta_height),
            (top + delta_height + gap, top + delta_height + gap + speed_height),
            (top + delta_height + gap + speed_height + gap, bottom),
        ]
        return left, right, width, panels

    def redraw(self) -> None:
        self.delete("all")
        comparison = self.comparison
        if comparison is None or not comparison.trace:
            self.create_text(
                self.winfo_width() / 2, self.winfo_height() / 2,
                text="请选择两个单圈进行比较", fill=MUTED, font=("Segoe UI", 12),
            )
            return
        trace = comparison.trace
        left, right, _, panels = self._geometry()
        max_distance = max(point.distance_m for point in trace)

        def x_position(distance: float) -> float:
            return left + (right - left) * distance / max(1.0, max_distance)

        for top, bottom in panels:
            self.create_rectangle(left, top, right, bottom, fill="#151c27", outline=GRID)
            for division in range(1, 5):
                x = left + (right - left) * division / 5
                self.create_line(x, top, x, bottom, fill=GRID, dash=(2, 5))

        delta_limit = max(100.0, max(abs(point.delta_ms) for point in trace) * 1.10)
        self._series(trace, "delta_ms", -delta_limit, delta_limit, panels[0], left, right, max_distance, AMBER)
        delta_zero = sum(panels[0]) / 2
        self.create_line(left, delta_zero, right, delta_zero, fill=MUTED, dash=(4, 4))

        speeds = [point.reference_speed_kph for point in trace] + [point.comparison_speed_kph for point in trace]
        speed_min = max(0.0, min(speeds) - 10)
        speed_max = max(speeds) + 10
        self._series(trace, "reference_speed_kph", speed_min, speed_max, panels[1], left, right, max_distance, GREEN)
        self._series(trace, "comparison_speed_kph", speed_min, speed_max, panels[1], left, right, max_distance, CYAN)

        self._series(trace, "reference_throttle", 0.0, 1.0, panels[2], left, right, max_distance, GREEN)
        self._series(trace, "comparison_throttle", 0.0, 1.0, panels[2], left, right, max_distance, CYAN)
        self._series(trace, "reference_brake", 0.0, 1.0, panels[2], left, right, max_distance, "#ff6468", dash=(4, 3))
        self._series(trace, "comparison_brake", 0.0, 1.0, panels[2], left, right, max_distance, "#d99cff", dash=(4, 3))

        for marker in comparison.reference_braking:
            x = x_position(marker.start_distance_m)
            self.create_line(x, panels[1][0], x, panels[2][1], fill=GREEN, dash=(2, 5))
        for marker in comparison.comparison_braking:
            x = x_position(marker.start_distance_m)
            self.create_line(x, panels[1][0], x, panels[2][1], fill=CYAN, dash=(2, 5))

        labels = (
            ("时间差", f"±{delta_limit / 1000:.2f}s"),
            ("速度", f"{speed_min:.0f}–{speed_max:.0f} km/h"),
            ("油门 / 刹车", "0–100%"),
        )
        for (top, _), (title, scale) in zip(panels, labels):
            self.create_text(8, top + 4, text=title, fill=TEXT, anchor="nw", font=("Segoe UI Semibold", 9))
            self.create_text(8, top + 22, text=scale, fill=MUTED, anchor="nw", font=("Segoe UI", 8))
        for division in range(6):
            distance = max_distance * division / 5
            self.create_text(
                x_position(distance), panels[2][1] + 9,
                text=f"{distance / 1000:.1f} km", fill=MUTED,
                anchor="n", font=("Segoe UI", 8),
            )

    def _series(
        self,
        trace: tuple,
        field: str,
        minimum: float,
        maximum: float,
        panel: tuple[float, float],
        left: float,
        right: float,
        max_distance: float,
        color: str,
        dash: tuple[int, int] | None = None,
    ) -> None:
        top, bottom = panel
        scale = max(1e-9, maximum - minimum)
        coordinates: list[float] = []
        for point in trace:
            x = left + (right - left) * point.distance_m / max(1.0, max_distance)
            value = float(getattr(point, field))
            y = bottom - (bottom - top) * (value - minimum) / scale
            coordinates.extend((x, min(bottom, max(top, y))))
        if len(coordinates) >= 4:
            self.create_line(*coordinates, fill=color, width=1.5, smooth=False, dash=dash)

    def _hover(self, event: tk.Event) -> None:
        comparison = self.comparison
        if comparison is None or not comparison.trace:
            return
        left, right, _, panels = self._geometry()
        if not left <= event.x <= right:
            return
        maximum = comparison.trace[-1].distance_m
        target = maximum * (event.x - left) / max(1.0, right - left)
        point = min(comparison.trace, key=lambda item: abs(item.distance_m - target))
        self.delete("hover")
        x = left + (right - left) * point.distance_m / max(1.0, maximum)
        self.create_line(x, panels[0][0], x, panels[2][1], fill=TEXT, dash=(3, 3), tags="hover")
        self.hover_var.set(
            f"{point.distance_m:.0f} 米｜时间差 {format_delta(point.delta_ms)}｜"
            f"基准 {point.reference_speed_kph:.0f} km/h｜对比 {point.comparison_speed_kph:.0f} km/h"
        )


class AnalysisWindow:
    def __init__(
        self,
        root: tk.Tk | tk.Toplevel,
        data_directory: Path,
        database: Path | None = None,
    ) -> None:
        self.root = root
        self.data_directory = data_directory
        self.repository: AnalysisRepository | None = None
        self.sessions = ()
        self.laps = ()
        self.comparison: LapComparison | None = None
        self._build_results: queue.Queue[tuple[str, object, object | None]] = queue.Queue()
        self._active_task: str | None = None
        self._last_build_session: Path | None = None
        self._session_labels: dict[str, str] = {}
        self._lap_labels: dict[str, int] = {}
        self._worker: threading.Thread | None = None
        self._closing = False
        self._shutdown_dialog: ShutdownDialog | None = None
        self._segment_labels: dict[str, int | None] = {"全部驾驶段": None}
        self._watched_database = database
        self._refresh_token = None
        self._manual_session = False
        self._quality_has_rows = False
        self._quality_expanded: bool | None = None

        root.title(f"{APP_NAME} {DISPLAY_VERSION} · 单圈分析")
        screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
        self._compact_screen = screen_height <= 800
        root.geometry(f"{min(1440, screen_width - 60)}x{min(940, screen_height - 100)}")
        root.minsize(min(940, screen_width - 60), min(620, screen_height - 100))
        root.configure(bg=BACKGROUND)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(6, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.database_var = tk.StringVar(value="尚未打开分析数据")
        self.status_var = tk.StringVar(value="可打开已有分析，或读取 Session 已整理的基础数据")
        self.session_var = tk.StringVar()
        self.reference_var = tk.StringVar()
        self.compare_var = tk.StringVar()
        self.segment_var = tk.StringVar(value="全部驾驶段")
        self.quality_var = tk.StringVar(value="仅数据完整且有效的圈进入对比；其他圈会列出原因。")
        self.reference_card = tk.StringVar(value="基准圈：—")
        self.compare_card = tk.StringVar(value="对比圈：—")
        self.delta_card = tk.StringVar(value="圈速差：—")
        self._configure_styles()
        self._build_layout()
        root.bind("<Configure>", self._window_resized, add="+")
        if database is not None:
            self.load_database(database)
        root.after(1500, self._poll_live_analysis)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Analysis.TCombobox", fieldbackground=PANEL_ALT, background=PANEL_ALT, foreground=TEXT)
        row_height = max(27, tkfont.nametofont("TkDefaultFont", root=self.root).metrics("linespace") + 8)
        style.configure("Metrics.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=row_height)
        style.configure("Metrics.Treeview.Heading", background=PANEL_ALT, foreground=TEXT, relief="flat")

    def _button(self, master: tk.Misc, text: str, command, accent: bool = False) -> tk.Button:
        return tk.Button(
            master, text=text, command=command,
            bg=ACCENT if accent else PANEL_ALT, activebackground="#b90500" if accent else "#34445a",
            fg="white", activeforeground="white", relief="flat", padx=14, pady=4,
            cursor="hand2", font=("Segoe UI Semibold", 9),
        )

    def _build_layout(self) -> None:
        header = tk.Frame(self.root, bg=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(12, 8))
        header.columnconfigure(0, weight=1)
        self.heading_label = tk.Label(header, text="单圈对比与驾驶指标", bg=BACKGROUND, fg=TEXT,
                                      font=("Segoe UI Semibold", 14 if self._compact_screen else 18), anchor="w")
        self.heading_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.toolbar = tk.Frame(header, bg=BACKGROUND)
        self.toolbar.grid(row=1, column=0, sticky="ew")
        self.database_button = self._button(self.toolbar, "打开分析数据库", self.choose_database)
        self.build_button = self._button(self.toolbar, "从 Session 生成分析", self.choose_session, accent=True)
        self.compress_button = self._button(self.toolbar, "压缩旧 Raw", self.choose_compress)
        self._toolbar_columns = None
        self.toolbar.bind("<Configure>", self._layout_toolbar)

        self.database_label = tk.Label(
            self.root, textvariable=self.database_var, bg=BACKGROUND, fg=MUTED,
            anchor="w", font=("Segoe UI", 9),
        )
        self.database_label.grid(row=1, column=0, sticky="ew", padx=20)

        controls = tk.Frame(self.root, bg=PANEL, padx=14, pady=8)
        controls.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 8))
        for column, (title, variable, width) in enumerate((
            ("会话", self.session_var, 34),
            ("基准圈", self.reference_var, 25),
            ("对比圈", self.compare_var, 25),
        )):
            block = tk.Frame(controls, bg=PANEL)
            block.grid(row=0, column=column, sticky="ew", padx=(0, 14))
            controls.grid_columnconfigure(column, weight=1, uniform="selector")
            tk.Label(block, text=title, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
            combo = ttk.Combobox(block, textvariable=variable, width=width, state="readonly", style="Analysis.TCombobox")
            combo.pack(fill="x", pady=(4, 0))
            if variable is self.session_var:
                self.session_combo = combo
                combo.bind("<<ComboboxSelected>>", self._session_selected)
            elif variable is self.reference_var:
                self.reference_combo = combo
                combo.bind("<<ComboboxSelected>>", self._selection_changed)
            else:
                self.compare_combo = combo
                combo.bind("<<ComboboxSelected>>", self._selection_changed)

        segment_controls = tk.Frame(self.root, bg=PANEL, padx=14, pady=5)
        segment_controls.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))
        segment_controls.columnconfigure(1, weight=1)
        tk.Label(segment_controls, text="驾驶段", bg=PANEL, fg=MUTED).grid(row=0, column=0, padx=(0, 10))
        self.segment_combo = ttk.Combobox(segment_controls, textvariable=self.segment_var,
                                         width=1, state="readonly", style="Analysis.TCombobox")
        self.segment_combo.grid(row=0, column=1, sticky="ew")
        self.segment_combo.bind("<<ComboboxSelected>>", self._segment_selected)
        self.quality_toggle = self._button(segment_controls, "展开单圈状态", self._toggle_quality)
        self.quality_toggle.configure(state="disabled", pady=3)
        self.quality_toggle.grid(row=0, column=2, padx=(10, 0))
        self.quality_label = tk.Label(segment_controls, textvariable=self.quality_var, bg=PANEL,
                                      fg=MUTED, anchor="w", justify="left")
        self.quality_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        segment_controls.bind("<Configure>", lambda e: self.quality_label.configure(wraplength=max(120, e.width - 28)))
        self.quality_frame = tk.Frame(self.root, bg=PANEL)
        self.quality_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 8))
        self.quality_tree = ttk.Treeview(self.quality_frame, columns=("lap", "time", "quality", "coverage"),
                                        show="headings", height=3, style="Metrics.Treeview")
        for key, label in (("lap", "总圈数"), ("time", "圈速"), ("quality", "数据状态"), ("coverage", "距离覆盖")):
            self.quality_tree.heading(key, text=label)
            self.quality_tree.column(key, width=140, anchor="center")
        quality_scroll = ttk.Scrollbar(self.quality_frame, orient="vertical", command=self.quality_tree.yview)
        self.quality_tree.configure(yscrollcommand=quality_scroll.set)
        quality_scroll.pack(side="right", fill="y")
        self.quality_tree.pack(side="left", fill="x", expand=True)
        self.quality_frame.grid_remove()  # Do not spend height on an empty table before parsing.
        cards = tk.Frame(self.root, bg=BACKGROUND)
        cards.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 8))
        for column, (variable, color) in enumerate(((self.reference_card, GREEN), (self.compare_card, CYAN), (self.delta_card, AMBER))):
            card = tk.Label(cards, textvariable=variable, bg=PANEL, fg=color, padx=15, pady=7, font=("Segoe UI Semibold", 11), anchor="w")
            cards.columnconfigure(column, weight=1, uniform="cards")
            card.grid(row=0, column=column, sticky="ew", padx=(0, 8))

        body = tk.PanedWindow(self.root, orient="horizontal", bg=BACKGROUND, sashwidth=7, sashrelief="flat")
        body.grid(row=6, column=0, sticky="nsew", padx=20)
        chart_frame = tk.Frame(body, bg=PANEL)
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)
        metric_frame = tk.Frame(body, bg=PANEL, width=330)
        body.add(chart_frame, stretch="always", minsize=380)
        body.add(metric_frame, minsize=280)
        self.chart = ComparisonChart(chart_frame)
        self.chart.grid(row=0, column=0, sticky="nsew")
        self.hover_label = tk.Label(chart_frame, textvariable=self.chart.hover_var, bg=PANEL,
                                    fg=MUTED, anchor="w", padx=10, pady=4)
        self.hover_label.grid(row=1, column=0, sticky="ew")
        metrics_body = tk.Frame(metric_frame, bg=PANEL)
        metrics_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        metrics_body.columnconfigure(0, weight=1)
        metrics_body.rowconfigure(0, weight=1)
        self.metrics = ttk.Treeview(metrics_body, columns=("metric", "reference", "compare"), show="headings", style="Metrics.Treeview")
        for name, title, width in (("metric", "指标", 128), ("reference", "基准圈", 82), ("compare", "对比圈", 82)):
            self.metrics.heading(name, text=title)
            self.metrics.column(name, width=width, anchor="center" if name != "metric" else "w")
        metrics_vertical = ttk.Scrollbar(metrics_body, orient="vertical", command=self.metrics.yview)
        metrics_horizontal = ttk.Scrollbar(metrics_body, orient="horizontal", command=self.metrics.xview)
        self.metrics.configure(yscrollcommand=metrics_vertical.set, xscrollcommand=metrics_horizontal.set)
        self.metrics.grid(row=0, column=0, sticky="nsew")
        metrics_vertical.grid(row=0, column=1, sticky="ns")
        metrics_horizontal.grid(row=1, column=0, sticky="ew")
        self.status_label = tk.Label(self.root, textvariable=self.status_var, bg=BACKGROUND, fg=MUTED,
                                     anchor="w", justify="left", pady=6)
        self.status_label.grid(row=7, column=0, sticky="ew", padx=20)
        self.status_label.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(160, e.width)))

    def _layout_toolbar(self, event) -> None:
        buttons = (self.build_button, self.database_button, self.compress_button)
        widths = [button.winfo_reqwidth() + 8 for button in buttons]
        columns = 3 if sum(widths) <= event.width else 2 if max(widths) * 2 <= event.width else 1
        if columns == self._toolbar_columns:
            return
        self._toolbar_columns = columns
        for column in range(3):
            self.toolbar.columnconfigure(column, weight=1 if column < columns else 0, uniform="actions" if column < columns else "")
        for index, button in enumerate(buttons):
            button.grid(row=index // columns, column=index % columns, sticky="ew", padx=(0, 8), pady=2)

    def _window_resized(self, event) -> None:
        if event.widget is self.root:
            self._sync_quality_visibility()

    def _toggle_quality(self) -> None:
        self._quality_expanded = not bool(self.quality_frame.grid_info())
        self._sync_quality_visibility()

    def _sync_quality_visibility(self) -> None:
        expanded = self._quality_expanded if self._quality_expanded is not None else self.root.winfo_height() >= 780
        if self._quality_has_rows and expanded:
            self.quality_frame.grid()
        else:
            self.quality_frame.grid_remove()
        if self._quality_has_rows:
            self.quality_label.grid()
        else:
            self.quality_label.grid_remove()
        self.quality_toggle.configure(state="normal" if self._quality_has_rows else "disabled",
                                      text="收起单圈状态" if self._quality_has_rows and expanded else "展开单圈状态")

    def choose_database(self) -> None:
        if self._closing or not self.shutdown_ready():
            return
        selected = filedialog.askopenfilename(
            parent=self.root, title="选择 telemetry_analysis.db",
            initialdir=self.data_directory,
            filetypes=(("分析数据库", "*.db"), ("所有文件", "*.*")),
        )
        if selected and not self._closing:
            self.load_database(Path(selected))

    def choose_session(self) -> None:
        if self._closing or not self.shutdown_ready():
            return
        selected = filedialog.askdirectory(
            parent=self.root, title="选择包含 raw_packets.bin 的 Session（采集中读取已提交快照）",
            initialdir=self.data_directory,
        )
        if not selected or self._closing:
            return
        session = Path(selected)
        if not (session / "raw_packets.bin").is_file():
            messagebox.showerror("无法生成分析", "所选目录中没有 raw_packets.bin。", parent=self.root)
            return
        self._start_session_build(session)

    def _start_session_build(self, session: Path, recover_tail: bool = False) -> None:
        if self._closing or not self.shutdown_ready():
            return
        self._active_task = "analysis"
        self._last_build_session = session
        self.build_button.configure(state="disabled", text="正在分析…")
        self.compress_button.configure(state="disabled")
        self.status_var.set("正在读取基础数据；旧 Raw 首次使用会建立可复用缓存，请稍候…")
        results = self._build_results

        def worker() -> None:
            try:
                options = {"progress_every": 0}
                if recover_tail:
                    options["recover_tail"] = True
                summary = build_analysis(session, **options)
                database = Path(summary["analysis_database"])
            except Exception as exc:
                results.put(("error", exc, None))
            else:
                results.put(("complete", database, summary))

        self._worker = threading.Thread(target=worker, name="lap-analysis", daemon=False)
        self._worker.start()
        self.root.after(100, self._poll_build_result)

    def choose_compress(self) -> None:
        if self._closing or not self.shutdown_ready():
            return
        self._active_task = "compression"
        selected = filedialog.askopenfilename(
            parent=self.root, title="选择已停止采集的旧 Raw 文件",
            initialdir=self.data_directory, filetypes=(("Raw 文件", "*.bin"),),
        )
        if not selected or self._closing:
            return
        source = Path(selected)
        metadata_path = source.parent / "metadata.json"
        if metadata_path.is_file() and source.name == "raw_packets.bin":
            import json

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("summary", {}).get("status") == "recording":
                messagebox.showerror("请先停止采集", "该 Session 仍标记为正在录制。", parent=self.root)
                return
        selected_output = filedialog.asksaveasfilename(
            parent=self.root, title="保存经过校验的压缩副本（原文件保留）",
            initialdir=source.parent, initialfile=source.stem + ".compressed.bin",
            defaultextension=".bin", filetypes=(("Raw 文件", "*.bin"),),
        )
        if not selected_output or self._closing:
            return
        target = Path(selected_output)
        if target.exists():
            messagebox.showerror("请选择新文件名", "压缩工具保留现有文件，请选择未使用的文件名。", parent=self.root)
            return
        self.build_button.configure(state="disabled")
        self.compress_button.configure(state="disabled", text="正在压缩…")
        self.status_var.set("正在压缩并逐包校验，完成后保存新副本…")
        results = self._build_results

        def worker() -> None:
            try:
                result = convert_archive(source, target)
            except Exception as exc:
                results.put(("error", exc, None))
            else:
                results.put(("archive", target, result))

        self._worker = threading.Thread(target=worker, name="raw-compression", daemon=False)
        self._worker.start()
        self.root.after(100, self._poll_build_result)

    def _poll_build_result(self) -> None:
        if self._closing or not self.root.winfo_exists():
            return
        if not self.shutdown_ready():
            self.root.after(100, self._poll_build_result)
            return
        try:
            state, value, details = self._build_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_build_result)
            return
        if state == "error":
            assert isinstance(value, Exception)
            self._build_failed(value)
        elif state == "archive":
            self.build_button.configure(state="normal", text="从 Session 生成分析")
            self.compress_button.configure(state="normal", text="压缩旧 Raw")
            assert isinstance(value, Path) and isinstance(details, dict)
            self.status_var.set(
                f"压缩副本已保存：{value}｜压缩比例 {details['saved_percent']:.1f}%｜"
                f"{details['verified_packet_count']:,} 包逐一相同。原文件已保留。"
            )
        else:
            assert isinstance(value, Path) and isinstance(details, dict)
            self._build_complete(value, details)

    def _build_failed(self, error: Exception) -> None:
        if self._closing:
            return
        self.build_button.configure(state="normal", text="从 Session 生成分析")
        self.compress_button.configure(state="normal", text="压缩旧 Raw")
        self.status_var.set("分析生成失败")
        if (self._active_task == "analysis" and self._last_build_session is not None
                and isinstance(error, ArchiveReadError) and error.truncated):
            if messagebox.askyesno(
                "Raw 尾部不完整",
                "发现末尾记录或压缩块被截断。是否仅分析此前完整且通过校验的数据？\n"
                "原 Raw 不会修改；结果将标注为部分数据。CRC 错误不会被忽略。",
                parent=self.root,
            ):
                self._start_session_build(self._last_build_session, recover_tail=True)
                return
        messagebox.showerror("分析失败", f"{type(error).__name__}: {error}", parent=self.root)

    def _build_complete(self, database: Path, summary: dict) -> None:
        if self._closing:
            return
        self.build_button.configure(state="normal", text="从 Session 生成分析")
        self.compress_button.configure(state="normal", text="压缩旧 Raw")
        if self.load_database(database) is False:
            return
        self.status_var.set(
            f"分析完成：{summary['laps_resampled']} 圈，{summary['resampled_points']:,} 个距离采样点，"
            f"{summary['decode_error_count']} 个解析错误"
            + ("｜已复用已有结果" if summary.get("reused") else "")
            + ("｜采集中已提交的数据快照" if summary.get("live_snapshot") else "")
            + ("｜仅包含完整校验前缀" if summary.get("tail_error") else "")
            + ("｜后台进度待补齐" if summary.get("status") == "pending" else "")
        )

    def shutdown_ready(self) -> bool:
        return self._worker is None or not self._worker.is_alive()

    def prepare_close(self) -> None:
        """Stop accepting work; current file/DB jobs are allowed to finish safely."""
        self._closing = True
        if not self.root.winfo_exists():
            return
        for button in (self.build_button, self.compress_button, self.database_button):
            button.configure(state="disabled")
        self.status_var.set("正在等待后台任务完成，随后安全关闭…")

    def request_close(self) -> None:
        if self._closing:
            return
        self.prepare_close()
        self._shutdown_dialog = ShutdownDialog(self.root, self.shutdown_ready, self.root.destroy)

    def load_database(self, database: Path) -> bool:
        old_uid = self._session_labels.get(self.session_var.get())
        if self.repository and self.repository.database != database.resolve():
            old_uid = None
            self._manual_session = False
            self._segment_labels = {"全部驾驶段": None}
            self.segment_var.set("全部驾驶段")
        try:
            repository = AnalysisRepository(database)
            sessions = repository.sessions()
            if not sessions:
                if not repository.has_segments:
                    raise AnalysisDatabaseError("数据库中没有可比较的完整单圈")
        except Exception as exc:
            messagebox.showerror("无法打开分析", str(exc), parent=self.root)
            self.status_var.set("尚无可比较的完整单圈，或数据无法载入")
            return False
        self.repository = repository
        self._watched_database = database
        self.sessions = sessions
        self.database_var.set(f"分析文件：{repository.database}")
        self._session_labels = {f"会话 {index} · {session.label}": session.session_uid
                                for index, session in enumerate(sessions, 1)}
        self.session_combo["values"] = tuple(self._session_labels)
        if sessions:
            ready_sessions = [s for s in sessions if s.lap_count]
            default = ready_sessions[-1] if ready_sessions else sessions[-1]
            keep = old_uid in self._session_labels.values() and (self._manual_session or any(s.session_uid == old_uid and s.lap_count for s in sessions))
            chosen = old_uid if keep else default.session_uid
            self.session_var.set(next(label for label, uid in self._session_labels.items() if uid == chosen))
            self._session_selected()
        else:
            self._clear_comparison()
            self.session_var.set("")
            self.quality_tree.delete(*self.quality_tree.get_children())
            self._quality_has_rows = False
            self._sync_quality_visibility()
            self.quality_var.set("等待已完成圈")
        self.status_var.set(f"已载入 {len(sessions)} 个会话；完成圈分析会自动刷新")
        return True

    def _session_selected(self, _event=None) -> None:
        if _event is not None:
            self._manual_session = True
        session_uid = self._session_labels.get(self.session_var.get())
        if session_uid:
            self._load_segments(session_uid)
            self._load_laps(session_uid)

    def _load_segments(self, session_uid: str) -> None:
        labels = {"全部驾驶段": None}
        segments = self.repository.segments(session_uid)
        for index, segment in enumerate(reversed(segments), 1):
            state = {"garage": "已回车库", "session_end": "会话结束", "open": "进行中 / 记录终点"}.get(segment["end_reason"], segment["end_reason"])
            partial = " / 中途开始" if not segment["start_complete"] else ""
            label = f"第{index}段 · 总第{segment['first_lap']}–{segment['last_lap']}圈 · {state}{partial}"
            labels[label] = segment["id"]
        previous = self._segment_labels.get(self.segment_var.get())
        self._segment_labels = labels
        self.segment_combo["values"] = tuple(labels)
        self.segment_var.set(next((label for label, key in labels.items() if key == previous), "全部驾驶段"))

    def _segment_selected(self, _event=None) -> None:
        uid = self._session_labels.get(self.session_var.get())
        if uid:
            self._load_laps(uid)

    def _clear_comparison(self) -> None:
        self.comparison = None
        self.chart.set_comparison(None)
        self.metrics.delete(*self.metrics.get_children())
        self.reference_var.set("")
        self.compare_var.set("")
        for variable, label in ((self.reference_card, "基准圈"), (self.compare_card, "对比圈"), (self.delta_card, "圈速差")):
            variable.set(label + "：—")

    def _poll_live_analysis(self) -> None:
        if self._closing or not self.root.winfo_exists():
            return
        database = self._watched_database
        if database and database.is_file() and self.shutdown_ready():
            try:
                summary = read_summary(database)
                token = (str(database), summary["total_raw_packets"], summary["status"])
                if token != self._refresh_token:
                    self.load_database(database)
                    self._refresh_token = token
            except (ValueError, sqlite3.Error, OSError):
                pass  # Legacy databases do not contain incremental metadata.
        self.root.after(1500, self._poll_live_analysis)

    def _load_laps(self, session_uid: str) -> None:
        assert self.repository is not None
        old_reference = self._lap_labels.get(self.reference_var.get())
        old_candidate = self._lap_labels.get(self.compare_var.get())
        quality = self.repository.quality(session_uid, self._segment_labels.get(self.segment_var.get()))
        allowed = {row["lap_number"] for row in quality}
        self.laps = self.repository.laps(session_uid)
        if self._segment_labels.get(self.segment_var.get()) is not None:
            self.laps = tuple(lap for lap in self.laps if lap.lap_number in allowed)
        self.quality_tree.delete(*self.quality_tree.get_children())
        titles = {"ready": "可分析", "invalid_lap": "游戏判定无效", "partial_capture": "距离覆盖不足",
                  "insufficient_samples": "样本不足", "pending": "等待完整数据"}
        for row in quality:
            self.quality_tree.insert("", "end", values=(row["lap_number"], format_lap_time(row["lap_time_ms"]),
                titles.get(row["quality_status"], row["quality_status"]),
                "—" if row["coverage_ratio"] is None else f"{row['coverage_ratio'] * 100:.1f}%"))
        self._quality_has_rows = bool(quality)
        self._sync_quality_visibility()
        self.quality_var.set(f"{len(quality)} 圈记录 / {len(self.laps)} 圈可对比")
        labels = {lap_label(lap): lap.lap_number for lap in self.laps}
        self._lap_labels = labels
        values = tuple(labels)
        self.reference_combo["values"] = values
        self.compare_combo["values"] = values
        if not self.laps:
            self._clear_comparison()
            self.status_var.set("该会话或驾驶段暂时没有可比较的完整有效圈；原因见数据状态。")
            return
        best = min(self.laps, key=lambda lap: lap.lap_time_ms)
        comparison = self.laps[-1] if self.laps[-1].lap_number != best.lap_number else self.laps[0]
        self.reference_var.set(next((lap_label(lap) for lap in self.laps if lap.lap_number == old_reference), lap_label(best)))
        self.compare_var.set(next((lap_label(lap) for lap in self.laps if lap.lap_number == old_candidate), lap_label(comparison)))
        self._refresh_comparison(session_uid)

    def _selection_changed(self, _event=None) -> None:
        session_uid = self._session_labels.get(self.session_var.get())
        if session_uid:
            self._refresh_comparison(session_uid)

    def _refresh_comparison(self, session_uid: str) -> None:
        reference_lap = self._lap_labels.get(self.reference_var.get())
        comparison_lap = self._lap_labels.get(self.compare_var.get())
        if self.repository is None or reference_lap is None or comparison_lap is None:
            return
        try:
            comparison = self.repository.comparison(session_uid, reference_lap, comparison_lap)
        except AnalysisDatabaseError as exc:
            self.status_var.set(str(exc))
            return
        self.comparison = comparison
        self.chart.set_comparison(comparison)
        reference = comparison.reference
        candidate = comparison.comparison
        self.reference_card.set(f"基准圈  {format_lap_time(reference.lap_time_ms)}")
        self.compare_card.set(f"对比圈  {format_lap_time(candidate.lap_time_ms)}")
        self.delta_card.set(f"圈速差  {format_delta(candidate.lap_time_ms - reference.lap_time_ms)}")
        self._render_metrics(reference, candidate)
        self.status_var.set(
            f"绿色：第 {reference.lap_number} 圈（基准）　蓝色：第 {candidate.lap_number} 圈（对比）　"
            "虚线表示制动起点"
        )

    def _render_metrics(self, reference: LapSummary, candidate: LapSummary) -> None:
        self.metrics.delete(*self.metrics.get_children())
        rows = (
            ("轮胎", reference.tyre.name, candidate.tyre.name),
            ("胎组 / 胎组圈数", f"{reference.tyre.stint_number or '—'} / {reference.tyre.tyre_lap_number or '—'}",
             f"{candidate.tyre.stint_number or '—'} / {candidate.tyre.tyre_lap_number or '—'}"),
            ("平均磨损", f"{reference.tyre.wear_percent:.1f}%" if reference.tyre.wear_percent is not None else "—",
             f"{candidate.tyre.wear_percent:.1f}%" if candidate.tyre.wear_percent is not None else "—"),
            ("赛段 1", format_lap_time(reference.sector1_ms), format_lap_time(candidate.sector1_ms)),
            ("赛段 2", format_lap_time(reference.sector2_ms), format_lap_time(candidate.sector2_ms)),
            ("赛段 3", format_lap_time(reference.sector3_ms), format_lap_time(candidate.sector3_ms)),
            ("全油门", f"{reference.full_throttle_percent:.1f}%", f"{candidate.full_throttle_percent:.1f}%"),
            ("制动", f"{reference.braking_percent:.1f}%", f"{candidate.braking_percent:.1f}%"),
            ("滑行", f"{reference.coasting_percent:.1f}%", f"{candidate.coasting_percent:.1f}%"),
            ("刹车油门重叠", f"{reference.overlap_percent:.1f}%", f"{candidate.overlap_percent:.1f}%"),
            ("最高速度", f"{reference.maximum_speed_kph:.0f} km/h", f"{candidate.maximum_speed_kph:.0f} km/h"),
            ("制动区", str(reference.braking_event_count), str(candidate.braking_event_count)),
            ("油门建立", str(reference.throttle_event_count), str(candidate.throttle_event_count)),
            ("升挡 / 降挡", f"{reference.upshift_count} / {reference.downshift_count}", f"{candidate.upshift_count} / {candidate.downshift_count}"),
            ("方向修正", str(reference.steering_correction_count), str(candidate.steering_correction_count)),
            ("转向变化/km", f"{reference.steering_variation_per_km:.2f}", f"{candidate.steering_variation_per_km:.2f}"),
        )
        for row in rows:
            self.metrics.insert("", "end", values=row)


def open_analysis_viewer(
    parent: tk.Tk,
    data_directory: Path,
    database: Path | None = None,
    session: Path | None = None,
) -> AnalysisWindow:
    window = tk.Toplevel(parent)
    if session is not None:
        database = analysis_path(session)
    elif database is None:
        database = find_latest_analysis_database(data_directory)
    viewer = AnalysisWindow(window, data_directory, database if database and database.is_file() else None)
    viewer._watched_database = database
    return viewer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open the F1 lap comparison viewer")
    parser.add_argument("database", nargs="?", type=Path)
    parser.add_argument("--data-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = tk.Tk()
    data_directory = (args.data_dir or application_root() / "data").expanduser().resolve()
    database = args.database or find_latest_analysis_database(data_directory)
    AnalysisWindow(root, data_directory, database)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
