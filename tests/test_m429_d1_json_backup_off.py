# -*- coding: utf-8 -*-
"""第429批 刀1：JSON 兼容备份开关已关闭（且不影响加载）。

被守护的三条：
  1) `config.SNAPSHOT_SAVE_JSON_BACKUP` 默认值为 **False**（本批裁决：关）；
  2) `PulseSnapshot._m68_json_backup_enabled()` 如实反映该开关（不是写死）；
  3) 加载源仍是 `parquet` —— 关备份是「少写一份冗余」，不是把读取路径也砍了。

★隔离：不读生产 `data/`，只查配置与判定函数（该函数只读 config，不碰磁盘）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


class _CfgSwitch:
    """临时切换 config 属性，退出时还原（与 m81b2 同一写法）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


def _bare_snapshot():
    """只取 `_m68_json_backup_enabled()`，不跑 `__init__`（那会去读生产快照）。"""
    return object.__new__(PulseSnapshot)


class TestM429D1JsonBackupOff(unittest.TestCase):
    def test_01_config_default_is_false(self):
        """★核心：默认必须是关 —— 这是本批的裁决落地。"""
        self.assertIs(
            getattr(config, "SNAPSHOT_SAVE_JSON_BACKUP", "<缺失>"), False,
            "SNAPSHOT_SAVE_JSON_BACKUP 应为 False（429 裁决）；"
            "回退方式：改回 True 即可，代码路径一字未删")

    def test_02_enabled_helper_follows_switch(self):
        """判定函数如实反映开关，两向都验（不是把返回值写死成 False）。"""
        s = _bare_snapshot()
        with _CfgSwitch(SNAPSHOT_SAVE_JSON_BACKUP=False):
            self.assertFalse(s._m68_json_backup_enabled())
        with _CfgSwitch(SNAPSHOT_SAVE_JSON_BACKUP=True):
            self.assertTrue(s._m68_json_backup_enabled())

    def test_03_load_source_still_parquet(self):
        """关的是「写备份」，加载源必须是 parquet（否则等于把读取也砍了）。"""
        self.assertEqual(getattr(config, "SNAPSHOT_LOAD_SOURCE", None), "parquet")
        self.assertIs(getattr(config, "PARQUET_AS_PRIMARY_STORAGE", None), True)


if __name__ == "__main__":
    unittest.main()
