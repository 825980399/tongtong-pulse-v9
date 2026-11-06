# -*- coding: utf-8 -*-
"""第473批 刀8 门控单测：人工批准必须能真正落地（闭环最后一环）。

根因
----
`tools/adjudicate_patch.py::_cmd_approve` 原实现只改四个字段：

    _p["status"] = "approved"
    _p["adjudicated"] = True
    _p["adjudicated_at"] = ...
    _p["adjudicated_by"] = ...

**从不写 `approved_source`**。而核心闸门
`PatchManager._m471_core_human_exempt` 的判据是：

    status == "approved"
    and not auto_approved / auto_released
    and approved_source == "human"

⇒ 人在 CLI 上批了，`approved_source` 仍是空 ⇒ 被当成「来源不明」
⇒ 核心闸门拦下 ⇒ 补丁永远不落地 ⇒ 自主进化闭环在这里断掉。

修复
----
改为走**唯一 approved 写入口** `PatchManager.approve_patch_by_id(by="human:...")`，
由它写 `approved_source="human"` 并跑完治理门禁；批准失败如实报错，
**不退回「直接改字段」那种假批准**。
"""

import contextlib
import importlib.util
import io
import os
import types
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_ROOT, "tools", "adjudicate_patch.py")


def _load_tool():
    _spec = importlib.util.spec_from_file_location("_m473_adjudicate", _TOOL)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _load_tool()

    def setUp(self):
        self.pending = [{"id": "p1", "status": "verified"}]
        self.history = []
        self.pm = mock.MagicMock()
        self.pm.get_pending_file.return_value = os.path.join(_ROOT, "data", "patches",
                                                             "pending_patches.json")
        self.pm._save_json.return_value = True
        self.args = types.SimpleNamespace(patch_id="p1", source="pending", reason="人工裁决")

    def _run(self):
        _o, _e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(_o), contextlib.redirect_stderr(_e):
            _rc = self.tool._cmd_approve(self.args, self.pm, self.pending,
                                         self.history, [], None)
        return _rc, _o.getvalue(), _e.getvalue()


class TestA_GoesThroughSingleWriteEntry(_Base):
    """判据：必须调 approve_patch_by_id，且签名带 human: 前缀。"""

    def test_01_calls_api_with_human_prefix(self):
        self.pm.approve_patch_by_id.return_value = {"ok": True, "reason": ""}
        self.pm._load_patch_list.return_value = [{"id": "p1", "status": "approved"}]
        _rc, _out, _err = self._run()
        self.assertEqual(_rc, 0, f"应成功，stderr={_err!r}")
        self.assertTrue(self.pm.approve_patch_by_id.called,
                        "没有调用唯一写入口 approve_patch_by_id —— 闭环仍断")
        _args, _kwargs = self.pm.approve_patch_by_id.call_args
        _by = _kwargs.get("by") or (_args[1] if len(_args) > 1 else "")
        self.assertTrue(str(_by).startswith("human:"),
                        f"by 必须以 human: 开头（否则 _approve_via 会落 auto 口），实得 {_by!r}")

    def test_02_no_direct_status_write(self):
        """行为断言：API 拒绝时，绝不能偷偷把 status 改成 approved。"""
        self.pm.approve_patch_by_id.return_value = {"ok": False, "reason": "治理门禁不过"}
        _rc, _out, _err = self._run()
        self.assertEqual(_rc, 2, f"应返回 2，实得 {_rc}")
        self.assertEqual(self.pending[0]["status"], "verified",
                         "API 拒绝后本地 status 被改了 —— 这就是原缺陷的假批准")
        self.assertNotIn("approved", self.pending[0].get("status", ""))

    def test_03_rejection_message_surfaced(self):
        self.pm.approve_patch_by_id.return_value = {"ok": False, "reason": "字段契约不完整"}
        _rc, _out, _err = self._run()
        self.assertIn("字段契约不完整", _err,
                      f"拒绝原因必须打到 stderr，实得 {_err!r}")

    def test_04_api_exception_not_swallowed_as_success(self):
        self.pm.approve_patch_by_id.side_effect = RuntimeError("boom")
        _rc, _out, _err = self._run()
        self.assertEqual(_rc, 1, f"异常应返回 1，实得 {_rc}")
        self.assertIn("boom", _err)


class TestB_AdjudicationMarkStillWritten(_Base):
    """判据：批准成功后仍要落「人工裁决」标记（留痕不能因为换写口就丢）。"""

    def test_01_mark_written_after_success(self):
        self.pm.approve_patch_by_id.return_value = {"ok": True, "reason": ""}
        self.pm._load_patch_list.return_value = [{"id": "p1", "status": "approved"}]
        _rc, _out, _err = self._run()
        self.assertEqual(_rc, 0, f"stderr={_err!r}")
        self.assertTrue(self.pm._save_json.called, "裁决标记没有写盘")
        _saved = self.pm._save_json.call_args[0][1]
        _row = next(x for x in _saved if x.get("id") == "p1")
        self.assertIs(_row.get("adjudicated"), True)
        self.assertEqual(_row.get("adjudicated_by"), "adjudicate_patch.cli")
        self.assertEqual(_row.get("adjudicated_verdict"), "approved")


class TestC_SourceLevelGuards(unittest.TestCase):
    """源码级：防后人把「只改字段」的假批准接回来。"""

    def _src(self):
        with open(_TOOL, encoding="utf-8") as fh:
            return fh.read()

    def test_01_no_bare_status_write(self):
        _s = self._src().replace("\r\n", "\n")
        self.assertNotIn('_p["status"] = "approved"\n    _p["adjudicated"] = True', _s,
                         "又变回「只改 status/adjudicated」的假批准 —— 刀8 回归")

    def test_02_uses_approve_patch_by_id(self):
        self.assertIn("approve_patch_by_id", self._src())

    def test_03_human_prefix_present(self):
        self.assertIn('"human:adjudicate_patch.cli"', self._src(),
                      "human 前缀是核心闸门豁免的唯一凭据，不能被删")

    def test_04_no_fallback_to_manual_write(self):
        """拒绝路径不得有「失败就自己改字段」的兜底。"""
        _s = self._src().replace("\r\n", "\n")
        _i = _s.find("批准未通过治理门禁")
        self.assertGreater(_i, 0)
        _tail = _s[_i:_i + 300]
        self.assertNotIn('= "approved"', _tail,
                         f"拒绝后仍写了 approved —— 假批准兜底回来了：{_tail[:200]!r}")


class TestD_RealPatchManagerContract(unittest.TestCase):
    """契约：`_m471_core_human_exempt` 认的就是 approved_source=="human"。

    这条不测 CLI，而是钉住**下游判据**——防止有人改了 CLI 却没人认。
    """

    def test_01_gate_reads_approved_source(self):
        _p = os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py")
        with open(_p, encoding="utf-8") as fh:
            _s = fh.read()
        self.assertIn('str(patch.get("approved_source") or "") == "human"', _s,
                      "核心闸门的人工判据被改了 —— 刀8 的前提失效，需同步复核")


if __name__ == "__main__":
    unittest.main(verbosity=2)
