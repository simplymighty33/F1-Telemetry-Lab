"""Pump the real Tk queue for asynchronous UI acceptance tests."""
import time
import gc


def ready_window(root, *args, **kwargs):
    from collector.analysis_gui import AnalysisWindow
    window = AnalysisWindow(root, *args, **kwargs)
    if not hasattr(root, '_test_analysis_windows'):
        root._test_analysis_windows = []
        original_destroy = root.destroy

        def destroy():
            for view in root._test_analysis_windows:
                view.prepare_close()
            for view in root._test_analysis_windows:
                wait_for_tasks(view)
            original_destroy()
            root._test_analysis_windows.clear()
            gc.collect()  # Tk finalizers must run on the test/UI thread.

        root.destroy = destroy
    root._test_analysis_windows.append(window)
    wait_for_tasks(window)
    return window


def wait_for_tasks(window, timeout=5):
    deadline = time.monotonic() + timeout
    while not window.shutdown_ready() and time.monotonic() < deadline:
        window.root.update()
        time.sleep(.01)
    window.root.update()
    # The result can arrive just after the scheduled poll.
    deadline = time.monotonic() + .12
    while time.monotonic() < deadline:
        window.root.update()
        time.sleep(.01)
    if not window.shutdown_ready():
        raise AssertionError('UI background task did not finish')
