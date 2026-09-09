"""QRZ Logbook 上传引擎（Playwright + 打包的 Chromium）。

不依赖系统浏览器：Playwright 用随 EXE 一起打包的 Chromium 工作。
  * 持久化 user-data-dir 登录一次 QRZ，之后复用会话（避免验证码/风控）。
  * 打开 Logbook -> Import/Export Tools -> Import from ADI File 上传 ADIF。

对外接口（供 worker/gui 使用）保持不变：
  * QrzUploader(account, profile_dir, logfn, keep_browser_open)
  * open() / upload_path(page, adif_path) / close()
  * open_for_manual_login(minutes)
  * QrzNeedsLogin, QrzLoginFailed
"""

import glob
import os
import sys
import time

from playwright.sync_api import sync_playwright

QRZ_LOGIN = "https://www.qrz.com/login"
QRZ_LOGBOOK = "https://logbook.qrz.com/"
QRZ_IMPORT = "https://logbook.qrz.com/lbimport"


class QrzNeedsLogin(Exception):
    """当前 profile 尚未登录 QRZ，需要用户手动登录一次。"""


class QrzLoginFailed(Exception):
    """登录失败（密码错误 / 账号异常 / 无法访问 qrz.com 等）。"""


class QrzUploadError(Exception):
    pass


def _chromium_exe():
    """返回打包进 EXE 的 Chromium chrome.exe 路径；找不到返回 None。"""
    base = getattr(sys, '_MEIPASS', None)
    candidates = []
    if base:
        candidates.append(os.path.join(base, 'chromium'))
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   'assets', 'chromium'))
    candidates.append(os.path.join(os.getcwd(), 'assets', 'chromium'))
    for c in candidates:
        if os.path.isdir(c):
            for exe in glob.glob(os.path.join(c, '**', 'chrome.exe'), recursive=True):
                if os.path.exists(exe):
                    return exe
    return _bundled_chromium_via_env()


def _bundled_chromium_via_env():
    # 兼容：若通过 PLAYWRIGHT_BROWSERS_PATH 指向打包目录
    base = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if base and os.path.isdir(base):
        for exe in glob.glob(os.path.join(base, '**', 'chrome.exe'), recursive=True):
            if os.path.exists(exe):
                return exe
    return None


class QrzUploader:
    def __init__(self, account, profile_dir, logfn=None, keep_browser_open=False):
        self.account = account
        self.profile_dir = profile_dir
        self.logfn = logfn or (lambda *a, **k: None)
        self.keep = keep_browser_open
        self.pw = None
        self.context = None
        self.page = None

    # ------------------------------------------------------------------
    def _log(self, msg):
        self.logfn(msg)

    def _start_pw(self):
        if self.pw is None:
            self.pw = sync_playwright().start()

    def _launch(self):
        """启动持久化 Chromium 上下文。返回 page。"""
        self._start_pw()
        if not os.path.isdir(self.profile_dir):
            os.makedirs(self.profile_dir, exist_ok=True)
        exe = _chromium_exe()
        headless = bool(self.account.headless)
        try:
            kwargs = dict(user_data_dir=self.profile_dir, headless=headless,
                          args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                          viewport={"width": 1280, "height": 900})
            if exe:
                kwargs["executable_path"] = exe
            self.context = self.pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            raise QrzUploadError("启动浏览器失败: %s（Chromium 路径=%s）" % (e, exe or "未找到"))
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    # ------------------------------------------------------------------
    def _goto(self, page, url, label):
        self._log("[QRZ] 正在打开%s：%s ..." % (label, url))
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            raise QrzLoginFailed("加载 %s 失败：%s" % (label, e))
        try:
            self._log("[QRZ] 已打开%s：%s（标题=%s）" % (label, page.url, page.title()))
        except Exception:
            pass

    def _body_text(self):
        try:
            return self.page.inner_text("body") or ''
        except Exception:
            return ''

    def _on_login_page(self):
        try:
            url = (self.page.url or '').lower()
            if '/login' in url:
                return True
        except Exception:
            pass
        try:
            return self.page.locator("input[type=password]").count() > 0
        except Exception:
            return False

    def _login_error(self):
        if not self._on_login_page():
            return ''
        text = self._body_text().lower()
        markers = ['incorrect', 'invalid', 'wrong', 'not correct', 'could not', 'failed',
                   'check your', 'does not match', 'unknown user', 'enter a valid', 'denied',
                   'we could not', 'no such user', 'cannot log', 'unable to log']
        for m in markers:
            if m in text:
                i = text.index(m)
                return text[max(0, i - 90): i + 130].strip()
        return ''

    def _is_logged_in(self):
        try:
            url = (self.page.url or '').lower()
        except Exception:
            url = ''
        # 已站在 QRZ Logbook 页（该页本身需要登录才能访问）=> 视为已登录
        if 'logbook.qrz.com' in url and '/login' not in url:
            return True
        # 登录页 / 存在密码输入框 => 未登录
        if '/login' in url or self._on_login_page():
            return False
        # 已离开登录页且无登录表单 => 视为已登录
        if '/login' not in url and not self._on_login_page():
            return True
        return False

    def _wait_logged_in(self, seconds=15):
        """等待页面进入已登录/或登录页状态，最多 seconds 秒。"""
        end = time.time() + seconds
        while time.time() < end:
            if self._is_logged_in() or self._on_login_page():
                return
            try:
                self.page.wait_for_timeout(500)
            except Exception:
                time.sleep(0.5)
        return

    # ------------------------------------------------------------------
    def open_for_manual_login(self, minutes=5):
        """打开浏览器让用户登录 QRZ 一次，之后会话保存到 profile 目录。"""
        try:
            page = self._launch()
        except QrzUploadError as e:
            raise QrzLoginFailed(str(e))
        try:
            self._goto(page, QRZ_LOGIN, "QRZ 登录页")
            self._log("[QRZ] 请在浏览器里完成登录，完成后本程序会自动确认。")
            waited = 0
            while waited < minutes * 60:
                time.sleep(3)
                waited += 3
                if self._on_login_page():
                    err = self._login_error()
                    if err:
                        raise QrzLoginFailed("QRZ 登录失败：%s" % err)
                if self._is_logged_in():
                    self._log("[QRZ] 登录成功，会话已保存。")
                    return True
                if waited % 30 == 0:
                    self._log("[QRZ] 仍在等待您完成登录 ...（%d 秒）" % waited)
            raise QrzNeedsLogin("等待手动登录超时（%d 分钟）" % minutes)
        finally:
            self.close()

    # ------------------------------------------------------------------
    def open(self):
        """启动浏览器并确保已登录。返回 page。之后可多次 upload_path。"""
        page = self._launch()
        self._goto(page, QRZ_LOGBOOK, "QRZ Logbook")
        self._wait_logged_in(seconds=12)
        if not self._is_logged_in():
            if self._on_login_page():
                if self.account.auto_login and self._auto_login():
                    self._log("[QRZ] 已自动登录。")
                elif self.account.auto_login:
                    raise QrzLoginFailed("自动登录失败，请先手动登录一次")
                else:
                    raise QrzNeedsLogin("该账号尚未登录 QRZ，请先“手动登录 QRZ”保存会话")
            else:
                # 不在登录页也未识别为已登录（可能仍在加载）
                raise QrzNeedsLogin("未能确认登录状态（当前页面=%s）。请先“手动登录 QRZ”保存会话。"
                                    % self._safe_url(page))
        self._log("[QRZ] 浏览器已就绪并登录。")
        return self.page

    def _auto_login(self):
        username = self.account.qrz_username
        password = self.account.get_password()
        if not username or not password:
            raise QrzNeedsLogin("账号未配置自动登录（无用户名/密码），请手动登录一次")
        self._log("[QRZ] 正在自动登录 ...")
        page = self.page
        try:
            page.locator("input[name=username], input[id=username], input[type=text]").first.fill(username)
        except Exception:
            raise QrzNeedsLogin("找不到登录用户名输入框")
        try:
            page.locator("input[type=password]").first.fill(password)
        except Exception:
            raise QrzNeedsLogin("找不到密码输入框")
        try:
            page.locator("input[type=submit], button[type=submit], button:has-text('Login'), "
                          "input[value='Login']").first.click()
        except Exception:
            page.keyboard.press("Enter")
        time.sleep(6)
        return self._is_logged_in()

    # ------------------------------------------------------------------
    def upload_path(self, page, adif_path, callsign=""):
        """在已打开的 page 上上传一个 ADIF 文件。返回 (ok, message)。"""
        if not os.path.exists(adif_path):
            return False, "ADIF 文件不存在: %s" % adif_path
        try:
            msg = self._do_import(page, adif_path)
            ok = ("success" in msg.lower()) or ("error" not in msg.lower())
            return ok, msg
        except Exception as e:
            return False, "上传出错: %s" % e

    def _click_first(self, page, texts, timeout_ms=8000):
        """按多个候选文本依次尝试点击，命中即返回 True。"""
        for t in texts:
            try:
                loc = page.get_by_text(t, exact=False).first
                if loc.count() > 0:
                    loc.click(timeout=timeout_ms)
                    self._log("[QRZ] 已点击：%s" % t)
                    return True
            except Exception:
                continue
        return False

    def _check_email_option(self, page):
        """用真实鼠标点击勾选 QRZ 上传页的‘Send me a report by e-mail...’。"""
        texts = ["send me a report by e-mail", "send me a report by email",
                 "send me a report by e mail", "send me a report by e-mail after",
                 "e-mail after this file is processed", "email after this file is processed",
                 "send me a report", "after this file is processed"]
        # 1) 依次用真实点击点 label / 含文本的元素（能触发自定义勾选框）
        for t in texts:
            try:
                loc = page.get_by_text(t, exact=False).first
                if loc.count() > 0:
                    loc.click(timeout=3000)
                    self._log("[QRZ] 已点击邮件通知选项：%s" % t)
                    return
            except Exception:
                continue
        # 2) 找 checkbox 元素并勾选（Playwright check 会真实点击）
        try:
            cbs = page.locator("input[type=checkbox]")
            for i in range(cbs.count()):
                cb = cbs.nth(i)
                try:
                    label_text = cb.evaluate(
                        "el => ((el.closest('label') ? el.closest('label').innerText : '') + ' ' +"
                        " (el.labels && el.labels.length ? el.labels[0].innerText : '')).toLowerCase()")
                except Exception:
                    label_text = ""
                if any(k in label_text for k in ("e-mail", "email", "mail", "send me", "notif", "报告", "邮件")):
                    if not cb.is_checked():
                        cb.check(timeout=4000)
                        self._log("[QRZ] 已勾选（checkbox）：上传完成邮件通知")
                    else:
                        self._log("[QRZ] 邮件通知已是勾选状态")
                    return
        except Exception:
            pass
        self._log("[QRZ] 未找到邮件通知勾选框（若导入页没有该选项可忽略）")

    def _has_file_input(self, page):
        try:
            return page.locator("input[type=file]").count() > 0
        except Exception:
            return False

    def _wait_for_text(self, page, text, secs=10):
        try:
            page.get_by_text(text).first.wait_for(state="visible", timeout=secs * 1000)
            return True
        except Exception:
            return False

    def _click_gear(self, page):
        """点击右上角设置齿轮图标，打开 Refresh Settings 菜单。"""
        sel = ("[title*='settings' i], [aria-label*='settings' i], [class*='settings' i], "
               "[class*='gear' i], [class*='fa-cog' i], [class*='fa-gear' i], "
               "[class*='wrench' i], [title*='options' i], [aria-label*='options' i], "
               "[class*='cog' i], [title*='tools' i]")
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=6000)
                self._log("[QRZ] 已点击设置齿轮，打开导入菜单")
                return True
        except Exception:
            pass
        # 回退：点含 Import 的链接/按钮
        return self._click_first(page, ["Import ADI File", "Import from ADI", "Import ADI", "Import"], 4000)

    def _open_import_dialog(self, page):
        """打开 'Import from ADI File' 对话框。返回 True 表示成功。"""
        # 先直接试点 Import ADI File（菜单可能已展开）
        if self._click_first(page, ["Import ADI File", "Import from ADI File", "Import ADI"], 4000):
            page.wait_for_timeout(800)
            return self._has_file_input(page) or self._wait_for_text(page, "Import from ADI File", 5)
        # 否则点齿轮打开菜单，再点 Import ADI File
        self._click_gear(page)
        page.wait_for_timeout(1000)
        self._click_first(page, ["Import ADI File", "Import from ADI File", "Import ADI",
                                 "Import from ADI"], 6000)
        page.wait_for_timeout(1000)
        return self._has_file_input(page) or self._wait_for_text(page, "Import from ADI File", 5)

    def _do_import(self, page, adif_path):
        # 进入可用的 Logbook 页面（不要直接跳 /lbimport，该链接已 404）
        try:
            page.goto(QRZ_LOGBOOK, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)   # 等待 Logbook UI 渲染
        except Exception as e:
            raise QrzUploadError("打开 QRZ Logbook 失败：%s" % e)

        # 1) 打开 'Import from ADI File' 对话框
        if not self._open_import_dialog(page):
            raise QrzUploadError("未能打开 QRZ 导入对话框（找不到设置齿轮 / Import ADI File）")
        page.wait_for_timeout(1200)

        # 2) 文件输入框（对应 Choose File...，用 set_input_files 直接注入，不弹系统文件框）
        file_input = None
        for _ in range(6):
            fi = page.locator("input[type=file]")
            if fi.count() > 0:
                file_input = fi.first
                break
            page.wait_for_timeout(1000)
        if file_input is None:
            raise QrzUploadError("未找到 ADIF 文件输入框（input[type=file]），导入弹窗可能未打开。")
        file_input.set_input_files(os.path.abspath(adif_path))
        self._log("[QRZ] 已注入文件: %s" % os.path.basename(adif_path))
        page.wait_for_timeout(800)

        # 3) 勾选“Send me a report by e-mail...”邮件通知
        self._check_email_option(page)

        # 4) 点击 Import ADI File 提交（优先按钮）
        done = False
        for sel in ("button:has-text('Import ADI File')", "button:has-text('Import ADI')",
                    "button:has-text('Import')", "input[type=submit]",
                    "a:has-text('Import ADI File')"):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=6000)
                    self._log("[QRZ] 已点击导入提交（%s）" % sel)
                    done = True
                    break
            except Exception:
                continue
        if not done:
            if not self._click_first(page, ["Import ADI File", "Import ADI", "Import"], 6000):
                raise QrzUploadError("未找到导入提交按钮")

        # 闭环检测：Continue 按钮出现（可靠可检测）或成功文本 -> 判成功并立即跳出
        result = ""
        end = time.time() + 35
        success = False
        while time.time() < end:
            page.wait_for_timeout(1500)
            # 主信号：Continue 按钮（已验证能检测到）
            try:
                if page.get_by_text("Continue").first.count() > 0:
                    success = True
                    self._log("[QRZ] 检测到上传成功（Continue 弹窗），QRZ 将后台处理这些记录")
                    result = self._read_result(page) or "已上传成功"
                    break
            except Exception:
                pass
            # 备选：成功/错误文本
            low = self._read_result(page).lower()
            if "uploaded successfully" in low or "will now process" in low or "successfully" in low:
                success = True
                self._log("[QRZ] 检测到上传成功，QRZ 将后台处理这些记录")
                result = self._read_result(page)
                break
            if any(k in low for k in ("error", "failed", "not found", "no file",
                                      "invalid", "could not", "exception")):
                self._log("[QRZ] 检测到错误提示：%s" % low[:140])
                break
        # 尽力点 Continue 关闭成功弹窗（点不掉也不影响结果，浏览器由程序关闭）
        if success:
            try:
                page.locator("button:has-text('Continue'), .btn:has-text('Continue'), "
                             "input[value='Continue']").first.click(timeout=4000)
                self._log("[QRZ] 已点击 Continue，关闭成功弹窗")
            except Exception:
                self._log("[QRZ] Continue 未能点击（不影响结果，浏览器将由程序关闭）")
        if not result:
            result = self._read_result(page)
        return result

    def _read_result(self, page):
        try:
            body = page.inner_text("body") or ''
        except Exception:
            return ''
        for key in ("success", "Success", "complete", "Complete", "processed", "Processed",
                    "imported", "Imported", "added", "Added", "Error", "error",
                    "failed", "Failed", "duplicate", "Duplicate", "in progress",
                    "In Progress", "submitted", "Submitted", "queued", "Queued"):
            if key in body:
                i = body.index(key)
                return body[max(0, i - 90): i + 170].replace("\n", " ").strip()
        return body[:220].replace("\n", " ").strip()

    # ------------------------------------------------------------------
    def close(self):
        if self.keep:
            self._log("[QRZ] keep_browser_open 已开启，浏览器保持打开便于排查。")
            return
        # 先优雅关闭页面，再关上下文，减少 Chromium"未正确关闭"提示
        try:
            if self.page is not None:
                try:
                    self.page.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        try:
            if self.pw is not None:
                self.pw.stop()
        except Exception:
            pass
        self.context = None
        self.page = None
        self.pw = None
