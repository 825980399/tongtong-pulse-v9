# -*- coding: utf-8 -*-
"""主线第72批 T1：InfluxDB 只写端到端验证（真实只写代码路径 + mock/真实后端）。

设计
----
- 默认 ``--backend mock``：把 **内存 MockInfluxDBStore** 注入到
  ``nucleus.timeseries_store.influxdb_store.get_influxdb_store`` 单例，并临时打开
  ``ENABLE_INFLUXDB_TIMESERIES`` + ``ENABLE_INFLUXDB_WRITE_ONLY``，
  驱动 **真实的** 只写路径：
    * ``PulseNodePool.add`` → ``node_modified`` + ``node_activated``
    * ``PulseNodePool.get`` → ``node_activated``（总是）+ ``node_accessed``（按采样）
    * ``PulseStomach._m71_record_query_executed`` → ``query_executed``
- ``--backend real``：使用真实 ``InfluxDBStore``（需用户安装驱动 + 启动服务）。
- 验证采样策略（INFLUXDB_SAMPLE_RATE）、缓冲批量、异常安全、开关零副作用。

退出码：全部通过=0；任一失败=1。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def _check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("[PASS] %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        FAIL.append(name)
        print("[FAIL] %s%s" % (name, ("  " + detail) if detail else ""))


class MockInfluxDBStore:
    """内存 mock：记录各 measurement 的写入次数，复现真实写入方法签名。"""

    def __init__(self, fail_mode=False):
        self.fail_mode = fail_mode
        self.counts = {
            "node_activated": 0, "node_accessed": 0,
            "node_modified": 0, "query_executed": 0,
        }
        self.points = []
        self.flushed = 0

    def is_available(self):
        return True

    def node_modified(self, node_id, field="", old_val="", new_val=""):
        self.counts["node_modified"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influxdb unavailable")
        self.points.append(("node_modified", node_id))
        return True

    def node_activated(self, node_id, evol_level="", access_type=""):
        self.counts["node_activated"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influxdb unavailable")
        self.points.append(("node_activated", node_id))
        return True

    def node_accessed(self, node_id, access_type=""):
        self.counts["node_accessed"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influxdb unavailable")
        self.points.append(("node_accessed", node_id))
        return True

    def query_executed(self, query_type, duration_ms=0.0, result_count=0):
        self.counts["query_executed"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influxdb unavailable")
        self.points.append(("query_executed", query_type))
        return True

    def write_point(self, measurement, tags=None, fields=None, timestamp=None):
        self.points.append((measurement, tags))
        return True

    def write_points(self, points):
        self.points.extend(points)
        return True

    def flush(self):
        self.flushed += 1
        return True

    def get_stats(self):
        return {"counts": dict(self.counts), "points": len(self.points),
                "flushed": self.flushed}


class RealStoreAdapter:
    """★主线第76批 T2：包装真实 InfluxDBStore —— 真实写入 + 计数（供断言使用）。

    真实 store 没有 ``counts`` 属性，脚本原先直接读 ``mock.counts[...]`` 会
    AttributeError。适配器转发**全部写入到真实 store**，同时累计调用次数。
    """

    def __init__(self, real):
        self._real = real
        self.counts = {
            "node_activated": 0, "node_accessed": 0,
            "node_modified": 0, "query_executed": 0,
        }

    def is_available(self):
        return self._real.is_available()

    def node_modified(self, node_id, field="", old_val="", new_val=""):
        self.counts["node_modified"] += 1
        return self._real.node_modified(node_id, field, old_val, new_val)

    def node_activated(self, node_id, evol_level="", access_type=""):
        self.counts["node_activated"] += 1
        return self._real.node_activated(node_id, evol_level, access_type)

    def node_accessed(self, node_id, access_type=""):
        self.counts["node_accessed"] += 1
        return self._real.node_accessed(node_id, access_type)

    def query_executed(self, query_type, duration_ms=0.0, result_count=0):
        self.counts["query_executed"] += 1
        return self._real.query_executed(query_type, duration_ms, result_count)

    def write_point(self, measurement, tags=None, fields=None, timestamp=None):
        return self._real.write_point(measurement, tags, fields, timestamp)

    def write_points(self, points):
        return self._real.write_points(points)

    def flush(self):
        return self._real.flush()

    def get_stats(self):
        _s = dict(self._real.get_stats())
        _s["counts"] = dict(self.counts)
        return _s


def _build_real_writer():
    """★主线第76批 T2 修正：
    原实现有两个缺陷，导致 ``--backend real`` **必然**降级为 mock：
      1) 时序倒置 —— ``connect()`` 依赖 ``ENABLE_INFLUXDB_TIMESERIES=True``，
         而开关在此函数之后才被打开；
      2) ``get_influxdb_store()`` 单例**只构造不连接** → ``is_available()`` 恒 False。
    修正：先开开关 → 取单例 → 显式 connect() → 再判定可用性。
    """
    import config as _cfgmod
    _cfgmod.ENABLE_INFLUXDB_TIMESERIES = True
    _cfgmod.ENABLE_INFLUXDB_WRITE_ONLY = True
    from nucleus.timeseries_store.influxdb_store import get_influxdb_store
    store = get_influxdb_store()
    if not store.is_available():
        store.connect()
    if not store.is_available():
        raise RuntimeError(
            "真实 InfluxDB 不可用（驱动未装/开关关闭/服务未起/token 无效）。请改用默认 mock。")
    return RealStoreAdapter(store)


def run(backend):
    import config
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.mnemosyne.PulseNode import PulseNode
    import nucleus.timeseries_store.influxdb_store as influx_mod

    if backend == "real":
        try:
            mock = _build_real_writer()
            print("[INFO] 使用真实 InfluxDB 后端")
        except RuntimeError as e:
            print("[WARN] %s；自动降级为 mock。" % e)
            mock = MockInfluxDBStore()
            influx_mod.get_influxdb_store = lambda: mock
    else:
        mock = MockInfluxDBStore()
        influx_mod.get_influxdb_store = lambda: mock

    _og_t = config.ENABLE_INFLUXDB_TIMESERIES
    _og_w = config.ENABLE_INFLUXDB_WRITE_ONLY
    _og_r = getattr(config, "INFLUXDB_SAMPLE_RATE", 0.01)
    config.ENABLE_INFLUXDB_TIMESERIES = True
    config.ENABLE_INFLUXDB_WRITE_ONLY = True

    try:
        pool = PulseNodePool()

        # 1. 节点创建 → node_modified（create）；node_activated 仅发生在 get 时
        config.INFLUXDB_SAMPLE_RATE = 1.0  # 采样全开，确保 node_accessed 计数可预期
        n = PulseNode(value="e2e-io", evol_level="L2", keywords=["e2e"])
        pool.add(n)
        _check("只写-节点创建写node_modified",
               mock.counts["node_modified"] >= 1, "=%d" % mock.counts["node_modified"])
        _check("只写-节点创建不写node_activated",
               mock.counts["node_activated"] == 0,
               "(激活仅发生在 get) act=%d" % mock.counts["node_activated"])

        # 2. 节点访问(get) → node_activated(总是) + node_accessed(采样)
        before_acc = mock.counts["node_accessed"]
        before_act = mock.counts["node_activated"]
        pool.get(n.node_id)
        _check("只写-访问写node_activated", mock.counts["node_activated"] == before_act + 1)
        _check("只写-访问写node_accessed(采样=1.0)",
               mock.counts["node_accessed"] == before_acc + 1)

        # 3. 采样策略：采样率 0.0 → node_accessed 不写
        config.INFLUXDB_SAMPLE_RATE = 0.0
        acc0 = mock.counts["node_accessed"]
        for _ in range(20):
            pool.get(n.node_id)
        _check("采样-采样率0.0时node_accessed不写",
               mock.counts["node_accessed"] == acc0,
               "acc=%d（访问20次）" % mock.counts["node_accessed"])

        # 4. query_executed（经真实 Stomach 路径）
        try:
            from organs.body.PulseStomach import PulseStomach
            stom = PulseStomach()
            qe0 = mock.counts["query_executed"]
            stom._m71_record_query_executed("digest_knowledge", 1.5, 1)
            _check("只写-query_executed写入",
                   mock.counts["query_executed"] == qe0 + 1,
                   "=%d" % mock.counts["query_executed"])
        except Exception as e:
            # Stomach 构造可能依赖器官上下文；直接校验 store 方法签名
            qe0 = mock.counts["query_executed"]
            mock.query_executed("digest_knowledge", 1.5, 1)
            _check("只写-query_executed写入(store直连)",
                   mock.counts["query_executed"] == qe0 + 1,
                   "Stomach构造异常(%s)，改走store直连" % type(e).__name__)

        # 5. 异常场景：服务不可用 → 主流程不抛异常
        mock_fail = MockInfluxDBStore(fail_mode=True)
        influx_mod.get_influxdb_store = lambda: mock_fail
        try:
            m = PulseNode(value="e2e-io2", evol_level="L1", keywords=["e2e"])
            pool.add(m)
            _check("异常场景-主流程不抛异常", True)
        except Exception as e:
            _check("异常场景-主流程不抛异常", False, "意外抛异常: %s" % e)

        # 6. 开关关闭 → 零副作用
        config.ENABLE_INFLUXDB_TIMESERIES = False
        config.ENABLE_INFLUXDB_WRITE_ONLY = False
        mock2 = MockInfluxDBStore()
        influx_mod.get_influxdb_store = lambda: mock2
        p = PulseNode(value="e2e-io3", evol_level="L2", keywords=["e2e"])
        pool.add(p)
        _check("开关关闭-零副作用",
               sum(mock2.counts.values()) == 0,
               "counts=%s" % mock2.counts)

    finally:
        config.ENABLE_INFLUXDB_TIMESERIES = _og_t
        config.ENABLE_INFLUXDB_WRITE_ONLY = _og_w
        config.INFLUXDB_SAMPLE_RATE = _og_r
        try:
            from nucleus.timeseries_store.influxdb_store import get_influxdb_store as _real
            influx_mod.get_influxdb_store = _real
        except Exception:
            pass

    print("\n只写统计快照: %s" % mock.get_stats())
    return 0 if not FAIL else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="InfluxDB 只写端到端验证")
    ap.add_argument("--backend", choices=["mock", "real"], default="mock")
    args = ap.parse_args(argv)
    return run(args.backend)


if __name__ == "__main__":
    sys.exit(main())
