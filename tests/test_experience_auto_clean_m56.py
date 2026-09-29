# -*- coding: utf-8 -*-
"""★主线第56批 T2/P2-387：经验库持续清洗机制（方案C=A+B）门控单测。

覆盖：
  A. 写入路径检测（record_experience 内复用第50批 SERP classify）
  B. 周期增量扫描（auto_clean_scan）
  + 灰度开关 ENABLE_EXPERIENCE_AUTO_CLEAN 关闭→零标记（零回归）
  + 幂等（已标记不重复计数）
不依赖框架重启；使用显式 base_dir 隔离生产数据（铁律 35/57）。
"""
import shutil
import tempfile
import unittest

from nucleus.mnemosyne.experience_pool import ExperiencePool


def _make_pool():
    _d = tempfile.mkdtemp(prefix="m56_exp_")
    return ExperiencePool(base_dir=_d), _d


class TestExperienceAutoCleanM56(unittest.TestCase):
    def setUp(self):
        self._pools = []
        # 保存 config 开关原值，便于按用例覆盖后还原
        import config as _cfg
        self._cfg = _cfg
        self._saved = getattr(_cfg, "ENABLE_EXPERIENCE_AUTO_CLEAN", None)

    def tearDown(self):
        for p in self._pools:
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
        # 还原开关
        if self._saved is None:
            if hasattr(self._cfg, "ENABLE_EXPERIENCE_AUTO_CLEAN"):
                delattr(self._cfg, "ENABLE_EXPERIENCE_AUTO_CLEAN")
        else:
            setattr(self._cfg, "ENABLE_EXPERIENCE_AUTO_CLEAN", self._saved)

    def _pool(self):
        pool, d = _make_pool()
        self._pools.append(d)
        return pool

    # ---------------- A. 写入路径检测 ----------------
    def test_write_path_marks_serp_template(self):
        """★摘要模板句写入即被标记 polluted=True（复用第50批 classify）。"""
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = True
        pool = self._pool()
        _tpl = "我曾因学习而行动，获得了认知奖赏"
        _id = pool.record_experience(content=_tpl)
        self.assertTrue(_id)
        _last = pool._experiences[-1]
        self.assertTrue(_last.get("polluted") is True)
        self.assertEqual(_last.get("cleanup_reason"), "template_summary_legacy")
        self.assertEqual(_last.get("cleanup_batch"), 56)

    def test_write_path_marks_boilerplate(self):
        """★写入侧样板短句（含 SERP 样板 token）写入即被标记 write_side_boilerplate。

        ★第97批 相关任务 修正：原载荷「动机循环内部评估」在第65批分类收窄后已不再
        被判污染（收窄为仅认 SERP 样板 token，修复 82.6% 误标）。该测试属 stale 用例，
        改为用真正的 SERP 样板短句验证写入路径仍标记 write_side_boilerplate 类。
        """
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = True
        pool = self._pool()
        _id = pool.record_experience(
            content="搜索结果相关搜索广告赞助商链接百度一下相关推荐")
        self.assertTrue(_id)
        _last = pool._experiences[-1]
        self.assertTrue(_last.get("polluted") is True)
        self.assertEqual(_last.get("cleanup_reason"), "write_side_boilerplate")

    def test_write_path_normal_not_marked(self):
        """★正常经验内容不被误标（零误杀）。"""
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = True
        pool = self._pool()
        _id = pool.record_experience(
            content="今天和用户讨论了项目进度，明确了后续分工，收获很大")
        self.assertTrue(_id)
        _last = pool._experiences[-1]
        self.assertFalse(_last.get("polluted") is True)

    def test_switch_off_no_mark(self):
        """★开关关闭→SERP 模板也不标记（与改造前行为一致，零回归）。"""
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = False
        pool = self._pool()
        _tpl = "我曾因复盘而行动，获得了成就奖赏"
        _id = pool.record_experience(content=_tpl)
        self.assertTrue(_id)
        _last = pool._experiences[-1]
        self.assertFalse(_last.get("polluted") is True)

    # ---------------- B. 周期增量扫描 ----------------
    def test_scan_marks_existing(self):
        """★auto_clean_scan 能标记内存中已存在的 SERP 污染存量。"""
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = True
        pool = self._pool()
        # 注入一条未标记的模板记录（绕过 record_experience 的写入检测）
        pool._experiences.append({
            "id": "exp_legacy_1",
            "summary": "我曾因调试而行动，获得了认知奖赏",
            "polluted": False,
        })
        _rep = pool.auto_clean_scan()
        self.assertEqual(_rep["enabled"], True)
        self.assertGreaterEqual(_rep["newly_marked"], 1)
        _legacy = pool._experiences[-1]
        self.assertTrue(_legacy.get("polluted") is True)
        self.assertEqual(_legacy.get("cleanup_batch"), 56)

    def test_scan_idempotent(self):
        """★已标记记录不重复计数（幂等）。"""
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = True
        pool = self._pool()
        pool._experiences.append({
            "id": "exp_legacy_2",
            "summary": "我曾因总结而行动，获得了成就奖赏",
        })
        _first = pool.auto_clean_scan()
        _second = pool.auto_clean_scan()
        self.assertGreaterEqual(_first["newly_marked"], 1)
        self.assertEqual(_second["newly_marked"], 0)

    def test_scan_switch_off_noop(self):
        """★开关关闭→扫描零动作。"""
        self._cfg.ENABLE_EXPERIENCE_AUTO_CLEAN = False
        pool = self._pool()
        pool._experiences.append({
            "id": "exp_legacy_3",
            "summary": "我曾因学习而行动，获得了认知奖赏",
        })
        _rep = pool.auto_clean_scan()
        self.assertEqual(_rep["enabled"], False)
        self.assertEqual(_rep["newly_marked"], 0)
        self.assertFalse(pool._experiences[-1].get("polluted") is True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
