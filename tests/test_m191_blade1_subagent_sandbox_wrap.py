# -*- coding: utf-8 -*-
"""191刀1：SUBAGENT 刀B 沙箱串联 + status 错标修正 隔离单测。

覆盖：
  - build_child_wrapper 包裹命令行（串联沙箱限制到子进程 / Windows 层串证）
  - timeout 仍生效
  - status 错标修正（CHEAP_MODEL 空 → refused 而非 budget_exceeded）
  - 真实端到端（Windows 下真起 worker，验证 argv 透传与 wrapper 串接可用）
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch

from nucleus.reasoning import subagent_runner as runner


def _cfg(**kw):
    base = dict(
        ENABLE_SUBAGENT_NO_MESSAGE_TOOL=True,
        SUBAGENT_CHEAP_MODEL="cheap-llm",
        SUBAGENT_BUDGET_TOKENS=0,
        SUBAGENT_TIMEOUT_SECONDS=42,
        ENABLE_SUBAGENT_RUNNER=True,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_wrapper_wired_into_command():
    """build_child_wrapper 串接到子进程命令行；allow_full_builtins=True；timeout 生效。"""
    with patch.object(runner, "build_child_wrapper", return_value="<WRAPPER_SRC>") as mk_wrap, \
         patch.object(runner.subprocess, "run") as mk_run:
        fake = MagicMock()
        fake.returncode = 0
        fake.pid = 1234

        def _side(cmd, **kw):
            # 模拟 worker 写回 output 文件（与真实真起行为一致），否则 runner 读不到结果。
            with open(cmd[5], "w", encoding="utf-8") as _f:
                json.dump({"stats": {"n": 3}}, _f)
            return fake

        mk_run.side_effect = _side
        r = runner.run_subagent({"task": {"op": "echo", "payload": "x"}},
                                 cfg=_cfg(), dry_run=False)
        # 串证：wrapper 手臂被调用
        assert mk_wrap.called
        assert mk_wrap.call_args.kwargs.get("allow_full_builtins") is True
        # cmd 形态：python -c <wrapper> <launcher> input output stderr
        cmd = mk_run.call_args.args[0]
        assert cmd[0] == sys.executable
        assert cmd[1] == "-c"
        assert cmd[2] == "<WRAPPER_SRC>"
        assert cmd[3].endswith(".py")          # launcher 文件被写入
        # timeout 仍透传
        assert mk_run.call_args.kwargs.get("timeout") == 42
        assert r.status == "success"
        assert r.stats.get("n") == 3


def test_status_refused_when_cheap_empty():
    """硬条款③：CHEAP_MODEL 空 → refused（191刀1 修正原 budget_exceeded 错标）。"""
    with patch.object(runner.subprocess, "run") as m:
        r = runner.run_subagent({"prompt": "x"},
                                 cfg=_cfg(SUBAGENT_CHEAP_MODEL=""), dry_run=False)
        assert r.status == "refused"
        assert "CHEAP_MODEL" in (r.error or "")
        m.assert_not_called()                  # 早于真起支路返回，未起子进程


def test_real_end_to_end_echo():
    """真实真起：Windows 下 wrapper 串接 + argv[1:] 透传可用（共窗前自证）。"""
    r = runner.run_subagent({"task": {"op": "echo", "payload": "hello-191"}},
                             cfg=_cfg(), dry_run=False)
    assert r.status == "success"
    assert r.stats.get("echo") == "hello-191"
    assert r.stats.get("deterministic") is True
