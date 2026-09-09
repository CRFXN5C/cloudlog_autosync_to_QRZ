"""定时调度器：后台线程按间隔周期执行同步，并支持手动立即同步。"""

import threading
import time
from datetime import datetime

from .worker import SyncEngine
from .logger import info, warn


class Scheduler:
    def __init__(self, config):
        self.config = config
        self.engine = SyncEngine(config)
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()       # 避免同时跑两个同步
        self._running = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def on_progress(self, callback):
        self.engine.on_progress = callback

    # ---- 启动定时循环 ----
    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="SyncScheduler", daemon=True)
        self._thread.start()
        info("定时调度已启动（每 %d 分钟一次）。", self.config.settings.interval_minutes)

    def stop(self):
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=3)
            self._thread = None
            info("定时调度已停止。")

    def _loop(self):
        # 启动时若配置了 sync_at_startup，先跑一次
        if self.config.settings.sync_at_startup:
            self.run_now()
        while not self._stop.is_set():
            interval = max(1, self.config.settings.interval_minutes) * 60
            # 分段等待，以便及时响应 stop
            waited = 0
            while waited < interval and not self._stop.is_set():
                time.sleep(1)
                waited += 1
            if self._stop.is_set():
                break
            self.run_now()

    # ---- 手动立即同步 ----
    def run_now(self, only_station_id=None) -> bool:
        if not self._lock.acquire(blocking=False):
            warn("已有同步在进行中，忽略本次请求。")
            return (False, ["已有同步在进行中"])
        try:
            info("==== 开始同步 %s ====", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            results = self.engine.run_all(only_station_id=only_station_id)
            ok_count = sum(1 for _, ok, _ in results if ok)
            info("==== 同步结束：%d/%d 成功 ====", ok_count, len(results))

            # 组装每个台站的明细，供界面弹窗展示
            details = []
            for station, ok, msg in results:
                label = station.callsign or station.name or station.id
                details.append("[%s] %s —— %s" % (label, "成功" if ok else "失败", msg))
            return (ok_count > 0, details)
        except Exception as e:
            warn("同步整体异常: %s", e)
            return (False, ["同步整体异常: %s" % e])
        finally:
            self._lock.release()
