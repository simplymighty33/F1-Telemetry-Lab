"""Tk desktop interface for live capture and lap-time stability checks."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from collector import __version__, APP_NAME, DISPLAY_VERSION
from collector.logging_setup import close_logging, configure_logging
from collector.runtime import application_root, resolve_runtime_path
from collector.service import CollectorService, CollectorSnapshot
from collector.settings import ConfigurationError, load_settings, parse_user_udp_port, save_udp_port
from collector.shutdown import ShutdownDialog


BACKGROUND = "#10141c"
PANEL = "#19212d"
TEXT = "#f4f7fb"
MUTED = "#9cabc0"
ACCENT = "#e10600"
GREEN = "#35c46a"
AMBER = "#f5b942"
RED = "#ff5a5f"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="F1 23/24/25 Telemetry Collector desktop UI")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--analysis", action="store_true", help="Open offline analysis without starting UDP capture")
    parser.add_argument("--analysis-db", type=Path, help="Open this analysis database without starting UDP capture")
    return parser


def format_lap_time(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_sector_time(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1_000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}.{millis:03d}" if minutes else f"{seconds}.{millis:03d}"


def _set_if_changed(variable: tk.StringVar, value: str) -> None:
    if variable.get() != value:
        variable.set(value)


class CollectorWindow:
    def __init__(
        self,
        root: tk.Tk,
        service: CollectorService,
        logger: logging.Logger,
        data_directory: Path,
        config_path: Path | None = None,
    ) -> None:
        self.root = root
        self.service = service
        self.logger = logger
        self.data_directory = data_directory
        self.config_path = config_path
        self._closing = False
        self._restarting = False
        self._analysis_windows = []
        self._shutdown_dialog: ShutdownDialog | None = None
        self._logger_closed = False
        self._status_color = AMBER
        self._displayed_session_uid: int | None = None

        root.title(f"{APP_NAME} {DISPLAY_VERSION}")
        root.minsize(760, 520)
        root.configure(bg=BACKGROUND)
        root.columnconfigure(0, weight=1)
        # Reserve the footer before allocating the remaining height to the table.
        root.rowconfigure(3, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.request_close)

        self.status_var = tk.StringVar(value="正在启动采集器")
        self.last_packet_var = tk.StringVar(value="尚未收到")
        self.session_var = tk.StringVar(value="正在创建 Session…")
        self.game_mode_var = tk.StringVar(value="游戏模式：等待数据")
        self.session_type_var = tk.StringVar(value="比赛阶段：—")
        self.track_var = tk.StringVar(value="赛道：—")
        self.error_var = tk.StringVar(value="")
        self.port_var = tk.StringVar(value=str(service.port))

        self._configure_styles()
        self._build_layout()
        self._size_initial_window()
        self.footer.bind("<Configure>", lambda _event: self._update_minimum_size())
        self.service.start()
        self.root.after(150, self._poll)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Telemetry.Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Telemetry.Treeview.Heading",
            background="#252f3e",
            foreground=TEXT,
            relief="flat",
            font=("Segoe UI Semibold", 10),
        )
        style.map("Telemetry.Treeview", background=[("selected", "#34445a")])

    def _build_layout(self) -> None:
        header = tk.Frame(self.root, bg=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 14))
        tk.Label(
            header,
            text=APP_NAME.upper(),
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI Semibold", 21),
        ).pack(side="left")
        tk.Label(
            header,
            text=DISPLAY_VERSION,
            bg=ACCENT,
            fg="white",
            padx=10,
            pady=4,
            font=("Segoe UI Semibold", 9),
        ).pack(side="left", padx=12)

        status_panel = tk.Frame(self.root, bg=PANEL, padx=18, pady=14)
        status_panel.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 12))
        status_row = tk.Frame(status_panel, bg=PANEL)
        status_row.pack(fill="x")
        self.status_dot = tk.Label(
            status_row,
            text="●",
            bg=PANEL,
            fg=AMBER,
            font=("Segoe UI", 17),
        )
        self.status_dot.pack(side="left")
        tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left", padx=(8, 0))

        connection = tk.Frame(status_panel, bg=PANEL)
        connection.pack(fill="x", pady=(8, 0))
        tk.Label(connection, text="UDP 端口", bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(side="left")
        self.port_entry = ttk.Entry(connection, textvariable=self.port_var, width=8)
        self.port_entry.pack(side="left", padx=10)
        self.port_button = tk.Button(
            connection, text="保存并重新监听", command=self.apply_port,
            bg="#252f3e", fg=TEXT, activebackground="#34445a", activeforeground=TEXT,
            relief="flat", padx=10, pady=5, cursor="hand2",
        )
        self.port_button.pack(side="left")
        tk.Label(connection, text="须与游戏内 UDP 端口一致", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=12)
        if self.config_path is None:
            self.port_button.configure(state="disabled")

        session_info = tk.Frame(self.root, bg=PANEL, padx=16, pady=11)
        session_info.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 12))
        for variable in (self.game_mode_var, self.session_type_var, self.track_var):
            tk.Label(
                session_info,
                textvariable=variable,
                bg=PANEL,
                fg=TEXT,
                font=("Segoe UI Semibold", 10),
            ).pack(side="left", expand=True, fill="x", padx=8)

        content = tk.Frame(self.root, bg=BACKGROUND)
        self.content = content
        content.grid(row=3, column=0, sticky="nsew", padx=28)
        tk.Label(
            content,
            text="玩家逐圈记录",
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w", pady=(2, 8))
        table_frame = tk.Frame(content, bg=PANEL)
        table_frame.pack(fill="both", expand=True)
        self.lap_table = ttk.Treeview(
            table_frame,
            columns=("lap", "tyre", "tyre_lap", "time", "s1", "s2", "s3", "status"),
            show="headings",
            style="Telemetry.Treeview",
            height=6,
        )
        self.lap_table.heading("lap", text="圈数")
        self.lap_table.heading("tyre", text="轮胎 / 磨损")
        self.lap_table.heading("tyre_lap", text="胎组圈数")
        self.lap_table.heading("time", text="圈速")
        self.lap_table.heading("s1", text="赛段 1")
        self.lap_table.heading("s2", text="赛段 2")
        self.lap_table.heading("s3", text="赛段 3")
        self.lap_table.heading("status", text="状态")
        self.lap_table.column("lap", width=90, anchor="center")
        self.lap_table.column("tyre", width=175, minwidth=135, anchor="center")
        self.lap_table.column("tyre_lap", width=125, minwidth=90, anchor="center")
        self.lap_table.column("time", width=130, anchor="center")
        self.lap_table.column("s1", width=110, anchor="center")
        self.lap_table.column("s2", width=110, anchor="center")
        self.lap_table.column("s3", width=110, anchor="center")
        self.lap_table.column("status", width=90, anchor="center")
        for column, minimum in (("lap", 70), ("tyre", 160), ("tyre_lap", 110),
                                ("time", 100), ("s1", 85), ("s2", 85), ("s3", 85), ("status", 60)):
            self.lap_table.column(column, minwidth=minimum)
        self.lap_table.tag_configure("best", foreground=GREEN)
        self.lap_table.tag_configure("invalid", foreground=RED)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.lap_table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.lap_table.xview)
        self.lap_table.configure(xscrollcommand=horizontal.set)
        self.lap_table.configure(yscrollcommand=scrollbar.set)
        horizontal.pack(side="bottom", fill="x")
        self.lap_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        tk.Label(content, text="轮胎磨损：该圈结束前四轮平均值；胎组圈数按每次换胎重新编号。",
                 bg=BACKGROUND, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(5, 0))

        footer = tk.Frame(self.root, bg=BACKGROUND)
        self.footer = footer
        footer.grid(row=4, column=0, sticky="ew", padx=28, pady=(12, 18))
        details = tk.Frame(footer, bg=BACKGROUND)
        details.pack(fill="x")
        tk.Label(
            details,
            textvariable=self.last_packet_var,
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        tk.Label(
            details,
            textvariable=self.session_var,
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))
        self.error_label = tk.Label(
            details,
            textvariable=self.error_var,
            bg=BACKGROUND,
            fg=RED,
            font=("Segoe UI Semibold", 9),
            wraplength=650,
            justify="left",
        )
        self.error_label.pack(anchor="w", pady=(3, 0))
        # Long session paths occupy their own row instead of squeezing buttons.
        actions = tk.Frame(footer, bg=BACKGROUND)
        self.actions = actions
        actions.pack(fill="x", pady=(10, 0))
        self.close_button = tk.Button(
            actions,
            text="安全停止并关闭",
            command=self.request_close,
            bg=ACCENT,
            activebackground="#b90500",
            fg="white",
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=10,
            cursor="hand2",
            font=("Segoe UI Semibold", 10),
        )
        self.close_button.pack(side="right", padx=(18, 0))
        self.analysis_button = tk.Button(
            actions,
            text="圈速分析",
            command=self.open_analysis,
            bg="#252f3e",
            activebackground="#34445a",
            fg="white",
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=10,
            cursor="hand2",
            font=("Segoe UI Semibold", 10),
        )
        self.analysis_button.pack(side="right")

    def _size_initial_window(self) -> None:
        self.root.update_idletasks()
        # Fonts follow Windows display scaling; size from actual widget requests.
        self.available_width = max(1, self.root.winfo_screenwidth() - 80)
        self.available_height = max(1, self.root.winfo_screenheight() - 100)
        width = min(self.available_width, max(1000, self.root.winfo_reqwidth() + 24))
        height = min(self.available_height, max(720, self.root.winfo_reqheight() + 24))
        self._update_minimum_size()
        self.root.geometry(f"{width}x{height}")

    def _update_minimum_size(self) -> None:
        if self._closing or not self.content.winfo_exists():
            return
        fixed_height = self.root.winfo_reqheight() - self.content.winfo_reqheight()
        minimum = (
            min(self.available_width, max(760, self.actions.winfo_reqwidth() + 56)),
            min(self.available_height, max(520, fixed_height + 100)),
        )
        if self.root.minsize() != minimum:
            self.root.minsize(*minimum)

    def _poll(self) -> None:
        if not self.root.winfo_exists() or self._closing:
            return
        if self._restarting:
            self.root.after(200, self._poll)
            return
        snapshot = self.service.snapshot()
        self._render_snapshot(snapshot)
        updates = self.service.drain_lap_updates()
        if updates:
            update = updates[-1]
            self._displayed_session_uid = update.session_uid
            self._render_laps(update.laps)
        if not self._closing:
            self.root.after(200, self._poll)

    def _render_snapshot(self, snapshot: CollectorSnapshot) -> None:
        _set_if_changed(self.status_var, snapshot.message)
        _set_if_changed(
            self.game_mode_var,
            f"游戏：{getattr(snapshot, 'game_name', '等待数据')}　模式：{snapshot.game_mode}",
        )
        _set_if_changed(self.session_type_var, f"比赛阶段：{snapshot.session_type}")
        _set_if_changed(self.track_var, f"赛道：{snapshot.track_name}")
        if snapshot.last_packet_at_ns is None:
            _set_if_changed(self.last_packet_var, "最近数据：尚未收到游戏 UDP 数据")
        else:
            received = datetime.fromtimestamp(
                snapshot.last_packet_at_ns / 1_000_000_000
            ).strftime("%H:%M:%S.%f")[:-3]
            _set_if_changed(self.last_packet_var, f"最近数据：{received}")
        if snapshot.session_directory is not None:
            _set_if_changed(self.session_var, f"Session：{snapshot.session_directory}")
        foundation_error = getattr(snapshot, "foundation_error", None)
        analysis_error = getattr(snapshot, "analysis_error", None)
        _set_if_changed(self.error_var, f"错误：{snapshot.error}" if snapshot.error else (
            "基础数据整理暂停，Raw 采集仍继续；停止后可恢复处理。" if foundation_error else
            "单圈分析暂停，Raw 采集仍继续；从 Session 可恢复处理。" if analysis_error else ""
        ))
        colors = {
            "starting": AMBER,
            "listening": AMBER,
            "receiving": GREEN,
            "stopping": AMBER,
            "stopped": MUTED,
            "error": RED,
        }
        color = colors.get(snapshot.state, MUTED)
        if color != self._status_color:
            self.status_dot.configure(fg=color)
            self._status_color = color
        if snapshot.state in {"stopped", "error"} and self.close_button.cget("text") != "关闭窗口":
            self.close_button.configure(text="关闭窗口")

    def _render_laps(self, laps: tuple) -> None:
        self.lap_table.delete(*self.lap_table.get_children())
        valid_times = [lap.lap_time_ms for lap in laps if lap.lap_valid]
        best_lap_ms = min(valid_times) if valid_times else None
        for lap in reversed(laps):
            tag = "invalid" if not lap.lap_valid else (
                "best" if lap.lap_time_ms == best_lap_ms else ""
            )
            self.lap_table.insert(
                "",
                "end",
                values=(
                    f"第 {lap.lap_number} 圈",
                    lap.tyre.label,
                    f"第 {lap.tyre.stint_number} 套 · 第 {lap.tyre.tyre_lap_number} 圈"
                    if lap.tyre.tyre_lap_number is not None else "—",
                    format_lap_time(lap.lap_time_ms),
                    format_sector_time(lap.sector1_ms),
                    format_sector_time(lap.sector2_ms),
                    format_sector_time(lap.sector3_ms),
                    "有效" if lap.lap_valid else "无效",
                ),
                tags=(tag,),
            )
        self.lap_table.yview_moveto(0.0)

    def request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._disable_connection()
        self.close_button.configure(text="正在保存…", state="disabled")
        self.analysis_button.configure(state="disabled")
        self.status_var.set("正在保存数据并安全关闭")
        self.status_dot.configure(fg=AMBER)
        for window in self._analysis_windows:
            window.prepare_close()
        self.service.stop()
        self._shutdown_dialog = ShutdownDialog(self.root, self._close_ready, self._finish_close)

    def _close_ready(self) -> bool:
        service_ready = self.service.wait(0)
        analysis_ready = all(window.shutdown_ready() for window in self._analysis_windows)
        if self._shutdown_dialog is not None:
            self._shutdown_dialog.message.set(
                "正在保存采集数据并停止监听，请稍候…" if not service_ready else
                "正在等待分析或压缩任务完整结束，请稍候…" if not analysis_ready else
                "数据保存完毕，正在关闭窗口…"
            )
        return service_ready and analysis_ready

    def _disable_connection(self) -> None:
        self.port_entry.configure(state="disabled")
        self.port_button.configure(state="disabled")

    def apply_port(self) -> None:
        if self._closing or self._restarting or self.config_path is None:
            return
        try:
            port = parse_user_udp_port(self.port_var.get())
        except ConfigurationError as exc:
            messagebox.showerror("UDP 端口无效", str(exc), parent=self.root)
            return
        # A stopped/error service can retry even when the configured port is unchanged.
        if port == self.service.port and self.service.snapshot().state not in {"error", "stopped"}:
            return
        if not messagebox.askyesno(
            "切换 UDP 端口",
            f"将保存端口 {port} 并重新监听。\n当前 Session 会先安全结束，再创建新的 Session。\n"
            "切换期间暂停接收；请在游戏暂停或进站后操作，并同步修改游戏端口。继续吗？",
            parent=self.root,
        ):
            return
        if self._closing or self._restarting:
            return  # Native dialogs run a nested event loop; close may occur there.
        try:
            save_udp_port(self.config_path, port)
        except (OSError, ConfigurationError) as exc:
            messagebox.showerror("无法保存设置", f"{exc}\n当前监听保持不变。", parent=self.root)
            return
        self._pending_port = port
        self._restarting = True
        self._disable_connection()
        self.analysis_button.configure(state="disabled")
        self.status_var.set("正在保存当前 Session，随后切换 UDP 端口…")
        self.service.stop()
        self.root.after(100, self._finish_restart)

    def _finish_restart(self) -> None:
        if self._closing:
            return  # Closing during a rebind must never start another collector.
        if not self.service.wait(0):
            self.root.after(100, self._finish_restart)
            return
        old = self.service
        settings = replace(old.settings, udp_port=self._pending_port)
        self.service = CollectorService(settings, self.data_directory, self.logger,
                                        host=old.host, port=self._pending_port)
        self._displayed_session_uid = None
        self._render_laps(())
        self.last_packet_var.set("最近数据：尚未收到游戏 UDP 数据")
        self.session_var.set("正在创建新 Session…")
        self.close_button.configure(text="安全停止并关闭")
        self._restarting = False
        self.port_entry.configure(state="normal")
        self.port_button.configure(state="normal")
        self.analysis_button.configure(state="normal")
        self.service.start()
        self.logger.info("UDP port changed to %s; previous session safely closed", self._pending_port)

    def open_analysis(self) -> None:
        if self._closing or self._restarting:
            return
        from collector.analysis_gui import open_analysis_viewer

        self._analysis_windows = [window for window in self._analysis_windows if window.root.winfo_exists()]
        current_session = self.service.snapshot().session_directory
        self._analysis_windows.append(open_analysis_viewer(self.root, self.data_directory, session=current_session))

    def _finish_close(self) -> None:
        if not self._logger_closed:
            close_logging(self.logger)
            self._logger_closed = True
        self.root.destroy()


def _show_startup_error(root: tk.Tk, message: str) -> None:
    root.title(f"{APP_NAME} - 启动错误")
    root.geometry("620x240")
    root.configure(bg=BACKGROUND)
    tk.Label(
        root,
        text="采集器无法启动",
        bg=BACKGROUND,
        fg=RED,
        font=("Segoe UI Semibold", 18),
    ).pack(pady=(35, 12))
    tk.Label(
        root,
        text=message,
        bg=BACKGROUND,
        fg=TEXT,
        wraplength=540,
        justify="left",
        font=("Segoe UI", 10),
    ).pack(padx=30)
    tk.Button(
        root,
        text="关闭窗口",
        command=root.destroy,
        bg=ACCENT,
        fg="white",
        relief="flat",
        padx=18,
        pady=9,
    ).pack(pady=24)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.analysis or args.analysis_db is not None:
        from collector.analysis_gui import main as analysis_main

        arguments = [str(args.analysis_db)] if args.analysis_db is not None else []
        if args.data_dir is not None:
            arguments.extend(("--data-dir", str(args.data_dir)))
        return analysis_main(arguments)
    root_path = application_root()
    window = tk.Tk()
    logger: logging.Logger | None = None
    try:
        config_path = (
            args.config.expanduser().resolve()
            if args.config is not None
            else root_path / "config" / "settings.json"
        )
        settings = load_settings(config_path)
        if args.port is not None and not 0 <= args.port <= 65_535:
            raise ConfigurationError("UDP port must be between 0 and 65535")
        data_directory = resolve_runtime_path(
            args.data_dir if args.data_dir is not None else settings.data_directory,
            root_path,
        )
        log_directory = resolve_runtime_path(
            args.log_dir if args.log_dir is not None else settings.log_directory,
            root_path,
        )
        data_directory.mkdir(parents=True, exist_ok=True)
        logger = configure_logging(
            log_directory,
            settings.log_max_bytes,
            settings.log_backup_count,
            console_enabled=False,
        )
        service = CollectorService(
            settings,
            data_directory,
            logger,
            host=args.host,
            port=args.port,
        )
        CollectorWindow(window, service, logger, data_directory, config_path=config_path)
    except Exception as exc:
        if logger is not None:
            logger.exception("Desktop collector startup failed: %s", exc)
            close_logging(logger)
        _show_startup_error(window, f"{type(exc).__name__}: {exc}")
    window.mainloop()
    return 0
