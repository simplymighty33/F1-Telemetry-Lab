"""Scrollable snapshot review; background work belongs to AnalysisWindow."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from collector import APP_NAME, DISPLAY_VERSION
from decoder.tyres import LapTyreInfo
from decoder.display import tyre_label


def seconds(value):
    return "未知" if value is None else f"{value / 1000:.3f} s"


def condition_text(value, unit):
    return "未知 / 未核验" if value is None else f"{value:.2f} {unit}"


def _table(parent, columns, rows):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    table = ttk.Treeview(frame, columns=tuple(k for k, _ in columns), show="headings")
    for key, title in columns:
        table.heading(key, text=title)
        table.column(key, width=130, minwidth=90, stretch=False)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
    table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    table.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    for values in rows:
        table.insert("", "end", values=values)
    return table


def _text(parent, content):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="word", padx=12, pady=12)
    scroll = ttk.Scrollbar(frame, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(fill="both", expand=True)
    text.insert("1.0", content)
    text.configure(state="disabled")
    return text


def _window(parent, title):
    window = tk.Toplevel(parent)
    window.title(f"{APP_NAME} {DISPLAY_VERSION} · {title}")
    width, height = max(300, parent.winfo_screenwidth() - 80), max(280, parent.winfo_screenheight() - 100)
    window.geometry(f"{min(1160, width)}x{min(740, height)}")
    window.minsize(min(760, width), min(420, height))
    return window


class PracticeWindow:
    def __init__(self, parent, report, on_audit, on_focus=None):
        self.report = report
        self.root = _window(parent, "练习复盘")
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="选择已停止的Session进行长测审计", command=on_audit).pack(side="left")
        ttk.Label(toolbar, text="已提交快照；新圈完成后关闭并重新打开即可刷新").pack(side="left", padx=8)
        tabs = ttk.Notebook(self.root)
        tabs.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tabs = tabs
        overview, conditions, repetition, help_page = (ttk.Frame(tabs) for _ in range(4))
        for frame, title in ((overview, "练习段汇总"), (conditions, "比较条件"), (repetition, "多圈重复观察"), (help_page, "长测验收说明")):
            tabs.add(frame, text=title)
        stats = report["statistics"]
        text = (f"{report['session']} · {report['scope']}\n"
                f"有效 {stats['count']} / 已列 {len(report['rows'])} 圈；最快 {seconds(stats['best_ms'])}；"
                f"中位 {seconds(stats['median_ms'])}；极差 {seconds(stats['spread_ms'])}；标准差 {seconds(stats['stddev_ms'])}\n"
                f"超出最近120圈范围未列：{report['omitted_laps']}。波动不是驾驶评分；条件变化可能影响结果。")
        label = ttk.Label(overview, text=text, padding=8, justify="left")
        label.pack(fill="x")
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - 20)))
        self.lap_table = _table(overview, (("lap", "总圈数"), ("time", "圈速"), ("s1", "赛段1"), ("s2", "赛段2"), ("s3", "赛段3"),
            ("set", "胎组 / 胎组圈"), ("tyre", "配方 / 磨损"), ("state", "统计状态 / 原因")),
            [(f"总第{r['lap']}圈", seconds(r["time_ms"]), *(seconds(v) for v in r["sector_ms"]),
              f"{r['tyre_set'] or '未知'} / {r['tyre_lap'] or '未知'}",
              tyre_label(LapTyreInfo(actual_compound=r['compound'],visual_compound=r['visual_compound'],wear_percent=r['wear_percent'])),
              "计入统计" if r["eligible"] else "；".join(r["reasons"])) for r in report["rows"]])
        groups = []
        for group in report["groups"]:
            s = group["statistics"]
            groups.append(f"驾驶段 {group['segment_id'] if group['segment_id'] is not None else '未知'} / 胎组 {group['tyre_set'] or '未知'}："
                          f"{s['count']}圈，中位 {seconds(s['median_ms'])}，极差 {seconds(s['spread_ms'])}；总圈号 {group['laps']}")
        ttk.Label(overview, text="驾驶段与胎组独立；分组统计见比较条件页。", padding=6).pack(fill="x")
        condition_intro = ("起/终燃油仅取距起/终点200米内最早/最晚的已验证同帧样本。\n"
                           "胎温为四轮平均内温的逐样本中位值；ERS为已验证储能中位值，非消耗量。\n"
                           "未知值不按零处理；不做燃油修正或自动因果解释。\n\n" + "\n".join(groups))
        condition_intro += "\n未知胎组归入未识别栏，不表示这些圈确实使用同一套胎。\n赛段汇总（仅有效赛段记录）："
        for i, sector in enumerate(report["sectors"], 1):
            condition_intro += f"\n赛段{i}：{sector['count']}条，最快 {seconds(sector['best_ms'])}，中位 {seconds(sector['median_ms'])}，极差 {seconds(sector['spread_ms'])}"
        self.condition_table = _table(conditions, (("lap", "总圈数"), ("fuel", "起始燃油 kg"), ("endfuel", "结束燃油 kg"),
            ("temperature", "内温中位 °C"), ("ers", "ERS中位 MJ"), ("modes", "ERS模式代码"), ("coverage", "同帧来源样本")),
            [(f"总第{r['lap']}圈", condition_text(r["conditions"]["fuel_start_kg"], ""), condition_text(r["conditions"]["fuel_end_kg"], ""),
              condition_text(r["conditions"]["tyre_inner_median_c"], ""), condition_text(r["conditions"]["ers_median_mj"], ""),
              str(r["conditions"]["ers_modes"]) if r["conditions"]["ers_modes"] else "未知",
              f"状态 {r['conditions']['status_verified_samples']}/{r['conditions']['source_samples']}；胎温 {r['conditions']['temperature_verified_samples']}") for r in report["rows"]])
        notes_frame = ttk.Frame(conditions, height=140)
        notes_frame.pack(fill="both")
        notes_frame.pack_propagate(False)
        notes = condition_intro + "\n\n" + "\n".join(f"第{r['lap']}圈 vs 基准：" + "；".join(r["warnings"]) for r in report["condition_warnings"])
        _text(notes_frame, notes)
        repeat_label = ttk.Label(repetition, padding=8, text=f"基准：{report['reference_lap'] or '不可用'}；候选：{report['candidate_laps']}；不同段/配方未混入：{report['condition_excluded_laps']}\n{report['repeatability_note']}")
        repeat_label.pack(fill="x")
        repeat_label.bind("<Configure>", lambda e: repeat_label.configure(wraplength=max(200, e.width - 20)))
        bins = sorted(report["repeatability"], key=lambda r: (not r["repeated_loss"], -r["loss_count"], r["start_m"]))
        self.repeat_bins = bins
        self.repeat_table = _table(repetition, (("distance", "距离 m"), ("state", "观察"), ("loss", "损失/已覆盖候选"),
            ("coverage", "覆盖/全部候选"), ("median", "区间净差中位"), ("laps", "观察损失圈号")),
            [(f"{r['start_m']:.0f}–{r['end_m']:.0f}", "重复观察损失" if r["repeated_loss"] else "不足重复门槛",
              f"{r['loss_count']}/{r['supported']}", f"{r['supported']}/{r['candidate_count']}", seconds(r["median_delta_ms"]), str(r["loss_laps"])) for r in bins])
        if on_focus:
            def focus(_event=None):
                selection = self.repeat_table.selection()
                if selection:
                    index = self.repeat_table.index(selection[0])
                    region = bins[index]
                    if region["loss_laps"]:
                        on_focus(report["reference_lap"], region["loss_laps"][0], (region["start_m"], region["end_m"]))
            self.repeat_table.bind("<Double-1>", focus)
        _text(help_page, "本版尚需新一轮真实游戏长测，软件测试/历史回放不能替代它。\n\n"
              "建议顺序：多圈连续驾驶 → 多次进站换胎 → Flashback → 暂停/菜单 → 切换Session → 边录制边打开复盘 → 用X安全关闭。\n"
              "测试时间、游戏/协议、操作次数请另外记录，便于区分暂停与网络异常。\n\n"
              "审计前安全停止所选录制。审计只读取Raw、录制索引和已有基础缓存，不重建也不覆盖它们。\n"
              "检查逐包CRC、Header重新打包、索引字段、基础缓存Header、正常关闭状态、软件丢弃计数和资源压力。\n"
              "审计摘要默认不包含玩家身份、sessionUID数值、IP或私人路径。基础/分析尚未追上、缺少旧字段会标为待核验。\n"
              "档案通过不是零丢包保证；游戏未发送/网络未送达的数据无法靠Raw审计证明。\n"
              "退出等待测试及进程内存趋势仍需实际长测观察，工具不会伪造验收勾选。")


def show_audit(parent, result):
    root = _window(parent, "长测审计摘要")
    content = json.dumps(result, ensure_ascii=False, indent=2)
    toolbar = ttk.Frame(root, padding=8)
    toolbar.pack(fill="x")
    ttk.Label(toolbar, text="档案检查通过" if result["status"] == "archive_checks_passed" else "存在待核验项；见下方明细").pack(side="left")
    def save():
        selected = filedialog.asksaveasfilename(parent=root, title="保存去身份化诊断摘要", defaultextension=".json",
                                               initialfile="F1TelemetryLab-audit.json", filetypes=(("JSON", "*.json"),))
        if selected:
            from pathlib import Path
            target = Path(selected)
            if target.suffix.lower() != ".json" or target.name.lower() in {"metadata.json", "settings.json"}:
                messagebox.showerror("无法保存", "请另选一个JSON文件名，不要覆盖录制元数据或程序配置。", parent=root)
                return
            try:
                target.write_text(content, encoding="utf-8")
            except OSError:
                messagebox.showerror("保存失败", "无法写入诊断摘要，请检查目录权限或磁盘空间。", parent=root)
    ttk.Button(toolbar, text="保存诊断摘要", command=save).pack(side="right")
    _text(root, content)
    return root
