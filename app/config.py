"""配置与凭据存储。

配置保存为 JSON，默认放在 %APPDATA%\\QRZCloudlogSync\\config.json。
敏感字段（QRZ 密码、CloudLog API key）用 Windows DPAPI（CryptProtectData）加密后 base64 存储，
仅当前 Windows 用户能解密，避免明文落盘。
"""

import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List

APP_NAME = "QRZCloudlogSync"


# ---------------------------------------------------------------------------
# DPAPI 加解密（仅 Windows 生效；非 Windows 退回 base64 明文编码）
# ---------------------------------------------------------------------------
class _CryptBlob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_protect(data: bytes) -> bytes:
    """返回 base64，可解密。"""
    if os.name != 'nt':
        return base64.b64encode(data)
    import ctypes.windll  # noqa: F401
    blob_in = _CryptBlob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                                 ctypes.POINTER(ctypes.c_byte)))
    blob_out = _CryptBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "QRZCloudlogSync",
                                                None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError()
    try:
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return base64.b64encode(out)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(b64: bytes) -> bytes:
    if os.name != 'nt':
        return base64.b64decode(b64)
    enc = base64.b64decode(b64)
    blob_in = _CryptBlob(len(enc), ctypes.cast(ctypes.create_string_buffer(enc, len(enc)),
                                                ctypes.POINTER(ctypes.c_byte)))
    blob_out = _CryptBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None,
                                                  None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    try:
        return _dpapi_protect(plain.encode('utf-8')).decode('ascii')
    except Exception:
        # 加密失败则回退为明文前缀标记，尽量不崩
        return 'PLAIN:' + base64.b64encode(plain.encode('utf-8')).decode('ascii')


def decrypt(encoded: str) -> str:
    if not encoded:
        return ""
    if encoded.startswith('PLAIN:'):
        return base64.b64decode(encoded[len('PLAIN:'):]).decode('utf-8')
    try:
        return _dpapi_unprotect(encoded.encode('ascii')).decode('utf-8')
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Account:
    """一个 QRZ 登录账号，可挂多个台站。"""
    id: str = ""
    name: str = ""
    qrz_username: str = ""
    qrz_password_enc: str = ""       # DPAPI 密文
    auto_login: bool = False          # 是否由程序自动填写密码登录（否则手动登录一次）
    browser: str = "edge"             # edge / chrome
    headless: bool = False            # 服务器无桌面时可开无头
    driver_path: str = ""             # 可选：指定驱动路径
    enabled: bool = True

    def set_password(self, plain: str):
        self.qrz_password_enc = encrypt(plain)

    def get_password(self) -> str:
        return decrypt(self.qrz_password_enc)


@dataclass
class Station:
    """一个 CloudLog 台站（一个呼号），对应上传到某个 QRZ 账号。"""
    id: str = ""
    account_id: str = ""
    name: str = ""
    callsign: str = ""                # 例如 W1AW
    cloudlog_url: str = ""            # 例如 https://your-instance.cloudlog.example.com/
    api_key_enc: str = ""             # DPAPI 密文
    station_profile_id: str = ""      # CloudLog station_id，可空
    enabled: bool = True
    last_sync: str = ""               # 上次同步时间(UTC ISO)

    def set_api_key(self, plain: str):
        self.api_key_enc = encrypt(plain)

    def get_api_key(self) -> str:
        return decrypt(self.api_key_enc)


@dataclass
class Settings:
    interval_minutes: int = 60
    batch_size: int = 200
    sync_at_startup: bool = True
    keep_browser_open: bool = False   # 出错时保留浏览器便于排查
    log_level: str = "INFO"
    browser_binary_default: str = "edge"
    headless_default: bool = False
    max_retries: int = 2
    cloudlog_default_url: str = ""    # 新建台站默认 CloudLog 地址（留空，由用户填写）
    single_upload: bool = True   # 一次把全部新增 QSO 上传成一个 ADIF（QRZ 异步处理）


class Config:
    """整份配置的读写与持久化。"""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')),
                                    APP_NAME)
        self.data_dir = data_dir
        self.config_path = os.path.join(data_dir, "config.json")
        self.state_dir = os.path.join(data_dir, "state")
        self.profiles_dir = os.path.join(data_dir, "profiles")
        self.log_dir = os.path.join(data_dir, "logs")
        self._ensure_dirs()
        self.accounts: List[Account] = []
        self.stations: List[Station] = []
        self.settings = Settings()

    def _ensure_dirs(self):
        for d in (self.data_dir, self.state_dir, self.profiles_dir, self.log_dir):
            os.makedirs(d, exist_ok=True)

    # ---- 持久化 ----
    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for a in data.get('accounts', []):
                    acc = Account(**{k: a.get(k, '') for k in
                                     ('id', 'name', 'qrz_username', 'qrz_password_enc',
                                      'auto_login', 'browser', 'headless', 'driver_path', 'enabled')})
                    self.accounts.append(acc)
                for s in data.get('stations', []):
                    st = Station(**{k: s.get(k, '') for k in
                                    ('id', 'account_id', 'name', 'callsign', 'cloudlog_url',
                                     'api_key_enc', 'station_profile_id', 'enabled', 'last_sync')})
                    self.stations.append(st)
                st = data.get('settings', {})
                for k, v in st.items():
                    if hasattr(self.settings, k):
                        setattr(self.settings, k, v)
            except Exception:
                pass

    def save(self):
        self._ensure_dirs()
        data = {
            'accounts': [asdict(a) for a in self.accounts],
            'stations': [asdict(s) for s in self.stations],
            'settings': asdict(self.settings),
        }
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ---- 便捷查询 ----
    def get_account(self, account_id: str):
        for a in self.accounts:
            if a.id == account_id:
                return a
        return None

    def get_station(self, station_id: str):
        for s in self.stations:
            if s.id == station_id:
                return s
        return None

    def stations_of(self, account_id: str):
        return [s for s in self.stations if s.account_id == account_id]

    def profile_dir_for(self, account_id: str):
        return os.path.join(self.profiles_dir, "acct_" + account_id)

    def state_file_for(self, station_id: str):
        return os.path.join(self.state_dir, "station_" + station_id + ".json")
