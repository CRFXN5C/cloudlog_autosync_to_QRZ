"""轻量 ADIF 解析 / 生成工具。

不依赖第三方库，按 ADIF 规范解析 `<TAG:length>value` 令牌。
支持：
  * parse_adif(text) -> (header, records)
  * build_adif(records, header=None) -> str
  * fingerprint(record) -> str   （用于本地去重）
"""

import re

# 字段标签： NAME:length 或 NAME:length:char 或 NAME:lengthchar
_TOKEN_RE = re.compile(r'^([A-Za-z0-9_]+):(\d+)(?:[A-Za-z])?$')
# 记录结束标记（大小写不敏感）
_EOR_RE = re.compile(r'<EOR>', re.IGNORECASE)
_EOH_RE = re.compile(r'<EOH>', re.IGNORECASE)


def _tokenize(segment):
    """把一个 ADIF 片段里的所有 <TAG:length>value 解析成 [(name, value), ...]。"""
    fields = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == '<':
            end = segment.find('>', i)
            if end == -1:
                break
            tag = segment[i + 1:end].strip()
            m = _TOKEN_RE.match(tag)
            if m:
                name = m.group(1).upper()
                length = int(m.group(2))
                # 值恰好是 length 个字符；若被截断则取实际可用部分
                value = segment[end + 1:end + 1 + length]
                fields.append((name, value))
                i = end + 1 + length
            else:
                # EOH / EOR / 其它无长度标记，忽略
                i = end + 1
        else:
            i += 1
    return fields


def parse_adif(text):
    """解析 ADIF 文本。

    返回 (header: dict, records: list[dict])。
    记录里字段名大写；同名冲突保留第一次出现的值。
    """
    if text is None:
        return {}, []
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    m = _EOH_RE.search(text)
    if m:
        header_text = text[:m.start()]
        body = text[m.end():]
    else:
        header_text = ''
        body = text

    header = {}
    for name, value in _tokenize(header_text):
        if name in ('EOH', 'EOR'):
            continue
        header.setdefault(name, value)

    records = []
    for raw in _EOR_RE.split(body):
        raw = raw.strip()
        if not raw:
            continue
        fields = _tokenize(raw)
        if not fields:
            continue
        rec = {}
        for name, value in fields:
            if name in ('EOH', 'EOR'):
                continue
            rec.setdefault(name, value)
        # 至少要有呼号或日期才算一条有效记录
        if 'CALL' in rec or 'QSO_DATE' in rec or 'QSO_DATE_OFF' in rec or 'STATION_CALLSIGN' in rec:
            records.append(rec)

    return header, records


def build_record(record, order=None):
    """把记录 dict 转成一段 ADIF 记录文本（不含 <EOR>）。"""
    out = []
    # 尽量保持字段稳定顺序，默认按字母顺序
    keys = list(record.keys())
    if order:
        keys = [k for k in order if k in record] + [k for k in keys if k not in order]
    for k in keys:
        v = record.get(k, '')
        if v is None:
            v = ''
        v = str(v)
        if v == '':
            # 字段无值就不写，避免影响 QRZ 解析
            continue
        out.append('<%s:%d>%s' % (k.upper(), len(v), v))
    return ''.join(out)


def build_adif(records, header=None):
    """把若干记录拼成完整 ADIF 文本（含 header 与 EOH、各记录后加 EOR）。"""
    if header is None:
        header = {}
    h = dict(header)
    h.setdefault('ADIF_VER', '3.1.0')
    h.setdefault('PROGRAMID', 'QRZCloudlogSync')
    h.setdefault('PROGRAMVERSION', '1.0')

    head = []
    for k in ('ADIF_VER', 'PROGRAMID', 'PROGRAMVERSION'):
        if k in h:
            v = str(h[k])
            head.append('<%s:%d>%s' % (k.upper(), len(v), v))
    # 其它 header 字段
    for k, v in h.items():
        if k.upper() in ('ADIF_VER', 'PROGRAMID', 'PROGRAMVERSION'):
            continue
        v = str(v)
        if v == '':
            continue
        head.append('<%s:%d>%s' % (k.upper(), len(v), v))

    lines = []
    lines.append(''.join(head))
    lines.append('<EOH>')
    for rec in records:
        lines.append(build_record(rec))
        lines.append('<EOR>')
    return '\n'.join(lines) + '\n'


def fingerprint(record):
    """生成用于本地去重的指纹。

    依据 QRZ 的去重逻辑（呼号+日期时间+波段+模式），这里用精确匹配避免重复上传。
    返回大写字符串，缺失字段用空串占位。
    """
    call = str(record.get('CALL', '')).upper()
    date = str(record.get('QSO_DATE', record.get('QSO_DATE_OFF', ''))).upper()
    time_on = str(record.get('TIME_ON', record.get('TIME_OFF', ''))).upper()
    band = str(record.get('BAND', '')).upper()
    mode = str(record.get('MODE', record.get('SUB_MODE', ''))).upper()
    return '|'.join([call, date, time_on, band, mode])


def count_records(text):
    """统计 ADIF 文本中的记录数（用于显示）。"""
    try:
        _, records = parse_adif(text)
        return len(records)
    except Exception:
        return 0
