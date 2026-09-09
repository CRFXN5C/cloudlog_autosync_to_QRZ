"""QRZ CloudLog 同步器 (QRZ CloudLog Sync)。

一个运行在 Windows 上的图形化工具：
  * 从 CloudLog (Wavelog) 通过 API key 拉取 ADIF 日志
  * 用浏览器（Selenium + Edge/Chrome）模拟上传到 QRZ Logbook
  * 支持多账号、多台站管理
  * 支持定时同步、可选开机自启动、运行日志落盘
"""

from .version import get_version, APP_NAME

__version__ = get_version()
APP_NAME = APP_NAME
