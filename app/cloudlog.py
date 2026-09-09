"""CloudLog (Wavelog) 客户端。

使用只读 API key 完成：
  * station_info  - 枚举台站（呼号、station_id、网格等）
  * statistics    - 台站统计
  * backup/adif   - 触发并直接返回整本 ADIF 文本（Wavelog 直接返回正文）
"""

import json
import re
import time
from urllib.parse import urljoin

import requests

from .adif import parse_adif

# Cloudflare 代理错误码：源站不可达/超时等，通常是临时故障，可重试
_CLOUDFLARE_ERR = {520, 521, 522, 523, 524, 525, 526, 527}


class CloudlogError(Exception):
    pass


class CloudlogClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 40):
        base_url = (base_url or '').strip().rstrip('/')
        if not base_url:
            raise CloudlogError("CloudLog 地址不能为空")
        if not base_url.startswith('http'):
            base_url = 'https://' + base_url
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json, text/*'})

    def _get(self, path: str, timeout: int = None, retries: int = 3) -> requests.Response:
        url = urljoin(self.base_url + '/', path.lstrip('/'))
        t = timeout or self.timeout
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, timeout=t)
                if resp.status_code in _CLOUDFLARE_ERR and attempt < retries:
                    self._log_retry(path, resp.status_code, attempt, retries)
                    time.sleep(2 * attempt)
                    continue
                if resp.status_code >= 400:
                    if resp.status_code in _CLOUDFLARE_ERR:
                        raise CloudlogError(
                            "CloudLog 源站响应超时/不可达（Cloudflare 错误 %s）。"
                            "通常是 logbook 服务器临时故障或过载，稍等片刻重试，"
                            "或确认 %s 网站当前是否能访问。" % (resp.status_code, self.base_url))
                    raise CloudlogError("CloudLog 返回 %s: %s" % (resp.status_code, resp.text[:200]))
                return resp
            except (requests.RequestException) as e:
                last_err = e
                if attempt < retries:
                    self._log_retry(path, '网络异常', attempt, retries)
                    time.sleep(2 * attempt)
                    continue
        raise CloudlogError("CloudLog 请求失败（已重试 %d 次）：%s" % (retries, last_err))

    def _log_retry(self, path, code, attempt, retries):
        try:
            from .logger import warn
            warn("CloudLog 请求 %s 遇 %s（第 %d/%d 次），重试中...", path, code, attempt, retries)
        except Exception:
            pass

    # ---- 台站信息 ----
    def station_info(self) -> list:
        resp = self._get('index.php/api/station_info/%s' % self.api_key)
        try:
            data = resp.json()
        except ValueError:
            raise CloudlogError("station_info 返回不是 JSON: %s" % resp.text[:200])
        if isinstance(data, dict) and data.get('error'):
            raise CloudlogError("station_info 错误: %s" % data.get('error'))
        if isinstance(data, list):
            return data
        raise CloudlogError("station_info 返回格式异常")

    # ---- 统计 ----
    def statistics(self) -> dict:
        resp = self._get('index.php/api/statistics/%s' % self.api_key)
        try:
            return resp.json()
        except ValueError:
            raise CloudlogError("statistics 返回不是 JSON")

    # ---- 拉取整本 ADIF ----
    def fetch_adif_text(self) -> str:
        """触发备份并返回 ADIF 正文。

        Wavelog 的 /index.php/backup/adif/<key> 直接返回 ADIF 文本。
        若返回的是 HTML（传统 Cloudlog），会尝试解析其中备份文件链接再下载。
        """
        resp = self._get('index.php/backup/adif/%s' % self.api_key, timeout=120)
        text = resp.text

        # 直接是 ADIF：以 ADIF_VER 开头
        if '<ADIF_VER' in text.upper() or '<ADIF_VER' in text:
            return text

        # 传统 Cloudlog：返回成功页，含备份文件名链接 /backup/logbook_xxx.adi
        m = re.search(r'(logbook_\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}\.adi)', text)
        if m:
            fname = m.group(1)
            dl = self._get('backup/%s' % fname, timeout=180)
            return dl.text

        # 帮助性报错
        raise CloudlogError("未从 CloudLog 得到 ADIF（返回内容不以 <ADIF_VER 开头，且未找到备份文件）。"
                            "请确认 API key 有效且有 ADIF 导出权限。原始返回: %s" % text[:200])

    def fetch_adif(self):
        """返回 (header, records)。"""
        text = self.fetch_adif_text()
        return parse_adif(text)

    # ---- 便捷：直接导出成可上传 ADIF 字符串 ----
    def fetch_adif_merged(self):
        header, records = self.fetch_adif()
        return header, records
