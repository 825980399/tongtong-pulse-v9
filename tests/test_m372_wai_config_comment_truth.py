# -*- coding: utf-8 -*-
"""刀外遗留小刀：`config.py` 注释口径与**实测值**的一致性守门（可执行，非文档约定）。

为什么需要：本窗口三条假红（knife8/m163、knife3/m162、m92 族）根因同一个 ——
注释写着"默认关闭/零行为变化"，值却早已被某一批正式翻闸；读注释的人会把
"产品现态"误判成"产品回归"。

判据（每条开关）：
  ① 实测值 == 登记的现态（防止有人偷偷改回去再说"已对齐"）；
  ② 赋值行**上方紧邻注释块**（最多 12 行）必须写明翻闸/定值溯源标记；
  ③ 若注释里出现「默认关闭 / 默认 False」而实测为 True，
     则同一注释块内必须出现翻闸标记（"翻闸 / 窗前定值 / 已翻 / 落盘 True"）——
     这就是当年的坑：只写历史默认、不写已翻。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config.py")

#: 键名 → (期望值, 溯源标记之一)
CASES = {
    "MAIN_LOOP_PULSE_ENABLED": (True, ("372刀外遗留订正", "d27ac1e7")),
    "ENABLE_COLD_POOL_PERCENTILE_SHRINK": (True, ("372刀外遗留订正", "第173批刀0")),
    "ENABLE_M92_PATCH_BASE_INDENT_GUARD": (True, ("第295批刀1",)),
    "ENABLE_M92_PATCH_AST_STRUCT_GUARD": (True, ("第295批刀1",)),
    # config 原文写「★356刀16（小林 2026-10-24 拍板『先接线』）」；
    # 「356B」是同一子批的另一种叫法，两种写法都算溯源在位。
    "ENABLE_EVENT_BUS": (True, ("356刀16", "356B")),
    "ENABLE_INFLUXDB_WRITE_ONLY": (True, ("173批刀0",)),
    "ENABLE_INFLUXDB_TIMESERIES": (True, ("173批刀0",)),
}

#: 出现这些词说明"注释在讲历史默认关"
_HIST_OFF = ("默认关闭", "默认 False", "默认 **关闭**")
#: 出现这些词说明"注释已交代翻闸"
_FLIPPED = ("翻闸", "窗前定值", "已翻", "落盘 True", "372刀外遗留订正")


def _block_for(src_lines, key):
    """取 `key = ...` 赋值行上方紧邻的注释块（最多 12 行，遇非注释即停）。"""
    pat = re.compile(r"^%s\s*=" % re.escape(key))
    for i, ln in enumerate(src_lines):
        if pat.match(ln):
            blk = []
            j = i - 1
            while j >= 0 and len(blk) < 12 and src_lines[j].lstrip().startswith("#"):
                blk.append(src_lines[j])
                j -= 1
            return list(reversed(blk)), ln
    return None, None


class TestConfigCommentTruth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with io.open(CFG, encoding="utf-8") as fh:
            cls._src = fh.read().replace("\r\n", "\n")
        cls._lines = cls._src.split("\n")

    def test_01_values_match_documented_state(self):
        import config as _cfg
        for key, (want, _marks) in CASES.items():
            self.assertIs(want, getattr(_cfg, key, None),
                          "%s 实测 %r != 登记现态 %r" % (key, getattr(_cfg, key, None), want))

    def test_02_flip_trace_present_in_comment_block(self):
        for key, (_want, marks) in CASES.items():
            blk, asg = _block_for(self._lines, key)
            self.assertIsNotNone(blk, "config.py 里找不到 %s 的赋值行" % key)
            # 溯源允许两种写法：上方注释块、或赋值行行尾注（如 influx 两键）。
            # 因此这里不强制"上方必须有注释"，只强制"块 + 赋值行里必须有溯源标记"。
            _text = "\n".join(blk) + "\n" + asg
            self.assertTrue(any(m in _text for m in marks),
                            "%s 的注释块缺翻闸溯源标记 %r，易被误读成「默认关」\n%s"
                            % (key, marks, _text))

    def test_03_no_history_off_without_flip_note(self):
        """★判别力核心：注释说"默认关"、值却是 True ⇒ 必须同时写清已翻闸。"""
        import config as _cfg
        bad = []
        for key in CASES:
            if getattr(_cfg, key, None) is not True:
                continue
            _blk, _asg = _block_for(self._lines, key)
            # 溯源可以写成行尾注（如 `= True  # ★第173批刀0 窗前定值`），
            # 故把赋值行一并纳入判据——只认"上方注释块"会漏掉这类合法写法。
            _blk, _asg = _block_for(self._lines, key)
            _text = "\n".join(_blk or []) + "\n" + (_asg or "")
            if any(w in _text for w in _HIST_OFF) and not any(f in _text for f in _FLIPPED):
                bad.append(key)
        self.assertEqual([], bad,
                         "这些开关注释写「默认关」但值为 True 且未交代翻闸：%r" % bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
