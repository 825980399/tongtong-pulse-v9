# -*- coding: utf-8 -*-
"""186批刀2：SUBAGENT worker 最小实装（零停窗）。

定位：
  - 子代理 runner（nucleus/reasoning/subagent_runner.py，183批刀1）在「真起子进程」
    （刀B共窗实证）时通过以下命令调起的 worker 本体：
        python -m nucleus.reasoning.subagent_worker <input.json> <output.json> [stderr.log]
  - 读 input json（task/budget/timeout）→ 单次调用（DETERMINISTIC 路径，不调 LLM）
    → 写 output json（result/exit_code）→ exit code 语义（0=成功 / 非0=失败）。

★不做（任务书明确）：
  - 不真起子进程：本文件就是被 runner 真起的 worker 本体；本刀只就位它，是否真起推刀B共窗；
  - 不翻开关：默认关——仅当 runner 在 ENABLE_SUBAGENT_RUNNER=True 且共窗验证后 dry_run=False
    才会调起本 worker，故本文件存在对系统「零行为变化」；
  - 不调 LLM：worker 只走 DETERMINISTIC 路径（纯函数计算 / 确定性 ack），无任何模型调用。

输入约定（与 subagent_runner 对齐）：
  input  : {"task": <dict|str>, "budget_tokens": <int>, "timeout": <seconds>, "model": <str>, ...}
            （runner 将 task 规格展开合并到顶层，故 task 缺省时取整个 spec）
输出约定：
  output : {"result": <any>, "exit_code": <int>, "stats": <any>}  # 187批刀5：stats 与 runner 对齐
  exit code: 0=成功；非0=失败（约定 2=输入读取/解析失败）。

确定性操作（task.op 可选）：
  echo / sum / hash / noop —— 均为纯函数，无副作用、无 IO（除 input/output 文件）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

from nucleus._silent_except import silent_exc

_MODULE_TAG = "subagent_worker"

# ★284批刀4：能力面声明 + 断言（NO_MESSAGE_TOOL worker 侧拦截实现）。
#   此前 NO_MESSAGE_TOOL 只在 runner 侧做准入拒绝（假护栏）；worker 侧无任何拦截。
#   本刀让 worker 显式声明「无发消息能力」（capabilities 空），并在启动时断言
#   未 import 器官链 / EventBus / Mouth，output 回传 capabilities=[] 供 runner 校验。
_CAPABILITIES: tuple[str, ...] = ()  # 能力面：空 = 无发消息能力（NO_MESSAGE_TOOL）


def _assert_no_message_capability() -> None:
    """能力面断言：worker 进程内不得 import 器官链（organs.*，含 Mouth/EventBus）。

    仅做「声明性拦截」——检查 sys.modules 里已加载的器官链模块名；
    命中则抛 AssertionError（由 main 捕获归一为 exit_code=5 拒绝产物）。
    不 import 任何器官链模块本身（零新增依赖）。
    生产态 worker 为独立子进程（sys.modules 干净），此断言真实生效；
    测试共享进程时由铁律151 逐文件隔离单跑规避顺序污染。
    """
    _loaded = [m for m in sys.modules if m == "organs" or m.startswith("organs.")]
    if _loaded:
        raise AssertionError(
            f"worker 越权加载器官链模块: {sorted(_loaded)[:5]}")

# ★275批刀2：产物 schema 分档
#   说明书类（方法级四字段）——器官职责说明书 / 种子记忆类共用
ORGAN_SPEC_FIELDS = ("功能", "关键步骤", "依赖的外部数据", "潜在风险")
#   梦境类（文本 + 关键词）——★明确不入节点池，防把虚构内容当认知沉淀
DREAM_SCHEMA_FIELDS = ("文本", "关键词")
DREAM_NO_NODE_POOL = True


def _open_stderr_log(stderr_file: str | None):
    """打开 stderr 诊断日志（可选）；返回可写句柄或 None。"""
    if not stderr_file:
        return None
    try:
        return open(stderr_file, "a", encoding="utf-8")
    except OSError as exc:
        silent_exc(exc, where="_open_stderr_log")
        return None


def _handle_llm_op(spec: dict, task: dict, result: dict, model_fallback) -> int:
    # ★275批刀2：op=llm —— 经 channel_client 单次 HTTP 调模型。
    #   返回 exit_code：0=成功；4=产物不可解析（schema 校验失败 / 调用失败 / 缺 prompt）。
    #   密钥从进程环境变量 SUBAGENT_API_KEY 取回（input_file 里不写 api_key）。
    _prompt = str(task.get("prompt", "") or "")
    if not _prompt:
        result["error"] = "op=llm 缺 prompt"
        return 4
    try:
        from nucleus.reasoning.channel_client import call_channel
    except Exception as _imp_e:
        silent_exc(_imp_e, where="subagent_worker::_handle_llm_op(import)")
        result["error"] = "channel_client 不可用: %s" % type(_imp_e).__name__
        return 4
    _rep = call_channel(spec.get("channel") or {}, _prompt,
                        timeout=spec.get("timeout"),
                        system=str(task.get("system", "") or ""))
    result["channel"] = _rep.get("channel")
    result["model"] = _rep.get("model") or model_fallback
    result["usage"] = _rep.get("usage") or {}
    if not _rep.get("ok"):
        result["error"] = _rep.get("error") or "渠道调用失败"
        return 4
    _answer = _rep.get("text") or ""
    result["answer"] = _answer
    _schema = str(task.get("schema", "organ_spec") or "organ_spec")
    result["schema"] = _schema
    # 产物可解析性校验（刀4：exit_code=4）
    _fields = DREAM_SCHEMA_FIELDS if _schema == "dream" else ORGAN_SPEC_FIELDS
    _missing = [k for k in _fields if k not in _answer]
    if _missing:
        result["missing_fields"] = _missing
        result["error"] = "产物缺字段: %s" % ",".join(_missing)
        return 4
    return 0


def run_once(spec: Any) -> tuple[dict, int]:
    """单次确定性调用（DETERMINISTIC 路径，不调 LLM）。

    Args:
        spec: 输入 JSON 反序列化后的对象（dict / str / 其它）。
    Returns:
        (result_dict, exit_code)
        - exit_code=0 表示确定性处理成功；
        - 仅当 spec 非法（非 dict 且无 type 可判定）时仍返回 0（确定性 ack），
          真正的输入读取失败由 main 层以 exit_code=2 处理。
    """
    budget = spec.get("budget_tokens") if isinstance(spec, dict) else None
    timeout = spec.get("timeout") if isinstance(spec, dict) else None
    model = spec.get("model") if isinstance(spec, dict) else None

    result: dict[str, Any] = {"deterministic": True, "model": model}

    if not isinstance(spec, dict):
        # 非 dict 输入：确定性回显其类型
        result["handled"] = True
        result["task_type"] = type(spec).__name__
        result["budget_tokens"] = budget
        result["timeout"] = timeout
        return result, 0

    task = spec.get("task", spec)
    op = task.get("op") if isinstance(task, dict) else None
    _exit = 0  # ★275批刀4：0成功 / 3未知op / 4产物不可解析

    if isinstance(task, dict):
        if op == "echo":
            result["echo"] = task.get("payload")
        elif op == "sum":
            _nums = task.get("nums", [])
            try:
                result["sum"] = sum(int(x) for x in _nums)
            except (TypeError, ValueError) as exc:
                silent_exc(exc, where="run_once.sum")
                result["sum"] = None
        elif op == "hash":
            result["sha256"] = hashlib.sha256(
                str(task.get("data", "")).encode("utf-8")
            ).hexdigest()
        elif op == "noop":
            result["handled"] = True
        elif op == "llm":
            # ★275批刀2：worker 接模型（甲′路线）—— 经 channel_client 单次 HTTP
            _exit = _handle_llm_op(spec, task, result, model)
        else:
            # ★275批刀4：未知 op 原为 exit 0（假绿根：prompt 打错字也算成功）
            #   ⇒ 改为 exit_code=3（未知任务）
            result["handled"] = True
            result["task_keys"] = sorted(task.keys())
            result["error"] = "unknown op: %r" % (op,)
            _exit = 3
    elif isinstance(task, str):
        result["echo"] = task
    else:
        result["handled"] = True

    result["budget_tokens"] = budget
    result["timeout"] = timeout
    return result, _exit


def _write_output(output_file: str, result: dict, exit_code: int) -> None:
    """安全写入 output json（先写临时文件再重命名，避免半写）。"""
    # ★284批刀4：output 回传 capabilities=[] 供 runner 校验（能力面声明落地）
    payload = {"result": result, "exit_code": exit_code, "stats": result,
               "capabilities": list(_CAPABILITIES)}  # 187批刀5：补 stats 键对齐 runner
    tmp = output_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    import os
    os.replace(tmp, output_file)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读 input → run_once → 写 output → 返回 exit_code。

    约定 argv = [input_file, output_file, stderr_file?]
    """
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write(
            "usage: subagent_worker <input.json> <output.json> [stderr.log]\n"
        )
        return 2

    input_file, output_file = argv[0], argv[1]
    stderr_file = argv[2] if len(argv) > 2 else None
    _log = _open_stderr_log(stderr_file)

    def _log_line(msg: str) -> None:
        if _log is not None:
            _log.write(msg + "\n")
            _log.flush()

    try:
        with open(input_file, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as exc:
        _log_line(f"[{_MODULE_TAG}] input read failed: {type(exc).__name__}: {exc}")
        _write_output(output_file, {
            "error": f"input read failed: {type(exc).__name__}: {exc}",
            "deterministic": True,
        }, 2)
        if _log is not None:
            _log.close()
        return 2

    # ★284批刀4：能力面断言——worker 启动即校验未接发消息/器官链能力；越权则拒产
    try:
        _assert_no_message_capability()
    except AssertionError as _cap_e:
        _log_line(f"[{_MODULE_TAG}] capability assertion failed: {_cap_e}")
        _write_output(output_file, {
            "error": f"capability assertion failed: {_cap_e}",
            "deterministic": True,
        }, 5)
        if _log is not None:
            _log.close()
        return 5

    result, exit_code = run_once(spec)
    _log_line(f"[{_MODULE_TAG}] deterministic run done: exit_code={exit_code}")
    _write_output(output_file, result, exit_code)
    if _log is not None:
        _log.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
