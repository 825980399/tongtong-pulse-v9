# -*- coding: utf-8 -*-
"""InfluxDB 时序存储封装 —— 主线第70批 T2/P1。

把节点激活 / 访问 / 修改等时序数据写入专业时序库，
支持时间范围查询、趋势分析、异常检测、降采样保留策略。

★第70批只做**设计与基础封装**，不启用、不迁移生产数据：
  - ``ENABLE_INFLUXDB_TIMESERIES`` 默认 False
  - influxdb-client 未安装时零副作用（is_available() 恒 False）
  - Token 从环境变量读取，绝不硬编码

用法
----
    from nucleus.timeseries_store.influxdb_store import get_influxdb_store
    store = get_influxdb_store()
    store.write_point("node_activated", {"node_id": "n1"}, {"count": 1})
    rows = store.query_range("node_activated", {"node_id": "n1"}, "-7d", "now()")
"""
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional
from nucleus._silent_except import silent_exc

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.influxdb_store")
except Exception:
    _logger = logging.getLogger("pulse.module.influxdb_store")

_INFLUX_STORE: Optional["InfluxDBStore"] = None


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _enabled() -> bool:
    """InfluxDB 总开关（默认关闭）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_INFLUXDB_TIMESERIES", False))
    except Exception as e:
        silent_exc(e, where="nucleus.timeseries_store.influxdb_store::_enabled L47")
        return False


class InfluxDBStore:
    """InfluxDB 时序存储封装。

    未启用 / 客户端缺失 / 连接失败时所有方法返回安全空值，
    绝不把异常抛给调用方 —— 时序库是增强项，不能拖垮主流程。
    """

    def __init__(self, url: str = "", token: str = "", org: str = "",
                 bucket: str = "") -> None:
        self._url = url or str(_cfg("INFLUXDB_URL", "http://localhost:8086"))
        # Token：优先环境变量，绝不硬编码
        self._token = token or os.environ.get("INFLUXDB_TOKEN", "") or str(_cfg("INFLUXDB_TOKEN", ""))
        self._org = org or str(_cfg("INFLUXDB_ORG", "tongtong"))
        self._bucket = bucket or str(_cfg("INFLUXDB_BUCKET", "pulse_metrics"))
        self._batch_size = int(_cfg("INFLUXDB_BATCH_SIZE", 5000))
        self._flush_interval = float(_cfg("INFLUXDB_FLUSH_INTERVAL", 5))
        self._lock = threading.Lock()
        self._client = None
        self._write_api = None
        self._query_api = None
        self._available = False
        self._client_missing = False
        self._buffer: List[dict] = []
        self._last_flush = 0.0
        try:
            import influxdb_client  # noqa: F401
        except ImportError:
            self._client_missing = True
            _logger.debug("[T2] influxdb-client 未安装，时序存储处于未启用状态（零副作用）")

    # ---------- 连接管理 ----------
    def connect(self, url: str = "", token: str = "", org: str = "") -> bool:
        """连接 InfluxDB。未启用/客户端缺失 → False。"""
        if not _enabled():
            return False
        if self._client_missing:
            return False
        with self._lock:
            try:
                from influxdb_client import InfluxDBClient
                self._client = InfluxDBClient(
                    url=url or self._url,
                    token=token or self._token,
                    org=org or self._org)
                self._write_api = self._client.write_api()
                self._query_api = self._client.query_api()
                # ★主线第76批 T2：凭证有效性校验。
                # InfluxDBClient 的构造不会验证 token —— 凭证失效时 connect() 仍返回 True，
                # 但后续 write 会静默失败（write 返回 True 而库里查不到数据）。
                # 实测 401 场景：写入「成功」、查询恒空、无任何报错。故此处主动校验一次。
                if _cfg("INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", True):
                    try:
                        self._query_api.query("buckets()", org=org or self._org)
                    except Exception as _ve:
                        self._available = False
                        self._write_api = None
                        _logger.warning(
                            "[T2] InfluxDB 凭证校验失败（token 无效/权限不足）: %s: %s",
                            type(_ve).__name__, _ve)
                        return False
                self._available = True
                _logger.info("[T2] InfluxDB 已连接: %s", url or self._url)
                return True
            except Exception as e:
                self._available = False
                _logger.warning("[T2] InfluxDB 连接失败: %s: %s", type(e).__name__, e)
                return False

    def close(self) -> None:
        """关闭连接（先冲刷缓冲区）。"""
        with self._lock:
            try:
                self.flush()
            except Exception as e:
                silent_exc(e, where="nucleus.timeseries_store.influxdb_store::close L124")
            if self._client is not None:
                try:
                    self._client.close()
                except Exception as e:
                    silent_exc(e, where="nucleus.timeseries_store.influxdb_store::close L129")
                self._client = None
            self._available = False

    def is_available(self) -> bool:
        return bool(self._available and self._write_api is not None)

    # ---------- RSS 采样（复用 InfoField 双口径读数） ----------
    def _sample_rss(self) -> Optional[dict]:
        """复用 InfoField 双口径内存读数（system_percent + process_rss_mb）。

        返回 ``{"process_rss_mb": float, "system_percent": float}``；
        任意环节失败（未启用 / InfoField 未就绪 / psutil 缺失 / 采集失败）返回 None，
        绝不抛异常、绝不阻塞写路径（时序库为增强项，不可拖垮主流程）。
        """
        try:
            if not bool(_cfg("ENABLE_INFLUXDB_RSS_SAMPLING", True)):
                return None
            from nucleus.field.InfoField import get_info_field
            _field = get_info_field()
            if _field is None:
                return None
            _m = _field._m169_memory_pressure()
            if not _m.get("ok"):
                return None
            return {
                "process_rss_mb": float(_m.get("process_rss_mb") or 0.0),
                "system_percent": float(_m.get("system_percent") or 0.0),
            }
        except Exception as _e:
            silent_exc(_e, where="nucleus.timeseries_store.influxdb_store::_sample_rss")
            return None

    # ---------- 写入 ----------
    def write_point(self, measurement: str, tags: Optional[dict] = None,
                    fields: Optional[dict] = None, timestamp: Optional[int] = None) -> bool:
        """写入单条时序数据（进入缓冲区，按批/定时冲刷）。"""
        if not self.is_available():
            return False
        with self._lock:
            self._buffer.append({
                "measurement": measurement,
                "tags": tags or {},
                "fields": fields or {},
                "time": timestamp or int(time.time() * 1e9),
            })
            if len(self._buffer) >= self._batch_size:
                return self._flush_locked()
            if time.time() - self._last_flush >= self._flush_interval:
                return self._flush_locked()
        return True

    def write_points(self, points: List[dict]) -> bool:
        """批量写入。"""
        if not self.is_available():
            return False
        with self._lock:
            self._buffer.extend(points)
            if len(self._buffer) >= self._batch_size:
                return self._flush_locked()
        return True

    def flush(self) -> bool:
        """冲刷缓冲区（外部可调用）。"""
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> bool:
        """冲刷缓冲区（调用方须持锁）。"""
        if not self._buffer or not self.is_available():
            return False
        try:
            from influxdb_client import Point
            _pts = []
            _rss = self._sample_rss()
            for _p in self._buffer:
                _pt = Point(_p["measurement"])
                for _k, _v in (_p.get("tags") or {}).items():
                    _pt = _pt.tag(_k, str(_v))
                for _k, _v in (_p.get("fields") or {}).items():
                    _pt = _pt.field(_k, _v)
                if _rss:
                    for _k, _v in _rss.items():
                        _pt = _pt.field(_k, _v)
                _pts.append(_pt.time(_p.get("time", int(time.time() * 1e9))))
            self._write_api.write(bucket=self._bucket, org=self._org, record=_pts)
            _n = len(self._buffer)
            self._buffer.clear()
            self._last_flush = time.time()
            _logger.debug("[T2] InfluxDB 冲刷 %d 条", _n)
            return True
        except Exception as e:
            _logger.warning("[T2] InfluxDB 写入失败: %s: %s", type(e).__name__, e)
            return False

    # ---------- 语义化写入（预封装测量点） ----------
    def node_activated(self, node_id: str, evol_level: str = "",
                       source: str = "") -> bool:
        """记录节点激活。"""
        _ok = self.write_point("node_activated",
                                {"node_id": node_id, "evol_level": evol_level},
                                {"source": source or "unknown", "count": 1})
        self.record_process_rss()  # ★174刀1：每次事件附 process_rss 采样点（恢复 T-InfluxDB接线含RSS-1 取证面）
        return _ok

    def node_accessed(self, node_id: str, access_type: str = "",
                      duration_ms: float = 0.0) -> bool:
        """记录节点访问。"""
        _ok = self.write_point("node_accessed",
                                {"node_id": node_id, "access_type": access_type or "get"},
                                {"duration_ms": float(duration_ms), "count": 1})
        self.record_process_rss()  # ★174刀1：每次事件附 process_rss 采样点（恢复 T-InfluxDB接线含RSS-1 取证面）
        return _ok

    def node_modified(self, node_id: str, field: str = "",
                      old_value: Any = "", new_value: Any = "") -> bool:
        """记录节点修改。"""
        _ok = self.write_point("node_modified",
                                {"node_id": node_id, "field": field or "unknown"},
                                {"old": str(old_value), "new": str(new_value), "count": 1})
        self.record_process_rss()  # ★174刀1：每次事件附 process_rss 采样点（恢复 T-InfluxDB接线含RSS-1 取证面）
        return _ok

    def query_executed(self, query_type: str, duration_ms: float = 0.0,
                       result_count: int = 0) -> bool:
        """记录查询执行。"""
        _ok = self.write_point("query_executed",
                                {"query_type": query_type or "unknown"},
                                {"duration_ms": float(duration_ms),
                                 "result_count": int(result_count)})
        self.record_process_rss()  # ★174刀1：每次事件附 process_rss 采样点（恢复 T-InfluxDB接线含RSS-1 取证面）
        return _ok

    def record_process_rss(self) -> bool:
        """补 RSS 采样点：写当前进程 RSS(MB) 与系统内存占比。

        测量点名为 ``process_rss``，字段含 ``process_rss_mb`` / ``system_percent``
        （由 ``_flush_locked`` 经 ``_sample_rss`` 自动附入）。仅作采样点、不依赖调度器；
        开关关闭 / InfoField 未就绪 / psutil 缺失时退化为一次普通 ``write_point``（零副作用）。
        """
        return self.write_point("process_rss", {}, {})

    # ---------- 查询 ----------
    @staticmethod
    def _build_tag_filter(tags: Optional[dict]) -> str:
        """构造 Flux 的 tag 过滤片段（各查询方法共用，避免散落的重复拼接）。"""
        _tag_f = ""
        for _k, _v in (tags or {}).items():
            _tag_f += f' |> filter(fn: (r) => r["{_k}"] == "{_v}")'
        return _tag_f

    def query_range(self, measurement: str, tags: Optional[dict] = None,
                    start: str = "-7d", end: str = "now()",
                    aggregation: str = "") -> List[dict]:
        """时间范围查询。"""
        if not self.is_available():
            return []
        try:
            _tag_f = self._build_tag_filter(tags)
            _agg = f'|> {aggregation}' if aggregation else ""
            _q = (f'from(bucket: "{self._bucket}") '
                  f'|> range(start: {start}, stop: {end}) '
                  f'|> filter(fn: (r) => r["_measurement"] == "{measurement}")'
                  f'{_tag_f} {_agg}')
            _res = self._query_api.query(_q, org=self._org)
            _out = []
            for _tb in _res:
                for _rec in _tb.records:
                    _out.append({"time": _rec.get_time(),
                                 "field": _rec.get_field(),
                                 "value": _rec.get_value()})
            return _out
        except Exception as e:
            _logger.warning("[T2] 范围查询失败: %s: %s", type(e).__name__, e)
            return []

    def query_latest(self, measurement: str, tags: Optional[dict] = None,
                     limit: int = 10) -> List[dict]:
        """查询最新 N 条。"""
        if not self.is_available():
            return []
        try:
            _tag_f = self._build_tag_filter(tags)
            _q = (f'from(bucket: "{self._bucket}") |> range(start: -30d) '
                  f'|> filter(fn: (r) => r["_measurement"] == "{measurement}")'
                  f'{_tag_f} |> sort(columns: ["_time"], desc: true) '
                  f'|> limit(n: {int(limit)})')
            _res = self._query_api.query(_q, org=self._org)
            _out = []
            for _tb in _res:
                for _rec in _tb.records:
                    _out.append({"time": _rec.get_time(),
                                 "field": _rec.get_field(),
                                 "value": _rec.get_value()})
            return _out
        except Exception as e:
            _logger.warning("[T2] 最新查询失败: %s: %s", type(e).__name__, e)
            return []

    def query_count(self, measurement: str, tags: Optional[dict] = None,
                    start: str = "-7d", end: str = "now()") -> int:
        """统计时间范围内记录数。"""
        rows = self.query_range(measurement, tags, start, end)
        return len(rows)

    # ---------- 趋势 / 异常 ----------
    def query_trend(self, measurement: str, tags: Optional[dict] = None,
                    start: str = "-7d", end: str = "now()",
                    interval: str = "1h") -> List[dict]:
        """趋势分析：按时间间隔聚合。"""
        return self.query_range(measurement, tags, start, end,
                                aggregation=f'aggregateWindow(every: {interval}, fn: mean)')

    def query_anomaly(self, measurement: str, tags: Optional[dict] = None,
                      window_size: int = 10, threshold: float = 2.0) -> List[dict]:
        """异常检测：滑动窗口均值 + 阈值倍数。

        纯客户端实现（不依赖 InfluxDB 的异常检测插件），
        先取窗口内数据，再按 |x - mean| > threshold * stdev 判定。
        """
        rows = self.query_range(measurement, tags)
        if len(rows) < 3:
            return []
        try:
            _vals = [float(_r.get("value") or 0) for _r in rows]
            _win = _vals[-int(window_size):] if window_size > 0 else _vals
            _mean = sum(_win) / len(_win)
            _var = sum((_x - _mean) ** 2 for _x in _win) / len(_win)
            _std = _var ** 0.5
            if _std <= 0:
                return []
            _out = []
            for _i, _r in enumerate(rows):
                _v = float(_r.get("value") or 0)
                if abs(_v - _mean) > threshold * _std:
                    _out.append({**_r, "z_score": round((_v - _mean) / _std, 4)})
            return _out
        except Exception as e:
            _logger.warning("[T2] 异常检测失败: %s: %s", type(e).__name__, e)
            return []

    def query_top_k(self, measurement: str, tags: Optional[dict] = None,
                    start: str = "-7d", end: str = "now()", k: int = 10,
                    order_by: str = "desc") -> List[dict]:
        """Top K 查询（按值排序）。"""
        rows = self.query_range(measurement, tags, start, end)
        try:
            rows.sort(key=lambda _r: float(_r.get("value") or 0),
                      reverse=(order_by != "asc"))
            return rows[:int(k)]
        except Exception:
            return rows[:int(k)]

    # ---------- 降采样保留策略（设计） ----------
    def get_retention_policy(self) -> Dict[str, str]:
        """返回降采样保留策略（第70批为设计，第71批+落到 InfluxDB 任务）。"""
        return {
            "raw": "7d",           # 原始数据保留 7 天
            "1m_agg": "30d",       # 1 分钟聚合保留 30 天
            "1h_agg": "365d",      # 1 小时聚合保留 1 年
        }

    def get_stats(self) -> dict:
        return {
            "enabled": _enabled(),
            "client_installed": not self._client_missing,
            "available": self.is_available(),
            "url": self._url,
            "bucket": self._bucket,
            "buffered": len(self._buffer),
            "batch_size": self._batch_size,
        }


def get_influxdb_store() -> "InfluxDBStore":
    """获取 InfluxDB 存储单例。"""
    global _INFLUX_STORE
    if _INFLUX_STORE is None:
        _INFLUX_STORE = InfluxDBStore()
        # ★第174批刀1 T-InfluxDB连接接线-1：启用时连接（灰度开关 ENABLE_INFLUXDB_AUTO_CONNECT）。
        # 默认 False：保持旧行为（不连接、is_available 恒 False、零副作用）。True 才在首次
        # 获取单例时主动 connect()，使 is_available() 返回 True、恢复写点与 T-InfluxDB接线含RSS-1
        # 取证面。connect() 内部已捕获全部异常并记日志（不静默），此处无需再包 try。
        if bool(_cfg("ENABLE_INFLUXDB_AUTO_CONNECT", False)):
            _INFLUX_STORE.connect()
    return _INFLUX_STORE
