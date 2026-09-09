"""QRZ CloudLog 同步器 - 程序入口。

用法：
    直接运行        -> 打开图形界面
    --autostart    -> 开机自启动：后台启动定时同步并最小化窗口
    --sync-now     -> 启动后立即执行一次同步（结合定时任务/命令行使用）
"""

import sys

import app  # noqa: F401


def main():
    args = sys.argv[1:]
    autostart = '--autostart' in args
    sync_now = '--sync-now' in args

    # 先保证 config 与日志就绪（在 GUI 里也会做，这里提前避免后台模式缺日志）
    from app.config import Config
    from app.logger import setup_logger, info
    cfg = Config()
    cfg.load()
    setup_logger(cfg.log_dir)

    if '--self-test-browser' in args:
        _self_test_browser(cfg)
        return

    if autostart:
        _run_headless_autostart(cfg, sync_now)
        return

    from app.gui import App
    if sync_now:
        # 命令行模式下也打开 GUI，但先触发一次同步
        pass
    root = App()
    if sync_now:
        root.after(500, root._do_sync)
    root.mainloop()


def _self_test_browser(cfg):
    """自检：解析打包的 Chromium 并实际拉起，验证浏览器可用。结果写入文件。"""
    import os
    import tempfile
    from app.qrz import QrzUploader, QrzNeedsLogin, QrzLoginFailed, QrzUploadError, _chromium_exe
    from app.config import Account
    from app.logger import info

    out = os.path.join(cfg.data_dir, "selftest_browser.txt")
    lines = []
    def outlog(msg):
        info(msg)
        lines.append(msg)

    exe = _chromium_exe()
    outlog("BUNDLED_CHROMIUM=%s" % (exe or "NOT_FOUND"))
    acc = Account(id="st", name="st", browser="chrome", headless=True)
    up = QrzUploader(acc, tempfile.mkdtemp(prefix="selftest_"), logfn=outlog)
    try:
        up.open()
        outlog("RESULT=UNEXPECTED: 未登录却返回成功")
    except QrzNeedsLogin:
        outlog("RESULT=OK: 内置浏览器已启动并打开 QRZ（判定为未登录，符合预期）")
    except QrzLoginFailed as e:
        outlog("RESULT=LOGIN_FAILED: %s" % e)
    except Exception as e:
        outlog("RESULT=ERROR: %s: %s" % (type(e).__name__, e))
    finally:
        try:
            up.close()
        except Exception:
            pass

    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    print("\n".join(lines))


def _run_headless_autostart(cfg, sync_now):
    """开机自启动：后台定时同步，窗口最小化。"""
    from app.scheduler import Scheduler
    from app.logger import info
    sched = Scheduler(cfg)
    sched.on_progress(info)
    sched.start()
    info("后台自启动模式运行中（--autostart）。")

    # 等待；若被命令行关闭则退出
    try:
        import time
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        sched.stop()
        info("收到退出信号，停止同步。")


if __name__ == "__main__":
    main()
