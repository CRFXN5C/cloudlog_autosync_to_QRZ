"""版本管理（唯一版本来源）。

VERSION 为当前版本号，其它模块从这里读取。
bump() 可递增版本并写回本文件，便于每次 release 区分版本号。
"""

import re

APP_NAME = "QRZCloudlogSync"
VERSION = "1.1.12"
_THIS = __file__


def _parts(v=None):
    v = v or VERSION
    return [int(x) for x in str(v).strip().split(".")]


def to_str(parts):
    return ".".join(str(p) for p in parts)


def bump(part="patch"):
    """递增版本号并写回本文件。返回新版本字符串。

    part: 'patch' | 'minor' | 'major'
    """
    p = _parts()
    if part == "major":
        p[0] += 1
        p[1] = 0
        p[2] = 0
    elif part == "minor":
        p[1] += 1
        p[2] = 0
    else:
        p[2] += 1
    new = to_str(p)
    with open(_THIS, "r", encoding="utf-8") as f:
        txt = f.read()
    txt = re.sub(r'VERSION = "[^"]*"', 'VERSION = "%s"' % new, txt, count=1)
    with open(_THIS, "w", encoding="utf-8") as f:
        f.write(txt)
    global VERSION
    VERSION = new
    return new


def get_version():
    return VERSION
