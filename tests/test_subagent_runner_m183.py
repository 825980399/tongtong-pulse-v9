# -*- coding: utf-8 -*-
"""183批刀1：SUBAGENT runner 协议刀隔离单测。

验证四条硬条款：①强制exit code检查 ②NO_MESSAGE_TOOL闸先行 ③预算校验 ④timeout清理。
"""
import types

from unittest.mock import patch, MagicMock

from nucleus.reasoning import subagent_runner as runner


def _cfg(**kw):
    base = dict(
        ENABLE_SUBAGENT_NO_MESSAGE_TOOL=False,
        SUBAGENT_CHEAP_MODEL="",
        SUBAGENT_BUDGET_TOKENS=0,
        SUBAGENT_TIMEOUT_SECONDS=300,
        ENABLE_SUBAGENT_RUNNER=False,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_no_message_tool_gate_refuses():
    """硬条款②：NO_MESSAGE_TOOL 未翻开 → refused，绝不真起。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=False, SUBAGENT_CHEAP_MODEL="cheap-llm")
    with patch.object(runner.subprocess, "run") as m:
        r = runner.run_subagent({"prompt": "x"}, cfg=cfg, dry_run=False)
        assert r.status == "refused"
        m.assert_not_called()


def test_cheap_model_required():
    """硬条款③：SUBAGENT_CHEAP_MODEL 为空（直连主模型）→ refused（191刀1 修正原 budget_exceeded 错标）。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="")
    r = runner.run_subagent({"prompt": "x"}, cfg=cfg, dry_run=False)
    assert r.status == "refused"
    assert "CHEAP_MODEL" in (r.error or "")


def test_budget_exceeded():
    """硬条款③：任务预算超过上限 → budget_exceeded。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="cheap-llm",
               SUBAGENT_BUDGET_TOKENS=1000)
    r = runner.run_subagent({"prompt": "x", "budget_tokens": 2000}, cfg=cfg, dry_run=False)
    assert r.status == "budget_exceeded"


def test_dry_run_success_when_gates_pass():
    """闸与预算通过 + dry_run=True → success（不真起，符合本刀不真起）。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="cheap-llm",
               SUBAGENT_BUDGET_TOKENS=5000)
    with patch.object(runner.subprocess, "run") as m:
        r = runner.run_subagent({"prompt": "x", "budget_tokens": 100}, cfg=cfg, dry_run=True)
        assert r.status == "success"
        assert r.stats.get("dry_run") is True
        m.assert_not_called()


def test_default_dry_run_when_switch_off():
    """默认 dry_run 由 ENABLE_SUBAGENT_RUNNER（默认关）驱动 → 不真起。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="cheap-llm")
    with patch.object(runner.subprocess, "run") as m:
        r = runner.run_subagent({"prompt": "x"}, cfg=cfg)  # dry_run=None
        assert r.status == "success"
        m.assert_not_called()


def test_exit_code_enforced_failure(tmp_path):
    """硬条款①：非零退出码 → failure（立法禁止不检查exit code）。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="cheap-llm")
    out = tmp_path / "out.json"
    out.write_text("{}", encoding="utf-8")
    fake = MagicMock()
    fake.returncode = 1
    fake.pid = 9999
    with patch.object(runner.subprocess, "run", return_value=fake):
        r = runner.run_subagent({"prompt": "x"}, cfg=cfg, dry_run=False,
                                output_file=str(out))
        assert r.status == "failure"
        assert r.returncode == 1


def test_exit_code_zero_success(tmp_path):
    """退出码 0 → success，且读取结果文件。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="cheap-llm")
    out = tmp_path / "out.json"
    out.write_text('{"stats": {"n": 3}}', encoding="utf-8")
    fake = MagicMock()
    fake.returncode = 0
    fake.pid = 8888
    with patch.object(runner.subprocess, "run", return_value=fake):
        r = runner.run_subagent({"prompt": "x"}, cfg=cfg, dry_run=False,
                                output_file=str(out))
        assert r.status == "success"
        assert r.pid == 8888
        assert r.stats.get("n") == 3


def test_timeout_cleanup(tmp_path):
    """硬条款④：超时 → 清理子进程 + 返回 timeout。"""
    cfg = _cfg(ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True, SUBAGENT_CHEAP_MODEL="cheap-llm")
    out = tmp_path / "out.json"

    class _Proc:
        def kill(self):
            self.killed = True
        def wait(self, timeout=None):
            return None
    proc = _Proc()
    exc = runner.subprocess.TimeoutExpired(cmd="x", timeout=1)
    exc.proc = proc

    with patch.object(runner.subprocess, "run", side_effect=exc):
        r = runner.run_subagent({"prompt": "x"}, cfg=cfg, dry_run=False,
                                output_file=str(out))
        assert r.status == "timeout"
        assert getattr(proc, "killed", False) is True
