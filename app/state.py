"""上传状态：记录每个台站已经上传过的 QSO 指纹，避免重复上传。

每个台站一个 JSON 文件（list of fingerprint）。同步成功后追加即可。
"""

import json
import os
import threading

from .adif import fingerprint


class StateStore:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self._cache = {}
        # 用可重入锁（RLock）：add() 会持锁调用 _load()，普通 Lock 会自我死锁
        self._lock = threading.RLock()

    def _path(self, station_id):
        return os.path.join(self.state_dir, "station_%s.json" % station_id)

    def _load(self, station_id):
        with self._lock:
            if station_id in self._cache:
                return self._cache[station_id]
            path = self._path(station_id)
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._cache[station_id] = set(data)
                except Exception:
                    self._cache[station_id] = set()
            else:
                self._cache[station_id] = set()
            return self._cache[station_id]

    def has(self, station_id, record) -> bool:
        key = fingerprint(record)
        return key in self._load(station_id)

    def add(self, station_id, records):
        """加入新的指纹并落盘。返回新增数量。"""
        with self._lock:
            s = set(self._load(station_id))
            added = 0
            for r in records:
                k = fingerprint(r)
                if k not in s:
                    s.add(k)
                    added += 1
            self._cache[station_id] = s
            path = self._path(station_id)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(sorted(s), f, ensure_ascii=False)
            return added

    def count(self, station_id) -> int:
        return len(self._load(station_id))

    def clear(self, station_id):
        with self._lock:
            self._cache[station_id] = set()
            path = self._path(station_id)
            if os.path.exists(path):
                os.remove(path)
