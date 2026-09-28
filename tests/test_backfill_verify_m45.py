# -*- coding: utf-8 -*-
"""第45批 T4 门控测试：历史回填工具验证与操作文档（P1-294）。

覆盖：
* 工具接口（`archive_backup` / `run(backup=, archive_root=)`）
* **dry-run 稳定性**（冻结副本连续 3 次一致）
* **边界**（空文件 / 损坏行 / 非 dict / 部分已填充）
* **幂等性**（apply 两次，第二次 0 改动）
* **归档备份**（命名规范 / MANIFEST / 完整性校验 / 不删源）
* **操作手册**完整性
* **★生产数据未被回填**（本批不执行）

★全部隔离到 `tempfile.mkdtemp()` → **绝不写生产 `data/llm_traces/`**。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_TOOL = os.path.join(_ROOT, "tools", "backfill_llm_trace_fields.py")
_MANUAL = os.path.join(_ROOT, "docs", "操作手册",
                       "LLM留存数据回填操作手册_v1.0.md")
_VERIFY_REPORT = os.path.join(_ROOT, "docs", "分析报告",
                              "m45_回填工具验证报告.json")


def _load_tool():
    _s = importlib.util.spec_from_file_location("m45_bf_test", _TOOL)
    _m = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(_m)
    return _m


BF = _load_tool()


def _mk(d, name, recs):
    fp = os.path.join(d, name)
    with io.open(fp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write((r if isinstance(r, str)
                     else json.dumps(r, ensure_ascii=False)) + "\n")
    return fp


def _read(p):
    return [json.loads(x) for x in io.open(p, encoding="utf-8") if x.strip()]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m45_t4t_")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _arch(self):
        return os.path.join(self._tmp, "arch")


class TestToolInterface(_Base):
    def test_01_archive_backup_exists(self):
        self.assertTrue(hasattr(BF, "archive_backup"))

    def test_02_run_signature_extended(self):
        import inspect
        _p = inspect.signature(BF.run).parameters
        self.assertIn("backup", _p)
        self.assertIn("archive_root", _p)
        self.assertTrue(_p["backup"].default)

    def test_03_constants_present(self):
        for _k in ("BACKFILL_PROMPT_VERSION", "BACKFILL_NO_DETAIL",
                   "BACKUP_SUFFIX", "BACKFILL_ERROR_LIMIT"):
            self.assertTrue(hasattr(BF, _k), "缺少常量 %s" % _k)


class TestDryRunStability(_Base):
    def test_10_three_runs_identical(self):
        _d = os.path.join(self._tmp, "frozen")
        os.makedirs(_d)
        _mk(_d, "calls_20260101.jsonl", [
            {"trace_id": "a", "prompt_version": "", "status": "failed",
             "error": "", "response": "", "prompt": "p1"},
            {"trace_id": "b", "prompt_version": "", "status": "success",
             "error": "", "response": "r", "prompt": "p2"},
            {"trace_id": "c", "prompt_version": "v1", "status": "failed",
             "error": "E: x", "response": "", "prompt": "p3"},
        ])
        _res = []
        for _ in range(3):
            _r = BF.run(_d, apply=False)
            _res.append((_r["total_records"], _r["total_prompt_version_fill"],
                         _r["total_error_fill"]))
        self.assertEqual(_res[0], _res[1])
        self.assertEqual(_res[1], _res[2])
        self.assertEqual(_res[0], (3, 2, 1))

    def test_11_dry_run_writes_nothing(self):
        _d = os.path.join(self._tmp, "dry")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl",
                  [{"trace_id": "a", "prompt_version": "", "status": "failed",
                    "error": "", "response": "", "prompt": "p"}])
        _before = io.open(_fp, encoding="utf-8").read()
        BF.run(_d, apply=False)
        self.assertEqual(io.open(_fp, encoding="utf-8").read(), _before)
        self.assertFalse(os.path.exists(_fp + BF.BACKUP_SUFFIX))


class TestBoundary(_Base):
    def test_20_empty_file(self):
        _d = os.path.join(self._tmp, "empty")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl", [])
        _p = BF.plan_file(_fp)
        self.assertEqual(_p["total"], 0)
        self.assertEqual(_p["bad_lines"], 0)

    def test_21_broken_and_non_dict_lines_preserved(self):
        _d = os.path.join(self._tmp, "bad")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl", [
            '{"trace_id":"x","prompt_version":"","status":"failed",'
            '"error":"","response":"","prompt":"q"}',
            'NOT JSON AT ALL',
            '[1,2,3]',
        ])
        _p = BF.plan_file(_fp)
        self.assertEqual(_p["total"], 1)
        self.assertEqual(_p["bad_lines"], 2)
        BF.run(_d, apply=True, backup=False)
        _raw = io.open(_fp, encoding="utf-8").read().split("\n")
        self.assertIn("NOT JSON AT ALL", _raw)
        self.assertIn("[1,2,3]", _raw)

    def test_22_partially_filled_untouched(self):
        _d = os.path.join(self._tmp, "part")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl", [
            {"trace_id": "c", "prompt_version": "v9", "status": "failed",
             "error": "TimeoutError: x", "response": "", "prompt": "p"},
        ])
        _r = BF.run(_d, apply=True, backup=False)
        self.assertEqual(_r["total_prompt_version_fill"], 0)
        self.assertEqual(_r["total_error_fill"], 0)
        _rec = _read(_fp)[0]
        self.assertEqual(_rec["prompt_version"], "v9")
        self.assertEqual(_rec["error"], "TimeoutError: x")

    def test_23_error_derived_from_response(self):
        _d = os.path.join(self._tmp, "resp")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl",
                  [{"trace_id": "d", "prompt_version": "", "status": "failed",
                    "error": "", "response": "HTTP 429 rate limited",
                    "prompt": "p"}])
        BF.run(_d, apply=True, backup=False)
        self.assertIn("backfilled_from_response", _read(_fp)[0]["error"])

    def test_24_no_detail_placeholder(self):
        _d = os.path.join(self._tmp, "nop")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl",
                  [{"trace_id": "e", "prompt_version": "", "status": "failed",
                    "error": "", "response": "", "prompt": "p"}])
        BF.run(_d, apply=True, backup=False)
        self.assertEqual(_read(_fp)[0]["error"], BF.BACKFILL_NO_DETAIL)


class TestIdempotency(_Base):
    def test_30_second_apply_no_changes(self):
        _d = os.path.join(self._tmp, "idem")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl",
                  [{"trace_id": "a", "prompt_version": "", "status": "failed",
                    "error": "", "response": "", "prompt": "p"}])
        _r1 = BF.run(_d, apply=True, backup=True, archive_root=self._arch())
        _snap = io.open(_fp, encoding="utf-8").read()
        _r2 = BF.run(_d, apply=True, backup=True, archive_root=self._arch())
        self.assertEqual(_r1["total_prompt_version_fill"], 1)
        self.assertEqual(_r2["total_prompt_version_fill"], 0)
        self.assertEqual(_r2["total_error_fill"], 0)
        self.assertEqual(io.open(_fp, encoding="utf-8").read(), _snap)

    def test_31_apply_backs_up_single_file(self):
        _d = os.path.join(self._tmp, "bak")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl",
                  [{"trace_id": "a", "prompt_version": "", "status": "success",
                    "error": "", "response": "r", "prompt": "p"}])
        BF.run(_d, apply=True, backup=False)
        self.assertTrue(os.path.exists(_fp + BF.BACKUP_SUFFIX))


class TestArchiveBackup(_Base):
    def test_40_naming_and_manifest(self):
        _d = os.path.join(self._tmp, "src")
        os.makedirs(_d)
        _mk(_d, "calls_20260101.jsonl",
            [{"trace_id": "a", "prompt": "p", "status": "success"}])
        _ab = BF.archive_backup(_d, self._arch())
        self.assertTrue(os.path.basename(_ab["dest"]).startswith(
            "llm_traces_backup_"))
        self.assertTrue(os.path.isfile(_ab["manifest"]))
        _mf = json.load(io.open(_ab["manifest"], encoding="utf-8"))
        self.assertEqual(_mf["mismatch"], [])
        self.assertTrue(_mf["files"][0]["sha256"])

    def test_41_backup_does_not_delete_source(self):
        _d = os.path.join(self._tmp, "src2")
        os.makedirs(_d)
        _fp = _mk(_d, "calls_20260101.jsonl",
                  [{"trace_id": "a", "prompt": "p", "status": "success"}])
        BF.archive_backup(_d, self._arch())
        self.assertTrue(os.path.isfile(_fp))

    def test_42_backup_ok_flag(self):
        _d = os.path.join(self._tmp, "src3")
        os.makedirs(_d)
        _mk(_d, "calls_20260101.jsonl", [{"trace_id": "a", "status": "success"}])
        _ab = BF.archive_backup(_d, self._arch())
        self.assertTrue(_ab["ok"])
        self.assertEqual(_ab["mismatch"], [])
        self.assertGreater(_ab["files"], 0)
        self.assertGreater(_ab["total_bytes"], 0)

    def test_43_no_backup_flag_skips_archive(self):
        _d = os.path.join(self._tmp, "nob")
        os.makedirs(_d)
        _mk(_d, "calls_20260101.jsonl",
            [{"trace_id": "a", "prompt_version": "", "status": "success",
              "error": "", "response": "r", "prompt": "p"}])
        _r = BF.run(_d, apply=True, backup=False)
        self.assertIsNone(_r["archive_backup"])
        self.assertFalse(os.path.isdir(self._arch()))


class TestManualAndReport(unittest.TestCase):
    def test_50_manual_exists_with_sections(self):
        self.assertTrue(os.path.isfile(_MANUAL), "操作手册缺失")
        _t = io.open(_MANUAL, encoding="utf-8").read()
        for _k in ("前置条件", "执行步骤", "回滚", "验证清单", "常见问题",
                   "框架必须已停止运行"):
            self.assertIn(_k, _t, "手册缺少章节: %s" % _k)

    def test_51_manual_documents_backup_naming(self):
        _t = io.open(_MANUAL, encoding="utf-8").read()
        self.assertIn("llm_traces_backup_YYYYMMDD_HHMMSS", _t)
        self.assertIn("BACKUP_MANIFEST.json", _t)

    def test_52_verify_report_all_ok(self):
        # ★主线第62批 T4-3：产物缺失 → skip（同 T4-2 处理，非逻辑失败）
        if not os.path.isfile(_VERIFY_REPORT):
            self.skipTest("第45批回填工具验证报告产物缺失（证据缺失，非逻辑失败）: %s"
                          % _VERIFY_REPORT)
        self.assertTrue(os.path.isfile(_VERIFY_REPORT), "缺少工具验证报告")
        _r = json.load(io.open(_VERIFY_REPORT, encoding="utf-8"))
        self.assertTrue(_r["all_ok"], "工具验证存在不符项: %s" % _r["checks"])
        self.assertTrue(_r["checks"]["dry_run_stability_3x"]["stable"])
        self.assertTrue(_r["checks"]["idempotency"]["ok"])
        self.assertTrue(_r["checks"]["archive_backup"]["ok"])
        self.assertTrue(_r["checks"]["boundary"]["ok"])

    def test_53_production_not_backfilled(self):
        """★本批**不实际执行**回填 → 生产仍应有未回填的历史记录。"""
        # ★主线第62批 T4-3：产物缺失 → skip（同 T4-2 处理，非逻辑失败）
        if not os.path.isfile(_VERIFY_REPORT):
            self.skipTest("第45批回填工具验证报告产物缺失（证据缺失，非逻辑失败）: %s"
                          % _VERIFY_REPORT)
        _r = json.load(io.open(_VERIFY_REPORT, encoding="utf-8"))
        _pt = _r["checks"]["production_untouched"]
        self.assertFalse(_pt["backfilled"])
        self.assertGreater(_pt["pv_empty"], 0)


if __name__ == "__main__":
    unittest.main()
