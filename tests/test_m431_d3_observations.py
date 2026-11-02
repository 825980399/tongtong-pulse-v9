# -*- coding: utf-8 -*-
"""第431批 刀3：三条观测旁路落盘。

被守护的九条：
  1) 三类各落自己那个文件，路径形如 `{kind}_YYYYMMDD.jsonl`；
  2) append-only：连写三条 ⇒ 三行，前两行原样不动；
  3) 一行一条合法 JSON（可被 `json.loads` 逐行解析）；
  4) 关开关 ⇒ 不建目录、不写一行（返回空串）；
  5) `M431_OBS_ROOT` 决定落盘根（铁律 38：先改路径）；
  6) 文本字段有界（超长截断，不把整个 prompt 抄进去）；
  7) **数值/布尔不被转成字符串**（431 实测踩过：strength 落成 "0.82"）；
  8) 未知 kind ⇒ `out_path` 抛 KeyError（拼错就当场炸，不静默写错文件）；
  9) `_append` 永不抛 —— 目录不可写时返回空串而不是把异常抛给主流程。
"""
import contextlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.observability import observations as obs


def _rows(path):
    with open(path, "r", encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


class TestM431D3Observations(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.iso = self._td.name
        self._old_env = os.environ.get("M431_OBS_ROOT")
        os.environ["M431_OBS_ROOT"] = self.iso
        self._old_sw = getattr(config, "ENABLE_M431_OBSERVATIONS", None)
        config.ENABLE_M431_OBSERVATIONS = True

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("M431_OBS_ROOT", None)
        else:
            os.environ["M431_OBS_ROOT"] = self._old_env
        if self._old_sw is None:
            # ★用 suppress 而不是 except AttributeError: pass ——
            #   CI 静默 handler 门禁会把裸 pass 判成「静默」，而这个 pass
            #   在语义上确实是没有就算了。suppress 同样表达该意图且不被误判。
            with contextlib.suppress(AttributeError):
                delattr(config, "ENABLE_M431_OBSERVATIONS")
        else:
            config.ENABLE_M431_OBSERVATIONS = self._old_sw
        self._td.cleanup()

    # ---- 1) 三类各落自己文件 ----
    def test_01_three_kinds_own_files(self):
        obs.record_intent_evolution({"intent_id": "i1", "intent_text": "t"},
                                    {"value": {"value": "求真", "strength": 0.8}})
        obs.record_experience_hits("q", [{"id": "e1", "score": 3, "text": "x"}], 10)
        obs.record_channel_choice("caller", "ch1", "free_first",
                                  [{"channel": "ch1", "priority": 1}])
        _st = obs.stats()
        for _k in ("intent_evolution", "experience_hits", "channel_choices"):
            self.assertEqual(_st["kinds"][_k]["lines"], 1, f"{_k} 应写 1 行")
            self.assertTrue(_st["kinds"][_k]["path"].startswith(self.iso),
                            "必须落在隔离根内")
            self.assertIn(_k + "_", os.path.basename(_st["kinds"][_k]["path"]))

    # ---- 2) append-only ----
    def test_02_append_only(self):
        for _i in range(3):
            obs.record_experience_hits(f"q{_i}", [], 0)
        _p = obs.out_path("experience_hits")
        _r = _rows(_p)
        self.assertEqual(len(_r), 3)
        self.assertEqual([x["question"] for x in _r], ["q0", "q1", "q2"],
                         "先写的行必须原样保留（append-only）")

    # ---- 3) 每行合法 JSON 且带 ts/kind ----
    def test_03_line_is_valid_json(self):
        obs.record_channel_choice("c", "p", "r", [])
        _r = _rows(obs.out_path("channel_choices"))[0]
        self.assertIn("ts", _r)
        self.assertEqual(_r["kind"], "channel_choices")
        self.assertIsInstance(_r["ts"], (int, float))

    # ---- 4) 关开关 ⇒ 零写盘 ----
    def test_04_switch_off_writes_nothing(self):
        config.ENABLE_M431_OBSERVATIONS = False
        _p = obs.record_experience_hits("q", [], 0)
        self.assertEqual(_p, "", "关闭时必须返回空串")
        self.assertFalse(os.path.exists(obs.out_dir()), "关闭时不得建目录")

    # ---- 5) 隔离根生效 ----
    def test_05_iso_root(self):
        self.assertEqual(obs.out_dir(), os.path.join(self.iso, "data", "observations"))
        obs.record_channel_choice("c", "p", "r", [])
        self.assertTrue(os.listdir(obs.out_dir()))

    # ---- 6) 文本有界 ----
    def test_06_text_is_bounded(self):
        _long = "问" * 5000
        obs.record_experience_hits(_long, [], 0)
        _r = _rows(obs.out_path("experience_hits"))[0]
        self.assertLessEqual(len(_r["question"]), obs._MAX_TEXT + 1,
                             "超长文本必须截断（+1 是省略号）")

    # ---- 7) 数值/布尔保持类型 ----
    def test_07_numeric_types_preserved(self):
        obs.record_intent_evolution(
            {"intent_id": "i", "intent_text": "t", "confidence": 0.655,
             "is_autonomous": True},
            {"value": {"value": "求真", "strength": 0.82},
             "experience": {"motivation": "m", "count": 3},
             "state": {"curiosity": 0.71}})
        _r = _rows(obs.out_path("intent_evolution"))[0]
        self.assertIsInstance(_r["confidence"], float, "confidence 不许变 str")
        self.assertIsInstance(_r["signal_value"][0]["strength"], float,
                              "strength 不许变 str（431 踩过）")
        self.assertIsInstance(_r["signal_experience"][0]["count"], int,
                              "count 不许变 str")
        self.assertIsInstance(_r["signal_state"][0]["curiosity"], float)

    # ---- 8) 未知 kind ----
    def test_08_unknown_kind_raises(self):
        with self.assertRaises(KeyError):
            obs.out_path("not_a_kind")

    # ---- 9) 永不抛 ----
    def test_09_append_never_raises(self):
        """★用例选择有讲究：第一版把根指到 `<iso>/NUL` 想造"不可写"，
        结果 Windows 的 `\\\\.\\NUL` 设备**忽略路径后缀照样能写**，用例直接假绿/假红。
        改为 monkeypatch `os.makedirs` 必抛 —— 这才是"建目录失败"的真实入口。
        """
        _orig = os.makedirs

        def _boom(*a, **kw):
            raise OSError("makedirs denied by test")

        os.makedirs = _boom
        try:
            _p = obs.record_experience_hits("q", [], 0)
        finally:
            os.makedirs = _orig
        self.assertEqual(_p, "", "建目录失败时必须返回空串而不是抛异常")
        # 也不能留下半截文件
        self.assertFalse(
            os.path.exists(os.path.join(self.iso, "data", "observations",
                                        "experience_hits_{}.jsonl".format(__import__("time").strftime("%Y%m%d")))),
            "失败时不得留下半截文件")


if __name__ == "__main__":
    unittest.main()
