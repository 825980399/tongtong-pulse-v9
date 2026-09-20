# -*- coding: utf-8 -*-
"""主线第81批 T5：两条 Parquet 加载路径统一到 _m81_load_parquet_unified + 开关组合矩阵。

验收映射：
- A2 两条路径统一：路径甲(_m68_load_from_parquet) 与 路径乙(_load_internal 的 parquet 分支)
  都经统一入口（显式带回分区列 + schema/列集合/分层三道校验 + FAIL-fast）。
- A7 开关组合矩阵：统一入口不受 PARQUET_AS_PRIMARY_STORAGE 主开关影响（它走 FEATURE.use_parquet_snapshot）；
  灰度 PARQUET_SCHEMA_M81_COMPLETE 仅影响列集合/校验严格度。

隔离约定：tempfile.mkdtemp()；路由测试仅 mock 下游统一入口以验证接线，不 mock 被测函数本身。
"""
import os
import sys
import tempfile
import threading
import unittest
import logging
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                    # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot        # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode                # noqa: E402

_PULSE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "mnemosyne", "PulseSnapshot.py")


class _CfgSwitch:
    """★第81批补2：临时切换 config 属性，退出时还原**原值**（不硬编码）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                if hasattr(config, k):
                    try:
                        delattr(config, k)
                    except Exception:
                        pass
            else:
                setattr(config, k, v)


def _mk_snap(tmpdir):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "pulse_knowledge_snapshot.json")
    s.parquet_dir = s._m68_parquet_dir()
    s._logger = logging.getLogger("test_m81_switch")
    s._logs = []
    s._log = lambda level, msg: s._logs.append(str(msg))
    s._lock = threading.RLock()
    return s


def _src():
    with open(_PULSE, encoding="utf-8", errors="replace") as f:
        return f.read()


@unittest.skipUnless(
    hasattr(config, "PARQUET_AS_PRIMARY_STORAGE"),
    "缺少 PARQUET_AS_PRIMARY_STORAGE 配置")
class TestLoadPathSwitchM81(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m81_switch_")
        self._dirs.append(d)
        return d

    def test_31_path_a_routes_through_unified(self):
        """★A2：路径甲 _m68_load_from_parquet 委托统一入口（mock 下游验证接线）。"""
        s = _mk_snap(self._d())
        sentinel = [PulseNode.from_dict({
            "node_id": "s", "value": "v", "evol_level": "L1"})] * 150
        with mock.patch.object(PulseSnapshot, "_m81_load_parquet_unified",
                               return_value=sentinel) as m:
            out = s._m68_load_from_parquet()
            self.assertIs(out, sentinel)
            m.assert_called_once()

    def test_32_path_b_wired(self):
        """★A2：路径乙 _load_internal 的 parquet 分支直接调用统一入口（源码断言接线真实存在）。"""
        src = _src()
        # 定位 _load_internal 函数体范围
        start = src.index("def _load_internal(")
        end = src.index("def _load_lazy", start) if "_load_lazy" in src[start:] else len(src)
        body = src[start:end]
        self.assertIn("self._m81_load_parquet_unified()", body,
                      "_load_internal 应直接调用统一加载入口")

    def test_33_path_a_delegate_wired(self):
        """★A2：_m68_load_from_parquet 仅委托统一入口，不另起炉灶（源码断言）。"""
        src = _src()
        start = src.index("def _m68_load_from_parquet(")
        end = src.index("def _m68_verify_parquet(", start)
        body = src[start:end]
        self.assertIn("return self._m81_load_parquet_unified()", body)

    def test_34_unified_switch_independent(self):
        """★A7：统一入口不读 PARQUET_AS_PRIMARY_STORAGE 主开关（开关独立，避免互相耦合）。"""
        src = _src()
        start = src.index("def _m81_load_parquet_unified(")
        end = src.index("def _m68_load_from_parquet(", start)
        body = src[start:end]
        self.assertNotIn("_m68_parquet_primary_enabled", body,
                         "统一入口不应依赖主存储开关")

    def test_35_primary_off_default(self):
        """★A7（第81批补2 隔离化改造）：_m68_parquet_primary_enabled() 如实反映 config。

        与生产全局值解耦：显式构造 False / True 两态各断言一次，不再把断言绑死在
        "当前生产默认必须是 False"（该语义属第80批止血期；第一步灰度已把
        PARQUET_AS_PRIMARY_STORAGE 正式置 True 并验收，绑死旧默认会让用例随开关翻转而红）。
        用例名保留历史名，语义以本 docstring 为准。
        """
        s = _mk_snap(self._d())
        with _CfgSwitch(PARQUET_AS_PRIMARY_STORAGE=False):
            self.assertFalse(s._m68_parquet_primary_enabled())
        with _CfgSwitch(PARQUET_AS_PRIMARY_STORAGE=True):
            self.assertTrue(s._m68_parquet_primary_enabled())

    def test_36_unified_used_when_primary_on(self):
        """★A7：即便主存储开关打开，统一入口仍按 FEATURE 路径被调用（开关组合不互斥）。"""
        s = _mk_snap(self._d())
        sentinel = [PulseNode.from_dict({
            "node_id": "s", "value": "v", "evol_level": "L1"})] * 150
        with mock.patch.object(PulseSnapshot, "_m81_load_parquet_unified",
                               return_value=sentinel):
            # 主存储开关打开不应改变 _m68_load_from_parquet 的委托行为
            # ★第81批补2：进入时保存原值、finally 还原原值（原实现硬编码还原 False，
            #   在灰度 PARQUET_AS_PRIMARY_STORAGE=True 时会污染同进程后续用例）。
            _orig_pap = config.PARQUET_AS_PRIMARY_STORAGE
            config.PARQUET_AS_PRIMARY_STORAGE = True
            try:
                self.assertIs(s._m68_load_from_parquet(), sentinel)
            finally:
                config.PARQUET_AS_PRIMARY_STORAGE = _orig_pap


if __name__ == "__main__":
    unittest.main()
