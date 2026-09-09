"""图形界面（Tkinter）。

标签页：
  1. 账号与台站 - 创建/编辑 QRZ 账号与 CloudLog 台站，可手动登录 QRZ
  2. 运行设置 - 同步间隔、批次大小、浏览器、无头、开机自启等
  3. 同步日志 - 实时日志查看

顶部控制：立即同步、启动/停止定时、开启开机自启。
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from uuid import uuid4

from .config import Config, Account, Station
from .version import get_version, APP_NAME
from .cloudlog import CloudlogClient, CloudlogError
from .logger import setup_logger, LogListener, info, warn, error
from .scheduler import Scheduler
from .qrz import QrzUploader, QrzNeedsLogin, QrzLoginFailed
from .state import StateStore


AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "QRZCloudlogSync"


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s 同步器 v%s" % (APP_NAME, get_version()))
        self.geometry("980x640")
        self.minsize(880, 560)
        self._set_window_icon()
        self._build_menubar()

        self.config = Config()
        self.config.load()
        setup_logger(self.config.log_dir)
        self.scheduler = Scheduler(self.config)
        self.scheduler.on_progress(self._append_log)
        self.state = StateStore(self.config.state_dir)

        self._build_ui()
        self._refresh_tree()
        self._load_settings_form()

        # 日志监听 -> 界面
        self._log_listener = LogListener(self._append_log)
        self._log_listener.start()
        self._update_status()

        # 关闭时保存
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _resource_path(self, name):
        base = getattr(sys, '_MEIPASS', None)
        candidates = []
        if base:
            candidates.append(os.path.join(base, 'assets', name))
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(here, 'assets', name))
        candidates.append(os.path.join(os.getcwd(), 'assets', name))
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _set_window_icon(self):
        p = self._resource_path('qrz.ico')
        if p:
            try:
                self.iconbitmap(default=p)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=6)
        top.pack(side="top", fill="x")

        ttk.Button(top, text="立即同步", command=self._do_sync).pack(side="left", padx=3)
        ttk.Button(top, text="启动定时", command=self._start_sched).pack(side="left", padx=3)
        ttk.Button(top, text="停止定时", command=self._stop_sched).pack(side="left", padx=3)
        self.autostart_var = tk.BooleanVar(value=self._is_autostart_enabled())
        ttk.Checkbutton(top, text="开机自启动", variable=self.autostart_var,
                        command=self._toggle_autostart).pack(side="left", padx=12)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(top, textvariable=self.status_var).pack(side="right", padx=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.nb = nb

        self._build_accounts_tab(nb)
        self._build_settings_tab(nb)
        self._build_log_tab(nb)

        # 状态栏
        sb = ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken")
        sb.pack(side="bottom", fill="x")

    def _build_menubar(self):
        menubar = tk.Menu(self)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于 / 版本",
                              command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)

    def _show_about(self):
        messagebox.showinfo("关于",
                            "%s\n版本: v%s\n\n定时将 CloudLog 日志上传到 QRZ Logbook。\n"
                            "多账号 / 多台站，浏览器模拟上传，运行日志落盘。"
                            % (APP_NAME, get_version()))

    # ================== 账号与台站 ==================
    def _build_accounts_tab(self, nb):
        frame = ttk.Frame(nb)
        nb.add(frame, text="账号与台站")

        left = ttk.Frame(frame, padding=4)
        left.pack(side="left", fill="both", expand=True)

        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=2)
        ttk.Button(bar, text="新建账号", command=self._new_account).pack(side="left", padx=2)
        ttk.Button(bar, text="新建台站", command=self._new_station).pack(side="left", padx=2)
        ttk.Button(bar, text="删除", command=self._delete_item).pack(side="left", padx=2)
        ttk.Button(bar, text="启用/禁用", command=self._toggle_enable).pack(side="left", padx=2)
        ttk.Button(bar, text="手动登录 QRZ", command=self._manual_login).pack(side="left", padx=2)
        ttk.Button(bar, text="测试登录状态", command=self._check_login_status).pack(side="left", padx=2)

        self.tree = ttk.Treeview(left, columns=("kind", "detail", "status"), show="tree headings",
                                 height=20)
        self.tree.heading("#0", text="账号 / 台站")
        self.tree.heading("kind", text="类型")
        self.tree.heading("detail", text="呼号/标识")
        self.tree.heading("status", text="状态")
        self.tree.column("#0", width=180)
        self.tree.column("kind", width=80)
        self.tree.column("detail", width=160)
        self.tree.column("status", width=80)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        right = ttk.Frame(frame, padding=6)
        right.pack(side="right", fill="both")

        self.form_title = ttk.Label(right, text="请选择左侧账号或台站查看/编辑", font=("", 11, "bold"))
        self.form_title.pack(anchor="w", pady=4)

        self.form_frame = ttk.Frame(right)
        self.form_frame.pack(fill="both", expand=True)
        self._vars = {}
        self._current_kind = None
        self._current_id = None

        ttk.Button(right, text="保存修改", command=self._save_item).pack(pady=8)

    # ---- tree 刷新 ----
    def _refresh_tree(self, select_id=None):
        self.tree.delete(*self.tree.get_children())
        for acc in self.config.accounts:
            acc_iid = "acct_%s" % acc.id
            kind = "QRZ账号" if acc.enabled else "QRZ账号(停用)"
            self.tree.insert("", "end", iid=acc_iid, text=acc.name or acc.qrz_username or "未命名账号",
                             values=(kind, acc.qrz_username, "启用" if acc.enabled else "停用"))
            for st in self.config.stations_of(acc.id):
                st_iid = "st_%s" % st.id
                skind = "台站" if st.enabled else "台站(停用)"
                self.tree.insert(acc_iid, "end", iid=st_iid, text=st.callsign or st.name,
                                 values=(skind, st.name or st.callsign, "启用" if st.enabled else "停用"))
        if select_id:
            try:
                self.tree.selection_set(select_id)
                self.tree.focus(select_id)
                self._on_tree_select()
            except Exception:
                pass

    def _on_tree_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        # 清空表单
        for child in self.form_frame.winfo_children():
            child.destroy()
        self._vars = {}

        if iid.startswith("acct_"):
            aid = iid[len("acct_"):]
            acc = self.config.get_account(aid)
            if not acc:
                return
            self._current_kind = "account"
            self._current_id = aid
            self.form_title.config(text="编辑 QRZ 账号：%s" % (acc.name or acc.qrz_username))
            self._render_account_form(acc)
        elif iid.startswith("st_"):
            sid = iid[len("st_"):]
            st = self.config.get_station(sid)
            if not st:
                return
            self._current_kind = "station"
            self._current_id = sid
            self.form_title.config(text="编辑台站：%s" % (st.callsign or st.name))
            self._render_station_form(st)

    def _var(self, key, initial=""):
        v = tk.StringVar(value=initial)
        self._vars[key] = v
        return v

    def _render_account_form(self, acc):
        f = self.form_frame
        r = 0
        ttk.Label(f, text="账号名称").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("name", acc.name), width=28).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="QRZ 用户名").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("qrz_username", acc.qrz_username), width=28).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="QRZ 密码(留空=不改)").grid(row=r, column=0, sticky="e", pady=2); r += 1
        self._pw_entry = ttk.Entry(f, textvariable=self._var("qrz_password", ""), width=28, show="*")
        self._pw_entry.grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="浏览器").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Combobox(f, textvariable=self._var("browser", acc.browser), values=("edge", "chrome"),
                     state="readonly", width=26).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="无头模式").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Checkbutton(f, variable=self._make_boolvar("headless", acc.headless)).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="自动登录").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Checkbutton(f, variable=self._make_boolvar("auto_login", acc.auto_login)).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="驱动路径(可选)").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("driver_path", acc.driver_path), width=28).grid(row=r, column=1, sticky="w", pady=2); r += 1

    def _render_station_form(self, st):
        f = self.form_frame
        r = 0
        ttk.Label(f, text="台站名称").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("name", st.name), width=30).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="呼号").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("callsign", st.callsign), width=30).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="CloudLog 地址").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("cloudlog_url", st.cloudlog_url), width=30).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="API Key").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("api_key", st.get_api_key()), width=30).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="station_id(可选)").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("station_profile_id", st.station_profile_id), width=30).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(f, text="上次同步").grid(row=r, column=0, sticky="e", pady=2); r += 1
        ttk.Entry(f, textvariable=self._var("last_sync", st.last_sync), width=30, state="readonly").grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Button(f, text="检测 CloudLog 台站（自动填呼号）", command=self._detect_station).grid(row=r, column=0, columnspan=2, sticky="w", pady=6); r += 1
        ttk.Label(f, text="(密码/API key 会加密保存)").grid(row=r, column=0, columnspan=2, sticky="w", pady=4); r += 1

    def _make_boolvar(self, key, initial):
        v = tk.BooleanVar(value=initial)
        self._vars[key] = v
        return v

    # ---- 账号/台站 CRUD ----
    def _selected_account_for_station(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个账号")
            return None
        iid = sel[0]
        if iid.startswith("acct_"):
            return iid[len("acct_"):]
        if iid.startswith("st_"):
            # 找父节点
            try:
                parent = self.tree.parent(iid)
                if parent.startswith("acct_"):
                    return parent[len("acct_"):]
            except Exception:
                pass
        messagebox.showwarning("提示", "请先选择一个账号作为台站的归属")
        return None

    def _new_account(self):
        acc = Account(id=uuid4().hex[:8], name="新账号", browser="edge")
        self.config.accounts.append(acc)
        self.config.save()
        self._refresh_tree("acct_%s" % acc.id)
        info("已新建账号 %s", acc.name)

    def _new_station(self):
        aid = self._selected_account_for_station()
        if not aid:
            return
        st = Station(id=uuid4().hex[:8], account_id=aid, name="新台站",
                     cloudlog_url=self.config.settings.cloudlog_default_url)
        self.config.stations.append(st)
        self.config.save()
        self._refresh_tree("st_%s" % st.id)
        info("已新建台站")

    def _delete_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if not messagebox.askyesno("确认", "确定删除所选账号/台站？该操作不可撤销。"):
            return
        if iid.startswith("acct_"):
            aid = iid[len("acct_"):]
            self.config.accounts = [a for a in self.config.accounts if a.id != aid]
            self.config.stations = [s for s in self.config.stations if s.account_id != aid]
        else:
            sid = iid[len("st_"):]
            self.config.stations = [s for s in self.config.stations if s.id != sid]
        self.config.save()
        self._refresh_tree()

    def _toggle_enable(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("acct_"):
            a = self.config.get_account(iid[len("acct_"):])
            if a:
                a.enabled = not a.enabled
        else:
            s = self.config.get_station(iid[len("st_"):])
            if s:
                s.enabled = not s.enabled
        self.config.save()
        self._refresh_tree(iid)

    # ---- 保存表单 ----
    def _save_item(self):
        if self._current_kind == "account":
            acc = self.config.get_account(self._current_id)
            if acc:
                acc.name = self._vars.get("name", tk.StringVar()).get()
                acc.qrz_username = self._vars.get("qrz_username", tk.StringVar()).get()
                pw = self._vars.get("qrz_password", tk.StringVar()).get()
                if pw:
                    acc.set_password(pw)
                acc.browser = self._vars.get("browser", tk.StringVar()).get() or "edge"
                acc.headless = self._vars.get("headless", tk.BooleanVar()).get()
                acc.auto_login = self._vars.get("auto_login", tk.BooleanVar()).get()
                acc.driver_path = self._vars.get("driver_path", tk.StringVar()).get()
                self.config.save()
                info("已保存账号 %s", acc.name)
                self._refresh_tree("acct_%s" % acc.id)
        elif self._current_kind == "station":
            st = self.config.get_station(self._current_id)
            if st:
                st.name = self._vars.get("name", tk.StringVar()).get()
                st.callsign = self._vars.get("callsign", tk.StringVar()).get()
                st.cloudlog_url = self._vars.get("cloudlog_url", tk.StringVar()).get()
                pkey = self._vars.get("api_key", tk.StringVar()).get()
                if pkey:
                    st.set_api_key(pkey)
                st.station_profile_id = self._vars.get("station_profile_id", tk.StringVar()).get()
                self.config.save()
                info("已保存台站 %s", st.callsign or st.name)
                self._refresh_tree("st_%s" % st.id)

    # ---- 手动登录 QRZ ----
    def _manual_login(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请选择一个账号")
            return
        iid = sel[0]
        if not iid.startswith("acct_"):
            messagebox.showwarning("提示", "请选择 QRZ 账号")
            return
        acc = self.config.get_account(iid[len("acct_"):])
        if not acc:
            return
        info("即将打开浏览器登录 QRZ（账号 %s）..." % (acc.name or acc.qrz_username))
        self._after_msg("即将打开浏览器登录 QRZ。\n\n请留意新弹出的浏览器窗口，完成登录后本程序会自动确认。\n"
                        "如果长时间停在主页没加载，请切换到【同步日志】页看具体进度。")
        self._run_bg(lambda: self._do_manual_login(acc))

    def _do_manual_login(self, acc):
        uploader = QrzUploader(acc, self.config.profile_dir_for(acc.id), logfn=info)
        try:
            uploader.open_for_manual_login(minutes=5)
            self.after(0, lambda: messagebox.showinfo("登录成功", "QRZ 登录成功，会话已保存。下次会自动复用。"))
        except QrzLoginFailed as e:
            self.after(0, lambda: messagebox.showerror("登录失败", "%s" % e))
        except QrzNeedsLogin as e:
            self.after(0, lambda: messagebox.showwarning("未登录", "登录未完成：%s" % e))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("错误", "登录出错：%s" % e))

    def _check_login_status(self):
        sel = self.tree.selection()
        if not sel or not sel[0].startswith("acct_"):
            messagebox.showwarning("提示", "请先选择一个 QRZ 账号")
            return
        acc = self.config.get_account(sel[0][len("acct_"):])
        if not acc:
            return
        info("正在检测账号 '%s' 的 QRZ 登录状态 ..." % (acc.name or acc.qrz_username))
        self._after_msg("正在检测登录状态并打开浏览器，请稍候 ...（详情看【同步日志】页）")
        self._run_bg(lambda: self._do_check_login(acc))

    def _do_check_login(self, acc):
        uploader = QrzUploader(acc, self.config.profile_dir_for(acc.id), logfn=info)
        try:
            uploader.open()          # 打开 Logbook，未登录会抛 QrzNeedsLogin
            self.after(0, lambda: messagebox.showinfo("已登录", "账号 '%s' 已登录 QRZ，会话有效。" % (acc.name or acc.qrz_username)))
        except QrzNeedsLogin as e:
            self.after(0, lambda: messagebox.showwarning("未登录", "账号未登录 QRZ：%s\n请点“手动登录 QRZ”登录一次。" % e))
        except QrzLoginFailed as e:
            self.after(0, lambda: messagebox.showerror("登录失败", "%s" % e))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("错误", "检测登录出错：%s" % e))
        finally:
            try:
                uploader.close()
            except Exception:
                pass

    # ---- 从 CloudLog 检测台站 ----
    def _detect_station(self):
        if self._current_kind != "station":
            self._after_msg("请先在左侧选中一个台站。", "warn")
            return
        cld_url = self._vars.get("cloudlog_url", tk.StringVar()).get().strip()
        api_key = self._vars.get("api_key", tk.StringVar()).get().strip()
        if not cld_url or not api_key:
            self._after_msg("请先填写 CloudLog 地址 和 API Key，再点检测。", "warn")
            return
        info("正在从 CloudLog 检测台站（%s）..." % cld_url)
        self._after_msg("正在从 CloudLog 检测台站...（详情看【同步日志】）", "info")
        self._run_bg(lambda: self._do_detect_station(cld_url, api_key))

    def _do_detect_station(self, cld_url, api_key):
        try:
            client = CloudlogClient(cld_url, api_key)
            sts = client.station_info()
        except Exception as e:
            self._after_msg("检测失败：%s" % e, "error")
            return
        if not sts:
            self._after_msg("该 CloudLog 下没有台站。", "warn")
            return

        def _fill():
            first = sts[0]
            self._vars.get("callsign", tk.StringVar()).set(str(first.get("station_callsign", "")))
            self._vars.get("station_profile_id", tk.StringVar()).set(str(first.get("station_id", "")))
            lines = ["该账号下有以下台站："]
            for s in sts:
                lines.append("  %s (station_id=%s, 网格=%s, %s)" % (
                    s.get("station_callsign"), s.get("station_id"),
                    s.get("station_gridsquare"), s.get("station_country")))
            msg = "检测到 %d 个台站，已填入第 1 个：%s\n\n%s\n请点【保存修改】生效。" % (
                len(sts), first.get("station_callsign"), "\n".join(lines))
            self._after_msg(msg, "info")
            if len(sts) > 1:
                info("共 %d 个台站，已填入 %s；如需其它台站请手动改呼号", len(sts), first.get("station_callsign"))
        try:
            self.after(0, _fill)
        except Exception:
            _fill()

    # ================== 运行设置 ==================
    def _build_settings_tab(self, nb):
        frame = ttk.Frame(nb, padding=12)
        nb.add(frame, text="运行设置")
        f = ttk.Frame(frame)
        f.pack(fill="both", expand=True)
        r = 0
        entries = [
            ("同步间隔(分钟)", "interval_minutes", 60),
            ("一次上传全部(不分批)", "single_upload", True),
            ("每批条数(上项关闭时用)", "batch_size", 200),
            ("CloudLog 默认地址", "cloudlog_default_url", ""),
            ("浏览器(默认)", "browser_binary_default", "edge"),
            ("无头模式(默认)", "headless_default", False),
            ("启动时立即同步", "sync_at_startup", True),
            ("出错保留浏览器", "keep_browser_open", False),
            ("日志级别", "log_level", "INFO"),
        ]
        self._setting_vars = {}
        for label, key, default in entries:
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="e", pady=6, padx=4)
            cur = getattr(self.config.settings, key, default)
            if isinstance(cur, bool):
                v = tk.BooleanVar(value=bool(cur))
                ttk.Checkbutton(f, variable=v).grid(row=r, column=1, sticky="w", pady=6)
            else:
                if key == "browser_binary_default":
                    v = tk.StringVar(value=cur)
                    ttk.Combobox(f, textvariable=v, values=("edge", "chrome"), state="readonly", width=20).grid(row=r, column=1, sticky="w")
                elif key == "log_level":
                    v = tk.StringVar(value=cur)
                    ttk.Combobox(f, textvariable=v, values=("DEBUG", "INFO", "WARNING", "ERROR"), state="readonly", width=20).grid(row=r, column=1, sticky="w")
                else:
                    v = tk.StringVar(value=str(cur))
                    ttk.Entry(f, textvariable=v, width=24).grid(row=r, column=1, sticky="w")
            self._setting_vars[key] = v
            r += 1

        ttk.Button(frame, text="保存设置", command=self._save_settings).pack(pady=10)
        ttk.Button(frame, text="清空某个台站的上传状态", command=self._clear_state).pack(pady=4)

    def _load_settings_form(self):
        pass  # 变量在 build 时从 config 读取

    def _save_settings(self):
        s = self.config.settings
        s.interval_minutes = self._to_int(self._setting_vars["interval_minutes"], 60)
        s.batch_size = self._to_int(self._setting_vars["batch_size"], 200)
        s.single_upload = self._setting_vars.get("single_upload", tk.BooleanVar()).get()
        s.browser_binary_default = self._setting_vars["browser_binary_default"].get()
        s.headless_default = self._setting_vars["headless_default"].get()
        s.sync_at_startup = self._setting_vars["sync_at_startup"].get()
        s.keep_browser_open = self._setting_vars["keep_browser_open"].get()
        s.log_level = self._setting_vars["log_level"].get()
        s.cloudlog_default_url = self._setting_vars.get("cloudlog_default_url", tk.StringVar()).get().strip()
        self.config.save()
        info("设置已保存；同步间隔 %d 分钟", s.interval_minutes)
        self._update_status()

    def _to_int(self, var, default):
        try:
            return int(var.get())
        except Exception:
            return default

    def _clear_state(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在【账号与台站】里选择一个台站")
            return
        iid = sel[0]
        if not iid.startswith("st_"):
            messagebox.showwarning("提示", "请选择台站")
            return
        sid = iid[len("st_"):]
        if messagebox.askyesno("确认", "清空该台站已上传状态？下次同步会重新上传（QRZ 会自动去重）。"):
            self.state.clear(sid)
            info("已清空台站 %s 的上传状态", sid)

    # ================== 同步日志 ==================
    def _build_log_tab(self, nb):
        frame = ttk.Frame(nb)
        nb.add(frame, text="同步日志")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=4, pady=4)
        ttk.Button(bar, text="导出日志", command=self._export_log).pack(side="left", padx=3)
        ttk.Button(bar, text="清空界面日志", command=self._clear_log_view).pack(side="left", padx=3)
        self.log_text = tk.Text(frame, wrap="none", state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)
        # 滚动条
        sb = ttk.Scrollbar(frame, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=sb.set)
        self._log_text = self.log_text

    def _export_log(self):
        """把当前界面日志导出到文件。"""
        content = "".join(self.log_text.get("1.0", "end") or [])
        path = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile="sync.log")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            info("日志已导出到 %s", path)
            self._after_msg("日志已导出到：\n%s" % path, "info")
        except Exception as e:
            self._after_msg("导出失败：%s" % e, "error")

    def _clear_log_view(self):
        try:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.config(state="disabled")
        except Exception:
            pass

    def _after_msg(self, msg, kind="info"):
        """安全地在主线程弹出提示框。kind: info / warn / error"""
        def _show():
            try:
                if kind == "error":
                    messagebox.showerror("提示", msg)
                elif kind == "warn":
                    messagebox.showwarning("提示", msg)
                else:
                    messagebox.showinfo("提示", msg)
            except Exception:
                pass
        try:
            self.after(0, _show)
        except Exception:
            _show()

    def _append_log(self, msg):
        try:
            def go():
                self._log_text.config(state="normal")
                self._log_text.insert("end", msg + "\n")
                self._log_text.see("end")
                self._log_text.config(state="disabled")
            # 尽量在主线程执行
            try:
                if threading.current_thread() is threading.main_thread():
                    go()
                else:
                    self.after(0, go)
            except Exception:
                go()
        except Exception:
            pass

    # ================== 控制操作 ==================
    def _do_sync(self):
        self.status_var.set("正在同步 ...")
        info("收到手动同步请求。")
        def _run():
            try:
                ok, details = self.scheduler.run_now()
                try:
                    self.after(0, lambda: self.status_var.set(
                        "同步完成：" + ("全部成功" if ok else "有失败")))
                except Exception:
                    pass
                summary = "同步完成%s\n\n" % ("（全部成功）" if ok else "（存在失败）")
                summary += "\n".join(details) if details else "（没有启用台站被处理）"
                self._after_msg(summary, "info")
            except Exception as e:
                error("同步执行异常: %s", e)
                self._after_msg("同步异常：%s" % e, "error")
            finally:
                self._update_status()
        self._run_bg(_run)

    def _start_sched(self):
        self.scheduler.start()
        self._update_status()

    def _stop_sched(self):
        self.scheduler.stop()
        self._update_status()

    def _run_bg(self, fn):
        threading.Thread(target=self._guarded, args=(fn,), daemon=True).start()

    def _guarded(self, fn):
        try:
            fn()
        except Exception as e:
            error("后台任务异常: %s", e)
        finally:
            self._update_status()

    def _update_status(self):
        sched = "运行中" if self.scheduler.running else "已停止"
        self.status_var.set("状态: 定时%s | 账号 %d | 台站 %d" % (
            sched, len(self.config.accounts), len(self.config.stations)))

    # ================== 开机自启 ==================
    def _is_autostart_enabled(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY)
            try:
                winreg.QueryValueEx(key, AUTOSTART_NAME)
                return True
            except OSError:
                return False
            finally:
                key.Close()
        except Exception:
            return False

    def _toggle_autostart(self):
        exe = self._get_exe_path()
        if self.autostart_var.get():
            self._set_autostart(True, exe)
            info("已开启开机自启动: %s", exe)
        else:
            self._set_autostart(False, exe)
            info("已关闭开机自启动")

    def _set_autostart(self, enable, exe):
        try:
            import winreg
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY)
            if enable:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, '"%s" --autostart' % exe)
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except OSError:
                    pass
            key.Close()
        except Exception as e:
            messagebox.showerror("错误", "设置开机自启失败：%s" % e)

    def _get_exe_path(self):
        if getattr(__import__('sys'), 'frozen', False):
            return __import__('sys').executable
        return os.path.abspath(__import__('os').path.join(__import__('os').path.dirname(__file__), '..', 'main.py'))

    # ================== 关闭 ==================
    def _on_close(self):
        self._log_listener.stop()
        self.scheduler.stop()
        self.config.save()
        self.destroy()
