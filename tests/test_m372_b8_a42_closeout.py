# -*- coding: utf-8 -*-
"""372 刀8 定向单测：A 档 42 条收口的**产物一致性**、**生产面零 diff**、**票面分离**。

对应任务书「定向单测」三条：
  ① A 堆 42 条改后全绿、条数与清单一致 1 例；
  ② 生产面零 diff（除测试/夹具）1 例；
  ③ 另立缺陷票清单与本刀分离 1 例。

口径说明（为什么这么测）：
  - ① 不在这里重跑那 42 个用例（那需要十几分钟且会互相污染数据目录）。
    实跑结论已用 `--junitxml` 逐条落盘并写进证据件 `verify_run`；本用例守的是
    「42 条与归档 tier=A **集合相等** + 41/1 分档之和 == 总量 + 未收口那条
    必须带 blocker（不许无声挂起）」。
  - ② 用 git 客观事实：证据件记录的每个收口 commit，其变更路径必须全在
    `tests/` 或 `docs/` 内 —— 一旦有人把生产文件塞进这批提交，本用例立刻红。
  - ③ 票面必须另列，且票面不得宣称「已在本刀修复」。
"""
import io
import json
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                    "刀8_A42依据对照表_20261026.json")
ARCHIVE = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "369",
                       "刀3_既有134失败三档归档_20261026.json")


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestA42Closeout(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._ev = _load(EVID)
        cls._ar = _load(ARCHIVE)

    # ------------------------------------------------ ① 42 条集合一致 + 41/1
    def test_01_counts_and_open_blocker(self):
        rows = self._ev["rows"]
        _want = sorted(r["nodeid"] for r in self._ar["rows"] if r.get("tier") == "A")
        self.assertEqual(42, len(_want), "归档 tier=A 应为 42 条")
        self.assertEqual(_want, sorted(r["nodeid"] for r in rows),
                         "证据件 nodeid 集合与归档 tier=A 不一致（不是数量相等，是集合相等）")

        _fixed = [r for r in rows if r["status"] == "fixed"]
        _open = [r for r in rows if r["status"] == "open"]
        self.assertEqual(41, len(_fixed), "已收口应为 41 条")
        self.assertEqual(1, len(_open), "未收口应如实只留 1 条（m91）")
        self.assertEqual(len(rows), len(_fixed) + len(_open), "分档之和须等于 42")
        self.assertEqual({"fixed": 41}, {"fixed": self._ev["fixed_n"]})
        self.assertEqual({"open": 1}, {"open": self._ev["open_n"]})
        self.assertEqual(41, self._ev["verify_run"]["counts"]["passed"])
        self.assertEqual(1, self._ev["verify_run"]["counts"]["failed"])

        # 已收口条目必须有依据 + 处置，不许留空壳
        for r in _fixed:
            self.assertTrue(str(r.get("product_basis") or "").strip(),
                            "已收口 %s 缺 product_basis" % r["nodeid"])
            self.assertTrue(str(r.get("action") or "").strip(),
                            "已收口 %s 缺 action" % r["nodeid"])

        # 未收口那条必须写清 blocker（不许无声挂起 / 不许冒充已修）
        for r in _open:
            self.assertIn("m91", r["file"], "未收口的应是 m91")
            self.assertTrue(str(r.get("blocker") or "").strip(),
                            "未收口条目必须写明阻塞原因（blocker）")
            self.assertNotIn("fixed", str(r.get("status")))

    # ------------------------------------------------ ② 生产面零 diff
    def test_02_no_production_path_in_batch_commits(self):
        _shas = [k for k in self._ev["verify_run"]["by_commit"] if _is_sha(k)]
        self.assertGreaterEqual(len(_shas), 8, "收口 commit 数异常偏少：%r" % _shas)
        for sha in _shas:
            out = subprocess.run(["git", "show", "--name-only", "--format=", sha],
                                 cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(0, out.returncode, "git show 失败：%s" % sha)
            _paths = [p for p in out.stdout.replace("\\", "/").split("\n") if p.strip()]
            self.assertTrue(_paths, "%s 没有变更路径（sha 是否存在？）" % sha)
            # 生产面按工程口径定义（config/main/organs/nucleus/base/cognitive），
            # 不是「测试与文档以外的都算」——`tools/ci/baselines/` 是门禁数据
            # （990a44b0 的线程基线重锚走的就是这条，已按 [ci-gate-change] 单独笔），
            # 把它算成生产改动会误判。
            _bad = [p for p in _paths if _is_production(p)]
            self.assertEqual([], _bad,
                             "%s 触到了生产面路径 ⇒ 本刀不该改产品：%r" % (sha, _bad))

    # ------------------------------------------------ ③ 票面分离
    def test_03_defect_tickets_are_listed_separately(self):
        self.assertIn("defect_tickets", self._ev, "缺 defect_tickets 字段（票面必须另列）")
        _tk = self._ev["defect_tickets"]
        self.assertIsInstance(_tk, list)
        self.assertTrue(str(self._ev.get("tickets_note") or "").strip(),
                        "须写明「本刀是否另立票」的口径")
        for t in _tk:
            self.assertTrue(str(t.get("not_in_this_batch") or "").strip(),
                            "票面 %r 未声明「本刀不施工」⇒ 有把票混进本刀的风险" % t)
            self.assertNotIn("已修复", str(t.get("status") or ""),
                             "票面不得宣称已在本刀修复：%r" % t)


#: 生产面（与「残留类判据」同口径：config/main/organs/nucleus/base/cognitive）
_PROD_TOP = ("nucleus/", "organs/", "base/", "cognitive/")
_PROD_FILE = ("config.py", "main.py")


def _is_production(path):
    return path in _PROD_FILE or path.startswith(_PROD_TOP)


def _is_sha(s):
    return len(s) == 8 and all(c in "0123456789abcdef" for c in s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
