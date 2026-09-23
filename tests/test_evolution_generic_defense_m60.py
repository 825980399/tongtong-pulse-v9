# -*- coding: utf-8 -*-
"""主线第60批 T2+T5 门控：snippet 诊断日志 + 通用防御（organ/method 空 + 降级 + 统计）。

覆盖：
  - T2：_read_snippet_from_file 的诊断日志（文件不存在 / 文件读取失败 / 取前 N 行）。
  - T2：repair_with_distillation 主循环方法体取不到时的「方法不存在」日志。
  - T5：organ/method 均空时的通用防御——
        ① 路径可推断 organ → 回填（不消耗名额）；
        ② 路径无法推断 → 跳过并告警（不消耗名额）；
        ③ _snippet 为空降级（文件存在且项目内 → 取前100行）。
  - T5：repair_with_distillation 入口统计「跳过」计数（过滤 + organ/method 空）。
"""
import logging
import os
import sys
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.SafeEvolutionExecutor import (  # noqa: E402
    SafeEvolutionExecutor,
    _infer_organ_from_path,
)


# ---------- T5：_infer_organ_from_path ----------

def test_infer_organ_from_path():
    """按已知目录前缀推断 organ；推断不到返回空串。"""
    assert _infer_organ_from_path("organs/body/PulseLung.py") == "body"
    assert _infer_organ_from_path("organs/brain/SelfAwarenessEngine.py") == "brain"
    assert _infer_organ_from_path("nucleus/reasoning/SafeEvolutionExecutor.py") == "nucleus"
    assert _infer_organ_from_path("utils/helper.py") == ""
    assert _infer_organ_from_path("") == ""
    assert _infer_organ_from_path("functions/web_chat.py") == ""


# ---------- T2：_read_snippet_from_file 诊断日志 ----------

def test_read_snippet_file_not_exist_logs(caplog, tmp_path):
    """文件不存在 → 返回空串并记录 [代码片段] 文件不存在。"""
    _ex = SafeEvolutionExecutor()
    _ex._project_root = str(tmp_path)
    _p = os.path.join(str(tmp_path), "nope.py")
    with caplog.at_level(logging.DEBUG, logger="SafeEvolutionExecutor"):
        _out = _ex._read_snippet_from_file(_p, "foo")
    assert _out == ""
    assert "[代码片段] 文件不存在" in caplog.text


def test_read_snippet_read_failure_logs(caplog, tmp_path):
    """文件读取抛异常 → 返回空串并记录 [代码片段] 文件读取失败。"""
    _ex = SafeEvolutionExecutor()
    _ex._project_root = str(tmp_path)
    _real = tmp_path / "real.py"
    _real.write_text("def foo():\n    return 1\n")
    with mock.patch("builtins.open", side_effect=OSError("denied")):
        with caplog.at_level(logging.DEBUG, logger="SafeEvolutionExecutor"):
            _out = _ex._read_snippet_from_file(str(_real), "foo")
    assert _out == ""
    assert "[代码片段] 文件读取失败" in caplog.text


def test_read_snippet_returns_head(tmp_path):
    """真实可读文件 → 返回非空片段（取文件头）。"""
    _ex = SafeEvolutionExecutor()
    _ex._project_root = str(tmp_path)
    _real = tmp_path / "real.py"
    _real.write_text("import os\n\ndef foo():\n    return 1\n")
    _out = _ex._read_snippet_from_file(str(_real), "foo", max_lines=100)
    assert "def foo" in _out


# ---------- T2：主循环「方法不存在」诊断日志 ----------

def test_repair_logs_method_not_exist(caplog):
    """organ+method 设了但方法体取不到 → 记录 [代码片段] 方法不存在。"""
    import logging as _lg

    _fake_hub = mock.MagicMock()
    _ex = SafeEvolutionExecutor()
    _inspector = mock.MagicMock()
    _inspector.get_method_body.return_value = {"body": ""}  # 取不到方法体
    _issues = [
        {"type": "print_instead_of_log", "organ": "body",
         "method": "nonexistent_method", "file": "organs/body/foo.py"},
    ]
    with caplog.at_level(_lg.DEBUG, logger="SafeEvolutionExecutor"):
        with mock.patch(
            "nucleus.mnemosyne.verification_learning_hub.get_verification_learning_hub",
            return_value=_fake_hub,
        ):
            with mock.patch.object(_ex, "_call_llm_for_repair", return_value=""):
                _ex.repair_with_distillation(_issues, self_inspector=_inspector)
    assert "[代码片段] 方法不存在" in caplog.text


# ---------- T5：organ/method 空通用防御 ----------

def test_organ_method_empty_uninferable_skipped(caplog):
    """organ/method 均空且路径无法推断 → 跳过并告警（不消耗名额）。"""
    _fake_hub = mock.MagicMock()
    _ex = SafeEvolutionExecutor()
    _issues = [
        {"type": "print_instead_of_log", "file": "utils/helper.py"},
    ]
    with caplog.at_level(logging.DEBUG, logger="SafeEvolutionExecutor"):
        with mock.patch(
            "nucleus.mnemosyne.verification_learning_hub.get_verification_learning_hub",
            return_value=_fake_hub,
        ):
            with mock.patch.object(_ex, "_call_llm_for_repair", return_value=""):
                _res = _ex.repair_with_distillation(_issues)
    assert isinstance(_res, dict)
    assert "organ/method空跳过1个" in caplog.text
    assert _res.get("skipped", 0) >= 1


def test_organ_method_empty_inferable_backfilled():
    """organ/method 均空但路径可推断 organ → 回填 organ 后保留（不跳过）。"""
    _fake_hub = mock.MagicMock()
    _ex = SafeEvolutionExecutor()
    _issues = [
        {"type": "print_instead_of_log", "file": "organs/body/foo.py"},
    ]
    with mock.patch(
        "nucleus.mnemosyne.verification_learning_hub.get_verification_learning_hub",
        return_value=_fake_hub,
    ):
        with mock.patch.object(_ex, "_call_llm_for_repair", return_value=""):
            _res = _ex.repair_with_distillation(_issues)
    assert isinstance(_res, dict)
    # 入口回填了 organ，问题被保留（未计入 skipped）
    assert _issues[0].get("organ") == "body"
    assert _res.get("skipped", 0) == 0


def test_repair_combines_backup_filter_and_empty_om(caplog):
    """集成：同一批混合「备份目录问题 + organ/method 空」→ 分类计数正确。"""
    _fake_hub = mock.MagicMock()
    _ex = SafeEvolutionExecutor()
    _issues = [
        {"type": "print_instead_of_log", "file": ".bak_batch59/organs/body/x.py"},
        {"type": "bare_except", "file": "utils/helper.py"},
    ]
    with caplog.at_level(logging.DEBUG, logger="SafeEvolutionExecutor"):
        with mock.patch(
            "nucleus.mnemosyne.verification_learning_hub.get_verification_learning_hub",
            return_value=_fake_hub,
        ):
            with mock.patch.object(_ex, "_call_llm_for_repair", return_value=""):
                _res = _ex.repair_with_distillation(_issues)
    assert isinstance(_res, dict)
    assert "备份目录1个" in caplog.text
    assert "organ/method空跳过1个" in caplog.text
    assert _res.get("skipped", 0) >= 2
