# -*- coding: utf-8 -*-
"""主线第81批【补】T2 生产热路径接线集成验收（A-E，先红后绿，禁 mock 召回）。

验证生产链路是否真正闭环（星轨终验指出：上窗口 T2 的 API 从未接进生产运行链路，
get() 内 0 处调用、main.py 对 materialize/set_cold_recall/recall_cold 0 匹配）。

- A 自动绑定：只调 snapshot.set_node_pool(pool)，断言 _m70_cold_recall_fn 已自动绑定
      （不手动 set_cold_recall_source）。
- B 真实 serve 回填（核心反"假成功"）：写真实临时冷存一个 L2/L3 节点，构造其内存
      blanked 占位（同 id、value 空、_m70_blanked=True）放入 pool；全程不手动调用任何
      materialize，直接 pool.get(node_id)；断言返回节点 value/linked_nodes 已等于冷存真值、
      _m70_blanked 已清。★接线前的代码上该断言必失败（get 返回空 value）= 先红。
- C 锁安全：并发多线程混合 blanked/正常节点 get，断言不死锁、总耗时不上锁饥饿恶化。
- D 关开关零回归：普通节点 get 行为与改动前一致（不触发任何回填分支、无新增 ERROR）。
- E 故障注入 fail-closed：blanked 且冷存无副本、无 keep 时，get 不返回脏空正文静默成功
      ——节点不丢失、不崩溃，且 ERROR 带 node_id 可见。

隔离：全部真实 PulseNode + 真实临时冷存（写 tmp，不碰真实 data/ 与救命备份）；不 mock 召回。
"""
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


def _mk_snap(tmpdir):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "pulse_knowledge_snapshot.json")
    s.parquet_dir = s._m68_parquet_dir()
    s._logger = logging.getLogger("test_m70b")
    s._logs = []
    s._log = lambda level, msg: s._logs.append(str(msg))
    s._lock = threading.RLock()
    return s


def _mk_node(nid, level):
    n = PulseNode(
        value="value_" + nid + "_" + level,
        keywords=["k1", "k2"],
        source_organ="test",
        evol_level=level,
        importance="B",
    )
    n.linked_nodes = ["lnk_a", "lnk_b"]
    n.semantic_relations = [{"rel": "r", "target": "t"}]
    n.node_id = nid
    return n


def _mk_pool(tmpdir):
    _pool = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)
    _pool.set_cold_storage(True, max_cold_cache=10_000_000, cold_dir=tmpdir)
    return _pool


class TestM70ProductionWiringM81b(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m70_wiring_")
        self._saved = getattr(config, "SNAPSHOT_HOT_COLD_LOAD", None)
        config.SNAPSHOT_HOT_COLD_LOAD = True

    def tearDown(self):
        if self._saved is None:
            if hasattr(config, "SNAPSHOT_HOT_COLD_LOAD"):
                try:
                    delattr(config, "SNAPSHOT_HOT_COLD_LOAD")
                except Exception:
                    pass
        else:
            config.SNAPSHOT_HOT_COLD_LOAD = self._saved
        try:
            shutil.rmtree(self._tmp, ignore_errors=True)
        except Exception:
            pass

    # ---------- A：自动绑定（根因解，消除 main 漏调→fn 永远 None）----------
    def test_A_auto_bind(self):
        _pool = _mk_pool(self._tmp)
        _snap = _mk_snap(self._tmp)
        _snap.set_node_pool(_pool)  # ★只调 set_node_pool，不手动 set_cold_recall_source
        self.assertEqual(_snap._m70_cold_recall_fn, _pool.recall_cold_nodes_batch,
                      "set_node_pool 应自动绑定冷召回源（无需手动 set_cold_recall_source）")

    # ---------- B：真实 serve 回填（核心反假成功，先红后绿）----------
    def test_B_real_serve_recall(self):
        _pool = _mk_pool(self._tmp)
        _cold_node = _mk_node("coldB", "L2")
        _pool._write_cold_node_to_disk(_cold_node)  # 真实写盘（真冷存）
        _pool.flush_cold_buffer()

        # 构造 blanked 占位（同 id、正文清空）放入热池
        _blanked = _mk_node("coldB", "L2")
        _blanked.value = ""
        _blanked._m70_blanked = True
        _blanked.linked_nodes = []
        if hasattr(_blanked, "_m70_keep"):
            del _blanked._m70_keep
        _pool._hot["coldB"] = _blanked

        # ★关键：全程不手动调用任何 materialize
        _got = _pool.get("coldB")
        self.assertIs(_got, _blanked, "get 应返回同一节点对象（自动回填）")
        self.assertEqual(_got.value, "value_coldB_L2",
                         "get 应经冷召回自动回填正文（接线前返回空 value=先红）")
        self.assertEqual(_got.linked_nodes, ["lnk_a", "lnk_b"], "linked_nodes 应还原")
        self.assertFalse(getattr(_got, "_m70_blanked", False), "回填后 _m70_blanked 应清除")

    # ---------- C：锁安全（并发冷召回不锁饥饿）----------
    def test_C_lock_safety(self):
        _pool = _mk_pool(self._tmp)
        _ids = ["clk%d" % i for i in range(10)]
        for _id in _ids:
            _n = _mk_node(_id, "L2")
            _pool._write_cold_node_to_disk(_n)
        _pool.flush_cold_buffer()
        # blanked 占位 + 正常节点混合放入热池
        for _id in _ids:
            _b = _mk_node(_id, "L2")
            _b.value = ""
            _b._m70_blanked = True
            _b.linked_nodes = []
            if hasattr(_b, "_m70_keep"):
                del _b._m70_keep
            _pool._hot[_id] = _b
        for i in range(10):
            _pool._hot["norm%d" % i] = _mk_node("norm%d" % i, "L1")

        _errors = []

        def _worker():
            try:
                for _id in _ids + ["norm%d" % i for i in range(10)]:
                    _g = _pool.get(_id)
                    if _g is None:
                        _errors.append("none:" + _id)
            except Exception as _e:
                _errors.append(repr(_e))

        _t0 = time.time()
        _ths = [threading.Thread(target=_worker) for _ in range(4)]
        for _t in _ths:
            _t.start()
        for _t in _ths:
            _t.join(timeout=30)
        _dt = time.time() - _t0

        self.assertEqual(_errors, [], "并发 get 不应出错/死锁: %s" % _errors)
        for _id in _ids:
            _filled = _pool._hot.get(_id) or _pool._warm.get(_id)
            self.assertIsNotNone(_filled, "%s 不应丢失（可能已降级到温池）" % _id)
            self.assertEqual(_filled.value, "value_" + _id + "_L2",
                             "%s 应经并发 get 回填" % _id)
        self.assertLess(_dt, 15.0, "并发冷召回不应锁饥饿恶化（耗时 %.2fs）" % _dt)

    # ---------- D：关开关零回归 ----------
    def test_D_switch_off_no_regression(self):
        _pool = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)  # 不启用冷存
        _n = _mk_node("d1", "L1")
        _pool._hot["d1"] = _n
        _logs = []
        _orig = _pool._log
        _pool._log = lambda lvl, msg: _logs.append((str(lvl), str(msg)))
        try:
            _g = _pool.get("d1")
        finally:
            _pool._log = _orig
        self.assertIs(_g, _n)
        self.assertEqual(_g.value, "value_d1_L1", "普通节点 get 行为不变")
        self.assertFalse(
            any(("懒加载" in m or "冷召回" in m) for _, m in _logs),
            "关开关不应触发任何回填分支")

    # ---------- E：故障注入 fail-closed ----------
    def test_E_fail_closed(self):
        _pool = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)  # 冷存无副本
        _b = _mk_node("missE", "L2")
        _b.value = ""
        _b._m70_blanked = True
        _b.linked_nodes = []
        if hasattr(_b, "_m70_keep"):
            del _b._m70_keep
        _pool._hot["missE"] = _b

        # fail-closed ERROR 走模块级 _module_logger（非 self._log），用 assertLogs 捕获
        with self.assertLogs(logger="pulse.module.PulseNodePool", level="ERROR") as _cm:
            _g = _pool.get("missE")  # 冷存无副本、无 keep → 无法回填
        # 节点不丢失、不崩溃、不静默伪装成功
        self.assertIs(_g, _b, "故障节点不应丢失")
        self.assertEqual(_g.value, "", "无法回填时应保留空白（fail-closed，不伪装成功）")
        _err_txt = "\n".join(_cm.output)
        self.assertIn("missE", _err_txt, "故障应 ERROR 可见且带 node_id")
        self.assertTrue(any("无法回填" in l for l in _cm.output),
                        "应有 fail-closed ERROR（不应被吞）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
