"""Non-blocking Tk shutdown: keep windows alive until their workers finish."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class ShutdownDialog:
    def __init__(self, root: tk.Misc, ready: Callable[[], bool], finish: Callable[[], None]) -> None:
        self.root, self.ready, self.finish = root, ready, finish
        self.window = tk.Toplevel(root)
        self.window.title("正在安全关闭")
        self.window.transient(root)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        self.message = tk.StringVar(master=self.window, value="正在保存数据并停止后台任务，请稍候…")
        ttk.Label(self.window, textvariable=self.message, padding=(24, 20), wraplength=370).pack()
        self.progress = ttk.Progressbar(self.window, mode="indeterminate", length=340)
        self.progress.pack(padx=24, pady=(0, 12))
        ttk.Label(self.window, text="完成后将自动关闭窗口，请勿强制结束进程。", padding=(24, 0, 24, 18)).pack()
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, root.winfo_rootx() + (root.winfo_width() - width) // 2)
        y = max(0, root.winfo_rooty() + (root.winfo_height() - height) // 2)
        self.window.geometry(f"+{x}+{y}")
        try:
            self.window.grab_set()
        except tk.TclError:
            # Another application's modal grab must not prevent our safe exit.
            # Owners also disable their actions and reject new work explicitly.
            pass
        self.progress.start(15)
        self._after_id: str | None = None
        self.window.bind("<Destroy>", self._destroyed)
        self._after_id = self.root.after(100, self._poll)

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is self.window:
            if self._after_id is not None:
                self.root.after_cancel(self._after_id)
                self._after_id = None
            # Do not leave callback cycles holding Tk variables for later GC on
            # a background thread after the owner window has been destroyed.
            self.ready = lambda: True
            self.finish = lambda: None

    def _poll(self) -> None:
        self._after_id = None
        if not self.ready():
            self._after_id = self.root.after(100, self._poll)
            return
        self.progress.stop()
        self.finish()  # Destroying the owner also destroys this child dialog.
