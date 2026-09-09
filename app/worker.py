"""同步执行引擎。

对每个启用的台站：
  1. 从 CloudLog 拉取整本 ADIF
  2. 按呼号过滤 + 本地去重（只上传新 QSO）
  3. 分批生成 ADIF，用浏览器上传到对应 QRZ 账号
  4. 成功则记录指纹，避免重复
"""

import os
import tempfile
from datetime import datetime, timezone

from .adif import parse_adif, build_adif
from .cloudlog import CloudlogClient, CloudlogError
from .qrz import QrzUploader, QrzNeedsLogin
from .state import StateStore
from .logger import info, warn, error


class SyncEngine:
    def __init__(self, config):
        self.config = config
        self.state = StateStore(config.state_dir)
        # 供 UI 显示进度
        self.on_progress = None   # callback(text)

    def _log(self, msg):
        info(msg)

    def _progress(self, msg):
        if self.on_progress:
            try:
                self.on_progress(msg)
            except Exception:
                pass

    def _filter_by_callsign(self, records, callsign):
        if not callsign:
            return list(records)
        c = callsign.strip().upper()
        out = []
        for r in records:
            stem = r.get('STATION_CALLSIGN', '')
            if stem and stem.upper() == c:
                out.append(r)
            elif not stem:
                # 无台站呼号字段的记录保留（通常是单台站导出）
                out.append(r)
        return out

    def run_station(self, station, account):
        """同步一个台站。返回 (ok, summary)。"""
        name = station.callsign or station.name or station.id
        if not account:
            warn("[%s] 失败：没有一个 QRZ 账号与这个台站绑定（account_id=%s 无效）", name, station.account_id)
            return False, "台站 %s 没有所属账号" % name
        if not (station.cloudlog_url or '').strip():
            warn("[%s] 失败：未配置 CloudLog 地址", name)
            return False, "台站 %s 未配置 CloudLog 地址" % name
        if not (station.get_api_key() or '').strip():
            warn("[%s] 失败：未配置 CloudLog API Key", name)
            return False, "台站 %s 未配置 CloudLog API Key" % name

        # 1) 拉取 ADIF
        info("[%s] 开始同步：CloudLog=%s，呼号=%s", name, station.cloudlog_url, station.callsign)
        client = CloudlogClient(station.cloudlog_url, station.get_api_key())
        try:
            self._progress("[%s] 正在从 CloudLog 拉取 ADIF ..." % name)
            text = client.fetch_adif_text()
        except CloudlogError as e:
            error("[%s] 拉取 ADIF 失败: %s", name, e)
            return False, "拉取失败: %s" % e

        header, records = parse_adif(text)
        total = len(records)
        self._progress("[%s] 拉取到 %d 条 QSO" % (name, total))

        # 2) 过滤 + 去重
        mine = self._filter_by_callsign(records, station.callsign)
        new_records = [r for r in mine if not self.state.has(station.id, r)]
        self._progress("[%s] 其中本台站 %d 条，新增待上传 %d 条" % (name, len(mine), len(new_records)))
        if not new_records:
            info("[%s] 没有需要上传的新 QSO（已完成）。", name)
            station.last_sync = datetime.now(timezone.utc).isoformat()
            self.config.save()
            return True, "无新 QSO"

        # 3) 上传（默认一次性上传全部；QRZ 异步后台处理，可接受大文件）
        single_upload = self.config.settings.single_upload
        uploader = QrzUploader(account, self.config.profile_dir_for(account.id),
                               logfn=self._log,
                               keep_browser_open=self.config.settings.keep_browser_open)
        try:
            driver = uploader.open()
        except QrzNeedsLogin as e:
            error("[%s] 需登录 QRZ: %s", name, e)
            return False, "需登录: %s" % e
        except Exception as e:
            error("[%s] 启动浏览器失败: %s", name, e)
            return False, "启动浏览器失败: %s" % e

        try:
            uploaded_keys = []
            ok_any = False
            batches = [new_records] if single_upload else \
                [new_records[start:start + max(1, self.config.settings.batch_size or 200)]
                 for start in range(0, len(new_records), max(1, self.config.settings.batch_size or 200))]

            for idx, batch in enumerate(batches, 1):
                adif_str = build_adif(batch, header)
                fd, tmp = tempfile.mkstemp(suffix='.adi', prefix='qrzsync_')
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(adif_str)
                    if single_upload:
                        self._progress("[%s] 一次性上传全部 %d 条（可能需较长时间，QRZ 后台处理）..."
                                       % (name, len(batch)))
                    else:
                        self._progress("[%s] 上传批次 %d/%d（共 %d 条）..." % (name, idx, len(batches), len(batch)))
                    ok, msg = uploader.upload_path(driver, tmp, station.callsign)
                    if ok:
                        ok_any = True
                        uploaded_keys.extend(batch)
                        info("[%s] 上传成功: %s", name, msg)
                    else:
                        error("[%s] 上传失败: %s", name, msg)
                        if not single_upload:
                            break
                finally:
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass

            # 记录已上传的 QSO 状态
            info("[%s] 上传返回，开始记录状态（%d 条）...", name, len(uploaded_keys))
            if uploaded_keys:
                try:
                    self.state.add(station.id, uploaded_keys)
                    info("[%s] 已记录 %d 条新 QSO 状态", name, len(uploaded_keys))
                except Exception as e:
                    error("[%s] 记录状态失败: %s", name, e)
            try:
                station.last_sync = datetime.now(timezone.utc).isoformat()
                self.config.save()
                info("[%s] 配置已保存，本次同步完成", name)
            except Exception as e:
                error("[%s] 保存配置失败: %s", name, e)
            return ok_any, "完成，上传 %d 条" % len(uploaded_keys)
        finally:
            uploader.close()

    def run_all(self, only_station_id=None):
        """同步所有启用的账号/台站。返回 (ok, messages) 摘要。"""
        accs = [a for a in self.config.accounts if a.enabled]
        sts = [s for s in self.config.stations if s.enabled]
        info("同步开始：启用账号 %d 个，启用台站 %d 个", len(accs), len(sts))
        results = []
        for account in self.config.accounts:
            if not account.enabled:
                continue
            my_stations = [s for s in self.config.stations
                           if s.enabled and s.account_id == account.id]
            if not my_stations:
                warn("账号 '%s' 没有绑定任何台站，跳过", account.name or account.qrz_username)
                continue
            for station in my_stations:
                if only_station_id and station.id != only_station_id:
                    continue
                label = station.callsign or station.name or station.id
                try:
                    ok, msg = self.run_station(station, account)
                    results.append((station, ok, msg))
                    info("[%s] 同步结果：%s —— %s", label, "成功" if ok else "失败", msg)
                except Exception as e:
                    error("[%s] 同步异常: %s", label, e)
                    results.append((station, False, "异常: %s" % e))
                    info("[%s] 同步结果：失败 —— 异常: %s", label, e)
        return results
