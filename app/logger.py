"""运行日志：落盘（滚动）+ 供 GUI 展示的内存队列。

通过 log_listener 订阅，GUI 能实时看到日志；日志同时写入 logs/sync.log。
"""

import logging
import logging.handlers
import os
import queue
import threading
import time

from .version import get_version

# 内存队列：GUI 读取；阻塞式 handler 会把日志投递进来
LOG_QUEUE = queue.Queue(maxsize=2000)

_initialized = False
_lock = threading.Lock()


def setup_logger(log_dir):
    """初始化全局 logger，返回 root logger。

    - 控制台（若在终端）
    - 滚动文件 logs/sync.log
    - 内存队列（供 GUI）
    """
    global _initialized, _lock
    with _lock:
        if _initialized:
            return logging.getLogger()
        os.makedirs(log_dir, exist_ok=True)
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

        # 文件
        logfile = os.path.join(log_dir, "sync.log")
        fh = logging.handlers.RotatingFileHandler(logfile, maxBytes=5 * 1024 * 1024,
                                                  backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        # 控制台
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # 内存队列 handler
        qh = _QueueHandler()
        qh.setFormatter(fmt)
        logger.addHandler(qh)

        _initialized = True
        logger.info("QRZCloudlogSync v%s 启动", get_version())
        logger.info("日志系统初始化完成 -> %s", logfile)
        return logger


class _QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LOG_QUEUE.put_nowait((time.time(), msg))
        except Exception:
            pass


class LogListener:
    """从队列里取日志并交给回调（供 Tkinter 在 UI 线程展示）。"""

    def __init__(self, callback, level=logging.INFO):
        self.callback = callback
        self._running = True
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                ts, msg = LOG_QUEUE.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.callback(msg)
            except Exception:
                pass


def info(msg, *args):
    logging.getLogger().info(msg, *args)


def warn(msg, *args):
    logging.getLogger().warning(msg, *args)


def error(msg, *args):
    logging.getLogger().error(msg, *args)
