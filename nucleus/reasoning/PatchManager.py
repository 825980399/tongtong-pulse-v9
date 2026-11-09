# -*- coding: utf-8 -*-
from nucleus._silent_except import silent_exc

"""
PatchManager.py —— 补丁管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 代码补丁的创建与应用管理
机制: 大型模块（2246行），包含1个类、10个核心方法，采用分层架构实现
定位: 进化管理层
"""

import ast
import builtins
import difflib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any, ClassVar

from nucleus.logger import (
    get_module_logger,
)
from nucleus.logger import (
    noise_reduction_enabled as _noise_reduce,
)

_module_logger = get_module_logger("PatchManager")

# ★第162批刀5（E2）：事件总线发布失败累计计数（可观测 + 门控读取）
_publish_fail_count = 0

# ======================================================================
# ★405刀3：共同决策（ConsentEvent）发射通道
# ======================================================================
# 背景（402 刀1 契约断裂 19 条里的第 1、2 条）：
#   `PulseConsent` 在 `get_resonance_conditions()` 里明写承接
#   `ConsentEvent.PROPOSE` / `ConsentEvent.DECIDE`，但**全库零发射**
#   ⇒ 共同决策器官永久空转（proposal_count / accepted_count 恒 0）。
#   查"该谁发"的结论：**审批流程**该发 —— 补丁入队 = 一次"重大变更提议"，
#   人工审批 = 这项提议的"决定"。PatchManager 正是这条流程的唯一收口。
#
# 为什么用注入式而不是直接 import 器官：
#   ① PatchManager 不持有器官/信息场（全库 `grep pulse_core` = 0），
#      它拿不到 PulseCore，硬接会引入反向依赖（reasoning → organs）；
#   ② 不注入 = 一条不发（默认零行为变化），单测/离线脚本完全不受影响；
#   ③ 与项目既有范式一致（`patch_execution_dispatch.set_dispatch_framework`）。
_CONSENT_EMITTER: Any = None
#: 发射失败累计（与 `_publish_fail_count` 同型：可观测、不阻断主流程）
_consent_emit_fail_count = 0


def set_consent_emitter(fn: Any) -> None:
    """注入共同决策脉冲发射器（装配层调用一次）。

    Args:
        fn: 形如 ``fn(event_type: str, payload: dict, priority: int) -> Any``。
            传 None 表示摘除（测试隔离用）。

    本函数不校验签名——校验的收益配不上它带来的耦合；真接错了，
    第一次发射就会在 `_emit_consent` 的异常分支里留下 WARNING。
    """
    global _CONSENT_EMITTER
    _CONSENT_EMITTER = fn


def get_consent_emitter() -> Any:
    """当前注入的发射器（None = 未注入）。"""
    return _CONSENT_EMITTER


def get_consent_emit_fail_count() -> int:
    """共同决策脉冲发射失败计数（供观测/测试断言）。"""
    return _consent_emit_fail_count


def _emit_consent(event_type: str, payload: dict,
                  priority: int = 5) -> bool:
    """发一条 ``consent.*`` 脉冲；未注入 / 发送失败均返回 False。

    ★**绝不抛异常、绝不阻断审批主流程**：审批是安全关键路径，脉冲是观测与
    协同手段。发射失败只累计计数 + 打一次 WARNING（限频，见下）。

    ★WARNING 限频：补丁入队在生产上可达数百次/天（实测 915 次/1.5h 的去重
    日志就是这个量级）。每次失败都打会淹掉日志，故只在前 3 次与每 50 次
    打一条 —— 既不会静默，也不会刷屏。
    """
    global _consent_emit_fail_count
    _fn = _CONSENT_EMITTER
    if _fn is None:
        return False
    try:
        from nucleus.const import ConsentEvent as _CE405
        if event_type not in (_CE405.PROPOSE, _CE405.DECIDE):
            return False
    except Exception as _e:
        silent_exc(_e, where="PatchManager::_emit_consent/const", level="debug")
    try:
        _fn(str(event_type), dict(payload or {}), int(priority))
        return True
    except Exception as _e:
        _consent_emit_fail_count += 1
        if _consent_emit_fail_count <= 3 or _consent_emit_fail_count % 50 == 0:
            _module_logger.warning(
                "[405刀3] 共同决策脉冲发射失败（第 %d 次，不影响审批主流程）: %s: %s",
                _consent_emit_fail_count, type(_e).__name__, _e)
        return False


def _m405_proposal_payload(patch: Any) -> dict:
    """把一条补丁折叠成 ``consent.propose`` 的载荷。

    ★只放**标识与定位**字段（id/file/method/source），不放 `modified_code`：
    整段代码进脉冲会让 pulse_log 体积按 MB 级膨胀，且决策方要的是"改哪儿"，
    不是"改成什么"。要看正文去 patch 队列按 id 取。
    """
    _p = patch if isinstance(patch, dict) else {}
    return {
        "proposal_id": str(_p.get("id") or ""),
        "details": {
            "file": str(_p.get("file") or ""),
            "method": str(_p.get("method") or ""),
            "repair_source": str(_p.get("repair_source") or ""),
            "entry_source": str(_p.get("entry_source") or ""),
            "trust_score": _p.get("trust_score"),
        },
        "source": "PatchManager",
    }


def _m344_split_failed_details(failed_details) -> dict:
    """★344批刀6：把失败记录按**形态**与**状态**分流（纯函数、无副作用）。

    为什么需要它：17:48:30 生产实测 `AttributeError: 'str' object has no attribute 'get'`
    —— 上游把一条 `str` 混进失败记录，原写法当"全是 dict"处理，一条坏数据就让
    **整批反思**被外层 except 吞掉（反思实际一条都没跑完）。分流后：
    坏数据逐条留痕、好数据照常反思。

    状态判定用**显式前缀/等值**，不从理由字符串里找关键字（铁律153）。

    Returns:
        `{"skipped_non_dict": int, "non_dict_values": list,   # 坏数据原值（留痕用）
          "dicts": list,                                      # 全部 dict 记录（保序）
          "actionables": list}`                               # 其中需反思的
    """
    _non: list = []
    _dicts: list = []
    _act: list = []
    for _d in (failed_details or []):
        if not isinstance(_d, dict):
            _non.append(_d)
            continue
        _dicts.append(_d)
        _status = str(_d.get("status", "") or "")
        if _status.startswith("rejected") or _status == "failed":
            _act.append(_d)
    return {"skipped_non_dict": len(_non), "non_dict_values": _non,
            "dicts": _dicts, "actionables": _act}


def _reflect_on_failed_patches(failed_details: list, project_root: str | None = None) -> None:
    """196刀2 运行时失败路→反思（半根刀补齐）。

    启动期接线 main.py:3934（SelfVerifier 验证失败路径）已通电；运行期 PatchManager
    失败路此前未接（半根刀）。本函数补齐运行期失败路：对 apply_all_pending 收集到的
    每条失败补丁触发 SelfReflectionEngine 维度级诊断。

    零行为变化：仅当 config.ENABLE_PATCH_FAILURE_REFLECTION 显式开启（默认 False /
    未设置）时触发；引擎不可用或任何异常均经 silent_exc 静默吞掉，绝不阻断主流程。
    """
    if not failed_details:
        return
    try:
        import config as _cfg
        if not getattr(_cfg, "ENABLE_PATCH_FAILURE_REFLECTION", False):
            return
        from nucleus.evolution.SelfReflectionEngine import get_self_reflection_engine
        _engine = get_self_reflection_engine(project_root=project_root or "")
        # ★344批刀6：失败记录**可能是 str**（17:48:30 生产实测
        #   `AttributeError: 'str' object has no attribute 'get'`）。
        #   原写法把 `failed_details` 当成"全是 dict"处理，一条坏数据就让
        #   **整批反思**被外层 except 吞掉（静默异常可见化里看得到痕迹，
        #   但反思实际一条都没跑完）。
        #   这里逐条按类型分流：非 dict ⇒ 不猜它的语义（拿不到 file/id，
        #   硬塞给引擎只会污染反思语料），但**必须留痕**，让"上游传了 str"
        #   这件事可见，而不是继续静默。
        _split = _m344_split_failed_details(failed_details)
        for _bad in _split["non_dict_values"]:
            _module_logger.info(
                "[344刀6] 失败记录非字典形态，跳过反思: type=%s value=%r",
                type(_bad).__name__, str(_bad)[:120])
        for _d in _split["actionables"]:
            try:
                _engine.reflect_and_improve(
                    {"id": _d.get("id"), "file": _d.get("file"), "reason": _d.get("reason")},
                    verification_detail=_d.get("reason", ""),
                )
            except Exception as _e:
                silent_exc(_e, where="nucleus.reasoning.PatchManager::_reflect_on_failed_patches", level="debug")
        if _split["skipped_non_dict"]:
            _module_logger.info(
                "[344刀6] 本批失败记录中非字典形态 %d/%d 条（已跳过并逐条留痕）",
                _split["skipped_non_dict"], len(failed_details))
    except Exception as _e:
        silent_exc(_e, where="nucleus.reasoning.PatchManager::_reflect_on_failed_patches", level="debug")


def get_publish_fail_count() -> int:
    """PatchManager 自上线以来事件总线发布失败累计次数（E2 升级 WARNING 后供读取）。"""
    return _publish_fail_count


def _m53_write_check_on() -> bool:
    """★第53批 T2（P0-补丁2）灰度开关：审批是否检查写入结果。

    开启（默认）→ approve_patch / approve_all_patches 检查 _save_patch_list 的返回值，
    写盘失败（如被 WriteGuard 拦截）时返回 ``ok=False``；
    关闭 → 恢复改造前行为（忽略写入结果，恒返回 ``ok=True``），零回归。

    每次调用重新 ``import config`` → 改配置即时生效，无需重启框架。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_PATCH_APPROVE_WRITE_CHECK", True))
    except Exception as _e:
        _module_logger.debug(f"[补丁审批] 开关读取失败，按默认(启用)处理: {type(_e).__name__}: {_e}")
        return True


def _m54_stale_days() -> float:
    """★第54批 T3.2：补丁过期天数（读配置，默认 7 天）。"""
    try:
        import config as _c
        return float(getattr(_c, "PATCH_AUTO_APPROVE_STALE_DAYS", 7))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m54_stale_days L57", level="debug")
        return 7.0


def _m54_stale_mark_on() -> bool:
    """★第54批 T3.2 灰度：关闭 → 不做过期标记（复现改造前行为）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_PATCH_STALE_MARK", True))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m54_stale_mark_on L66", level="debug")
        return True


def _m54_auto_apply_on() -> bool:
    """★第54批 T3.3：auto_apply_enabled 总开关（默认 False → 只审批不应用）。"""
    try:
        import config as _c
        if not getattr(_c, "ENABLE_PATCH_APPROVE_AUTO_APPLY", True):
            return False
        return bool(getattr(_c, "EVOLUTION_CONFIG", {}).get("auto_apply_enabled", False))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m54_auto_apply_on L77", level="debug")
        return False


# ★第89批 T-89a：LLM 补丁完整性「最低相似度」阈值（改造前硬编码 0.5）。
_M89_PATCH_MIN_SIMILARITY = 0.3
# ★第89批 T-89a：与阈值并列的「长度下限」（modified 不得短于 original 的 1/3；0 = 关闭）。
_M89_PATCH_MIN_LEN_RATIO = 1.0 / 3.0
# 改造前的阈值（开关关闭时逐字回退到此值）
_M89_PATCH_MIN_SIMILARITY_LEGACY = 0.5


def _m89_patch_sim_switch_on() -> bool:
    """★第89批 T-89a 灰度开关：LLM 补丁完整性阈值调整（含长度下限）是否启用。

    开启（默认）→ 相似度阈值 0.3 + 长度下限 1/3；
    关闭 → 相似度阈值 0.5 且无长度下限（**逐字**回到第89批前行为），零回归。

    默认值内联在本模块（遵守本批红线「不改 config.py 开关」）；
    每次调用重新 ``import config`` → 改配置即时生效，无需重启框架。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M89_PATCH_SIM_THRESHOLD", True))
    except Exception as _e:
        _module_logger.debug(
            f"[补丁完整性] 阈值开关读取失败，按默认(启用)处理: {type(_e).__name__}: {_e}")
        return True


def _m89_min_similarity() -> float:
    """★第89批 T-89a：读取 LLM 补丁最低相似度阈值（开关关 → 0.5，改造前值）。

    阈值下调依据（实测）：
      · 过闸的 3 个真实代码补丁按本函数同口径复算 = 0.5137 / **0.5006** / 0.5774
        （`organs/body/PulseLiver.py:_m70_kal_node_count` 距 0.5 仅 **0.0006**）；
      · 0.5 对「LLM 输出整段替换代码」这一形态过紧——合法的局部改写（保留了原文
        约 69% 内容却新增大量代码）ratio 会自然落到 0.5 附近，抖动即误杀。
    """
    if _m89_patch_sim_switch_on():
        return _M89_PATCH_MIN_SIMILARITY
    return _M89_PATCH_MIN_SIMILARITY_LEGACY


def _m89_len_floor() -> float:
    """★第89批 T-89a：读取「长度下限」比值（开关关 → 0.0 = 不启用）。

    为什么阈值下调必须**同时**补长度下限（见 `_check_llm_patch_completeness` 关3b）：
    相似度是**对称**指标，对「modified 远短于 original」的截断形态不敏感；
    只降阈值会在长方法上放行「删掉 80% 方法体」的补丁。
    """
    if _m89_patch_sim_switch_on():
        return _M89_PATCH_MIN_LEN_RATIO
    return 0.0


# ========================================== 376刀4：try 块平衡静态闸（关2a）
#  _m376_try_balance
#  【为什么】跨夜 12 次 `patch_multi_1793...` 全部因
#    `SyntaxError: expected 'except' or 'finally' block (<llm-patch>, line 35~44)`
#    被关2 拒收：日志里只有 ast 的泛化文案，看不出「几个 try 缺收尾、缺在第几行」，
#    而补丁正文不落盘（`data/` 全库查不到该 id）⇒ 事后无法复盘，下一轮照原样重犯。
#  【做什么】入库前先数一遍**语句级** `try` 与 `except`/`finally`（tokenize 口径，
#    字符串/注释/属性名自动排除 —— 铁律227），不平衡时给出可执行的原因：
#    缺几处、缺在第几行、片段末尾长什么样。
#  【只在必然非法时才拒】两条形态：
#    ① `try` 数 > `except`+`finally` 数 ⇒ 至少一个 try 没有收尾；
#    ② 有 `except`/`finally` 而 `try` 数为 0 ⇒ 片段起点落在 try 内部（窗口错位）。
#    两条都**必然**让关2 的 ast.parse 失败 ⇒ 关2a 不改变任何补丁的通过/拒绝结论，
#    只把理由从「ast 泛化文案」换成可定位、可动手的信息。
#  【能力边界】它是**下界**探测器，不是完整校验器：
#    `try/except` 与 `try/finally` 写成一缺一（tries=2 / handlers=2）时判 ok，
#    这种形态仍由关2 兜住。不猜、不补。
#  【为什么不自动补全】用 `except Exception: raise` 补上，会让「被丢掉的原 handler」
#    静默变成「向上抛」，而关0/1/2/3/3b **全绿** —— 语义变了判据看不见。
#    实证见 `docs/路灯与星轨对话/交付报告/证据件/376/刀4_try平衡闸_20261028.md`。
_M376_TRY_KEYWORDS = ("try", "except", "finally")

# ★421刀4：markdown 围栏剥离。
#   为什么放在 `dedent` **之后**：围栏行通常顶格写（```python），而 dedent 以整段
#   的**公共缩进**为基准 —— 先剥会把「围栏 + 代码」的相对缩进一起算进去，剥完反而错位。
#   为什么必须剥：围栏字符会同时污染三道闸 —— 关2 的 `ast.parse` 直接报
#   SyntaxError（真凶：合法补丁被当成语法错），关3 的相似度被 ```/语言名稀释，
#   关3b 的长度被虚增。实测 `data/audit/failed_patches/` 里 47 条 2a 标本中有
#   16 条正文带 ```python。
#   零裁决变化：只削**首尾**围栏行，中间内容一字不动；围栏本身不含 try/except，
#   剥它不会让「本该被拒」的补丁变成"平衡"。
_M421_FENCE_PREFIX = "```"


def _m421_strip_md_fence(text: str) -> str:
    """剥掉首尾 markdown 代码围栏，返回 strip 后的正文（★421刀4）。

    只削**首尾**围栏行::

        "```python\n    def f():\n        pass\n```"  →  "def f():\n    pass"

    中间出现的 ``` 一律不动 —— 那是正文里真的三反引号，剥了会改语义。
    无围栏时等价于 ``text.strip()``（对既有输入是恒等变换）。
    """
    _t = (text or "").strip()
    if not _t.startswith(_M421_FENCE_PREFIX):
        return _t
    _lines = _t.split("\n")
    if _lines and _lines[0].strip().startswith(_M421_FENCE_PREFIX):
        _lines = _lines[1:]
    if _lines and _lines[-1].strip().startswith(_M421_FENCE_PREFIX):
        _lines = _lines[:-1]
    return "\n".join(_lines).strip()


def _m427_strip_fence_lines(text: str) -> str:
    """★427刀1：只删**首尾围栏行**，其余行与缩进**一字不动**。

    为什么不能直接复用 `_m421_strip_md_fence`：后者先 `.strip()` 再删行，
    它是按「放在 dedent **之后**」设计的 —— 那个位置 strip 无害。可一旦挪到
    dedent **之前**，`.strip()` 会削掉**首行缩进**，dedent 的公共前缀随即
    恒为空，整段的统一缩进直接失效（这正是 421 版真实发生的失效机制）。

    本函数只删围栏那两行：
      · 首个**非空**行 lstrip 后以 ``` 开头 ⇒ 删掉该行；
      · 最后一个**非空**行 lstrip 后以 ``` 开头 ⇒ 删掉该行；
      · 其余内容（含缩进、空行、正文中间的三反引号）原样返回。

    ★对**无围栏**输入是**恒等变换**（连 strip 都不做）⇒ 既有全部历史补丁的
      `_orig_norm` / `_mod_norm` 逐字节不变，本刀零裁决回归。
    """
    _t = text or ""
    if _M421_FENCE_PREFIX not in _t:
        return _t
    _lines = _t.split("\n")
    # 首个非空行
    for _i, _ln in enumerate(_lines):
        if not _ln.strip():
            continue
        if _ln.lstrip().startswith(_M421_FENCE_PREFIX):
            del _lines[_i]
        break
    # 最后一个非空行（重新扫描：上面的 del 改了长度）
    for _j in range(len(_lines) - 1, -1, -1):
        if not _lines[_j].strip():
            continue
        if _lines[_j].lstrip().startswith(_M421_FENCE_PREFIX):
            del _lines[_j]
        break
    return "\n".join(_lines)



def _m376_try_balance(code: str) -> dict:
    """语句级统计 `try` / `except` / `finally`（纯函数，不读写任何全局状态）。

    返回 `{"tries","handlers","deficit","open_lines","state","tail","detail"}`；
    `state` 四态：

      | state | 含义 | 调用方动作 |
      |---|---|---|
      | `ok` | 两侧自洽（含「根本没有 try」） | 放行，交关2 |
      | `unbalanced` | `try` 多于 handler ⇒ 至少一个 try 缺收尾 | 关2a 拒收 |
      | `orphan` | 有 handler 却没有 try ⇒ 片段起点落在 try 内部 | 关2a 拒收 |
      | `unknown` | tokenize 读不动 ⇒ **不拦**（不猜） | 放行，交关2 |
    """
    import io as _io376
    import tokenize as _tk376
    _tries = 0
    _exc = 0
    _fin = 0
    _try_lines = []
    _h_line = 0
    try:
        _toks = list(_tk376.generate_tokens(_io376.StringIO(code or "").readline))
    except Exception as _e376:
        # 读不动就是读不动：不猜、不拦，把裁决权交回关2（它有自己的 try/except）
        silent_exc(_e376, where="nucleus.reasoning.PatchManager::_m376_try_balance", level="debug")
        return {"tries": 0, "handlers": 0, "deficit": 0, "open_lines": [],
                "state": "unknown", "tail": "",
                "detail": f"tokenize 失败（不拦）: {type(_e376).__name__}: {_e376}"}
    _prev = None
    for _t in _toks:
        if _t.type in (_tk376.NL, _tk376.NEWLINE, _tk376.INDENT, _tk376.DEDENT):
            _prev = _t.type
            continue
        if _t.type == _tk376.COMMENT:
            continue
        # 语句级判据：关键字必须处在行首位置（前一个有效 token 是 NEWLINE/INDENT/
        #   DEDENT 或文件开头）。`x.try` / `@try` / `"try:"` 一律不计数。
        if _t.type == _tk376.NAME and _t.string in _M376_TRY_KEYWORDS:
            if _prev in (None, _tk376.NEWLINE, _tk376.INDENT, _tk376.DEDENT):
                if _t.string == "try":
                    _tries += 1
                    _try_lines.append(_t.start[0])
                elif _t.string == "except":
                    _exc += 1
                    if not _h_line:
                        _h_line = _t.start[0]
                else:
                    _fin += 1
                    if not _h_line:
                        _h_line = _t.start[0]
        _prev = _t.type
    _handlers = _exc + _fin
    _deficit = _tries - _handlers
    if _deficit > 0:
        _state = "unbalanced"
        _open = _try_lines[-_deficit:]
    elif _tries == 0 and _handlers > 0:
        _state = "orphan"
        _open = [_h_line]
        _deficit = _handlers
    else:
        _state = "ok"
        _open = []
        _deficit = 0
    # 片段末行：补丁不落盘，留这一小段让下一次能一眼看出「断在哪」（长度有界）
    _tail = ""
    for _ln in reversed((code or "").split("\n")):
        if _ln.strip():
            _tail = _ln.strip()[:60]
            break
    return {"tries": _tries, "handlers": _handlers, "deficit": _deficit,
            "open_lines": _open, "state": _state, "tail": _tail, "detail": ""}


def _m376_try_balance_reason(tb: dict) -> str:
    """把关2a 的统计翻成「问题 + 定位 + 已拦截」三段式文案。"""
    _lns = "、".join(str(x) for x in (tb.get("open_lines") or []))
    _tail = tb.get("tail") or ""
    if tb.get("state") == "unbalanced":
        return (f"try 块不平衡：{tb.get('tries')} 个 try / "
                f"{tb.get('handlers')} 处 except+finally，缺 {tb.get('deficit')} 处收尾"
                f"（第 {_lns} 行的 try 没有对应 except/finally）；"
                f"片段末行={_tail!r}"
                "（入库前静态闸拦截，未进入副本验证）")
    if tb.get("state") == "orphan":
        return (f"try 块不平衡：有 {tb.get('handlers')} 处 except/finally 却没有 try"
                f"（第 {_lns} 行）——补丁起点落在 try 内部（fragment 窗口错位）；"
                f"片段末行={_tail!r}"
                "（入库前静态闸拦截，未进入副本验证）")
    # ★按状态显式分支，不留 else 兜底：原实现的 `if unbalanced ... else 说 orphan`
    #   会把 `ok` / `unknown` 也描述成「片段窗口错位」（V2 反向注入暴露）。
    return f"try 块平衡检查未给出拒收结论（state={tb.get('state')}，不拦）"


def _m427_parse_precheck_on() -> bool:
    """★427刀2：片段可解析预检开关（默认开）。

    关掉 ⇒ 回到 376 行为（2a 先跑、语法错留到关2 才报），用于灰度回退。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M427_PARSE_PRECHECK", True))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m427_parse_precheck_on", level="debug")
        return True


def _m427_parse_fragment(norm: str) -> dict:
    """★427刀2：判断「片段本身能否被 Python 解析」（纯函数，唯一实现）。

    包装方式与关2 **逐字一致**：dedent 后的方法体片段可能带 `return`，顶层
    非法 ⇒ 必须包进虚拟函数；完整方法（`def` 开头）包进去是嵌套 def，亦合法。

    ★单一真源：关2 与本预检都调这里，不许各写一份 —— 否则会出现
      「预检说能解析、关2 说不能」的判据分叉，而两者对同一输入必然同结论。

    返回 `{"ok": bool, "detail": str}`；`detail` 沿用关2 原格式
      `语法错误(<异常类名>): <message>`，既有断言与 grep 可直接沿用。
    """
    import ast as _ast427
    import textwrap as _tw427
    try:
        _ast427.parse("def _wrap():\n" + _tw427.indent(norm or "", "    "),
                      filename="<llm-patch>")
        return {"ok": True, "detail": ""}
    except SyntaxError as _e427:
        # 语法错是**预期路径**（真截断），但门禁口径是「每个 except 都要留痕」，
        # 照办：留痕只写 debug，不影响返回值，也不改变「不完整」这一结论。
        silent_exc(_e427, where="nucleus.reasoning.PatchManager::_m427_parse_fragment:syntax", level="debug")
        return {"ok": False,
                "detail": f"语法错误({type(_e427).__name__}): {_e427}"}
    except Exception as _x427:
        # 非 SyntaxError（MemoryError 等极小概率）：不猜，按不可解析拒收并留痕
        silent_exc(_x427, where="nucleus.reasoning.PatchManager::_m427_parse_fragment", level="debug")
        return {"ok": False,
                "detail": f"解析异常({type(_x427).__name__}): {_x427}"}


def _m427_cooldown_wire_on() -> bool:
    """★427刀3：生成侧失败接入方法冷却的开关（默认开；关掉逐字回到 426 行为）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M427_METHOD_COOLDOWN_WIRE", True))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m427_cooldown_wire_on", level="debug")
        return True


def _m427_note_method_fail(patch: Any, reason: str) -> None:
    """★427刀3：把生成侧（语法预检 / 2a）拒收计入 422 的方法级冷却账。

    ★为什么不新建 `data/audit/patch_fail_counts.json`（任务书原方案）：
      422 批刀2 已建 `data/patches/method_cooldown.json` 做同一件事 ——
      键 = (归一化 file, method)、阈值「连续 3 次」、冷却「24h」，接口
      `_m422_note_method_fail` / `_m422_cooldown_reason` 齐备，且已接在
      派单/执行两条路径上。再造一份 = **双真源**，两处计数各说各话。
      故本刀只**接线**，不复制机制。

    ★为什么在调用方（`_verify_in_copy`）而不是判据内部：
      `_check_llm_patch_completeness` 是纯判据，有 5 个测试文件直接调它；
      在判据里写盘会让「跑一次判据」变成有副作用的操作（且 65 条标本重放
      会写 65 次盘）。留在判据外，职责分界不动。

    ★真实缺口（本刀要补的那一个）：422 的计数只在 `_write_back` 失败与
      dispatch blocked 两处置账，而坏片段在 **2a 就被拒**、根本走不到派单 ⇒
      `PulseLiver._compress_group` 四天被生成 64 次、次次 2a 拒收，
      `method_cooldown.json` 里却**一条记录都没有**（本批 T0 实测）。
    """
    if not isinstance(patch, dict):
        return
    try:
        from nucleus.evolution.patch_execution_dispatch import _m422_note_method_fail
        _rs = str(reason or "")
        _cat = ("fragment_incomplete" if _rs.startswith("生成片段不完整")
                else ("try_unbalanced" if _rs.startswith("try 块不平衡")
                      else "completeness_failed"))
        _r = _m422_note_method_fail(patch, reason=_rs[:200], category=_cat)
        if isinstance(_r, dict) and _r.get("entered_cooldown"):
            _module_logger.info(
                "[427刀3][方法冷却] 生成侧失败累计触发冷却: file=%s method=%s "
                "consec=%s total=%s category=%s",
                patch.get("file"), patch.get("method"),
                _r.get("consec_fail"), _r.get("total_fail"), _cat)
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m427_note_method_fail", level="debug")


def _m427_status_guard_on() -> bool:
    """★427刀5：状态-真值一致性守卫开关（默认开；关掉逐字回到 426 行为）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M427_STATUS_VERIFY_GUARD", True))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.PatchManager::_m427_status_guard_on", level="debug")
        return True


def _m427_converge_status(data: list) -> list:
    """★427刀5：落盘前收敛「status 说验证通过、嵌套真值说验证失败」的矛盾。

    只治一种形态：`status == "runtime_verified"` 且
    `runtime_verify_result.verified is False` —— 同一份记录自相矛盾，
    下游（派单/审批/风险闸）据此做的任何判断都不可信。

    ★为什么**不**按任务书原方案补写顶层 `runtime_verified=True`：
      105 批 T-105a 与 280 批刀4 已把顶层 `runtime_verified` 定为**污染字段**
      并有意删除（它曾被 `verify_submitted_patches` 对**所有处理过的补丁**无条件
      置 True，含失败补丁），审批关卡早已改为只读嵌套
      `runtime_verify_result.verified`。补写顶层 = 复活一个已知会说谎、且审批
      根本不看的字段 —— 表面"对齐"了，实际只是把矛盾从一处搬到另一处。

    ★为什么只认 `is False` 不认 `None`：`None` 表示"没跑过验证"，是**合法**的
      中间态（423 归档恢复出来的条目就落在这一档）；把 None 当矛盾会误伤。
    ★为什么返回新列表而不是原地改：`_save_patch_list` 会就地改写元素的 `file`
      （既有行为），调用方（如 `_m94_apply_pending_aging`）依赖原列表的绝对路径；
      本函数只换 status，故对矛盾项做**浅拷贝**，不把改动泄漏回调用方的内存对象。
    """
    if not isinstance(data, list):
        return data
    _n_fix = 0
    _out = []
    for _it in data:
        if not isinstance(_it, dict):
            _out.append(_it)
            continue
        _rvr = _it.get("runtime_verify_result")
        if (str(_it.get("status") or "") == "runtime_verified"
                and isinstance(_rvr, dict) and _rvr.get("verified") is False):
            _n = dict(_it)
            _n["status"] = "needs_reverify"
            _n["m427_status_fix"] = {
                "from": "runtime_verified",
                "to": "needs_reverify",
                "why": "status 说已验证，但 runtime_verify_result.verified is False",
                "at": time.time(),
                "batch": "427",
            }
            _out.append(_n)
            _n_fix += 1
        else:
            _out.append(_it)
    if _n_fix:
        _module_logger.warning(
            "[427刀5][状态一致性] 本次落盘收敛 %d 条矛盾记录："
            "status=runtime_verified 但嵌套 verified=False ⇒ needs_reverify", _n_fix)
    return _out

# ============== ★433刀3：applied_at 哨兵归一 / schema 校验 ==============
# 病因（433 T0 实测）：`patch_history` 里 6 条**未落地**补丁的 `applied_at` 是
# `0`（epoch 0），报表按时间戳渲染成 `1970-01-01 08:00` —— 一个看起来"有值"、
# 实际含义是"从未落地"的假象；4 个补丁构造点把 `"applied_at": 0` 写死是根源。
# 下游读点已逐个核过 None 安全（`x.get("applied_at") or ...` / `if not _applied_at`
# / `isinstance(..., (int, float))` 三类），故归一为 None 不会炸任何调用方。

#: `applied_at` 合法下界：2000-01-01T00:00:00Z。低于它的都是坏哨兵，不是真时刻。
MIN_APPLIED_AT = 946684800


def _m433_normalize_applied_at(value):
    """把 ``applied_at`` 归一为「合法时刻」或 ``None``。返回 ``(新值, 是否改动)``。"""
    if value is None:
        return None, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float)):
        return (None, True) if float(value) <= MIN_APPLIED_AT else (value, False)
    if isinstance(value, str):
        try:
            _f = float(value.strip())
        except (TypeError, ValueError) as _e433v:
            # 非数字字符串 = 坏哨兵：归一为 None，但要留痕（否则查不出上游写坏了什么）
            silent_exc(_e433v,
                       where="PatchManager._m433_normalize_applied_at 非数字字符串",
                       level="debug")
            return None, True
        return (None, True) if _f <= MIN_APPLIED_AT else (_f, False)
    return None, True


def _m433_applied_at_valid(value) -> bool:
    """schema 判据：``applied_at`` 只能是 ``None`` 或 ``> MIN_APPLIED_AT`` 的数值。"""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) > MIN_APPLIED_AT


def _m433_normalize_applied_at_in(rows) -> int:
    """对整份补丁列表**就地**归一 ``applied_at``，返回改动条数。"""
    _n = 0
    if not isinstance(rows, list):
        return 0
    for _r in rows:
        if not isinstance(_r, dict) or "applied_at" not in _r:
            continue
        _new, _chg = _m433_normalize_applied_at(_r.get("applied_at"))
        if _chg:
            _r["applied_at"] = _new
            _n += 1
    if _n:
        _module_logger.info("[433刀3] applied_at 哨兵归一为 None：%d 条", _n)
    return _n


# ============== ★433刀2：history 按 id 合并（落盘/读入共用同一实现）==============
def _m433_upsert_history(rows, where: str = ""):
    """history 列表按 id 合并（幂等）；任何异常一律原样返回，绝不影响保存/读取。"""
    try:
        from nucleus.evolution.patch_dedup import upsert_by_id as _upsert433
        _out, _st = _upsert433(rows)
        if _st.get("merged"):
            _module_logger.warning(
                "[433刀2] history 按 id 合并 %d 条重复快照（%d 行 -> %d 行）ids=%s where=%s",
                _st["merged"], _st["before"], _st["after"],
                _st["merged_ids"][:5], where)
        return _out
    except Exception as _e433:
        silent_exc(_e433, where="PatchManager._m433_upsert_history", level="warning")
        return rows




# ========================================== 377刀3：失败补丁正文独立审计件
#  _m377_dump_failed_patch
#  【为什么】376刀4 把关2a 的拒收理由换成了可执行的定位文案（缺几处、缺在第几行），
#    但**补丁正文仍不落盘** —— 376 刀4 证据件的原话是：跨夜 12 次 `patch_multi_1793...`
#    在 `data/` 全库查不到该 id。于是下一次同类残片重来时，能读到「缺几处」，
#    读不到「缺的到底是什么写法」⇒ 复盘只能靠猜，同一生成缺陷反复重犯。
#  【做什么】关2a 拒收时，把该补丁**正文原样**落 `data/audit/failed_patches/` 独立 JSON：
#    带 issue id（`id` / `dedup_key`）、`file` / `method`、关名、拒收原因、try 统计、片段末行。
#  【能力边界】**只加写盘，不改闸的任何裁决**：本函数返回 `{"written": bool, ...}`，
#    调用方**不看**返回值（拒收照旧、reason 一字不变）；落盘失败/目录满/写盘守卫拒写
#    一律返 `written=False` 并留痕，绝不抛异常、绝不改变补丁是否被拒。
#  【为什么不落 `data/patches/`】那是补丁池与历史主数据（`_save_patch_list` 18 个写点）；
#    审计件是旁路证据，放 `data/audit/` 既有人工审计区，互不覆盖。
#  【为什么封顶】常驻活体会持续产出残片，无上限长跑会把盘写满；封顶后只停写盘，
#    仍返回 `dir_full` 让调用方可观测（不是静默丢弃）。
_M377_FAILED_PATCH_SUBDIR = ("data", "audit", "failed_patches")
_M377_FAILED_PATCH_MAX = 400               # 目录内 .json 条目上限（含），超出只报 dir_full
_M377_FAILED_PATCH_FIELD_MAX = 200_000     # 单个正文字段的截断上限（字符），防单件过大


# ===== ★472刀2（台账 S6 / F1）：补丁验证子进程的环境白名单 =====
# 背景：`_verify_import_in_subprocess` 调 `execute_code_in_subprocess(...)` 时**没传**
#   `child_env`，而 `subprocess.Popen(env=None)` 的语义是「完整继承父进程环境」。
#   父进程环境里有 `PULSE_FRAMEWORK=1`，而 `nucleus/data/write_guard.py` 正是靠这个变量
#   判定「我是框架进程，允许写 data/」——于是「被验证的补丁代码」在验证期拿到了可信
#   写入者身份，可以往生产数据目录写东西。
#   同一份白名单 `nucleus/review/ScriptExecutor.py` 早就有了（S7 修的），补丁验证这条路
#   漏了 —— 典型的「修一半」（F1）。这里对齐，并加一条 PULSE_* 兜底剥离。
_M472_CHILD_ENV_ALLOWLIST = (
    # 跨平台通用
    "PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONIOENCODING",
    "PYTHONUTF8", "PYTHONHASHSEED", "PYTHONNOUSERSITE",
    "HOME", "USER", "USERPROFILE", "LANG", "LC_ALL", "TZ",
    # Linux/macOS
    "TMPDIR", "LD_LIBRARY_PATH", "SHELL",
    # Windows（缺失会导致 Python 无法定位系统 DLL）
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "PATHEXT", "COMSPEC", "APPDATA", "LOCALAPPDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "USERDOMAIN", "COMPUTERNAME",
)


def _m472_build_child_env() -> dict[str, str]:
    """补丁验证子进程的最小环境变量集（白名单制 · 默认 deny）。

    清单与 `nucleus/review/ScriptExecutor.py::ScriptExecutor.ENV_ALLOWLIST` 同源，
    由单测断言两者逐项一致 —— 防止下一次「修一半」再次漂移。

    最后一段是**兜底**：即便将来有人把某个 `PULSE_*` 加进白名单，
    这里也会把它剥掉。`PULSE_FRAMEWORK` 是 write_guard 的身份凭据，绝不能透传给
    「正在被验证的陌生代码」。
    """
    _env: dict[str, str] = {}
    _allow = {k.upper() for k in _M472_CHILD_ENV_ALLOWLIST}
    for _key, _val in os.environ.items():
        if _key.upper() in _allow:
            _env[_key] = _val
    # 兜底：连 PATH 都没有时子进程几乎无法启动，补一个最小 PATH
    if not _env.get("PATH"):
        _env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    # 强制 UTF-8 输出，避免 Windows(GBK) 下补丁代码 print 非 ASCII 字符崩溃
    _env.setdefault("PYTHONIOENCODING", "utf-8")
    # ★兜底剥离：PULSE_* 一律不带（write_guard 身份凭据 / 测试标记 / 框架开关）
    for _k in [k for k in _env if k.upper().startswith("PULSE_")]:
        _env.pop(_k, None)
    return _env


def _m377_failed_patch_dir(root: str | None = None) -> str:
    """失败补丁留档目录。`root` 缺省取本仓库根（`nucleus/reasoning/` 上溯三层）。"""
    _base = root or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(_base, *_M377_FAILED_PATCH_SUBDIR)


def _m377_dump_failed_patch(patch: Any, reason: str, *,
                            stage: str = "2a",
                            root: str | None = None,
                            tb: dict | None = None) -> dict:
    """把关2a 拒收的补丁正文落一份独立审计件。**绝不抛异常、绝不改裁决。**"""
    try:
        if not isinstance(patch, dict):
            return {"written": False, "why": "not_dict"}
        _dir = _m377_failed_patch_dir(root)
        try:
            os.makedirs(_dir, exist_ok=True)
        except Exception as _me377:
            silent_exc(_me377, where="PatchManager::_m377_dump_failed_patch makedirs", level="debug")
            return {"written": False, "why": "mkdir_failed"}
        try:
            _have = sum(1 for _f in os.listdir(_dir) if _f.endswith(".json"))
        except Exception as _le377:
            silent_exc(_le377, where="PatchManager::_m377_dump_failed_patch listdir", level="debug")
            _have = 0
        if _have >= _M377_FAILED_PATCH_MAX:
            return {"written": False, "why": "dir_full", "count": _have}

        # ★427顺带：`issue_id` 兜底 —— 377 刀3 自己落档时写的键就是 `issue_id`，
        #   但本函数只认 id/patch_id ⇒ 回放这类标本时全落成 `unknown_<ts>_2a.json`，
        #   同一秒内多条会**互相覆盖**（实测 65 条只剩 2 件）。生产补丁有 id 故未暴露。
        _pid = str(patch.get("id") or patch.get("patch_id")
                   or patch.get("issue_id") or "unknown")
        _safe = "".join(_c if (_c.isalnum() or _c in "-_") else "_"
                        for _c in _pid)[:48]
        _ts = time.time()
        _name = "{}_{}_{}.json".format(
            time.strftime("%Y%m%d_%H%M%S", time.localtime(_ts)), _safe, stage)
        _path = os.path.join(_dir, _name)
        _orig = str(patch.get("original_code") or "")
        _mod = str(patch.get("modified_code") or "")
        _rec = {
            "schema": "failed_patch/377.v1",
            "saved_at": _ts,
            "saved_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_ts)),
            "stage": stage,
            "gate": "关2a try 块平衡（376刀4）" if stage == "2a" else stage,
            "reason": str(reason or "")[:2000],
            "issue_id": _pid,
            "dedup_key": patch.get("dedup_key") or patch.get("dedup_key_v2"),
            "file": patch.get("file"),
            "method": patch.get("method"),
            "issue_type": patch.get("issue_type"),
            "source": patch.get("source") or patch.get("repair_source"),
            "risk_level": patch.get("risk_level"),
            "try_balance": (
                {_k: tb.get(_k) for _k in
                 ("state", "tries", "handlers", "deficit", "open_lines", "tail")}
                if isinstance(tb, dict) else None),
            "original_code": _orig[:_M377_FAILED_PATCH_FIELD_MAX],
            "modified_code": _mod[:_M377_FAILED_PATCH_FIELD_MAX],
            "orig_len": len(_orig),
            "mod_len": len(_mod),
            "truncated": (len(_orig) > _M377_FAILED_PATCH_FIELD_MAX
                          or len(_mod) > _M377_FAILED_PATCH_FIELD_MAX),
            "pid": os.getpid(),
        }
        from nucleus.data.write_guard import guard_write as _m377_gw
        if not _m377_gw(_path, component="FailedPatchAudit"):
            return {"written": False, "why": "write_guard"}
        _tmp = _path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_rec, ensure_ascii=False, indent=2))
            _f.flush()
            os.fsync(_f.fileno())
        os.replace(_tmp, _path)
        return {"written": True, "path": _path, "bytes": os.path.getsize(_path),
                "truncated": _rec["truncated"]}
    except Exception as _e377:
        silent_exc(_e377, where="PatchManager::_m377_dump_failed_patch", level="debug")
        return {"written": False, "why": "exception"}


# ★第90批 T-90a：LLM 补丁「字段契约」必填校验（关0）。
#   为什么必须单列一关（而不是靠关1/关3 顺带拦下）：
#     · 关1 的判据是 `not _mod_norm or _mod_norm == _orig_norm` ——
#       original 为空、modified 非空时 **关1 通过**；
#     · 关3 才以 `ratio(("", mod)) == 0.0 < 阈值` 拦下，报的却是
#       「与原文相似度过低(0.00 < 0.3)」⇒ 把「字段缺失」误报成「内容残缺」；
#     · 关3 内部还有一段 `except: pass`（相似度计算失败不阻断），关3b 又要求
#       `_orig_norm` 非空且 `_m89_len_floor() > 0` —— 三者叠加时，空 original_code
#       的补丁可**同时绕过关3 与关3b**。
#   ⇒ 关0 把「字段缺失」这条判据**结构性**固定下来，与任何灰度开关/阈值无关。
_M90_PATCH_REQUIRED_FIELDS = ("original_code", "modified_code")


def _m90_patch_field_contract_on() -> bool:
    """★第90批 T-90a 灰度开关：补丁字段契约必填校验（关0）是否启用。

    开启（默认）→ `_check_llm_patch_completeness` 先校验 original_code /
    modified_code 非空，缺失即返回带**字段名**的明确原因；
    关闭 → 逐字回到第90批前行为（缺失字段由关1/关3 以别的理由拦下）。

    默认值内联本模块（遵守本批红线「不改 config.py 开关」）；
    每次调用重新 ``import config`` → 改配置即时生效，无需重启框架。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M90_PATCH_FIELD_CONTRACT", True))
    except Exception as _e:
        _module_logger.debug(
            f"[补丁字段契约] 开关读取失败，按默认(启用)处理: {type(_e).__name__}: {_e}")
        return True


def _m90_missing_patch_fields(patch: dict) -> list:
    """返回补丁中「缺失或为空」的必填字段名列表（空列表 = 契约完整）。"""
    _miss = []
    for _f in _M90_PATCH_REQUIRED_FIELDS:
        if not str(patch.get(_f, "") or "").strip():
            _miss.append(_f)
    return _miss


def _c96_import():
    """★第96批 T-96b：惰性取 config 模块（保持与同文件其它处同风格）。

    入口收口处需多次读配置，抽成单点便于测试注入，也避免重复 try/except。
    """
    import config as _c96
    return _c96


# ★第92批 T-92c/T-92d：验证侧两道**防御性**结构化关（任务书要求默认**关闭**，先观察一批）。
#   两者都只做「拒绝」判定，不改任何既有放行逻辑 ⇒ 关闭时逐字回到第91批末行为。
# ★第303批 刀1（隐形总闸）：原两者皆为 False（92 批「先观察一批」的保守默认）。
#   295 批已把 config.py 两个开关翻 True ⇒ 运行时读的是 config（getattr 优先），早已生效；
#   本批把**兜底默认**也翻 True，使「未定义 config 键」时同样是开闸态（双保险）。
#   关闸路径保持有效：把 config.py 对应键显式置 False 即可（getattr 优先于本默认值）。
_M92_BASE_INDENT_GUARD_DEFAULT = True
_M92_AST_STRUCT_GUARD_DEFAULT = True
#   T-92d 判据：类方法总数缩水**超过**该比例即拒绝。
_M92_STRUCT_SHRINK_RATIO = 0.10


def _m92_base_indent_guard_on() -> bool:
    """★第92批 T-92c 灰度开关：验证侧「基础缩进相等」结构化关。

    开启 → `_verify_in_copy` 在写副本前要求
    `base(modified_code) == base(original_code)`，不等即拒绝
    （stage=`base_indent_guard_failed`）；
    关闭（默认）→ 逐字回到第91批末行为，零回归。

    为什么需要（T0 实测根因）：`_verify_in_copy` 的
    `modified_full = full_content.replace(original_code, modified_code)`
    是**原位整段替换**，对缩进一字不改 ⇒ base 不一致时替换结果**仍是合法
    Python**（`ast.parse` / `compile` / `py_compile` / `import` 全部放行），
    却会把类体/函数体**提前终止**。第91批只在**构造侧**对齐（三个构造点），
    本关是**验证侧**兜底。

    默认值为 False（关闭）是任务书要求：防御性校验先观察一批，
    避免对既有补丁链产生未评估的行为变化。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M92_PATCH_BASE_INDENT_GUARD",
                            _M92_BASE_INDENT_GUARD_DEFAULT))
    except Exception as _e:
        _module_logger.debug(
            f"[补丁基础缩进关] 开关读取失败，按默认(关闭)处理: {type(_e).__name__}: {_e}")
        return _M92_BASE_INDENT_GUARD_DEFAULT


def _m92_ast_struct_guard_on() -> bool:
    """★第92批 T-92d 灰度开关：验证侧「AST 结构不变量」关。

    开启 → `_verify_in_copy` 在写副本前统计**类方法总数**，若
    `after < before * (1 - _M92_STRUCT_SHRINK_RATIO)` 即拒绝
    （stage=`ast_structure_guard_failed`）；
    关闭（默认）→ 逐字回到第91批末行为，零回归。

    为什么需要（第91d 事故）：LLM 补丁把类方法**改写成模块级函数**
    （original 基础缩进 8 → modified 0），`ast.parse` / `compile` /
    `import` **全部放行** —— 「语法合法 ≠ 结构未退化」。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M92_PATCH_AST_STRUCT_GUARD",
                            _M92_AST_STRUCT_GUARD_DEFAULT))
    except Exception as _e:
        _module_logger.debug(
            f"[补丁结构关] 开关读取失败，按默认(关闭)处理: {type(_e).__name__}: {_e}")
        return _M92_AST_STRUCT_GUARD_DEFAULT


def _m92_base_indent(code: str) -> int:
    """代码片段的**基础缩进宽度** = 首个**可执行行**的前导空白宽度（tab 按 4 展开）。

    ★口径必须与第91批 `SafeEvolutionExecutor._m91_base_indent` **完全一致**
    （构造侧与验证侧用同一把尺子），故**优先复用同一实现**；只有在 import
    失败时才用下面的本地等价实现兜底（等价性由 m92 门控单测交叉断言）。

    为什么跳过**空行与纯注释行**：第91批 T0 实测，真实 LLM 补丁常见形态是
    「col 0 前置模块级注释/import + 保留原缩进的方法体」，例如
    `patch_llm_1789462016_8789` 首行是 col 0 注释而方法体仍在 8；
    Python 词法器**不把注释当作缩进层级** ⇒ 首行注释 col 0 无害。
    按「首个非空行」算会把 base 误判为 0。
    """
    if not code:
        return -1
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import (
            SafeEvolutionExecutor as _SEE,
        )
        _fn = getattr(_SEE, "_m91_base_indent", None)
        if callable(_fn):
            return int(_fn(str(code)))
    except Exception as e:
        silent_exc(e, "nucleus/reasoning/PatchManager.py:262", level="warning")
    for _ln in str(code).replace("\r\n", "\n").split("\n"):
        _s = _ln.strip()
        if not _s or _s.startswith("#"):
            continue
        return len(_ln[: len(_ln) - len(_ln.lstrip(" \t"))].expandtabs(4))
    return -1


def _m92_count_class_methods(src: str):
    """→ (类方法总数, 类总数)；源码无法解析返回 ``None``。

    ★为什么是「ClassDef 的**直接**函数成员」而不是 `ast.walk` 全量函数：
    第91d 事故的形态是把类方法 dedent 成**模块级函数**或**嵌套进其它函数**
    —— 全文件的 `FunctionDef` 总数**一个不少**，只有「类的方法数」会掉。
    用全量函数计数会**完全漏判**（T0 实测 `tmp/m92_t0d.txt`：PulseInnerWorld
    `ast.walk` 函数数基本不变，类方法 368 → 271）。
    """
    try:
        _tree = ast.parse(src, filename="<m92-class-method-counter>")
    except SyntaxError as e:
        silent_exc(e, "nucleus/reasoning/PatchManager.py:284:_m92_count_class_methods", level="debug")
        return None
    _methods = 0
    _classes = 0
    _stack = [_tree]
    while _stack:
        _nd = _stack.pop()
        for _ch in ast.iter_child_nodes(_nd):
            if isinstance(_ch, ast.ClassDef):
                _classes += 1
                _methods += len([
                    _y for _y in _ch.body
                    if isinstance(_y, (ast.FunctionDef, ast.AsyncFunctionDef))
                ])
                _stack.append(_ch)
    return _methods, _classes


def _m92_ast_struct_guard(before_src: str, after_src: str):
    """T-92d 判据：比较补丁前后「类方法总数」。

    → ``None`` = 本关不适用（任一侧无法解析，交由既有语法/编译关处置）；
      否则 ``{"ok": bool, "before": int, "after": int, "ratio": float, "reason": str}``。

    只在**缩水**时判定（增/平一律放行）；`before == 0` 亦放行（无方法可失）。
    纯函数、无副作用，便于单测直接调用与先红后绿取证。
    """
    _b = _m92_count_class_methods(before_src)
    _a = _m92_count_class_methods(after_src)
    if _b is None or _a is None:
        return None
    _nb, _na = _b[0], _a[0]
    if _nb <= 0 or _na >= _nb:
        return {"ok": True, "before": _nb, "after": _na, "ratio": 0.0, "reason": ""}
    _drop = (_nb - _na) / float(_nb)
    if _drop <= _M92_STRUCT_SHRINK_RATIO:
        return {"ok": True, "before": _nb, "after": _na, "ratio": _drop, "reason": ""}
    return {
        "ok": False,
        "before": _nb,
        "after": _na,
        "ratio": _drop,
        "reason": (
            f"类方法数量异常缩水（未应用）: {_nb} → {_na}"
            f"（-{_drop:.1%} > {_M92_STRUCT_SHRINK_RATIO:.0%} 阈值），"
            f"疑似补丁把类方法改写成模块级/嵌套函数（语法仍合法但是结构破坏）"
        ),
    }



def _m428_perf_detail(patch, project_root=None):
    """★428刀3：`perf_delta_ms` 的**明细**（P95 / 内存 / A/B 模式），供 verdict 存数。

    为什么不并进 `_m412_perf_delta_ms`：那个函数的返回值被既有单测钉成 float|None
    （铁律「既有测试的桩点也是契约」）⇒ 只加不改，另开一个返回明细的口。
    开关/异常语义与 412 完全一致：开关关、测不出、抛异常 ⇒ 一律 `None`。
    """
    try:
        import config as _c428
        if not bool(getattr(_c428, "ENABLE_M412_PERF_DELTA", True)):
            return None
        from nucleus.evolution.perf_delta_probe import perf_delta_for_patch as _p428
        _r = _p428(patch, project_root=project_root)
    except Exception as _e428:
        silent_exc(_e428, where="PatchManager::_m428_perf_detail", level="debug")
        return None
    if not isinstance(_r, dict) or _r.get("state") != "ok":
        _module_logger.debug(
            "[428刀3] perf 明细未测出（%s=%s）⇒ verdict 三个 perf 字段留 None",
            (_r or {}).get("state"), (_r or {}).get("reason"))
        return None
    return _r


def _m412_perf_delta_ms(patch, project_root=None):
    """★412刀3：给 `verify()` 的第三维**供数**（改后导入耗时 − 改前导入耗时）。

    为什么以前恒 None：`verify()` 要 `problem_gone/function_ok/perf_ok` 三维全知
    才算 passed，少一维就恒落 unverifiable。本函数补第三维 ——
    **A/B 沙箱导入耗时**（详见 `nucleus/evolution/perf_delta_probe.py`）。

    ★拿不到就返回 None：**0 会被判成 perf_ok**，那是编造"没有变化"。
    开关关闭 / 缺 original_code / 沙箱跑不出读数 ⇒ 一律 None（与改造前一致）。
    """
    try:
        import config as _c412
        if not bool(getattr(_c412, "ENABLE_M412_PERF_DELTA", True)):
            return None
        from nucleus.evolution.perf_delta_probe import perf_delta_for_patch as _p412
        _r = _p412(patch, project_root=project_root)
    except Exception as _e412:
        silent_exc(_e412, where="PatchManager::_m412_perf_delta_ms", level="debug")
        return None
    _d = (_r or {}).get("delta_ms")
    if not isinstance(_d, (int, float)):
        _module_logger.debug(
            "[412刀3] perf_delta 未测出（%s=%s）⇒ 保持 perf 未测量",
            (_r or {}).get("state"), (_r or {}).get("reason"))
        return None
    return float(_d)

def _m469_source_precheck_on() -> bool:
    """★469刀1 开关：补丁应用/副本验证之前的「源码一致性核对」。

    关闭 ⇒ `_m469_source_precheck` 返回 kind="guard_off"，调用方按 469 之前
    的老逻辑继续（不做提前止损，行为与改造前完全一致）。
    """
    try:
        import config as _c469
        return bool(getattr(_c469, "ENABLE_M469_SOURCE_PRECHECK", True))
    except Exception as _e469:
        silent_exc(_e469, where="PatchManager::_m469_source_precheck_on", level="debug")
        return True


def _m469_file_sha256(path: str) -> str:
    """目标文件内容指纹（先把 CRLF 归一成 LF，避免行尾差异被误判成源码漂移）。

    读不到 ⇒ 返回空串。调用方据此**不判漂移** —— 绝不拿空串当"一致"，
    也绝不拿空串当"不一致"（都是编造结论）。
    """
    try:
        import hashlib
        with open(path, "rb") as _f469:
            _raw = _f469.read()
        return hashlib.sha256(_raw.replace(b"\r\n", b"\n")).hexdigest()
    except Exception as _e469:
        silent_exc(_e469, where="PatchManager::_m469_file_sha256", level="debug")
        return ""


def _m469_source_precheck(patch: dict, project_root: str = "") -> dict:
    """★469刀1：**补丁应用之前**的源码一致性核对（只读，不改任何文件）。

    病因（2026-11-06 实测）：补丁的 `original_code` 钉的是生成那一刻的源码片段。
    之后任何一方（主线批次 / 用户 / 星轨）改过同一个文件，apply 时
    `full_content.count(original_code)` 就是 0。改造前这条只会落到
    `apply_error="原始代码未找到，可能已被修改"` 并被计成 `failed`：
      · 分不清「补丁本身写错」和「源码已经漂移」；
      · 白写一次整文件备份（备份在读数之前就做了）；
      · 白跑一次副本验证（建临时目录 + 整文件 copy2 + 语法 + 导入）；
      · 在 468 刀6 之前还会白吃一次审批日限额。

    本函数把这一步提到最前，并且**只判性质**：
      kind="ok"            original_code 在当前文件中恰好出现 1 次
      kind="stale"         出现 0 次 ⇒ 源码已漂移，补丁需要重新生成
      kind="ambiguous"     出现 >1 次 ⇒ 无法唯一替换（与写盘点 rejected_ambiguous 同判据）
      kind="no_code"       补丁缺 original_code 字段（字段契约不完整）
      kind="file_missing"  目标文件不存在 / 读不到 / 缺 file 字段
      kind="guard_off"     开关关闭，调用方按老逻辑继续

    `snapshot_drift` 只在补丁带 `source_file_sha256`（469刀2 写入）且与当前文件
    不符时为 True。它**不单独拒绝**补丁 —— 别人改的是同一文件里的另一个方法时，
    original_code 仍在，补丁照样能应用；此字段只用于归因留痕。
    """
    _out = {"kind": "ok", "ok": True, "occurrences": 0, "detail": "",
            "snapshot_drift": False, "source_file_sha256_now": ""}
    if not _m469_source_precheck_on():
        _out["kind"] = "guard_off"
        return _out
    if not isinstance(patch, dict):
        _out["kind"] = "no_code"
        _out["ok"] = False
        _out["detail"] = "补丁不是字典，无法核对源码"
        return _out
    _orig = patch.get("original_code")
    if not isinstance(_orig, str) or not _orig.strip():
        _out["kind"] = "no_code"
        _out["ok"] = False
        _out["detail"] = "补丁缺少 original_code 字段（字段契约不完整），无法定位待替换片段"
        return _out
    _fp = str(patch.get("file") or "").strip()
    if not _fp:
        _out["kind"] = "file_missing"
        _out["ok"] = False
        _out["detail"] = "补丁缺少 file 字段，无法核对源码"
        return _out
    if not os.path.isabs(_fp) and project_root:
        _fp = os.path.join(project_root, _fp)
    if not os.path.isfile(_fp):
        _out["kind"] = "file_missing"
        _out["ok"] = False
        _out["detail"] = f"目标文件不存在: {_fp}"
        return _out
    try:
        with open(_fp, "r", encoding="utf-8", errors="replace") as _f469r:
            _now = _f469r.read()
    except Exception as _e469r:
        silent_exc(_e469r, where="PatchManager::_m469_source_precheck 读目标文件", level="debug")
        _out["kind"] = "file_missing"
        _out["ok"] = False
        _out["detail"] = f"目标文件读取失败: {type(_e469r).__name__}: {_e469r}"
        return _out
    # 行尾归一后再比对：补丁里存的是 \n，磁盘上可能是 \r\n
    _cnt = _now.replace("\r\n", "\n").count(_orig.replace("\r\n", "\n"))
    _out["occurrences"] = int(_cnt)
    _out["source_file_sha256_now"] = _m469_file_sha256(_fp)
    _want = str(patch.get("source_file_sha256") or "").strip()
    if _want and _out["source_file_sha256_now"] and _want != _out["source_file_sha256_now"]:
        _out["snapshot_drift"] = True
    _base = os.path.basename(_fp)
    if _cnt == 1:
        return _out
    if _cnt == 0:
        _drift_note = "；文件指纹与生成时不同" if _out["snapshot_drift"] else ""
        _out["kind"] = "stale"
        _out["ok"] = False
        _out["detail"] = (
            f"源码已漂移：original_code 在 {_base} 中已找不到"
            f"（该文件在补丁生成后被改动过） ⇒ 补丁需重新生成{_drift_note}")
        return _out
    _out["kind"] = "ambiguous"
    _out["ok"] = False
    _out["detail"] = (f"原始代码在 {_base} 中出现 {_cnt} 次，"
                       "无法唯一定位（请提供更长上下文）")
    return _out


class PatchManager:
    # ★第298批刀2：入队哨兵计数器（类属性，内存态跨实例共享，供日报四数读取）
    _shape_rejected_count = 0   # 入队形态拒绝数（片段→完整）
    _enqueue_count = 0          # 入队数
    _m92_rejected_count = 0     # M92 守卫拒绝数（base_indent + ast_struct）

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._patch_dir = os.path.join(project_root, "data", "patches")
        self._history_file = os.path.join(self._patch_dir, "patch_history.json")
        self._pending_file = os.path.join(self._patch_dir, "pending_patches.json")
        os.makedirs(self._patch_dir, exist_ok=True)
        # ★v16.0新增：防循环重启计数器
        self._restart_counter = 0
        self._max_restart_count = 3
        # ★v23.0新增：集中备份管理器
        from nucleus.reasoning.CodeBackupManager import CodeBackupManager
        self._backup_manager = CodeBackupManager(project_root)

    @staticmethod
    def _patch_dedup_key(patch: dict[str, Any]) -> tuple[str, str, str]:
        """★7-3修复（P2，2026-09-05）：补丁去重键 (file, method, repair_source)。

        设计取舍：
            用 method（结构性标识）而非 original_code（代码文本）作为主键维度。
            original_code 由 LLM 或规则从源码中截取，极易因上下文窗口、
            缩进、相邻行差异而出现「同一处改动、两样文本」，导致去重失配——
            补丁#1/#2 正是这样漏网的。method 是方法名，天然稳定。

        为什么纳入 repair_source：
            同文件同方法下，llm_multi_file 与 local_rule 是两套独立生成路径，
            本就应当作为不同候选**双双保留**以参与择优（这是 E6 多候选择优
            合并的设计意图）。若去重键不含来源，会误删其中一条候选，
            反而削弱择优空间。

        兼容性：
            本键是**叠加**在原有 (file, original_code) 键之上的第二道判定，
            两键取「或」，只增拦截、不减能力。
        """
        return (
            str(patch.get("file", "") or ""),
            str(patch.get("method", "") or ""),
            str(patch.get("repair_source", "") or ""),
        )

    @staticmethod
    def _m352_ensure_dedup_key(patch: dict[str, Any]) -> dict:
        """★352刀2：入池口强制 `dedup_key`（与 `audit_problem_pool` 同口径）。

        为什么要在入池口做：烛微352 实测 pending 40/40 全缺 `dedup_key`，
        而池（`audit_problem_pool`）侧早就有——两侧无法对账，于是“这条补丁对应哪个
        问题”永远只能靠 file+method 字面匹配。

        四种情形（**不同事实不同措辞**，铁律166）：
          · `ok`        已有且与可复算值一致
          · `filled`    缺失 ⇒ 现算补齐（本批前的全部历史条目就是这类）
          · `recomputed` 有但与可复算值不同 ⇒ 以可复算值为准，原值留 `dedup_key_raw`
                        并告警。为什么不拒收：不自洽的键比没键更糟，但因它
                        拒收会把真补丁打死（上游可能只是算法旧版本）。
          · `rejected`  file 或 method 缺失 ⇒ **无法定位，拒收**（没定位的补丁
                        进了池也只会每拍生成失败一次）。

        参数 `patch` 会被**就地修改**（这是入池口的使用方式，与 `_m323_normalize_patch` 一致）。
        """
        _f = str(patch.get("file") or "").strip()
        _m = str(patch.get("method") or "").strip()
        _i = str(patch.get("issue_type") or "").strip()
        _out = {"ok": False, "action": "rejected", "key": "", "expect": "",
                "raw": str(patch.get("dedup_key") or ""), "why": "",
                "degraded": False, "schema": "pending_dedup_key/352.v1"}
        if not _f or not _m:
            _out["why"] = "missing_%s" % ("file" if not _f else "method")
            return _out
        try:
            from nucleus.evolution.patch_generator import make_dedup_key as _mk
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m352_ensure_dedup_key import", level="debug")
            _out["why"] = f"key_module_unavailable:{type(_e).__name__}"
            return _out
        _expect = _mk(_f, _m, _i)
        _out["expect"] = _expect
        _out["key"] = _expect
        _out["degraded"] = not bool(_i)
        _cur = str(patch.get("dedup_key") or "").strip()
        if not _cur:
            _out["ok"] = True
            _out["action"] = "filled"
            _out["why"] = "absent_%s" % ("degraded_no_issue_type" if _out["degraded"] else "ok")
        elif _cur == _expect:
            _out["ok"] = True
            _out["action"] = "ok"
            _out["why"] = "consistent"
        else:
            _out["ok"] = True
            _out["action"] = "recomputed"
            _out["why"] = f"inconsistent:{_cur[:16]}!={_expect[:16]}"
            patch["dedup_key_raw"] = _cur
        patch["dedup_key"] = _expect
        return _out

    @staticmethod
    def _m384_ensure_dedup_key_v2(patch: dict[str, Any]) -> dict:
        """★384刀4：产出 `dedup_key_v2`（按 **modified_code 里的真实方法名**）。

        与 v1（`_m352_ensure_dedup_key`）的**唯一**差别是 method 的取值来源：
          · v1 用 `patch["method"]` 字段 —— 历史条目里可能是推断名 / 带占位 / 与代码
            实际 def 名不一致；
          · v2 用 `_m323_ast_def_name(modified_code)` 解析出的**真实 `def` 名**（AST，
            带重缩进重试，能处理片段是方法体的情况）。
        公式**完全相同**：`sha1(norm_file|method|issue_type)[:16]`（复用
        `patch_generator.make_dedup_key`，不另写一份）。

        ★双写不迁移：存量条目的 method 是否真实**无法自动判定**，若就地改写会让
          v1 与池侧 `dedup_key` 对不上、两侧对账直接断掉 ⇒ 迁移并入下一个停机窗。
          本刀只保证**新条目**起同时带 v1 与 v2。

        三态（不同事实不同措辞，铁律166）：
          · `same`    真实方法名 == method 字段 ⇒ v2 与 v1 同值（仍写，便于下游统一取 v2）
          · `differs` 两者不同 ⇒ 这正是 v2 要暴露的问题，写 v2 并留痕
          · `skipped` 解析不出 def 名（空 modified_code / 无 def / 语法坏）⇒ **不写 v2**
                      （宁可没有键，也不编一个假键 —— 假键会让去重按错的组归并）
        """
        _out = {"ok": False, "action": "skipped", "v2": "", "real_method": "",
                "why": "", "schema": "pending_dedup_key_v2/384.v1"}
        _f = str((patch or {}).get("file") or "").strip()
        _m = str((patch or {}).get("method") or "").strip()
        _i = str((patch or {}).get("issue_type") or "").strip()
        if not _f:
            _out["why"] = "missing_file"
            return _out
        _code = (patch or {}).get("modified_code")
        if not isinstance(_code, str) or not _code.strip():
            _out["why"] = "no_modified_code"
            return _out
        _real = str(PatchManager._m323_ast_def_name(_code) or "").strip()
        if not _real:
            _out["why"] = "no_def_in_code"
            return _out
        _out["real_method"] = _real
        try:
            from nucleus.evolution.patch_generator import make_dedup_key as _mk
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m384_ensure_dedup_key_v2 import", level="debug")
            _out["why"] = f"key_module_unavailable:{type(_e).__name__}"
            return _out
        _v2 = _mk(_f, _real, _i)
        _out["v2"] = _v2
        _out["ok"] = True
        _out["action"] = "same" if _real == _m else "differs"
        if not _m:
            _out["why"] = "method_empty_use_real"
        patch["dedup_key_v2"] = _v2
        return _out

    # ========== ★主线第80批 T7 (P0-3)：核心文件免签改写统一闸门 ==========
    #   单一路由，供 save_pending_patch（入队覆写）/ 反思直批(main.py) /
    #   落地(apply_all_pending) 共用。核心性单一真源为 SafeEvolutionExecutor._is_core_file。
    _M80_CORE_FILE_MARKERS = (
        "main.py", "config.py",
        "/base/", "base/",
        "nucleus/pulse/", "nucleus/field/", "nucleus/mnemosyne/",
        "nucleus/reasoning/", "nucleus/security/", "nucleus/evolution/",
        # ★T7-3：安全/宪法相关器官（精神宪法/人格内核/伦理）
        "PulseSpiritConstitution.py", "PulsePersonalityKernel.py", "PulseEthics.py",
    )

    @staticmethod
    def _m80_is_core_file(file_path: str) -> bool:
        """★T7(P0-3)：核心文件判定——优先复用 SafeEvolutionExecutor 单一真源。"""
        try:
            from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
            return SafeEvolutionExecutor._is_core_file(file_path or "")
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_m80_is_core_file L1195", level="debug")
            _norm = (file_path or "").replace("\\", "/")
            return any(m in _norm for m in PatchManager._M80_CORE_FILE_MARKERS)

    @staticmethod
    def _m80_auto_apply_enabled(evo_cfg: dict | None = None) -> bool:
        """★T7(P0-3)：读取 auto_apply_enabled 总开关（默认 False = 安全红线）。"""
        if evo_cfg is None:
            try:
                import config as _c
                evo_cfg = getattr(_c, "EVOLUTION_CONFIG", {})
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.PatchManager::_m80_auto_apply_enabled L404", level="debug")
                return False
        return bool(evo_cfg.get("auto_apply_enabled", False))

    @staticmethod
    def _m80_allow_core_auto_apply(evo_cfg: dict | None = None) -> bool:
        """★T7(P0-3)：核心文件自动批准专用红线开关（默认 False = 禁止核心自动应用）。
        与 auto_apply_enabled 分离：即便总开关打开，核心文件仍须 allow_core_auto_apply=True
        才放行（仅紧急回退用）；落地双判定以此为准。
        """
        if evo_cfg is None:
            try:
                import config as _c
                evo_cfg = getattr(_c, "EVOLUTION_CONFIG", {})
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.PatchManager::_m80_allow_core_auto_apply L418", level="debug")
                return False
        return bool(evo_cfg.get("allow_core_auto_apply", False))

    @staticmethod
    def _m471_core_human_exempt(patch: dict) -> bool:
        """★471刀1（裁决①·T-96b 一致性）：核心文件对「显式人工批准」开放落地豁免。

        背景：本文件 `apply_all_pending` 的核心闸门原本不区分来源一律拦，而同一方法的
        T-96b 契约写明「人工批准即为最高授权，不得被自动化开关反向否决」；
        `_core_markers` 含 `organs/brain/`（T-135d）⇒ 人工批准的 brain 补丁也落不了地。

        ★判据从严（fail-closed）：只认**正面证据** `approved_source == "human"`。
        该字段由 `_approve_via`（本文件唯一 approved 写入口）在人工口写入；
        `approve_patch_by_id` 也只在 `by` 以 `human:` 开头时才走 human 口。
        ★刻意不收 legacy：`approved_source` 缺失的旧获批件无法证明来源，一律不放行
        （否则 T-135d 的 `test_apply_now_landing_core_rejected` 会被这条豁免放行）。

        开关 `ENABLE_M471_CORE_HUMAN_OVERRIDE` 关闭 ⇒ 恒 False（回到改造前行为）。
        """
        try:
            import config as _c471
            if not getattr(_c471, "ENABLE_M471_CORE_HUMAN_OVERRIDE", True):
                return False
        except Exception as _e471:
            silent_exc(_e471,
                       where="PatchManager::_m471_core_human_exempt 读配置失败(fail-closed)",
                       level="debug")
            return False
        if not isinstance(patch, dict):
            return False
        if str(patch.get("status") or "") != "approved":
            return False
        if patch.get("auto_approved") or patch.get("auto_released"):
            return False
        return str(patch.get("approved_source") or "") == "human"

    @staticmethod
    def _m80_gate_blocked(patch: dict, evo_cfg: dict | None = None) -> bool:
        """★T7(P0-3)：免签改写闸门——命中核心文件或总开关关闭 → 拦截(True)。
        与 status==verified 组合：verified 补丁一律禁止升 approved。
        """
        # ★421：短路顺序与原 if 链一致（先判核心文件，再判总开关）
        return (PatchManager._m80_is_core_file(patch.get("file", ""))
                or not PatchManager._m80_auto_apply_enabled(evo_cfg))

    # ★第96批 T-96c（N2-② 烛微第1期）：**禁止自动补丁**的目标符号清单。
    #   历史补丁曾对 `_safe_eval_arithmetic` 这类**安全护栏**函数自动生成补丁，
    #   并一度被记为 applied。护栏一旦被无人类复核地改写，其约束语义可能被
    #   静默削弱 ⇒ 一律退回人工审批（不论来源/灵敏度/风险如何）。
    #   ★按**方法名精确匹配**（不做前缀模糊，避免误伤同名业务方法）。
    _M96_NO_AUTO_PATCH_METHODS = frozenset((
        "_safe_eval_arithmetic",
        "guard_write",
        "_check_patch_safety",
        "_check_patch_path",
        "_verify_in_copy",
        "_is_core_file",
        "_m80_gate_blocked",
    ))

    @staticmethod
    def _m85_local_low_risk_auto_apply(patch: dict, evo_cfg: dict | None = None) -> bool:
        """★第85批 T-85d（D84-4）：本地生成的低风险补丁是否放行自动应用。

        ★口径按**生产实测字段**（任务书给的 ``source="local"`` /
        ``confidence>=0.9`` / ``risk_level="low"`` 与实测不符，见交付报告
        「任务书偏差清单」——实测 64 条：source 恒为 ``local_rule``、
        confidence 为**字符串枚举** ``high``、risk_level 为**中文** ``低``）：
            source      == "local_rule"
            confidence  == "high"
            risk_level  rank == 1（中英通用：低 ↔ low；判据单点 `PatchManager._m346_is_low_risk`，函数 `_m85_local_low_risk_auto_apply`；346刀 5 前该比较在第 590 行）
        且必须同时满足：
            * **非核心文件** —— 沿用既有红线（``_m80_is_core_file``），本批不绕过；
            * **非学习尝试补丁** —— ``learning_attempt=True`` /
              ``source="local_learning"`` → 任务书 §T-85a.3「默认不自动应用」。

        ★LLM 补丁（``source="llm"``）一律返回 False —— 即使 auto_approved=True
        也仍走人工审批（任务书 §T-85d.2）。
        ★开关 ``EVOLUTION_CONFIG["local_auto_apply_enabled"]``（默认 True）
        关闭即整条放行失效（零副作用回退）。
        """
        if not isinstance(patch, dict):
            return False
        if evo_cfg is None:
            try:
                import config as _c
                evo_cfg = getattr(_c, "EVOLUTION_CONFIG", {})
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.PatchManager::_m85_local_low_risk_auto_apply L480", level="debug")
                return False
        if not isinstance(evo_cfg, dict):
            return False
        try:
            if not bool(evo_cfg.get("local_auto_apply_enabled", True)):
                return False
            if patch.get("learning_attempt"):
                return False
            if str(patch.get("source", "")) != "local_rule":
                return False
            if str(patch.get("confidence", "")) != "high":
                return False
            # ★346刀 5：读 rank（中英通用），不再比中文字面。判据单点：`PatchManager._m346_is_low_risk`
            if not PatchManager._m346_is_low_risk(patch.get("risk_level")):
                return False
            if PatchManager._m80_is_core_file(str(patch.get("file", ""))):
                return False
            # ★第96批 T-96c（N2-②）：安全护栏函数禁止自动补丁 ⇒ 转人工审批。
            if str(patch.get("method", "")) in PatchManager._M96_NO_AUTO_PATCH_METHODS:
                _module_logger.debug(
                    "[补丁自动审批][M96] 目标为安全护栏函数，转人工审批: %s",
                    patch.get("method", ""))
                return False
            # ★406刀4：diff 窄闸（只收窄）。既有判据不看"动了什么"，这里补上。
            _m406_ok, _m406_why = PatchManager._m406_narrow_gate(patch)
            if not _m406_ok:
                _module_logger.info(
                    "[补丁自动审批][406窄闸] 本地低风险放行被拦下(%s): %s:%s",
                    _m406_why, patch.get("file", ""), patch.get("method", ""))
                return False
            return True
        except Exception as _e:
            _module_logger.debug(
                "[补丁自动审批][M85] 本地放行判据异常，按保守(不放行)处理: %s: %s",
                type(_e).__name__, _e)
            return False

    @staticmethod
    def _m101_low_risk_release_path(patch: dict, evo_cfg: dict | None = None) -> bool:
        """★第101批 T-101a（P0）：低风险可放行路径判据（纯函数，保守默认 False）。

        放行条件（全部满足）：
            * **总开关 `auto_apply_enabled` 为 True** —— ★372刀2（裁决 C-1 甲）新增。
              改前本通道**故意**绕开总开关（旧注释：「比 auto_apply_enabled 更严格，
              故即使总开关关闭也放行」）⇒ 总开关关着也能自动升 approved，是安全面裂缝。
              现与 A 通道口径对齐：总开关关闭 ⇒ B 同样拒（不放行，退回人工）。
            * **source 不得为 `llm`** —— ★372刀2（裁决 C-1 甲）新增。
              改前本通道**允许** LLM 来源，而 A 通道（`_m85`）llm 一律拒；账本实测
              12/12 全 source=llm ⇒ 两条通道对同一来源给出相反结论。现与 A 通道对齐。
            * risk_level rank == 1（低/low，见 `PatchManager._m346_is_low_risk`；
              旧只认中文「低」使英文 low 走人工，346刀 5 修）
            * runtime_verify_result.verified is True —— ★第105批修正：读**嵌套**字段
              （唯一真验证字段）。顶层 runtime_verified 由 verify_submitted_patches
              对所有处理过的补丁无条件置 True（含失败补丁），属污染字段，不可作放行依据；
              另要求 baseline_errors>0（不可判定者转人工队列）。
            * 非核心文件（_m80_is_core_file 单一真源；落地核心门再兑底一次）
            * 目标方法不在安全护栏禁改清单 _M96_NO_AUTO_PATCH_METHODS
        不满足 / 异常 → False（保守：不放行，退回人工）。

        ★两调用点同受控：`PatchManager.save_pending_patch`（入队）与
        `SafeEvolutionExecutor.verify_submitted_patches`（验证后复查·补回边）
        都经 `_m105_try_release_low_risk` 落到本函数 ⇒ 此处收口即两点同时生效，
        **不需要**另开 B 独立开关（否 C-1 乙）。
        """
        if not isinstance(patch, dict):
            return False
        try:
            # ★372刀2（裁决 C-1 甲）闸一：并入总开关收口。
            #   改前本通道故意绕开总开关；现总开关关闭 ⇒ B 同样拒（与 A 通道对齐）。
            if not PatchManager._m80_auto_apply_enabled(evo_cfg):
                return False
            # ★372刀2（裁决 C-1 甲）闸二：source=llm 一律排除。
            #   A 通道（`_m85`）早已 llm 一律拒；本通道改前允许 ⇒ 同一来源两条通道
            #   结论相反。归一后风险档位判断不变，只掐掉"llm 走 B 通道"这条旁路。
            if str(patch.get("source", "") or "").strip().lower() == "llm":
                return False
            # ★346刀 5：读 rank（中英通用），不再比中文字面。判据单点：`PatchManager._m346_is_low_risk`
            if not PatchManager._m346_is_low_risk(patch.get("risk_level")):
                return False
            # ★第105批 T-105a：读嵌套 runtime_verify_result.verified（唯一真验证字段）。
            #   顶层 runtime_verified 由 verify_submitted_patches 对所有处理过的补丁
            #   无条件置 True（含失败补丁），属污染字段，不可作放行依据。
            _rvr = patch.get("runtime_verify_result") or {}
            if not isinstance(_rvr, dict) or _rvr.get("verified") is not True:
                return False
            # ★第105批 T-105a：要求 baseline_errors>0（不可判定者转人工队列）。
            #   优先取顶层 baseline_errors，回退 runtime_verify_result.baseline。
            _baseline = patch.get("baseline_errors")
            if not isinstance(_baseline, (int, float)) or _baseline <= 0:
                _baseline = _rvr.get("baseline")
                if not isinstance(_baseline, (int, float)) or _baseline <= 0:
                    return False
            _fp = str(patch.get("file", "") or "")
            if not _fp:
                return False
            if PatchManager._m80_is_core_file(_fp):
                return False
            if str(patch.get("method", "")) in PatchManager._M96_NO_AUTO_PATCH_METHODS:
                return False
            # ★406刀4：diff 窄闸（只收窄）。B 通道此前也只看 trust/risk/类型，
            #   不看"动了什么"；本闸是 PATCH_M406_NARROW_GATE 控制的可回退增量。
            _m406_ok, _m406_why = PatchManager._m406_narrow_gate(patch)
            if not _m406_ok:
                _module_logger.info(
                    "[T-101a][406窄闸] 低风险放行被拦下(%s): %s:%s",
                    _m406_why, patch.get("file", ""), patch.get("method", ""))
                return False
            return True
        except Exception:
            # ★T-101e：门禁友好——非静默上报（判据异常时保守不放行，但须留痕）
            _module_logger.debug("[T-101a] 低风险放行判据异常，保守退回人工: %s",
                                 type(Exception).__name__)
            return False

    # ========== ★第129批 T-129a（P1）：治理五口收敛器 ==========
    #   六票终裁后加固治理门禁，防止「下一个六票」。
    #   五处 approved 赋值（W1自动/W2老化/W3保存/W4人工/W5批量）全部经
    #   _approve_via 唯一写入口，fail-closed 双层串联
    #   （_govern_fields_ok 字段契约 + _boundary_check 边界），
    #   W4/W5 人工口强制 human:<名> 签名，W5 批量口单次≤10。
    #   ★apply 层 T-123b 冻结门不动（本收敛器只管「升 approved」）。

    @staticmethod
    def _govern_fields_ok(patch: dict) -> bool:
        """纯函数·fail-closed 第一关：approved 候选字段契约校验。

        - 必须是 dict
        - 必须有非空目标文件 file
        - 必须至少含一段代码（original_code 或 modified_code），否则是空壳补丁
        """
        if not isinstance(patch, dict):
            return False
        _file = patch.get("file")
        if not isinstance(_file, str) or not _file.strip():
            return False
        return bool(patch.get("original_code") or patch.get("modified_code"))

    @staticmethod
    def _boundary_check(patch: dict, source: str, signer) -> bool:
        """纯函数·fail-closed 第二关：approved 边界校验。

        - source 必须在白名单 {auto, aging, save, human}
        - human 口：signer 必须以 "human:" 开头（强制人工签名）
        - 非 human 口：signer 不得为人工签名（机器口不得冒充人工）
        """
        if source not in ("auto", "aging", "save", "human"):
            return False
        if source == "human":
            if not isinstance(signer, str) or not signer.startswith("human:"):
                return False
        else:
            if isinstance(signer, str) and signer.startswith("human:"):
                return False
        return True

    # ========== ★第161批下 刀6（T-补丁审批真值校验-1，P1）：真值校验第三关 ==========

    @staticmethod
    def _truth_check_enabled() -> bool:
        """真值校验总开关（config.PATCH_APPROVE_TRUTH_CHECK，默认 True）。

        False = 一键回退到施工前审批口径（只校字段契约与边界）。
        读取失败按「启用」处理 —— fail-closed 偏向不放行可疑补丁。
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "PATCH_APPROVE_TRUTH_CHECK", True))
        except Exception as _e:
            from nucleus._silent_except import silent_exc
            silent_exc(_e, "PatchManager._truth_check_enabled", level="debug")
            return True

    @staticmethod
    def _is_false_success(patch: dict) -> bool:
        """★判据与 tests/test_t100a_pending_no_false_success.py **逐字一致**（单一真相源）。

        假成功 = baseline_errors==0（无错可修）却申报 verified=True，
        且仍在待审批链路（status ∈ runtime_verified / approved / pending）。
        """
        if not isinstance(patch, dict):
            return False
        rv = patch.get("runtime_verify_result")
        if not isinstance(rv, dict):
            rv = {}
        baseline = patch.get("baseline_errors") or 0
        status = patch.get("status")
        return (baseline == 0 and rv.get("verified") is True
                and status in ("runtime_verified", "approved", "pending"))

    @staticmethod
    def _truth_ok(patch: dict) -> bool:
        """纯函数·fail-closed 第三关：申报结果 vs 真值一致性校验。

        未申报成功（verified 非 True）的补丁不涉及真值校验，一律放行 ——
        本关只治「自称成功」，不拦「老实说没成功」的正常补丁。

        ★磁盘真值（代码是否真的落地）由既有 disk_verified 读回机制负责
        （SafeEvolutionExecutor），此处**不重复实现**，避免第二判据分叉。
        """
        if not PatchManager._truth_check_enabled():
            return True
        if not PatchManager._is_false_success(patch):
            return True
        # ★留痕：审批记录标注真值不符（任务书验收要求「审批记录标注真值不符」）
        patch["truth_check_failed"] = True
        patch["truth_reason"] = (
            "baseline_errors==0 却申报 verified=True（无错可修的空转假成功）")
        return False

    @staticmethod
    def _approve_via(patch: dict, source: str, signer,
                     judged_by: str | None = None,
                     basis: dict | None = None) -> bool:
        """★第129批 唯一 approved 写入口（五口收敛器核心）。

        fail-closed：任一校验不过 → 不置 approved、返回 False。
        通过 → 统一落 status=approved + approved_source + approved_signer + approved_at，
        返回 True。各口在调用后再叠加自身留痕字段（auto_released / aged_* 等）。
        """
        if not PatchManager._govern_fields_ok(patch):
            return False
        if not PatchManager._boundary_check(patch, source, signer):
            return False
        # ★第161批下 刀6：第三关——申报结果 vs 真值（假成功一律不升 approved）
        if not PatchManager._truth_ok(patch):
            return False
        patch["status"] = "approved"
        patch["approved_source"] = source
        patch["approved_signer"] = signer
        patch["approved_at"] = time.time()
        # ★339刀1（E裁决）：SLA 四字段从「signer+at」
        #   补齐到「signer / at / judged_by(人|机) / basis」——没有 judged_by 就
        #   分不清「谁批的」和「谁判的」；没有 basis 就回答不了
        #   「凭什么批的」（与 338刀4 证据门同源：值必须带底座）。
        if judged_by is None:
            judged_by = "human" if str(source) == "human" else "machine"
        patch["approved_judged_by"] = str(judged_by)
        if basis is not None:
            patch["approved_basis"] = (dict(basis) if isinstance(basis, dict)
                                      else {"raw": str(basis)})
        return True

    def _m105_try_release_low_risk(self, patch: dict,
                                   evo_cfg: dict | None = None) -> bool:
        """★第105批 T-105a（P0）：对单条补丁尝试 T-101a 低风险放行（置 approved + 留痕）。

        判据见 ``_m101_low_risk_release_path``（嵌套 runtime_verify_result.verified
        + baseline_errors>0；★372刀2 起另需总开关为 True 且 source≠llm）。
        命中则就地置 ``status='approved'`` + ``auto_released=True``
        + 日志「低风险可放行(已升approved)」，返回 True；否则不动、返回 False。
        供 ``save_pending_patch``（入队）与 ``verify_submitted_patches``（验证后复查·补回边）
        两处复用，避免放行逻辑分叉。

        ``evo_cfg``：★372刀2 新增透传口。传 None（默认）⇒ 判据自己读 ``config``；
        传入则用调用方**已读到的同一份**配置。目的有二：
          ① 避免同一次判定里两次读 config 得到不一致口径；
          ② 让测试可显式注入配置，不必 monkeypatch 全局 `config`。
        """
        if not isinstance(patch, dict):
            return False
        if str(patch.get("status", "")) == "approved":
            return False
        if patch.get("auto_released"):
            return False
        if not PatchManager._m101_low_risk_release_path(patch, evo_cfg):
            return False
        if not PatchManager._approve_via(patch, "auto", None):
            return False
        patch["auto_released"] = True
        patch["release_reason"] = "low_risk_release:T-101a"
        _module_logger.info(
            f"[补丁自动审批][T-101a] 低风险可放行(已升approved): "
            f"{patch.get('file','')}:{patch.get('method','')} "
            f"risk={patch.get('risk_level')}, "
            f"runtime_verify_result.verified="
            f"{(patch.get('runtime_verify_result') or {}).get('verified')}")
        return True

    # ========== ★主线第94批 T-94a（P0）：待审批队列老化策略 ==========
    #   背景（第93批只读诊断）：14 轮「发现 168 / 修复 4」= 2.38%；空转主因
    #   「已有待审批」48 次（28.6%，单项最大）= 补丁入 pending 后等人工裁决，
    #   而自动化流程无人裁决 ⇒ 永久 pending ⇒ 同位置反复被跳过。
    #   ★本批 T0 实测（重要前提修正）：pending 队列 12 条**全部 source=llm**，
    #   local_rule **0 条** —— 因为 local_rule 在**入队时**已被
    #   `_m85_local_low_risk_auto_apply` 放行为 approved（65 条 local_rule
    #   全在 patch_history 且 applied=True）。故本策略的放行判据在真实队列上
    #   命中 0 条，属「纵深防御 + 未来场景」。已记 D94-1 上报星轨裁决。

    _M94_AGING_DEFAULT_MAX_COUNT = 20        # pending 条数阈值（任务书建议 20 条）
    _M94_AGING_DEFAULT_MAX_HOURS = 24.0      # 未裁决时长阈值（任务书建议 24 小时）

    # ★T-94a：任务书点名的「上帝文件」排除（只加严，不放松）——详见 `_m94_is_god_file`
    _M94_GOD_FILE_MARKERS = ("PulseInnerWorld.py",)

    @staticmethod
    def _m94_pending_aging_on() -> bool:
        """★T-94a：老化策略总开关（``ENABLE_PENDING_QUEUE_AGING``，默认 False）。

        关闭 → 全链路零行为变化（不读队列、不改状态、不写盘）。
        """
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_PENDING_QUEUE_AGING", False))
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m94_pending_aging_on L666", level="debug")
            return False

    @staticmethod
    def _m94_aging_max_count() -> int:
        """★T-94a：pending 条数阈值（可配，默认 20）。"""
        try:
            import config as _c
            return max(1, int(getattr(_c, "PENDING_AGING_MAX_COUNT",
                                      PatchManager._M94_AGING_DEFAULT_MAX_COUNT)))
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_m94_aging_max_count L1593", level="debug")
            return PatchManager._M94_AGING_DEFAULT_MAX_COUNT

    @staticmethod
    def _m94_aging_max_hours() -> float:
        """★T-94a：未裁决时长阈值（小时，可配，默认 24.0）。"""
        try:
            import config as _c
            return max(0.0, float(getattr(_c, "PENDING_AGING_MAX_AGE_HOURS",
                                          PatchManager._M94_AGING_DEFAULT_MAX_HOURS)))
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_m94_aging_max_hours L1603", level="debug")
            return PatchManager._M94_AGING_DEFAULT_MAX_HOURS

    @staticmethod
    def _m94_patch_age_hours(patch: dict, now: float | None = None) -> float | None:
        """★T-94a：补丁在 pending 中的停留时长（小时）。

        ★口径与 `_is_stale`（第54批）保持一致的时间戳候选集，并**追加**
        ``saved_at``（`save_pending_patch` 入队时必写，比 generated_at 更贴近
        「等待裁决」的起点）。取不到任何时间戳 → ``None``（**不误伤**历史补丁）。
        """
        if not isinstance(patch, dict):
            return None
        _now = float(time.time() if now is None else now)
        for _k in ("saved_at", "created_at", "submitted_at", "timestamp",
                   "generated_at"):
            _t = patch.get(_k)
            if isinstance(_t, (int, float)) and not isinstance(_t, bool) and _t > 0:
                return max(0.0, (_now - float(_t)) / 3600.0)
        return None

    @staticmethod
    def _m94_aging_eligible(patch: dict) -> bool:
        """★T-94a 放行判据（纯函数，逐条对齐任务书 §T-94a.1）：

            source           == "local_rule"（本地规则来源）
            非核心文件       —— 复用 `_m80_is_core_file` 单一真源
                              **再叠加** `_m94_is_god_file`（任务书点名上帝文件）
            runtime_verify_result.verified is True（★第105批修正：读嵌套字段；顶层 runtime_verified 为污染字段）
            risk_level       rank == 1（低/low，见 `PatchManager._m346_is_low_risk`；函数 `_m94_aging_eligible`，346刀 5 前该比较在第 912 行）

        ★字段口径按**生产实测**（与第85批 T-85d 同型）：实测 risk_level 为**中文**
        ``"低"``、source 为 ``"local_rule"``；任务书未给字段字面量，此处以实测为准。
        ★非 dict / 判据异常 → **一律返回 False**（保守：不放行）。
        ★``file`` 缺失 → False（保守：无路径信息不可判定是否非核心）。
        """
        if not isinstance(patch, dict):
            return False
        try:
            if str(patch.get("source", "")) != "local_rule":
                return False
            # ★第105批 T-105a：读嵌套 runtime_verify_result.verified（唯一真验证字段）。
            #   顶层 runtime_verified 由 verify_submitted_patches 对所有处理过的补丁
            #   无条件置 True（含失败补丁），属污染字段，不可作放行依据。
            _rvr = patch.get("runtime_verify_result") or {}
            if not isinstance(_rvr, dict) or _rvr.get("verified") is not True:
                return False
            # ★第105批 T-105a：要求 baseline_errors>0（不可判定者转人工队列）。
            #   优先取顶层 baseline_errors，回退 runtime_verify_result.baseline。
            _baseline = patch.get("baseline_errors")
            if not isinstance(_baseline, (int, float)) or _baseline <= 0:
                _baseline = _rvr.get("baseline")
                if not isinstance(_baseline, (int, float)) or _baseline <= 0:
                    return False
            # ★346刀 5：读 rank（中英通用），不再比中文字面。判据单点：`PatchManager._m346_is_low_risk`
            if not PatchManager._m346_is_low_risk(patch.get("risk_level")):
                return False
            _fp = str(patch.get("file", "") or "")
            if not _fp:
                return False          # ★无路径信息 → 不放行（保守）
            if PatchManager._m80_is_core_file(_fp):
                return False
            return not PatchManager._m94_is_god_file(_fp)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m94_aging_eligible L750", level="debug")
            return False

    @staticmethod
    def _m94_is_god_file(file_path: str) -> bool:
        """★T-94a：任务书点名的「上帝文件」排除（**只加严，不放松**）。

        任务书 §T-94a.1 原文：「非核心文件（排除 organs/brain/PulseInnerWorld.py
        等上帝文件）」。而既有单一真源 `_is_core_file` / `_m80_is_core_file` 的
        ``_core_markers`` **不含** ``organs/`` ⇒ 该上帝文件会被判为「非核心」。
        此处叠加一层**薄**排除，且**仅在 M94 放行判据内生效** ——
        *不改* `_is_core_file` 全局语义（免签闸门等既有链路零影响）。
        判定异常 / 无路径信息 → True（保守：视为受保护）。
        """
        try:
            _norm = str(file_path or "").replace("\\", "/")
            if not _norm:
                return True          # 无路径信息 → 保守视为受保护
            return any(m in _norm for m in PatchManager._M94_GOD_FILE_MARKERS)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m94_is_god_file L769", level="debug")
            return True

    @staticmethod
    def _m94_aging_should_run(pending: list,
                              now: float | None = None) -> tuple[bool, str]:
        """★T-94a：是否达到老化触发条件。

        任务书 §T-94a.1：「pending 超过 N 条（建议 20）**或**超过 T 小时（建议 24）
        未裁决」⇒ 二者取**或**。返回 ``(是否触发, 原因串)``，原因串供日志留痕。
        """
        if not isinstance(pending, list) or not pending:
            return False, "empty"
        _n = len([p for p in pending if isinstance(p, dict)])
        _maxc = PatchManager._m94_aging_max_count()
        if _n >= _maxc:
            return True, f"count={_n}>={_maxc}"
        _ages = [a for a in (PatchManager._m94_patch_age_hours(p, now)
                             for p in pending) if isinstance(a, float)]
        _maxh = PatchManager._m94_aging_max_hours()
        if _ages and max(_ages) >= _maxh:
            return True, f"max_age={max(_ages):.1f}h>={_maxh:.1f}h"
        _max_age_s = f"{max(_ages):.1f}h" if _ages else "unknown"
        return False, f"below_threshold(n={_n},max_age={_max_age_s})"

    def pending_aging_preview(self, now: float | None = None) -> dict[str, Any]:
        """★T-94a：只读预览——当前 pending 队列在老化策略下会放行哪些补丁。

        **不写盘、不改状态**；供诊断 / 交付报告 / 单测使用。
        ★``triggered`` 为**假设语义**：「若开关开启，当前队列是否会达阈值」，
        与 ``enabled``（开关真实状态）相互独立 —— 便于开关关闭时评估影响面。
        """
        _out: dict[str, Any] = {
            "enabled": self._m94_pending_aging_on(), "triggered": False,
            "reason": "", "checked": 0, "eligible": 0, "released": [],
            "max_count": self._m94_aging_max_count(),
            "max_hours": self._m94_aging_max_hours(),
        }
        try:
            _pending = self._load_patch_list(self._pending_file)
        except Exception as _e:
            silent_exc(_e, where="nucleus.reasoning.PatchManager::pending_aging_preview", level="debug")
            _out["reason"] = f"load_failed:{type(_e).__name__}"
            return _out
        if not isinstance(_pending, list):
            return _out
        _out["checked"] = len(_pending)
        _should, _why = self._m94_aging_should_run(_pending, now)
        _out["triggered"] = bool(_should)
        _out["reason"] = _why
        # ★条数触发=队列拥堵 ⇒ 放行全部合格项；时长触发 ⇒ 只放行「自身也超期」的
        _by_count = str(_why).startswith("count=")
        _maxh = self._m94_aging_max_hours()
        for _p in _pending:
            if not isinstance(_p, dict):
                continue
            if str(_p.get("status", "")) == "approved":
                continue
            if not self._m94_aging_eligible(_p):
                continue
            if not _by_count:
                _age = self._m94_patch_age_hours(_p, now)
                if _age is None or _age < _maxh:
                    continue
            _out["eligible"] += 1
            _out["released"].append({
                "id": _p.get("id"), "file": _p.get("file"),
                "method": _p.get("method"),
                "age_h": self._m94_patch_age_hours(_p, now),
            })
        return _out

    def _m94_apply_pending_aging(self, pending: list,
                                 now: float | None = None) -> dict[str, Any]:
        """★T-94a：对 pending 队列执行老化放行（就地改 status 并**落盘**）。

        * 开关关闭（默认）→ 立即返回 ``enabled=False``，**不触碰**任何补丁；
        * 未达阈值 → 返回 ``triggered=False``，同样不改状态；
        * 达阈值 → 对 `_m94_aging_eligible` 命中的补丁置 ``status='approved'``
          （+ ``aged_approved`` / ``aged_approved_at`` / ``aged_reason`` 留痕），
          使后续 ``only_approved=True`` 的过滤能捞到它们。
          ★放行集合按触发器区分：**条数触发**（队列拥堵）放行全部合格项；
          **时长触发**只放行「自身停留也 ≥ max_hours」的合格项 ——
          避免队列里刚入队的合格补丁被顺手放行（违背「老化」本意）。

        ★落盘用**浅拷贝**列表：`_save_patch_list` 会把 ``file`` 归一化为相对路径
        （原地改写元素），若直接传原列表会污染后续 apply 循环依赖的绝对路径。
        """
        _out: dict[str, Any] = {
            "enabled": False, "triggered": False, "reason": "",
            "released": [], "eligible": 0, "checked": 0, "persisted": False,
        }
        if not self._m94_pending_aging_on():
            return _out
        _out["enabled"] = True
        if not isinstance(pending, list) or not pending:
            _out["reason"] = "empty"
            return _out
        _out["checked"] = len(pending)
        _should, _why = self._m94_aging_should_run(pending, now)
        _out["reason"] = _why
        if not _should:
            return _out
        _out["triggered"] = True
        _now = float(time.time() if now is None else now)
        # ★条数触发=队列拥堵 ⇒ 放行全部合格项；时长触发 ⇒ 只放行「自身也超期」的
        _by_count = str(_why).startswith("count=")
        _maxh = self._m94_aging_max_hours()
        _released = []
        for _p in pending:
            if not isinstance(_p, dict):
                continue
            if str(_p.get("status", "")) == "approved":
                continue
            if not self._m94_aging_eligible(_p):
                continue
            if not _by_count:
                _age_h = self._m94_patch_age_hours(_p, now)
                if _age_h is None or _age_h < _maxh:
                    continue
            if not PatchManager._approve_via(_p, "aging", None):
                continue
            _p["aged_approved"] = True
            _p["aged_approved_at"] = _now
            _p["aged_reason"] = _why
            _released.append({
                "id": _p.get("id"), "file": _p.get("file"),
                "method": _p.get("method"),
                "age_h": self._m94_patch_age_hours(_p, now),
            })
        _out["eligible"] = len(_released)
        _out["released"] = _released
        if _released:
            try:
                _payload = [dict(_p) if isinstance(_p, dict) else _p
                            for _p in pending]
                _out["persisted"] = bool(self._save_patch_list(
                    self._pending_file, _payload))
            except Exception as _e:
                _module_logger.warning(
                    "[补丁老化][M94] 释放结果落盘失败: %s: %s",
                    type(_e).__name__, _e)
            _module_logger.info(
                "[补丁老化][M94] 触发(%s)：放行 %d 条合格补丁为 approved"
                "（判据 local_rule+非核心+runtime_verified+低风险）",
                _why, len(_released))
        else:
            _module_logger.info(
                "[补丁老化][M94] 触发(%s)但无合格补丁可放行"
                "（判据 source=local_rule / 非核心 / runtime_verified=True / risk_level=低）",
                _why)
        return _out

    # ========== ★主线第339批 刀1（E裁决）：审批分道 —— O3「确认后自动派单」 ==========
    #   背景（烛微338前置§2.2）：O3 入池条目一律 `requires_confirmation=True`、
    #   `risk=高` ⇒ 进不了自动审批；人工入口存在但「无裁决人 + 时限关着」
    #   （`ENABLE_PENDING_QUEUE_AGING=False`，280刀3 关闭理由 = "aging 对 source=llm
    #   队列空转，明写 llm 永不自动"）。94批自陈空转主因「已有待审批」48 次（28.6%）。
    #   真实断点 = **没裁决人 + 时限关着**，不是"审批逻辑写错了"。
    #
    #   ★本批**绝不动** llm 类语义：`source=llm` 一律「永不自动」（280刀3 裁定，
    #     一字不改）；分道只给 `source=o3` 开一条**显式**通道，而且前提是**人已裁决**
    #     （`approved_signer` 落签名 + 四项审核齐 + `basis` 可复算）。

    _M339_O3_SOURCES = ("o3_open_gate", "o3")
    _M339_O3_ISSUE_TYPES = ("dead_code", "dead_code_candidate")
    _M339_REVIEW_ITEMS = ("evidence_tier", "probe_out_of_graph",
                          "misjudge_sample", "god_file")
    _M339_O3_AGING_TARGET = "expired_pending"
    _M339_O3_AGING_REVERT = "pending"

    @staticmethod
    def _m339_is_o3(patch: dict) -> bool:
        """O3（判死类）来源判定——**显式字段**，从理由串里找关键字（铁律153）。"""
        if not isinstance(patch, dict):
            return False
        if patch.get("o3_dead_code") is True:
            return True
        if str(patch.get("source") or "") in PatchManager._M339_O3_SOURCES:
            return True
        return str(patch.get("issue_type") or "") in PatchManager._M339_O3_ISSUE_TYPES

    @staticmethod
    def _m339_review_items_ok(basis) -> tuple:
        """审核面固定四项是否齐。

        四项：`evidence_tier`（证据档）/ `probe_out_of_graph`（图外 probe 四类）/
        `misjudge_sample`（抽验 misjudge 样本行）/ `god_file`（是否上帝文件）。

        返回 `(ok, reason, missing)`。缺项 ⇒ False（fail-closed：四项不是"建议"，
        是 O3 自动派单的**前置条件**——没它，"确认后自动派单"就退化成"点一下就放"）。
        """
        if not isinstance(basis, dict):
            return False, "basis 缺失（需 dict）", list(PatchManager._M339_REVIEW_ITEMS)
        _items = basis.get("items") if isinstance(basis.get("items"), dict) else basis
        _miss = [k for k in PatchManager._M339_REVIEW_ITEMS if _items.get(k) is None]
        if _miss:
            return False, "审核四项缺 {}".format(",".join(_miss)), _miss
        # 上帝文件只加严不放松：显式 True ⇒ 不许走自动派单
        if _items.get("god_file") is True:
            return False, "目标为上帝文件（PulseInnerWorld.py）⇒ 只加严不放松", ["god_file"]
        return True, "ok", []

    @staticmethod
    def _m339_o3_aging_on() -> bool:
        """O3 类老化开关（`ENABLE_PENDING_QUEUE_AGING_O3`，**默认 True**）。

        ★为何默认开：任务书刀1 明写"`ENABLE_PENDING_QUEUE_AGING` 对 O3 类单独
        开启（对 llm 类保持关闭）"。O3 的老化动作是**降级不删除**
        （`pending → expired_pending`，可回滚），不是放行 —— 与 llm 类 aging
        的"自动 approved"是**两种不同动作**，不能共用一个开关（共用一个就等于
        顺手给 llm 开了自动审批）。

        读不到配置 ⇒ True（保持任务书要求的开启态），但 **留痕**。
        """
        try:
            import config as _c339
            return bool(getattr(_c339, "ENABLE_PENDING_QUEUE_AGING_O3", True))
        except Exception as e:
            silent_exc(e, where="PatchManager::_m339_o3_aging_on", level="debug")
            return True

    def _m339_o3_aging(self, pending: list, now: float | None = None) -> dict:
        """★339刀1：O3 条目超时未裁决 ⇒ **降级不删除**（可回滚）。

        与 `_m94_apply_pending_aging` 的**动作不同**：
          · M94（llm/local_rule）：到期 ⇒ 置 approved（自动审批，280刀3 已关）
          · M339（O3）：到期 ⇒ 置 expired_pending（**只是退出待裁决队列**，
            原状态记在 `acked_origin_status`，`expired_revertible=True` 可回滚）
        两条**互不引用**，避免"改 O3 顺手改了 llm"。时限沿用 24h（不新增数）。
        """
        _out = {"enabled": False, "checked": 0, "expired": 0, "ids": [],
                "max_hours": None, "persisted": False, "reason": ""}
        if not self._m339_o3_aging_on():
            _out["reason"] = "disabled"
            return _out
        _out["enabled"] = True
        if not isinstance(pending, list) or not pending:
            _out["reason"] = "empty"
            return _out
        _out["checked"] = len(pending)
        _now = float(time.time() if now is None else now)
        _maxh = self._m94_aging_max_hours()
        _out["max_hours"] = _maxh
        _ids = []
        for _p in pending:
            if not isinstance(_p, dict) or not self._m339_is_o3(_p):
                continue
            if str(_p.get("status") or "") != "pending":
                continue
            _age = self._m94_patch_age_hours(_p, _now)
            if _age is None or _age < _maxh:
                continue
            _p["acked_origin_status"] = str(_p.get("status") or PatchManager._M339_O3_AGING_REVERT)
            _p["status"] = PatchManager._M339_O3_AGING_TARGET
            _p["expired_at"] = _now
            _p["expired_reason"] = f"o3_unjudged_age={_age:.1f}h>={_maxh:.0f}h"
            _p["expired_revertible"] = True
            _ids.append(str(_p.get("id") or "")[:32])
        _out["expired"] = len(_ids)
        _out["ids"] = _ids
        if _ids:
            try:
                _payload = [dict(_x) if isinstance(_x, dict) else _x for _x in pending]
                _out["persisted"] = bool(self._save_patch_list(self._pending_file, _payload))
            except Exception as _e:
                _module_logger.warning(
                    "[339审批][O3老化] 落盘失败（内存已改、盘未改，下轮重试）: %s: %s",
                    type(_e).__name__, _e)
            _module_logger.info(
                "[339审批][O3老化] 超时未裁决 ⇒ 降级 %d 条 pending→expired_pending"
                "（不删除，可经 acked_origin_status 回滚）: %s max_hours=%.0f",
                len(_ids), ",".join(_ids), _maxh)
        else:
            _module_logger.info(
                "[339审批][O3老化] 无超期未裁决 O3 条目（checked=%d max_hours=%.0f）",
                _out["checked"], _maxh)
        return _out

    def approve_o3_patch(self, index: int, signer: str, basis: dict,
                         judged_by: str = "human") -> dict:
        """★339刀1：O3 条目的**唯一人工裁决口**（四项审核齐才放行）。

        语义："人工确认后**不再要求二次审批**自动派单"——所以本方法成功后
        直接置 `o3_auto_dispatch_ready=True` + `requires_confirmation=False`，
        派单侧（`patch_execution_dispatch.dispatch_o3_approved`）看到这两个显式
        标记即自动派单，**不再回头看 status 重审**（避免"再审批一次"的二次门）。

        反例：`source=llm` 的条目**不许**走本口（`_m339_is_o3` 不为真 ⇒ 直接拒），
        保证 llm 的"永不自动"一字不动。
        """
        _pending = self._load_patch_list(self._pending_file)
        if not (0 <= index < len(_pending)):
            return {"ok": False,
                    "reason": f"索引越界，共{len(_pending)}个待审批补丁"}
        _p = _pending[index]
        if not self._m339_is_o3(_p):
            _module_logger.warning(
                "[339审批] 拒绝：非 O3 来源走本口（source=%s issue_type=%s）"
                " ⇒ llm/生成类走人工常口，永不自动语义不变",
                _p.get("source"), _p.get("issue_type"))
            return {"ok": False, "reason": "非 O3 来源，不得走确认后自动派单通道"}
        _ok, _why, _miss = self._m339_review_items_ok(basis)
        if not _ok:
            _module_logger.warning(
                "[339审批] 拒绝：审核四项不齐（%s）patch=%s",
                _why, str(_p.get("id") or "")[:24])
            return {"ok": False, "reason": _why, "missing": _miss}
        if not PatchManager._approve_via(_p, "human", signer,
                                         judged_by=judged_by, basis=basis):
            return {"ok": False, "reason": "审批被治理门禁拦截（需 human:<名> 签名且字段契约完整）"}
        _p["o3_auto_dispatch_ready"] = True
        _p["requires_confirmation"] = False
        _p["o3_confirmed_at"] = time.time()
        if not self._save_patch_list(self._pending_file, _pending):
            return {"ok": False, "reason": "写入失败（可能被 WriteGuard 拦截）"}
        # ★343刀1：落盘后**复核条目仍在池内** —— 这是本次死链的教训：
        #   改前 approve 返回 ok=True，而条目已在 `_m280_converge_pending` 里被
        #   作废过滤掉（假成功）。凡"写回成功"都必须再读一次盘确认，
        #   否则报喜不报丧（铁律166：机制生效但产出是 0 要能区分）。
        _after = self._load_patch_list(self._pending_file)
        _still = None
        for _x in (_after if isinstance(_after, list) else []):
            if isinstance(_x, dict) and str(_x.get("id") or "") == str(_p.get("id") or ""):
                _still = _x
                break
        if (_still is None
                or str(_still.get("status") or "") != "approved"
                or _still.get("o3_auto_dispatch_ready") is not True):
            _module_logger.warning(
                "[343O3] 审批落盘复核失败：patch=%s 池内=%s status=%s ready=%s"
                " ⇒ 条目未留在待修池（派单侧将看不到）",
                str(_p.get("id") or "")[:32],
                "在" if _still is not None else "缺失",
                (_still or {}).get("status"), (_still or {}).get("o3_auto_dispatch_ready"))
            return {"ok": False,
                    "reason": "落盘复核未通过：条目未留在待修池（占位=被收敛吞掉）",
                    "patch_id": _p.get("id"), "auto_dispatch_ready": False,
                    "persisted_present": _still is not None}
        _module_logger.info(
            "[339审批] O3 确认后自动派单已置位：patch=%s %s::%s signer=%s "
            "judged_by=%s ⇒ 派单侧下一拍自动执行（无二次审批）",
            str(_p.get("id") or "")[:24], _p.get("file"), _p.get("method"),
            signer, judged_by)
        return {"ok": True, "patch_id": _p.get("id"), "auto_dispatch_ready": True}

    def o3_pending_sla_summary(self, now: float | None = None) -> dict:
        """★339刀1：O3 待裁决队列的周报口径（**未裁决条数 / 最老时长**）。

        与 `publishers.publish_patch_quality` 的 `verifiable_rate` **同型**：
        只产字段，不判红绿、不触发动作。
        """
        _now = float(time.time() if now is None else now)
        _out = {"ok": False, "unjudged": None, "oldest_hours": None,
                "expired": None, "max_hours": None}
        try:
            _pending = self._load_patch_list(self._pending_file)
        except Exception as _e:
            silent_exc(_e, where="PatchManager::o3_pending_sla_summary load", level="debug")
            return _out
        if not isinstance(_pending, list):
            return _out
        _un = []
        _exp = 0
        for _p in _pending:
            if not isinstance(_p, dict) or not self._m339_is_o3(_p):
                continue
            _st = str(_p.get("status") or "")
            if _st == PatchManager._M339_O3_AGING_TARGET:
                _exp += 1
                continue
            if _st in ("approved", "applied"):
                continue
            _un.append(_p)
        _ages = [a for a in (self._m94_patch_age_hours(_p, _now) for _p in _un)
                 if isinstance(a, float)]
        _out.update({"ok": True, "unjudged": len(_un),
                     "oldest_hours": round(max(_ages), 1) if _ages else None,
                     "expired": _exp, "max_hours": self._m94_aging_max_hours()})
        return _out

    # ★第309批 刀1：**去掉误加的 @staticmethod**。它把带 self 的普通方法绑成静态函数，
    #   调用方 `PatchManager(root).rescan_high_risk_levels()` 一实参都不传 ⇒
    #   TypeError: missing 1 required positional argument: 'self'。
    #   后果：300 批的存量高危回扫**从未成功跑过一次**（306/308 两次升 CRITERIA_VERSION
    #   之后的「按新判据重算」全部死在这一行，仅由 silent_exc 可见化留痕）。
    def rescan_high_risk_levels(self) -> dict:
        """★第300批刀7：存量高危 issue_type 回扫定级（280刀2只管新补丁，欠一次回扫）。

        只做**升级**（高危族 sql_injection/lock_with_emit/non_atomic_write → 高），
        不做降级（避免把 code_optimization「中等→低」这类语义变更带进来）。
        幂等：已是"高"则跳过，重复调用无副作用。
        """
        _HIGH = ("sql_injection", "lock_with_emit", "non_atomic_write")
        _hist = self._load_patch_list(self._history_file)
        if not isinstance(_hist, list):
            return {"changed": 0, "total": 0}
        _changed = 0
        for _p in _hist:
            if not isinstance(_p, dict):
                continue
            if str(_p.get("issue_type", "")) in _HIGH and _p.get("risk_level") != "高":
                _p["risk_level"] = "高"
                _p["_300_rescanned_high"] = True
                _changed += 1
        if _changed:
            self._save_patch_list(self._history_file, _hist)
            _module_logger.warning(
                f"[存量回扫][300刀7] 高危族重定级 {_changed} 条 → 高")
        return {"changed": _changed, "total": len(_hist)}

    def _m348_o3_wontfix(self, patch_id: str, reason: str = "", source: str = "",
                         by: str = "") -> dict:
        """★348刀10（74号裁定）：把一条 O3 候选**撤单**并落台账。

        为什么必须有这个方法（而不是手工改 json）：撤单不是一个字段，
        是「status + 不派单位 + 留痕」三件一起，缺任何一件都会静默失效 ——
        只改 reason 不改 status ⇒ 下拍仍被 apply_all_pending 当 approved 处理；
        只改 status 不置 `o3_auto_dispatch_ready=False` ⇒ 派单链仍会认领。

        Returns: ``{ok, changed, id, from_status, to_status, reason}``；
        找不到 / 非法迁移一律 ``ok=False`` 且**不写盘**（不猜、不静默纠正）。
        """
        _pid = str(patch_id or "").strip()
        if not _pid:
            return {"ok": False, "changed": False, "id": "", "reason": "empty_patch_id"}
        from nucleus.evolution import patch_lifecycle as _pl
        _hist = self._load_patch_list(self._history_file)
        if not isinstance(_hist, list):
            return {"ok": False, "changed": False, "id": _pid,
                    "reason": "history_unavailable"}
        for _p in _hist:
            if not isinstance(_p, dict) or str(_p.get("id") or "") != _pid:
                continue
            _r = _pl.mark_history_wont_fix(_p, reason=reason, source=source, by=by)
            _out = {"ok": bool(_r.get("ok")), "changed": bool(_r.get("changed")),
                    "id": _pid, "from_status": _r.get("from_status"),
                    "to_status": _r.get("to_status"), "reason": _r.get("reason")}
            if not _out["ok"]:
                _module_logger.warning(
                    "[348刀10] 撤单被拒（非法迁移，未写盘）id=%s status=%s reason=%s",
                    _pid, _r.get("from_status"), _r.get("reason"))
                return _out
            # ★348刀10 订正（实测暴露）：`_save_patch_list` 会走 `guard_write`，
            #   守卫拒绝时**返回值就是 False**；原先不接返回值 ⇒ 内存已改、文件没写，
            #   方法却回 ok=True。d10 首轮 P2 就是这么被现场抓到的：ret.ok=True 而
            #   patch_history.json 的 mtime 一字未动、status 仍是 approved。
            #   「有键无色」同罪（铁律156）：**落盘与否必须上报**。
            _save_ok = bool(self._save_patch_list(self._history_file, _hist))
            _out["persisted"] = _save_ok
            if not _save_ok:
                _out["ok"] = False
                _out["reason"] = "persist_refused_or_failed"
                _module_logger.warning(
                    "[348刀10] 撤单**未落盘**（内存已改，文件未写）id=%s 原状=%s",
                    _pid, _r.get("from_status"))
                return _out
            _module_logger.warning(
                "[348刀10] O3 撤单 wont_fix id=%s 原状=%s 理由=%s by=%s",
                _pid, _r.get("from_status"), reason, by)
            return _out
        return {"ok": False, "changed": False, "id": _pid, "reason": "patch_not_found"}

    def _m280_extract_def_name(code: str) -> str:
        # ★280批刀1（P0）：从 modified_code 提取首个 def 名。
        #   解析失败/无 def → 空串（保守：不误拦）。
        if not code or not isinstance(code, str):
            return ""
        try:
            import re
            _m = re.search(
                r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
                code, re.MULTILINE)
            return _m.group(1) if _m else ""
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m280_extract_def_name", level="debug")
            return ""

    #: ★372刀1（裁决 C-2 甲）：族 → 风险档。**表即唯一真源**。
    #:   原先是函数内局部 dict：取值一样，但「族覆盖是否完备」既不能被测试枚举，
    #:   也无法回答「这个族到底登记过没有」。移到类属性后 `_m372_family_registered`
    #:   与族覆盖对照件共用同一份数据，不会两处各抄一份再漂移。
    #:   ★取值一字未动（280刀2 立的 11 族：7 高 / 3 中 / 1 低）。
    # ★421：`ClassVar` 把"这是**类级只读常量**、全实例共享"写进类型。
    #   原写法只有裸赋值，ruff 判不出意图故报 RUF012。
    _M280_RISK_BY_FAMILY: ClassVar[dict] = {
        "sql_injection": "高",
        "lock_with_emit": "高",
        "non_atomic_write": "高",
        "command_injection": "高",
        "path_traversal": "高",
        "eval_usage": "高",
        "pickle_load": "高",
        "thread_no_daemon": "中",
        "resource_no_close": "中",
        "cross_module_singleton_call": "中",
        "periodic_task_no_reentry": "低",
    }

    #: ★372刀1：族 → 信任分。同上，表即唯一真源（280刀4 立的 7 族，取值一字未动）。
    # ★421：`ClassVar` 把"这是**类级只读常量**、全实例共享"写进类型。
    #   原写法只有裸赋值，ruff 判不出意图故报 RUF012。
    _M280_TRUST_BY_FAMILY: ClassVar[dict] = {
        "sql_injection": 20.0,
        "lock_with_emit": 25.0,
        "non_atomic_write": 30.0,
        "thread_no_daemon": 50.0,
        "resource_no_close": 55.0,
        "cross_module_singleton_call": 60.0,
        "periodic_task_no_reentry": 75.0,
    }

    #: ★372刀1（裁决 C-2 甲 · fail-closed）：**未登记族**的风险兜底档。
    #:   改前是 "低"。实测依据（T0，2026-10-26 HEAD `5dff8abe`）：
    #:     · `data/patches/patch_history.json` 87 条里 **81 条（93.1%）**属未登记族
    #:       （silent_exception 57 / Traceback 9 / print_instead_of_log 8 /
    #:        bare_return_none_in_except 3 / code_optimization 2 / dead_code_candidate 2）；
    #:       pending 11 条里 4 条未登记。
    #:     · 两条自动面**只以 `_m346_is_low_risk`（rank==1）为枢轴**（三条闸都不读 trust）：
    #:       `_m85_local_low_risk_auto_apply` / `_m101_low_risk_release_path` /
    #:       `_m94_aging_eligible`；另 `patch_execution_dispatch.select_for_auto_triage`
    #:       要求 risk 低 + trust 达标。
    #:   ⇒ 未登记族一律"低"＝自动档对主力族全开（fail-open）。
    #:   改"中"后：上述自动面全部关闭，`PatchAutoApprover.classify` 落 need_human
    #:   （**转人工**，不是直接拒）——与裁决口径「转人工或拒自动档」取前者，
    #:   保留人工审的可能，也避免把主力族打成"高风险拒绝"。
    #:   ★零放宽：只把**未登记族**从"低"提到"中"；已登记 11 族取值一字未动。
    _M372_DEFAULT_FAMILY_RISK = "中"

    #: ★372刀1：未登记族的信任分兜底，**保持 40.0 不变**（与
    #:   `config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD`=40 相等，恰踩线）。
    #:   为什么不一起压低：risk 已是唯一枢轴，trust 只影响 need_human / reject 的分界；
    #:   压低会把「人能看、能批」的条目直接打成「拒绝」，反而丢掉人工审。
    #:   ★残留如实记：单看 trust=40 仍与门槛线相等 ⇒ **不得**再新增「只看 trust」的放行口。
    _M372_DEFAULT_FAMILY_TRUST = 40.0

    @staticmethod
    def _m280_risk_by_issue_type(issue_type: str) -> str:
        # ★280批刀2：高危族按 issue_type 强制重定级（覆盖 LLM 自评分）。
        # ★346刀 4：高危族扩表（安全面收紧，零放宽）。
        #   烛微346 实测：command_injection / path_traversal / eval_usage / pickle_load
        #   四族全判**低**——于是它们能走 auto_apply 面。定级只认**族名**（不猜形态）。
        # ★372刀1：表移到 `_M280_RISK_BY_FAMILY`；未命中族的兜底由 "低" 改 "中"
        #   （fail-closed，见 `_M372_DEFAULT_FAMILY_RISK`）。
        return PatchManager._M280_RISK_BY_FAMILY.get(
            str(issue_type or ""), PatchManager._M372_DEFAULT_FAMILY_RISK)

    @staticmethod
    def _m383_family_triage_enabled() -> bool:
        """★383刀5：三级族分级总开关（读 config，**默认关**）。

        关 ⇒ `_m383_family_triage` 一律返 "off"，调用点逐字回到 176 批的
        SAFE_TO_AUTO_FIX 单表逻辑 ⇒ 零行为变化，可随时回滚。
        """
        try:
            import config as _c383
            return bool(getattr(_c383, "ENABLE_M383_FAMILY_TRIAGE", False))
        except Exception as _e383:
            silent_exc(_e383, where="PatchManager::_m383_family_triage_enabled",
                       level="debug")
            return False

    @staticmethod
    def _m383_family_triage(patch) -> str:
        """★383刀5：把补丁的 issue_type 归入 SAFE / REVIEW / BLOCKED。

        **档次直接派生自 `_M280_RISK_BY_FAMILY`**，不另立第二张族表：
            风险低（rank 1） ⇒ SAFE     可自动审批
            风险中（rank 2） ⇒ REVIEW   转人工（**含未登记族**，372刀1 的兜底）
            风险高（rank 3） ⇒ BLOCKED  永不自动，另打 WARNING 留痕

        返回 "off" 表示开关关闭 —— 调用方按旧逻辑处理（不是"未知档"）。
        档次判定走 `_m346_risk_rank`，不写中文字面比较（346刀5 的纪律）。
        """
        if not PatchManager._m383_family_triage_enabled():
            return "off"
        _it = str((patch or {}).get("issue_type", "") or "")
        _rank = PatchManager._m346_risk_rank(
            PatchManager._m280_risk_by_issue_type(_it))
        if _rank == 1:
            return "safe"
        if _rank == 3:
            return "blocked"
        # rank 2 与「不认识」（None）都落 REVIEW：保守转人工，绝不放行
        return "review"

    @staticmethod
    def _m383_family_triage_table() -> dict:
        """★383刀5：三级各自的**具体族名**（从风险表派生，供诊断与交付物）。

        票面要求「每级列具体族名」—— 这里列的是**在册真实族**，不是示意。
        ★SAFE 只有 1 个族：`_M280_RISK_BY_FAMILY` 里只有 1 个「低」族。
          要凑出第 2 个必须把某个「中」族降到「低」，那是安全面放宽，
          不在本刀范围，也**不该为了"每级≥2"去凑数**。
        """
        _by_rank = {1: "SAFE", 2: "REVIEW", 3: "BLOCKED"}
        out = {"SAFE": [], "REVIEW": [], "BLOCKED": []}
        for _fam, _risk in PatchManager._M280_RISK_BY_FAMILY.items():
            _lv = _by_rank.get(PatchManager._m346_risk_rank(_risk))
            if _lv:
                out[_lv].append(str(_fam))
        for _vals in out.values():
            _vals.sort()
        out["UNREGISTERED"] = "REVIEW"
        out["DEFAULT_FAMILY_RISK"] = PatchManager._M372_DEFAULT_FAMILY_RISK
        out["SOURCE"] = "_M280_RISK_BY_FAMILY"
        return out

    #: 风险档位权重（★344刀3）。中英双写是因为历史数据里两种都有
    #: （`select_for_auto_triage` 至今仍写 `in ("低", "low")`）。
    # ★421：`ClassVar` 把"这是**类级只读常量**、全实例共享"写进类型。
    #   原写法只有裸赋值，ruff 判不出意图故报 RUF012。
    _M344_RISK_RANK: ClassVar[dict] = {"低": 1, "low": 1, "中": 2, "medium": 2, "中危": 2,
                       "高": 3, "high": 3}
    # ★421：`ClassVar` 把"这是**类级只读常量**、全实例共享"写进类型。
    #   原写法只有裸赋值，ruff 判不出意图故报 RUF012。
    _M344_RANK_NAME: ClassVar[dict] = {1: "低", 2: "中", 3: "高"}

    @staticmethod
    def _m346_risk_rank(risk_level) -> int | None:
        """风险档位 → 权重（1低/2中/3高）；
        **不认识返回 None**（不猜）。

        ★346刀 5：下游**一律走这里**，
        禁止再写 `!= "低"` 这类中文字面比较。

        为什么：344 刀入已建 `_M344_RISK_RANK`（中英双写），
        但当时下游三处（`_m85_local_low_risk_auto_apply` /
        `_m101_low_risk_release_path` / `_m94_aging_eligible`）**仍在比中文字面**，
        于是英文 `low` 被当成"非低"——**保守，但计数被低估**
        （低风险自动应用面实测 = 0，而台账里
        `risk_level="low"` 的条目并不少）。

        保守边界：取不到或不认识 ⇒ 返 None
        ⇒ 调用方必须当"非低风险"处理（与改前行为一致）。
        """
        _raw = str(risk_level if risk_level is not None else "").strip()
        if not _raw:
            return None
        _rank = PatchManager._M344_RISK_RANK.get(_raw.lower())
        if _rank is None:
            _rank = PatchManager._M344_RISK_RANK.get(_raw)
        return int(_rank) if _rank is not None else None

    @staticmethod
    def _m406_narrow_gate(patch: dict) -> tuple:
        """★406刀4：免签通道的**最后一道 diff 窄闸**（只收窄、不放宽）。

        为什么需要：`_m85` / `_m101` 两条通道都只看 trust / risk_level / issue_type，
        **从不看"这次改动到底动了什么"** —— 实测 pending 里 8 条补丁全部走到
        `runtime_verified + verified=True` 却 `applied=False`，而它们动的都是逻辑行。
        任务书对 low 的定义是「只改注释/文档/测试/配置默认值 False」⇒ 本闸把这句变成代码。

        Returns:
            `(allowed, why)`。判据实现在 `nucleus/evolution/patch_risk_gate.gate_allows`：
              · `PATCH_M406_NARROW_GATE=False` ⇒ `(True, "gate_off")`（完全回退 406 前行为）
              · 否则只有 diff∈{comment_only,doc_only,test_only} ∧ 单测全绿 ∧ 不跨器官
                ∧ 不碰审批池 才 `(True, "low:<kind>")`，其余一律 `(False, ...)`

        ★异常 → `(False, "gate_error")`：本闸是"前置"约束，闸自己坏了不该放行
          （与 `_m101` except 的保守口径一致），且经 silent_exc 留痕。
        """
        try:
            from nucleus.evolution.patch_risk_gate import gate_allows
            return gate_allows(patch)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m406_narrow_gate", level="debug")
            return False, "gate_error"

    @staticmethod
    def _m346_is_low_risk(risk_level) -> bool:
        """是否"低风险"档（rank == 1）。
        只有明确判定为低才返 True。"""
        return PatchManager._m346_risk_rank(risk_level) == 1

    @staticmethod
    def _m344_merge_risk(explicit, issue_type: str) -> tuple:
        """★344批刀3：入队口风险的**唯一裁决口** —— 显式声明优先，但只许升不许降。

        病根（343 交付 §9.2 + 纯 HEAD 连跑 3 次稳定 FAIL）：
        `_m280_risk_by_issue_type` 在入队口**无条件覆盖** `risk_level`，
        于是测试/人工显式声明的"高"被重定成"低"，直传 T-101a 放行判据。

        但**不能简单改成"显式就是一切"**：280刀2 立这条覆盖，正是为了拦住
        "LLM 自评把 sql_injection 报成低风险"（烛微279：48 条全低，其中 8 条
        高危被误判）。所以裁决规则是取**档位较高者**：

            final = max(rank(显式), rank(按 issue_type 推断))

          · 显式 高 + issue_type 默认低 ⇒ **高**（本刀要修的场景，安全面**更严**）
          · 显式 低 + issue_type=sql_injection ⇒ **高**（280刀2 原意，保持不变）
          · 无显式 ⇒ 按 issue_type 推断（原行为）

        Returns:
            `(final_level, basis)`，`basis ∈ {"explicit","issue_type",
            "escalated_by_issue_type"}`
        """
        _derived = PatchManager._m280_risk_by_issue_type(issue_type)
        _d_rank = PatchManager._M344_RISK_RANK.get(str(_derived), 1)
        _ex = str(explicit or "").strip()
        if not _ex:
            return _derived, "issue_type"
        _e_rank = PatchManager._M344_RISK_RANK.get(_ex)
        if _e_rank is None:
            # 显式值不在已知档位（拼错/新词）⇒ 不猜：按 issue_type 处理并留痕，
            # 否则"写了个错词"会变成"静默放行"。
            return _derived, "issue_type"
        if _e_rank >= _d_rank:
            # ★344批刀3附：**归一化**。原实现返回 `_ex` 原文，于是 "high"/"low"
            #   这类英文（或别名）档位会原样写进 patch —— 而下游有三处只认中文
            #   （`PatchManager.py:590/624/912` 用 `!= "低"`）。英文 "low" 在那里
            #   会被判成"非低" ⇒ 低风险补丁永不放行（更严但不合语义、不可预测）。
            #   归一到中文后 `risk_level` 只有一种形态；原值另存 `risk_level_raw`。
            return PatchManager._M344_RANK_NAME[_e_rank], "explicit"
        return PatchManager._M344_RANK_NAME[_d_rank], "escalated_by_issue_type"

    @staticmethod
    def _m280_trust_by_issue_type(issue_type: str) -> float:
        # ★280批刀4：trust_score 改为按 issue_type 有信息量（替代恒70零信息量）。
        #   高危族低信任、低危族高信任，使 PATCH_AUTO_APPROVE_TRUST_THRESHOLD 恢复效力。
        # ★372刀1：表移到 `_M280_TRUST_BY_FAMILY`；兜底值 **40.0 保持不变**
        #   （理由与残留风险见 `_M372_DEFAULT_FAMILY_TRUST`）。
        return PatchManager._M280_TRUST_BY_FAMILY.get(
            str(issue_type or ""), PatchManager._M372_DEFAULT_FAMILY_TRUST)

    @staticmethod
    def _m372_family_registered(issue_type: str) -> bool:
        """★372刀1：该族是否**已登记**（risk 表或 trust 表有其一）。纯函数。

        为什么用"或"不用"与"：两表覆盖面本就不同（risk 11 族 / trust 7 族），
        任一表认识它即"族名已知、有据可依"；两表都无条目才是"族未登记"，
        此时 fail-closed 至 `_M372_DEFAULT_FAMILY_RISK`。

        边界：空串 / None / 纯空白 一律判未登记（**不猜**，与 344 刀3
        "显式值不认识就按族推断"同一取向：不认识的输入不许变成放行）。
        纯函数，不读盘、不打日志、无 except。
        """
        _fam = str(issue_type or "").strip()
        if not _fam:
            return False
        return (_fam in PatchManager._M280_RISK_BY_FAMILY
                or _fam in PatchManager._M280_TRUST_BY_FAMILY)

    @staticmethod
    def _m338_build_evidence(patch: dict) -> dict:
        """★338批刀4：入队时生成**证据三要素**（risk/trust/blast 各带 basis+source）。

        为何不在这里重写一套规则：生成侧与派单侧若各算各的，必然再现
        “生成侧填了、派单侧不认”（与 300刀1 / 298刀1 同型教训）。所以直接委派
        `patch_execution_dispatch._m338_derive_evidence`（**唯一取数口**）。

        ★入队时机很关键：此时 `risk_level` / `trust_score` 已经过 _m280 重定级，
        证据里记下的就是**真正入队的那个值**，而不是 LLM 刚吐出的原始自评。
        """
        try:
            from nucleus.evolution.patch_execution_dispatch import (
                _m338_derive_evidence as _m338_dev,
            )

            return _m338_dev(patch)
        except Exception as _e:
            silent_exc(
                _e,
                where="nucleus.reasoning.PatchManager::_m338_build_evidence 取数口回退", level="debug")
            # 取数口不可用时**不伪造**：显式标 derive_error，
            # 派单侧证据门会按“证据缺”拒入（宁可转人工也不猜）。
            return {"evidence_version": "338.d4.v1",
                    "risk": None, "trust": None, "blast": None,
                    "derive_error": f"{type(_e).__name__}: {_e}"}

    # ★343批刀1（E-12 · P0）：O3 判死候选豁免 284 刀5 的 def 校验。
    #   病根（星轨 12:51 现场 + 12:45 断点定位）：`approve_o3_patch` 人工 approve
    #   返回 ok=True（内存里 status/o3_auto_dispatch_ready 都置好了），但**落盘**
    #   要经 `_save_patch_list` → `_m280_converge_pending`；284 刀5 的判据是
    #   「modified_code 里没有 def ⇒ 结构缺陷 ⇒ 作废」。而 O3 判死候选的动作是
    #   **删除**——天然没有 modified_code、也没有要替换的 def。
    #   ⇒ 用同一把尺子量两种动作，O3 必然 100% 被判"结构缺陷"过滤出 pending，
    #     派单侧（dispatch_o3_approved 读 pending）永远看不到它 ⇒ 审批链走完，
    #     派单/执行/applied 全 0（7 天 0 派单的直接原因之一）。
    #   豁免边界（**只放松这一种情形，别的照旧**）：
    #     · 只豁免「方法名有、def 提取不到」这一个分支（=无 modified_code 的常态）；
    #     · 「def 名与 method 错配」**不豁免** —— 那是真结构缺陷，与判死动作无关；
    #     · 判据只看显式字段（`o3_dead_code` / `source` / `issue_type`），
    #       不从理由串里找关键字（铁律153）。
    @staticmethod
    def _m343_o3_def_exempt(patch) -> bool:
        """该条目是否为「O3 判死候选」⇒ 豁免 284 刀5 的 def 校验（纯函数）。"""
        return PatchManager._m339_is_o3(patch)

    # ★343批刀1②（洞鉴第二季 · P0）：自主修复通道豁免 298刀1。
    #   为什么需要**显式来源标记**：`SafeEvolutionExecutor` 提交时**不写 source 字段**
    #   （实测全库 `llm_auto_fix` 零出现）⇒ 无法从现有字段区分"自主演化自动生成"
    #   与"外部/人工提交"。故在入队唯一口补 `entry_source`（见 save_pending_patch），
    #   判据只认这个显式标记 + 白名单 source，不从理由串里找关键字（铁律153）。
    _M343_AUTO_FIX_SOURCES = ("llm_auto_fix",)
    _m343_shape_exempt_count = 0     # 豁免计数（回归指标：应只见于自主修复通道）

    #: ★447刀2：落地留痕（相对仓根）+ 进程级 apply 锁
    _M447_APPLY_TRACE_REL = os.path.join("data", "patches", "apply_trace.jsonl")
    _M447_APPLY_LOCK = threading.Lock()

    @staticmethod
    def _m447_apply_trace_path() -> str:
        """落地留痕文件绝对路径（相对路径按仓根解析；空串 ⇒ 关闭）。"""
        try:
            import config as _c447t
            _p = str(getattr(_c447t, "M447_APPLY_TRACE_PATH",
                             PatchManager._M447_APPLY_TRACE_REL) or "").strip()
            if not _p:
                return ""
            if os.path.isabs(_p):
                return _p
            return os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))), _p)
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m447_apply_trace_path", level="debug")
            return ""

    @staticmethod
    def _m447_note_apply(patch, before: str, after: str, **extra) -> bool:
        """★447刀2：**落地那一刻**把修改前/后完整内容写进 `apply_trace.jsonl`。

        任务书原文：「修改前/修改后完整内容 100% 留痕，就算改错了也能追溯」。
        所以这里**不截断**（`M447_APPLY_TRACE_MAX_CHARS=0` 缺省不截）；
        要临时收缩排障时把该配置设成正数即可（截断会置 `truncated=True`，不是静默丢）。

        ★失败绝不影响 apply：本方法内部吞掉一切异常，只返回 bool（铁律40）。
        """
        try:
            import config as _c447w
            if not bool(getattr(_c447w, "ENABLE_M447_APPLY_TRACE", True)):
                return False
            _cap = int(getattr(_c447w, "M447_APPLY_TRACE_MAX_CHARS", 0) or 0)
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m447_note_apply 读配置", level="debug")
            _cap = 0
        try:
            _p = PatchManager._m447_apply_trace_path()
            if not _p:
                return False
            # ★测试进程不许写**默认生产路径**（446 给 detector/effect 两个口加过同款护栏；
            #   本口漏了这一步，实测一次 pytest 就灌进 4 行空 patch 的假落地记录）。
            #   单测把 M447_APPLY_TRACE_PATH 指到 tmp ⇒ 照常写。
            #   ★路径比较必须 normcase+normpath：配置里写的是 `data/patches/...`
            #   （正斜杠），`_M447_APPLY_TRACE_REL` 是 `os.path.join` 出来的反斜杠，
            #   直接字符串比会永远不等 ⇒ 护栏静默失效（446 的同类坑）。
            _def_path = os.path.normcase(os.path.normpath(os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))),
                PatchManager._M447_APPLY_TRACE_REL)))
            if "pytest" in sys.modules and os.path.normcase(
                    os.path.normpath(_p)) == _def_path:
                return False
            _b = str(before if before is not None else "")
            _a = str(after if after is not None else "")
            _trunc = False
            if _cap > 0 and (len(_b) > _cap or len(_a) > _cap):
                _b, _a, _trunc = _b[:_cap], _a[:_cap], True
            import hashlib as _hl
            _row = {
                "ts": time.time(),
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "apply",
                "patch_id": str((patch or {}).get("id") or "")[:64],
                "file": str((patch or {}).get("file") or ""),
                "method": str((patch or {}).get("method") or ""),
                "issue_type": str((patch or {}).get("issue_type") or ""),
                "backup_path": str((patch or {}).get("backup_path") or ""),
                "rollback_path": str((patch or {}).get("backup_path") or ""),
                "rollback_available": (patch or {}).get("rollback_available"),
                "before_sha256": _hl.sha256(_b.encode("utf-8", "replace")).hexdigest(),
                "after_sha256": _hl.sha256(_a.encode("utf-8", "replace")).hexdigest(),
                "truncated": _trunc,
                "before_chars": len(str(before if before is not None else "")),
                "after_chars": len(str(after if after is not None else "")),
                "before_content": _b,
                "after_content": _a,
            }
            _row.update({k: v for k, v in (extra or {}).items()})
            _dir = os.path.dirname(_p)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
            return True
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m447_note_apply 写盘", level="warning")
            return False

    def apply_approved_once(self, *, reason: str = "m447_tick") -> dict[str, Any]:
        """★447刀2：带**进程级锁**的 apply 入口（`only_approved=True` 的唯一并发安全路径）。

        为什么必须有这道锁：apply 现在有三个触发口（周期 5 分钟 tick / 进程退出 finally /
        `has_apply_request()` 立即请求），三者可能同时进 —— 同一补丁被两路同时
        `str.replace` + `os.replace`，后果是「后写覆盖前写」且两边都报 applied。

        ★非阻塞语义：抢不到锁**直接返回**（`skipped_locked=True`），不排队。
        排队会让退出 finally 卡住等满一整轮 apply；跳过更安全 —— 下一拍还会再来。
        """
        _lock = PatchManager._M447_APPLY_LOCK
        if not _lock.acquire(blocking=False):
            _module_logger.info(
                "[447刀2] apply 进行中，本次并发调用直接跳过: reason=%s", reason)
            return {"applied": 0, "failed": 0, "details": [],
                    "skipped_locked": True, "apply_reason": reason}
        try:
            _r = self.apply_all_pending(only_approved=True)
            _out = dict(_r) if isinstance(_r, dict) else {
                "applied": 0, "failed": 0, "details": []}
            _out["apply_reason"] = reason
            return _out
        finally:
            _lock.release()

    #: ★446刀2：入队口必须随 patch 落 pending 的基线四字段（任务书 §刀2 第一条）
    _M446_BASELINE_FIELDS = ("baseline_errors", "baseline_source",
                             "baseline_window_sec", "baseline_shadow")

    @staticmethod
    def _m446_ensure_baseline_fields(patch) -> dict:
        """★446刀2：保证四个基线字段**在条目上存在**（缺则补 None，不覆盖已有值）。

        为什么「键存在」比「值对不对」更关键：`baseline_probe.apply_to_patch()` 有四条
        早退分支（总开关关、`file` 为空、已有基线、探测异常），它们**一个字都不写** ⇒
        条目上根本没有这个键。读的人于是无法区分：

            · 键缺失     = 这条走的是不探测的路径（或入队时 438 还没上线）
            · 值为 None  = 探测了，但判不了（窗口未覆盖 / 无检测器）

        两者在 `approval_bridge.baseline_positive` 眼里都是「不放行」，但对账时是两回事
        （见 `effect_verify_health` 的 `missing` 与 `unavailable` 之分）。
        ★只补缺失、绝不改值 —— `setdefault` 语义，零判定影响。
        """
        _filled = []
        if not isinstance(patch, dict):
            return {"filled": _filled}
        for _k in PatchManager._M446_BASELINE_FIELDS:
            if _k not in patch:
                patch[_k] = None
                _filled.append(_k)
        return {"filled": _filled}

    @staticmethod
    def _m446_carry_baseline_to_history(patch) -> dict:
        """★446刀2：应用落 `patch_history` 前把基线四字段一并带过去，并留 carry 时间戳。

        盘面事实（2026-11-04 实测）：`patch_history.json` **87 条里**
        `baseline_source` **0 条**、`baseline_window_sec` **0 条**、
        `baseline_shadow` **0 条**。不是被谁删了 —— 这些条目入队时（438 之前）
        根本没有这些键，`history.append(patch)` 自然带不出不存在的东西。
        本口保证从这一批起，每条落 history 的补丁都带四字段；
        真带得出 `baseline_source` 的打 `baseline_carried_at`，事后可数、可对账。
        """
        _out = {"filled": [], "baseline_source": None, "carried": False}
        if not isinstance(patch, dict):
            return _out
        _out["filled"] = PatchManager._m446_ensure_baseline_fields(patch)["filled"]
        _src = patch.get("baseline_source")
        _out["baseline_source"] = _src
        if _src and not patch.get("baseline_carried_at"):
            patch["baseline_carried_at"] = time.time()
            _out["carried"] = True
        return _out

    # ==================== ★448批：把八闸里"死的两闸"供上数 ====================
    #
    # 【为什么要这两块】
    # 447 把审批桥接进 repair 后实测：真实补丁过 **5/8** 闸 ——
    #   trust=30~40 < 60（`M439_APPROVE_MIN_TRUST`）
    #   problem_gone=None（八闸⑤只认显式 True）
    # 这两闸不是判据错，是**上游从来没供过数**（446 只补了 baseline_errors）。
    #
    # 【安全边界（必须先读）】
    # 抬高 trust **只影响审批桥那一道闸**（门槛 60），不影响任何自动应用面：
    #   · `EVOLUTION_CONFIG["auto_apply_min_trust"]` = **40**，改前就已达线；
    #   · `PATCH_AUTO_APPROVE_TRUST_THRESHOLD` = **40**，同上；
    #   · `_check_patch_safety` 用的也是 40。
    # 所以 40→70 不会新开任何自动应用通道（这次改动只把"审批桥能不能看一眼"打开）。
    # 真正决定能不能落地的是审批桥外面那四条安全边界（447 刀1）+ 应用侧既有闸。

    #: 448 信任加分明细（任务书 §刀1 逐条落地）
    _M448_TRUST_RULES: ClassVar[tuple] = (
        ("risk_low", 20.0),
        ("single_file", 10.0),
        ("blast_le_1", 10.0),
        ("baseline_positive", 10.0),
        ("problem_gone_true", 20.0),
    )

    @staticmethod
    def _m448_trace_path(rel: str, cfg_name: str) -> str:
        """留痕路径（相对路径按仓根解析；配置为空串 ⇒ 关闭）。"""
        try:
            import config as _c448
            _p = str(getattr(_c448, cfg_name, rel) or "").strip()
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m448_trace_path", level="debug")
            _p = rel
        if not _p:
            return ""
        if os.path.isabs(_p):
            return _p
        return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), _p)

    @staticmethod
    def _m448_note(rel: str, cfg_name: str, row: dict, *, extra_cfg: str) -> bool:
        """追加一行留痕。**失败绝不影响入队/判定**（铁律40）。

        ★测试进程 + 默认生产路径 ⇒ 不写（446/447 同款护栏；本批两个口一开始就带上，
          省得又出现"跑一遍 pytest 灌一堆假判定"）。
        """
        try:
            import config as _c448w
            if not bool(getattr(_c448w, extra_cfg, True)):
                return False
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m448_note 开关", level="debug")
        try:
            _p = PatchManager._m448_trace_path(rel, cfg_name)
            if not _p:
                return False
            _def = os.path.normcase(os.path.normpath(
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))), rel)))
            if "pytest" in sys.modules and os.path.normcase(
                    os.path.normpath(_p)) == _def:
                return False
            _dir = os.path.dirname(_p)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return True
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m448_note 写盘", level="warning")
            return False

    @staticmethod
    def _m448_calc_trust(patch) -> dict:
        """★448刀1：按证据加分的信任分（只升不降），并留痕每一项加分。

        模型（任务书 §刀1 原文）::

            risk=低        +20
            单文件         +10
            blast<=1       +10
            baseline>0     +10
            problem_gone   +20
            ---------------------
            满分 70（够 60 那道门）

        ★三个刻意的口径选择，都有理由：
          ① **不设基础分**：任务书说"加起来应该能到≥60"，那就是 70，不是 40+70。
          ② **只升不降**（`max(旧值, 新值)`）：信任分是**多个面共用的字段**
             （自动应用线 40 / 审批线 60）。若某条证据不全就把它从 40 压到 20，
             会顺手关掉本来能过的自动应用面 —— 那是本批没授权、也没评估过的副作用。
          ③ **证据不全不等于低分**：五项全不成立时新分=0，`max` 保住旧值，
             不会把"这条没证据"变成"这条更可疑"。
        """
        _out = {"before": None, "after": None, "score": 0.0, "applied": False,
                "items": [], "reason": ""}
        if not isinstance(patch, dict) or not patch:
            _out["reason"] = "patch 为空"
            return _out
        try:
            import config as _c448t
            if not bool(getattr(_c448t, "ENABLE_M448_TRUST_CALC", True)):
                _out["reason"] = "ENABLE_M448_TRUST_CALC=False"
                return _out
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m448_calc_trust 开关", level="debug")

        _old = patch.get("trust_score")
        try:
            _old_f = float(_old) if _old is not None else 0.0
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="PatchManager::_m448_calc_trust 旧值", level="debug")
            _old_f = 0.0
        _out["before"] = _old

        _files = []
        _f1 = str(patch.get("file") or "")
        if _f1:
            _files.append(_f1)
        _fl = patch.get("files")
        if isinstance(_fl, (list, tuple, set)):
            _files.extend([str(x) for x in _fl if str(x).strip()])
        _files = list(dict.fromkeys(_files))
        _blast = len(_files)
        _base = patch.get("baseline_errors")

        _facts = {
            "risk_low": (str(patch.get("risk_level") or "").strip() in ("低", "low"),
                         f"risk_level={patch.get('risk_level')!r}"),
            "single_file": (len(_files) == 1, f"文件数={len(_files)}"),
            "blast_le_1": (_blast <= 1, f"blast={_blast}"),
            "baseline_positive": (isinstance(_base, (int, float)) and _base > 0,
                                  f"baseline_errors={_base!r}"),
            "problem_gone_true": (patch.get("problem_gone") is True,
                                  f"problem_gone={patch.get('problem_gone')!r}"),
        }
        _score = 0.0
        for _name, _delta in PatchManager._M448_TRUST_RULES:
            _ok, _why = _facts.get(_name, (False, "未判"))
            _score += _delta if _ok else 0.0
            _out["items"].append({"rule": _name, "ok": bool(_ok),
                                  "delta": _delta if _ok else 0.0, "why": _why})
        _score = max(0.0, min(100.0, _score))
        _out["score"] = _score
        _new = max(_old_f, _score)
        if _new > _old_f or _old is None:
            patch["trust_score"] = round(_new, 1)
            patch["trust_calc_score"] = round(_score, 1)
            patch["trust_calc_at"] = time.time()
            _out["applied"] = True
        _out["after"] = patch.get("trust_score")
        _out["reason"] = f"按证据加分 {_score} → 取 max(旧,新)"
        PatchManager._m448_note(
            os.path.join("data", "patches", "trust_calc_trace.jsonl"),
            "M448_TRUST_TRACE_PATH",
            {"ts": time.time(),
             "at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "kind": "trust_calc",
             "patch_id": str(patch.get("id") or "")[:64],
             "file": str(patch.get("file") or ""),
             "method": str(patch.get("method") or ""),
             "issue_type": str(patch.get("issue_type") or ""),
             "before": _out["before"],
             "after": _out["after"],
             "score": _score,
             "applied": bool(_out["applied"]),
             "items": _out["items"]},
            extra_cfg="ENABLE_M448_TRUST_CALC")
        return _out

    def _m448_verdict_for(self, patch_id: str) -> dict:
        """从 `data/evolution/effect_verify.json` 取该补丁的判定（取不到 → 空 dict）。"""
        _pid = str(patch_id or "").strip()
        if not _pid:
            return {}
        _root = str(getattr(self, "_project_root", "") or "")
        _p = os.path.join(_root, "data", "evolution", "effect_verify.json")
        try:
            with open(_p, "r", encoding="utf-8") as _f:
                _doc = json.load(_f)
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="PatchManager::_m448_verdict_for", level="debug")
            return {}
        _v = (_doc.get("verdicts") or {}).get(_pid) if isinstance(_doc, dict) else None
        return _v if isinstance(_v, dict) else {}

    def _m448_sync_problem_gone(self, patch) -> dict:
        """★448刀2：把 `problem_gone` 带到补丁**顶层**，并留痕判定来源与理由。

        流转事实（T0 实测，不是推测）：
          · `EvolutionEffectVerifier.verify()` 只把判定写进
            `effect_verify.json → verdicts[id].dims.problem_gone`，**从不写补丁顶层**；
          · 而 `verify()` 是**应用之后**才跑的；`approval_bridge` 是**应用之前**判的
            ⇒ 对一条新 pending 件，`verdicts` 里根本没有它的记录
            （实测 43 条 verdict 与 pending 的**交集 = 0**）。
          所以「审批前从台账同步」对**新件**是空操作。

        本函数的取数顺序（先强后弱，各记来源）：
          ① 顶层已有显式布尔 ⇒ 不覆盖（人/上游已经判过）；
          ② `effect_verify` 台账里有该 id 的 `dims.problem_gone` ⇒ 用它（重新入队的件）；
          ③ **静态复现**：`active_reprobe(original_code, modified_code)` 的结论按
             `_M375_PROBLEM_GONE_MAP` 映射（`true_pass` ⇒ True）。
             ★这是 375刀4 已经采信的同一条映射，不是新口径；差别只在"应用前先算"。
             语义要说清：它是「**修复后的代码里静态检测不到该缺陷**」，
             不是「运行时问题消失了」。八闸⑤要的正是前者这种"显式为真的证据"。

        Returns:
            ``{"changed", "problem_gone", "source", "reason", "verdict"}``
        """
        _out = {"changed": False, "problem_gone": None, "source": "", "reason": "",
                "verdict": ""}
        if not isinstance(patch, dict) or not patch:
            _out["reason"] = "patch 为空"
            return _out
        try:
            import config as _c448p
            if not bool(getattr(_c448p, "ENABLE_M448_PROBLEM_GONE_SYNC", True)):
                _out["reason"] = "ENABLE_M448_PROBLEM_GONE_SYNC=False"
                return _out
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m448_sync_problem_gone 开关", level="debug")

        _cur = patch.get("problem_gone")
        if _cur is True or _cur is False:
            _out.update(problem_gone=_cur, source="existing",
                        reason="顶层已有显式布尔，不覆盖")
            return _out

        _pg, _src, _why, _verd = None, "", "", ""
        _v = self._m448_verdict_for(patch.get("id"))
        if _v:
            _dims = _v.get("dims") or {}
            _vp = _dims.get("problem_gone")
            if _vp is True or _vp is False:
                _pg, _src = _vp, "effect_verify"
                _why = (f"台账判定 problem_gone={_vp!r}"
                        f"（{str(_v.get('reason') or '')[:120]}）")
                _verd = str(_v.get("passed"))

        if _pg is None and not _src:
            try:
                from nucleus.evolution.EvolutionEffectVerifier import (
                    EvolutionEffectVerifier as _EV448,
                )
                from nucleus.evolution.patch_active_reprobe import (
                    active_reprobe as _m448_reprobe,
                )
                _r = _m448_reprobe(patch)
                _verd = str(_r.get("reprobe_verdict") or "")
                _pg, _why = _EV448.problem_gone_from_reprobe(_r)
                _src = "reprobe"
                patch["reprobe_verdict"] = _verd or patch.get("reprobe_verdict")
                patch["reprobe_baseline_hits"] = _r.get("reprobe_baseline_hits")
            except Exception as _e:
                silent_exc(_e, where="PatchManager::_m448_sync_problem_gone 复现",
                           level="warning")
                _why = f"{type(_e).__name__}: {_e}"
                _src = "error"

        if _pg is True or _pg is False:
            patch["problem_gone"] = _pg
            patch["problem_gone_source"] = _src
            patch["problem_gone_at"] = time.time()
            _out["changed"] = True
        _out.update(problem_gone=_pg, source=_src, reason=_why, verdict=_verd)

        PatchManager._m448_note(
            os.path.join("data", "patches", "problem_gone_trace.jsonl"),
            "M448_PROBLEM_GONE_TRACE_PATH",
            {"ts": time.time(),
             "at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "kind": "problem_gone",
             "patch_id": str(patch.get("id") or "")[:64],
             "file": str(patch.get("file") or ""),
             "method": str(patch.get("method") or ""),
             "issue_type": str(patch.get("issue_type") or ""),
             "problem_gone": _pg,
             "source": _src,
             "reprobe_verdict": _verd,
             "baseline_errors": patch.get("baseline_errors"),
             "reason": str(_why or "")[:300]},
            extra_cfg="ENABLE_M448_PROBLEM_GONE_SYNC")
        return _out

    @staticmethod
    def _m343_auto_fix_exempt(patch) -> bool:
        """该条目是否来自自主修复通道（豁免 298刀1 形态拒绝）。"""
        if not isinstance(patch, dict):
            return False
        if str(patch.get("entry_source") or "") == "llm_auto_fix":
            return True
        return str(patch.get("source") or "") in PatchManager._M343_AUTO_FIX_SOURCES

    @staticmethod
    def _m445_full_def_need() -> int:
        """连续多少次 no_def 后要求生成器输出**完整方法**（默认 2）。"""
        try:
            import config as _c445b
            return max(1, int(getattr(_c445b, "M445_REQUIRE_FULL_DEF_STREAK", 2)))
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m445_full_def_need", level="debug")
            return 2

    def _m280_converge_pending(self, data):
        # ★280批刀4：pending 队列落盘前状态机收敛。
        #   ① applied=True 即出队（不再留在 pending）；
        #   ② 删顶层 runtime_verified（留嵌套 runtime_verify_result.verified 为唯一真值）。
        # ★284批刀5：③ 结构缺陷补丁批量作废（def名!=method 错配 或 modified_code 无 def），
        #   回生成侧重新生成（存量 29 条不再污染队列类指标）。
        if not isinstance(data, list):
            return data
        _out = []
        _voided = 0
        _exempt = 0          # ★343刀1：判死候选豁免计数（可 grep/[343O3]）
        for _it in data:
            if not isinstance(_it, dict):
                _out.append(_it)
                continue
            if _it.get("applied"):
                _module_logger.info(
                    "[280刀4] applied补丁出队: %s:%s",
                    _it.get("file", ""), _it.get("method", ""))
                continue
            # ★284批刀5：结构缺陷补丁作废（错配 def!=method / 无 def）
            _def_name = PatchManager._m280_extract_def_name(
                _it.get("modified_code", "") or "")
            _method_name = str(_it.get("method", "") or "").strip()
            _mismatch = bool(_method_name and _def_name and _def_name != _method_name)
            _no_def = bool(_method_name and not _def_name)
            if _mismatch or _no_def:
                # ★343刀1：判死候选（删除动作）无 def 是**常态**，不是结构缺陷。
                if _no_def and not _mismatch and PatchManager._m343_o3_def_exempt(_it):
                    _it["_m343_def_exempt"] = True
                    _exempt += 1
                    _module_logger.info(
                        "[343O3] 判死候选豁免284刀5: %s file=%s method=%s"
                        "（删除动作无 modified_code 属常态，不作废）",
                        str(_it.get("id") or "")[:32], _it.get("file", ""),
                        _method_name)
                    _out.append(_it)
                    continue
                _module_logger.warning(
                    "[284刀5] 结构缺陷补丁作废: %s:%s "
                    "(def=%r method=%r 错配=%s 无def=%s)",
                    _it.get("file", ""), _method_name, _def_name, _method_name,
                    _mismatch, _no_def)
                # ★344批刀2：作废**必须落结构化台账**。原因（实测）：
                #   作废此前只进 pulse.log，而 pulse.log 是 10MB×5 滚动文件，
                #   336 期（10-21 13:00→10-22 01:08）的日志已轮转消失 ⇒
                #   即便补上"从日志数作废"的采集器，历史 12 圈的 B 段也永远
                #   补不回来。台账是 append-only 的，滚不掉。
                #   写失败不影响作废主流程（作废照常发生，只留 warning）。
                try:
                    from nucleus.evolution import criteria_window as _cw
                    # ★375刀9：作废条目从 pending 池**直接消失**（不写 status、
                    #   不进 history）⇒ 当日入池计数会**回退**，1/天 限额可被
                    #   「生成→作废→再生成」绕开。台账必须自带入池标记与**入池
                    #   时间**，`_m333_intake_rate` 才能按同日口径把它补算回来
                    #   （缺这两个字段，第三轨就只能瞎猜或虚增）。
                    _cw.note_converge_voided(
                        _it.get("id"), file=str(_it.get("file") or ""),
                        method=_method_name, reason="284刀5 结构缺陷补丁作废",
                        def_name=str(_def_name or ""),
                        mismatch=bool(_mismatch), no_def=bool(_no_def),
                        issue_type=str(_it.get("issue_type") or ""),
                        o3_dead_code=bool(_it.get("o3_dead_code") is True),
                        generated_at=_it.get("generated_at") or 0)
                except Exception as _cw_e:
                    silent_exc(_cw_e, where="PatchManager::_m280 344作废台账",
                               level="warning")
                # ★445刀2：把「因缺 def 被作废」反馈给生成侧（回灌）。
                #   只在 `no_def`（不是 def 名错配）时反馈 —— 错配是生成器写错了名字，
                #   换模板解决不了。连续次数写进台账，生成侧据此决定是否换模板。
                if _no_def and not _mismatch:
                    try:
                        from nucleus.evolution import criteria_window as _cw2
                        _st445 = _cw2.no_def_streak(
                            str(_it.get("file") or ""), _method_name)
                        _streak445 = int(_st445.get("streak") or 0) + 1
                        _strat445 = ("require_full_def"
                                     if _streak445 >= self._m445_full_def_need() else "unchanged")
                        _cw2.note_generation_feedback(
                            file=str(_it.get("file") or ""), method=_method_name,
                            reason="no_def", streak=_streak445, strategy=_strat445,
                            patch_id=str(_it.get("id") or "")[:64])
                        _module_logger.info(
                            "[445刀2] 生成侧反馈: %s::%s 连续no_def=%d 策略=%s",
                            _it.get("file", ""), _method_name, _streak445, _strat445)
                    except Exception as _fb_e:
                        silent_exc(_fb_e, where="PatchManager::_m280 445反馈回灌",
                                   level="warning")
                _voided += 1
                continue
            _it.pop("runtime_verified", None)
            _out.append(_it)
        if _voided:
            _module_logger.info(
                "[284刀5] 结构缺陷补丁批量作废: %d 条（回生成侧重新生成）", _voided)
        if _exempt:
            _module_logger.info(
                "[343O3] 判死候选豁免284刀5 汇总: %d 条（保留在 pending，"
                "供 dispatch_o3_approved 派单）", _exempt)
        return _out

    # ===== ★323批 刀2（P-03）：生成侧真值 =====
    # 烛微323：补丁的 def 名与 method 字段是**两把尺子**——method 取"发现时所在方法"，
    # def 取"生成文本首行解析"，两源分叉 ⇒ PatchManager 入队口拒收 25 条。
    # 根治：入队（唯一汇聚点）前以 **AST enclosing def** 为唯一真值回填 method，
    # 并统一 re-indent（F-4 IndentationError 12 条同源：片段带原缩进）。
    _m323_def_mismatch_rejected = 0     # AST 真值仍对不上 ⇒ 真异常，拒收并计数（回归指标）
    _m323_normalized = 0                # 回填/修正过 method 的补丁数
    _m323_reindented = 0                # 做过 re-indent 的补丁数

    @staticmethod
    def _m323_reindent(code: str) -> tuple:
        """剥掉生成片段的前导公共缩进（不碰相对层级）；返回 (新文本, 是否改动)。

        只做「整体左移」这一件事：片段被整块缩进时，写入文件会导致
        IndentationError；左移公共前缀既修问题又不改语义。
        """
        if not code:
            return code, False
        _lines = code.replace("\r\n", "\n").split("\n")
        _widths = []
        for _ln in _lines:
            _s = _ln.strip()
            # ★323批刀2修正：纯注释行不参与公共缩进计算——生成片段常以
            #   0 缩进注释打头（"# Make sure..."），否则 cut 恒为 0、re-indent 空转。
            if not _s or _s.startswith("#"):
                continue
            _w = len(_ln) - len(_ln.lstrip(" \t"))
            _widths.append(_w)
        if not _widths:
            return code, False
        _cut = min(_widths)
        if _cut <= 0:
            return code, False
        _out = []
        for _ln in _lines:
            _s = _ln.strip()
            if not _s:
                _out.append("")
                continue
            _w = len(_ln) - len(_ln.lstrip(" \t"))
            if _s.startswith("#") and _w < _cut:
                _out.append(_ln)          # 注释行缩进不足 ⇒ 原样保留（顶层注释合法）
                continue
            _out.append(_ln[_cut:] if len(_ln) >= _cut else _ln.lstrip(" \t"))
        return "\n".join(_out), True

    @staticmethod
    def _m323_ast_def_name(code: str) -> str:
        """取补丁文本里**第一个 def/async def** 的名字（AST，不靠文本正则）。

        优先顶层 def；顶层没有则取深层第一个（片段是方法体时可能出现内部函数）。
        """
        if not (code or "").strip():
            return ""
        try:
            _tree = ast.parse(code)
        except (SyntaxError, ValueError) as _e:
            silent_exc(_e, where="nucleus.reasoning.PatchManager::_m323_ast_def_name 首解析",
                       level="warning")
            _fixed, _chg = PatchManager._m323_reindent(code)
            if not _chg:
                return ""
            try:
                _tree = ast.parse(_fixed)
            except (SyntaxError, ValueError) as _e2:
                silent_exc(_e2, where="nucleus.reasoning.PatchManager::_m323_ast_def_name 重试解析",
                           level="warning")
                return ""
        for _n in _tree.body:
            if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return _n.name
        for _n in ast.walk(_tree):
            if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return _n.name
        return ""

    #: ★325批刀1：生成侧被判"结构性无法通过沙箱"的补丁计数（只增）
    _m325_generated_flagged = 0

    @staticmethod
    def get_m325_danger_metrics(history_path: str | None = None) -> dict:
        """★325批刀1 / ★326批刀6：生成侧危险关键字计数（只读）。

        ★326 为什么扩：原实现只有**进程内**计数器 `_m325_generated_flagged`，
        子进程/重启一律读 0 —— 与 317 批 `runtime_metrics` 同一个坑（"读数为 0"与
        "没接通"不可区分）。故补**持久读**：现扫生产台账 84 条，按 class 分档计数，
        进程内计数保留为 `generated_flagged_dangerous`（含义不变，只加不改成）。
        """
        _out = {"generated_flagged_dangerous": int(PatchManager._m325_generated_flagged),
                "process_scoped": True, "source": "unavailable"}
        try:
            import json as _json

            from nucleus.evolution.patch_execution_dispatch import _history_path as _hp
            from nucleus.evolution.patch_execution_dispatch import is_sandbox_incompatible, scan_dangerous_keywords
            _p = history_path or _hp()
            _out["source"] = _p
            with open(_p, encoding="utf-8") as _hf:
                _hist = _json.load(_hf)
            if isinstance(_hist, dict):
                _hist = _hist.get("patches") or []
            _cls: dict = {}
            _incompat = 0
            for _it in (_hist or []):
                if not isinstance(_it, dict):
                    continue
                _d = scan_dangerous_keywords(_it.get("modified_code")
                                             or _it.get("original_code") or "")
                _k = str(_d.get("class") or "none")
                _cls[_k] = int(_cls.get(_k, 0)) + 1
                if _it.get("sandbox_incompatible") is True or is_sandbox_incompatible(_it):
                    _incompat += 1
            _out.update({"records": len(_hist or []), "class_dist": _cls,
                         "real_class_count": int(_cls.get("real", 0)),
                         "idiom_class_count": int(_cls.get("idiom", 0)),
                         "literal_class_count": int(_cls.get("literal", 0)),
                         "unparsed_class_count": int(_cls.get("unparsed", 0)),
                         "sandbox_incompatible_count": int(_incompat)})
        except Exception as _e:
            silent_exc(_e, where="PatchManager::get_m325_danger_metrics 持久读",
                       level="warning")
            _out["error"] = type(_e).__name__
        return _out

    # ===== ★445刀1：片段 → 完整方法 包裹器（影子优先） =====
    #: 影子统计（读 `get_m445_wrap_metrics()`；进程内累计，重启归零）
    _m445_wrap_ok = 0
    _m445_wrap_fail = 0
    _m445_wrap_skipped = 0

    @staticmethod
    def _m445_wrap_mode() -> str:
        """包裹模式：`off` / `shadow`（默认，只留痕不改）/ `apply`（真改）。"""
        try:
            import config as _c445
            _m = str(getattr(_c445, "M445_WRAP_FRAGMENT_MODE", "shadow") or "").strip().lower()
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m445_wrap_mode", level="debug")
            return "shadow"
        return _m if _m in ("off", "shadow", "apply") else "shadow"

    @staticmethod
    def _m445_trace_path() -> str:
        """留痕落盘路径（测试可注入 `M445_WRAP_TRACE_PATH`）。"""
        try:
            import config as _c445
            _rel = str(getattr(_c445, "M445_WRAP_TRACE_PATH",
                               "data/patches/wrap_trace.jsonl") or "")
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m445_trace_path", level="debug")
            _rel = "data/patches/wrap_trace.jsonl"
        if os.path.isabs(_rel):
            return _rel
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        return os.path.join(_root, _rel)

    @staticmethod
    def _m445_find_signature(file_rel: str, method: str) -> dict:
        """从目标文件 AST 取 `def <method>(...)` 的真实签名。

        Returns:
            ``{"ok", "prefix", "args", "indent", "reason"}``
            · `prefix`：`def` 或 `async def`
            · `args`  ：`self, x: int = 0, **kw`（`ast.unparse` 出来的原文）
            · `indent`：该方法在文件里的缩进列（类方法 4，模块级 0）
        """
        _out = {"ok": False, "prefix": "def", "args": "", "indent": 4, "reason": ""}
        if not file_rel or not method:
            _out["reason"] = "缺 file/method"
            return _out
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        _p = file_rel if os.path.isabs(file_rel) else os.path.join(
            _root, str(file_rel).replace("\\", "/"))
        if not os.path.isfile(_p):
            _out["reason"] = f"目标文件不存在: {file_rel}"
            return _out
        try:
            with open(_p, encoding="utf-8", errors="replace") as _f:
                _src = _f.read()
            _tree = ast.parse(_src)
        except (OSError, SyntaxError, ValueError) as _e:
            silent_exc(_e, where="PatchManager::_m445_find_signature parse", level="debug")
            _out["reason"] = f"解析失败: {type(_e).__name__}: {_e}"
            return _out
        for _node in ast.walk(_tree):
            if not isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _node.name != method:
                continue
            try:
                _args = ast.unparse(_node.args)
            except Exception as _e:
                silent_exc(_e, where="PatchManager::_m445_find_signature unparse",
                           level="debug")
                _args = "self"
            _out.update({
                "ok": True,
                "prefix": ("async def" if isinstance(_node, ast.AsyncFunctionDef) else "def"),
                "args": _args,
                "indent": int(getattr(_node, "col_offset", 4) or 0),
                "reason": "",
            })
            return _out
        _out["reason"] = f"目标方法不存在: {method}"
        return _out

    @staticmethod
    def _m445_wrap_fragment(patch: dict) -> dict:
        """把「只有方法体」的 `modified_code` 包成完整方法（★445刀1）。

        ★为什么要做：LLM 常只给方法体（无 `def`），验证侧用"虚拟函数包装"解析说通过，
        而入队侧（284 刀5）要求能解析出 `def` ⇒ 两条尺子不一致，今天 10 条全被判结构缺陷。

        ★安全边界：
          · 只在 `modified_code` **确实没有 def** 时动手（已有 def 一律不碰）；
          · 包完必须 `ast.parse` 通过才可能写回；失败保持原样（仍交 284 判），绝不强行包裹；
          · 默认 **shadow**：只留痕、不改 `modified_code`（先跑 24h 看成功率）。

        Returns:
            ``{"status", "parse_ok", "reason", "wrapped", "signature"}``
        """
        _t0 = time.perf_counter()
        _mode = PatchManager._m445_wrap_mode()
        _out = {"status": "skipped", "parse_ok": None, "reason": "",
                "wrapped": "", "signature": None, "mode": _mode}
        if _mode == "off":
            PatchManager._m445_wrap_skipped += 1
            _out["reason"] = "模式 off"
            return _out
        _code = str(patch.get("modified_code") or "")
        _file = str(patch.get("file") or "")
        _method = str(patch.get("method") or "").strip()
        if not _code.strip() or not _method:
            PatchManager._m445_wrap_skipped += 1
            _out["reason"] = "缺 modified_code / method"
            return _out
        if PatchManager._m280_extract_def_name(_code):
            PatchManager._m445_wrap_skipped += 1
            _out["status"] = "already_full"
            _out["reason"] = "已有 def 签名，不需要包裹"
            return _out
        _sig = PatchManager._m445_find_signature(_file, _method)
        _out["signature"] = _sig
        if not _sig.get("ok"):
            PatchManager._m445_wrap_fail += 1
            _out["status"] = "no_signature"
            _out["parse_ok"] = False
            _out["reason"] = _sig.get("reason") or "取不到签名"
            _out["elapsed_ms"] = round((time.perf_counter() - _t0) * 1000.0, 3)
            PatchManager._m445_write_trace(patch, _out, _code, "")
            return _out
        # 先统一左移（片段常带原始缩进），再按「方法体缩进 = def 缩进 + 4」包
        _body, _ = PatchManager._m323_reindent(_code)
        _indent = " " * int(_sig.get("indent") or 0)
        _prefix = _sig.get("prefix") or "def"
        _args = _sig.get("args") or "self"
        _indented = textwrap.indent(_body.rstrip() + "\n", _indent + "    ")
        _wrapped = f"{_indent}{_prefix} {_method}({_args}):\n{_indented}"
        _parse_ok = True
        # ★445刀1 修正：类方法的 `def` 本身带 4 空格缩进，**单独 parse 必然
        #   "unexpected indent"**（这不是包裹错，是校验方式错）。缩进 >0 时套一层
        #   类壳再 parse —— 壳只用于校验，绝不写进补丁。
        _check = _wrapped
        if int(_sig.get("indent") or 0) != 0:
            _check = "class _M445WrapShim:" + chr(10) + _wrapped
        try:
            ast.parse(_check)
        except (SyntaxError, ValueError) as _e:
            silent_exc(_e, where="PatchManager::_m445_wrap_fragment ast", level="debug")
            _parse_ok = False
            _out["reason"] = f"包裹后 AST 校验失败: {type(_e).__name__}: {_e}"
        _out["parse_ok"] = _parse_ok
        _out["wrapped"] = _wrapped
        _out["elapsed_ms"] = round((time.perf_counter() - _t0) * 1000.0, 3)
        if _parse_ok:
            PatchManager._m445_wrap_ok += 1
            _out["status"] = "wrapped"
            if _mode == "apply":
                patch["modified_code"] = _wrapped
                _out["applied"] = True
            else:
                _out["applied"] = False
        else:
            PatchManager._m445_wrap_fail += 1
            _out["status"] = "parse_failed"
        PatchManager._m445_write_trace(patch, _out, _code, _wrapped)
        return _out

    @staticmethod
    def _m445_write_trace(patch: dict, out: dict, before: str, after: str) -> None:
        """把一次包裹尝试写入 `wrap_trace.jsonl`（**修改前/后全文**都在，可追溯）。

        写失败只留 warning，绝不影响入队主流程。
        """
        _rec = {
            "ts": time.time(),
            "patch_id": str((patch or {}).get("id") or "")[:64],
            "file": str((patch or {}).get("file") or ""),
            "method": str((patch or {}).get("method") or ""),
            "mode": out.get("mode"),
            "status": out.get("status"),
            "parse_ok": out.get("parse_ok"),
            "applied": bool(out.get("applied")),
            "reason": str(out.get("reason") or "")[:300],
            "elapsed_ms": out.get("elapsed_ms"),
            "signature": out.get("signature"),
            # ★留痕硬要求：修改前 / 修改后都是**全文**，不看长度截断
            "modified_code_before": before or "",
            "modified_code_after": after or "",
        }
        try:
            _p = PatchManager._m445_trace_path()
            _d = os.path.dirname(os.path.abspath(_p))
            if _d and not os.path.isdir(_d):
                os.makedirs(_d, exist_ok=True)
            with open(_p, "a", encoding="utf-8", newline="\n") as _f:
                _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m445_write_trace", level="warning")

    @staticmethod
    def get_m445_wrap_metrics() -> dict:
        """包裹器进程内指标（ok / fail / skipped）。"""
        return {"wrapped_ok": int(PatchManager._m445_wrap_ok),
                "wrap_failed": int(PatchManager._m445_wrap_fail),
                "skipped": int(PatchManager._m445_wrap_skipped),
                "mode": PatchManager._m445_wrap_mode()}

    @staticmethod
    def _m473_realign_on() -> bool:
        """★473刀15 灰度开关：入队时把整方法对齐回原文基础缩进（默认开）。

        关闭 ⇒ 逐字回到 473 刀15 之前的行为（只左移、不对齐）。
        """
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_M473_REINDENT_REALIGN", True))
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m473_realign_on", level="debug")
            return True

    @staticmethod
    def _m473_shift_indent(code: str, delta: int) -> str:
        """整体平移代码块的基础缩进（正=右移，负=左移），保持相对层级不变。

        ★三引号字符串的**内部续行不平移**：那里是字符串内容，平移等于改语义；
          不平移也不影响语法（字符串续行的缩进属于值，不属于语句层级）。
        tokenize 失败 ⇒ **原样返回**（宁可不补偿，也不猜）。
        """
        if not code or not delta:
            return code
        _lines = code.replace("\r\n", "\n").split("\n")
        _skip: set[int] = set()
        try:
            import io as _io
            import tokenize as _tk
            for _t in _tk.generate_tokens(_io.StringIO(code).readline):
                if _t.type == _tk.STRING and _t.end[0] > _t.start[0]:
                    _skip.update(range(_t.start[0] + 1, _t.end[0] + 1))
        except Exception as _e:
            silent_exc(_e, where="PatchManager::_m473_shift_indent tokenize", level="debug")
            return code
        _out = []
        for _i, _ln in enumerate(_lines, start=1):
            if _i in _skip:
                _out.append(_ln)
                continue
            if not _ln.strip():
                _out.append("")
                continue
            if delta > 0:
                _out.append(" " * delta + _ln)
            else:
                _cut = -delta
                _w = len(_ln) - len(_ln.lstrip(" \t"))
                _out.append(_ln[_cut:] if _w >= _cut else _ln.lstrip(" \t"))
        return "\n".join(_out)

    @staticmethod
    def _m473_realign_indent(patch: dict) -> dict:
        """把**完整方法**的 modified_code 对齐回 original_code 的基础缩进。

        病灶（2026-11-07 实测）：`_m323_reindent` 只做「剥公共缩进」，对含 def 的
        完整方法也照剥（4 格 def → 0 格），而入队前 original_code 仍是文件里的
        4/8 格 ⇒ 存盘后 base 不等 ⇒ 验证/应用两侧的 `_m92_base_indent_guard` 必拒
        ⇒ approved 也 applied=False。patch_history 里 4 条 approved 全部如此，
        其中 3 条报「基础缩进不一致 original=8/4 modified=0」。

        只处理「两侧都含 def」的整方法替换：片段（无 def）交给 445 包裹，
        原文无 def（O3 删除类候选）不适用。
        """
        _out = {"changed": False, "reason": ""}
        if not PatchManager._m473_realign_on():
            _out["reason"] = "switch_off"
            return _out
        _orig = str(patch.get("original_code") or "")
        _mod = str(patch.get("modified_code") or "")
        if not _orig.strip() or not _mod.strip():
            _out["reason"] = "empty"
            return _out
        if not (PatchManager._m280_extract_def_name(_orig)
                and PatchManager._m280_extract_def_name(_mod)):
            _out["reason"] = "not_full_method"
            return _out
        _bo = _m92_base_indent(_orig)
        _bm = _m92_base_indent(_mod)
        _out["base_before"] = _bm
        _out["base_target"] = _bo
        if _bo < 0 or _bm < 0:
            _out["reason"] = "no_indent_info"
            return _out
        if _bm == _bo:
            _out["reason"] = "already_aligned"
            return _out
        _new = PatchManager._m473_shift_indent(_mod, _bo - _bm)
        if not _new.strip():
            _out["reason"] = "shift_empty"
            return _out
        # 壳只用于校验（4 格 def 单独 parse 必 "unexpected indent"），不写进补丁
        try:
            ast.parse("class _M473Shim:" + chr(10) + _new)
        except (SyntaxError, ValueError) as _e:
            silent_exc(_e, where="PatchManager::_m473_realign_indent ast", level="warning")
            _out["reason"] = "shift_parse_failed"
            return _out
        patch["modified_code"] = _new
        _out["changed"] = True
        _out["reason"] = "realigned"
        _out["base_after"] = _m92_base_indent(_new)
        return _out

    @staticmethod
    def _m323_normalize_patch(patch: dict) -> dict:
        """以 AST enclosing def 为唯一真值回填 method（★323批刀2）。

        返回审计信息（写进 patch["m323_truth"]，可核可回滚）。
        """
        _info = {"method_before": str(patch.get("method") or "").strip(),
                 "def_name": "", "method_after": "", "action": "unchanged",
                 "reindented": False, "parse_ok": True}
        _code = patch.get("modified_code", "") or ""
        if not _code.strip():
            return _info
        _new, _chg = PatchManager._m323_reindent(_code)
        if _chg:
            patch["modified_code"] = _new
            _code = _new
            _info["reindented"] = True
            PatchManager._m323_reindented += 1
        try:
            ast.parse(_code)
        except (SyntaxError, ValueError) as _e:
            silent_exc(_e, where="nucleus.reasoning.PatchManager::_m323_normalize_patch 语法校验",
                       level="warning")
            _info["parse_ok"] = False
        # ★473刀15：左移补偿 —— 完整方法必须对齐回原文基础缩进。
        #   不补偿 ⇒ base(modified)=0 ≠ base(original)=4/8 ⇒ 验证/应用两侧的
        #   `_m92_base_indent_guard` 必拒 ⇒ approved 也 applied=False（实测 4/4）。
        _m473_rl = PatchManager._m473_realign_indent(patch)
        if _m473_rl.get("changed"):
            # _code 故意不更新：它只用于 _m323_ast_def_name 取 def 名，
            # 保持左移后的 0 缩进可直接 parse；存盘走 patch["modified_code"]。
            _info["realigned"] = _m473_rl
        # ★445刀1：片段 → 完整方法 包裹（影子模式只留痕）。放在取 def **之前**：
        #   否则 284 刀5 看到的仍是"无 def"，作废判定不变。
        try:
            _m445_r = PatchManager._m445_wrap_fragment(patch)
            _info["m445_wrap"] = {k: _m445_r.get(k) for k in
                                  ("status", "parse_ok", "reason", "mode", "applied")}
            if _m445_r.get("status") == "wrapped" and _m445_r.get("applied"):
                _code = str(patch.get("modified_code") or "")
        except Exception as _m445_e:
            silent_exc(_m445_e, where="PatchManager::_m323_normalize_patch 445包裹",
                       level="warning")
        _def = PatchManager._m323_ast_def_name(_code)
        _info["def_name"] = _def
        if _def:
            if _info["method_before"] != _def:
                _info["action"] = ("method_backfilled" if _info["method_before"]
                                   else "method_filled")
                PatchManager._m323_normalized += 1
            patch["method"] = _def
        _info["method_after"] = str(patch.get("method") or "")
        patch["m323_truth"] = _info
        return _info

    @staticmethod
    def get_m323_generation_metrics() -> dict:
        """生成侧真值回归指标（拒收数应 → 0）。"""
        return {"def_mismatch_rejected": int(PatchManager._m323_def_mismatch_rejected),
                "normalized": int(PatchManager._m323_normalized),
                "reindented": int(PatchManager._m323_reindented)}

    # ===== ★323批刀3（P-14）：pending 滞留件出口 =====
    _M323_RF_MAX_SWEEPS = 3       # 同一补丁在 runtime_failed 上被扫到的次数上限
    _M323_RF_TTL_HOURS = 24.0     # 或超龄即归档（二者取先到）

    def sweep_runtime_failed(self, max_sweeps: int = 0,
                             ttl_hours: float = 0.0) -> dict:
        """给 `runtime_failed` 补丁一个**出口**：重试上限 + 过期归档（★323批刀3）。

        背景（烛微323 §0-2）：pending 首条 `runtime_failed` 长期滞留——既不再重试，
        也不退出队列，于是"入池非零"被滞留件污染，开闸连锁误判。
        本方法：每次触碰队列时累加扫描次数；超上限或超龄 ⇒ 移到
        `pending_patches_archive.json`（可追溯，不删除）。
        """
        _max = int(max_sweeps or self._M323_RF_MAX_SWEEPS)
        _ttl = float(ttl_hours or self._M323_RF_TTL_HOURS) * 3600.0
        _res = {"scanned": 0, "runtime_failed": 0, "archived": 0,
                "kept": 0, "archived_ids": [], "skipped_reason": ""}
        try:
            _raw = {}
            if os.path.exists(self._pending_file):
                with open(self._pending_file, "r", encoding="utf-8") as _f:
                    _raw = json.load(_f)
            if isinstance(_raw, dict):
                _items = _raw.get("patches") or _raw.get("items") or []
            elif isinstance(_raw, list):
                _items = _raw
            else:
                _items = []
            _keep, _arch = [], []
            _now = time.time()
            for _p in _items:
                if not isinstance(_p, dict):
                    continue
                _res["scanned"] += 1
                if str(_p.get("status") or "") != "runtime_failed":
                    _keep.append(_p)
                    continue
                _res["runtime_failed"] += 1
                _n = int(_p.get("m323_rf_sweeps") or 0) + 1
                _p["m323_rf_sweeps"] = _n
                _age = 0.0
                for _k in ("failed_at", "updated_at", "saved_at", "generated_at", "timestamp"):
                    _v = _p.get(_k)
                    if isinstance(_v, (int, float)) and _v > 0:
                        _age = max(_age, _now - float(_v))
                if _n > _max or (_ttl > 0 and _age > _ttl):
                    _p["m323_archived_at"] = _now
                    _p["m323_archive_reason"] = ("retry_cap" if _n > _max else "ttl_expired")
                    _arch.append(_p)
                    _res["archived"] += 1
                    _res["archived_ids"].append(str(_p.get("id") or _p.get("patch_id") or ""))
                else:
                    _keep.append(_p)
            _res["kept"] = len(_keep)
            if _arch:
                _ap = os.path.join(self._patch_dir, "pending_patches_archive.json")
                _old = []
                if os.path.exists(_ap):
                    try:
                        with open(_ap, "r", encoding="utf-8") as _f:
                            _ov = json.load(_f)
                        _old = _ov if isinstance(_ov, list) else (_ov.get("patches") or [])
                    except (OSError, ValueError) as _e:
                        silent_exc(_e, where="nucleus.reasoning.PatchManager::sweep_runtime_failed 归档读",
                                   level="warning")
                        _old = []
                _payload = (_old + _arch)[-500:]
                with open(_ap, "w", encoding="utf-8") as _f:
                    json.dump(_payload, _f, ensure_ascii=False, indent=2)
            if isinstance(_raw, dict):
                _raw["patches"] = _keep
            else:
                _raw = _keep
            if _arch or _res["runtime_failed"]:
                with open(self._pending_file, "w", encoding="utf-8") as _f:
                    json.dump(_raw, _f, ensure_ascii=False, indent=2)
        except Exception as _e:
            silent_exc(_e, where="nucleus.reasoning.PatchManager::sweep_runtime_failed 323", level="warning")
            _res["skipped_reason"] = f"{type(_e).__name__}: {_e}"
        return _res

    def save_pending_patch(self, patch: dict[str, Any]):
        """
        保存待应用的补丁到队列。
        ★v23.0增强：自动补齐元数据 + 去重检查。
        """
        # ★475刀1：曈曈主笔草稿的**影子旁路**（角色翻转第一步）。
        #   只读 patch（内部深拷贝）、只写自有台账，不改任何字段/状态/判据；
        #   不替换 LLM 产物、不碰审批与应用；异常一律吞掉，绝不阻断入池。
        try:
            from nucleus.autonomy.patch_draft import note_patch_draft_shadow
            note_patch_draft_shadow(patch)
        except Exception as _m475e:
            silent_exc(_m475e, "PatchManager::save_pending_patch 475刀1", level="debug")
        # ★343刀1②：自主修复通道**显式标记**（生产唯一调用方 =
        #   `SafeEvolutionExecutor` 的 5 处 `save_pending_patch`，均不写 source）。
        #   标在 `entry_source` 而不是覆盖 `source`：`source` 有既有消费者
        #   （`_m339_is_o3` 用它判 O3 来源），覆盖会改变审批分道语义。
        if isinstance(patch, dict):
            patch.setdefault("entry_source", "llm_auto_fix")
        # ★344补 3（A-17 三类出口②）：补丁池**拒收隔离产物**。
        #   实测：pending_patches 里曾有 `file=tmp/p342_root/d1_iso.py`
        #   的条目——隔离脚本产物进了生产池，事后读的人分不清
        #   "哪些是真生产补丁"。
        #   判定用**路径结构**（前缀/后缀），不看文件名关键字（铁律153）。
        try:
            from nucleus import domain_guard as _m344_dg
            _m344_ok, _m344_why = _m344_dg.guard_patch_entry(
                patch, trace=lambda _f, *a: _module_logger.info(_f, *a))
            if not _m344_ok:
                return
        except Exception as _m344_dge:
            silent_exc(_m344_dge,
                       where="PatchManager.save_pending_patch 344补 3 域过滤",
                       level="warning")
        # ★第280批刀1（P0）：生成侧 def 对齐——出补丁即断言 def名==method，不一致拒绝入库。
        #   烛微279批发现：23条def名与method错配＋6条modified_code无def＝29条(60.4%)。
        #   源头拦比队列清便宜：解析 modified_code 首个 def 名，与 method 字段比对。
        # ★323批刀3（P-14）：入队即给滞留件一次"出口机会"（重试上限 + 过期归档）。
        #   放在入队口而不是定时器上：队列被触碰时才需要清理，不新增调度面。
        try:
            _sw = self.sweep_runtime_failed()
            if _sw.get("archived"):
                _module_logger.info(
                    "[323刀3] pending 滞留件归档 %d 条（runtime_failed 出口）: %s",
                    _sw["archived"], ",".join(_sw.get("archived_ids") or [])[:200])
        except Exception as _sw_e:
            silent_exc(_sw_e, where="nucleus.reasoning.PatchManager::save_pending_patch 323滞留清理", level="warning")
        # ★438刀1：按龄过期 / 内容 obsolete 的件**归档出** pending（清堵）。
        #   为什么放在入队口：队列被触碰时才需要清理，不新增调度面（与 323刀3 同款思路）。
        #   为什么必须节流：`classify()` 的 obsolete 判据要读目标文件，每次入队都跑会
        #   把入队路径变成 IO 密集；1h 一次足够（7 天阈值下不存在秒级过期的件）。
        #   为什么必须有这一句：`PatchAutoApprover.prune_pending()` 原本**全仓无调用方**
        #   ⇒ 判定天天在跑、队列从不回收（11-03 实测 9 条 pending 里 8 条已判 reject_stale
        #   却仍躺在池里，其中 2 条 obsolete）。启动时另有一次（main.py[438刀1]）。
        try:
            _m438_now = time.time()
            import config as _m438_cfg  # 本模块无模块级 config 导入，按 240 行同款就地取
            _m438_iv = float(getattr(_m438_cfg, "M438_PRUNE_INTERVAL_SEC", 3600.0) or 3600.0)
            if (_m438_now - float(getattr(PatchManager, "_m438_last_prune_at", 0.0) or 0.0)
                    >= _m438_iv):
                PatchManager._m438_last_prune_at = _m438_now
                from nucleus.evolution.PatchAutoApprover import (
                    PatchAutoApprover as _m438_paa,
                )
                _m438_pr = _m438_paa().prune_pending()
                if _m438_pr.get("pruned"):
                    _module_logger.info(
                        "[438刀1] 入队口清理：归档 %s 条（remaining=%s）",
                        _m438_pr.get("pruned"), _m438_pr.get("remaining"))
        except Exception as _m438_pr_e:
            silent_exc(_m438_pr_e,
                       where="nucleus.reasoning.PatchManager::save_pending_patch 438队列清理",
                       level="warning")
        # ★323批刀2（P-03）：入队前先做「真值归一」——AST enclosing def 为唯一真值
        #   回填 method、统一 re-indent。280 的拒收闸门保留兜底（只拦真异常）。
        try:
            PatchManager._m323_normalize_patch(patch)
        except Exception as _m323_e:
            silent_exc(_m323_e, where="nucleus.reasoning.PatchManager::save_pending_patch 323真值归一", level="warning")
        # ★325批刀1：生成侧危险关键字预检——**入队前**就判定"沙箱是否必然拦截"。
        #   为什么在生成侧做：派单侧拦是"事后"，同一补丁会被反复选中反复失败
        #   （324刀0 实测六圈同一 id）；入队时打标 ⇒ 派单侧一次即跳过，且
        #   `generated_flagged_dangerous` 让"又一条静默失败"变成可数事实。
        #   ★不改沙箱 DANGEROUS_KEYWORDS（安全闸不因本批放宽）。
        try:
            from nucleus.evolution.patch_execution_dispatch import (
                scan_dangerous_keywords as _m325_scan,
            )
            _d = _m325_scan(patch.get("modified_code", "") or "")
            patch["danger_keyword_class"] = _d.get("class")
            patch["danger_real_calls"] = _d.get("real_calls") or []
            patch["sandbox_blocked_keywords"] = _d.get("sandbox_blocked") or []
            patch["sandbox_incompatible"] = bool(_d.get("would_block"))
            if _d.get("would_block"):
                PatchManager._m325_generated_flagged += 1
                _module_logger.info(
                    "[325刀1][生成侧] 补丁结构性无法通过沙箱预检(转人工): "
                    "file=%s method=%s class=%s kw=%s",
                    patch.get("file", ""), patch.get("method", ""),
                    _d.get("class"),
                    ",".join((_d.get("sandbox_blocked") or [])[:5]))
        except Exception as _m325_e:
            silent_exc(_m325_e, where="nucleus.reasoning.PatchManager::save_pending_patch 325危险预检",
                       level="warning")
        _def_name = PatchManager._m280_extract_def_name(
            patch.get("modified_code", "") or "")
        _method_name = str(patch.get("method", "") or "").strip()
        if _method_name and _def_name and _def_name != _method_name:
            PatchManager._m323_def_mismatch_rejected += 1
            _module_logger.error(
                f"[补丁入队][280刀1/323刀2] 补丁def名与method字段不一致，拒绝入库: "
                f"def={_def_name!r} method={_method_name!r} "
                f"file={patch.get('file','')} "
                f"(323真值归一后仍错配 ⇒ 真异常，计入回归指标)")
            return
        # ★第300批刀1：7 vs 5 口径对齐——入队口与派单侧**共用同一个** is_fragment_to_full，
        #   消除「各算各的」造成的口径差（295报7条 vs 烛微复算5条，疑 original 为空边界处理不同）。
        #   与295刀5/298刀1同源；源头拦比派单侧过滤更彻底（坏补丁根本不入队）。
        try:
            from nucleus.evolution.patch_execution_dispatch import (
                is_fragment_to_full as _m300_iftf,
            )
            _frag2full = bool(_m300_iftf(patch))
        except Exception as _m300_e:
            silent_exc(_m300_e, where="nucleus.reasoning.PatchManager::save_pending_patch 形态判据回退", level="debug")
            _orig_def298 = PatchManager._m280_extract_def_name(
                patch.get("original_code", "") or "")
            _frag2full = (not _orig_def298) and bool(_def_name)
        if _frag2full:
            # ★343刀1②：自主修复通道豁免 —— "片段→完整"是 LLM **正常产出形态**
            #   （给方法体片段、由入队口补成完整方法），284刀5 拿它当"结构缺陷"
            #   属口径错配。豁免后仍走后续的 280危险预检 / 风险定级 / 人工审批 /
            #   运行时验证，**不放宽安全面**（只是不再"提交前就丢件"）。
            if PatchManager._m343_auto_fix_exempt(patch):
                PatchManager._m343_shape_exempt_count += 1
                patch["_m343_shape_exempt"] = True
                _module_logger.info(
                    "[343O3] llm_auto_fix豁免298刀1: %s file=%s method=%s "
                    "（片段→完整属 LLM 合理产出形态；仍走审批/验证）",
                    str(patch.get("id") or "")[:32], patch.get("file", ""),
                    _method_name)
            else:
                PatchManager._shape_rejected_count += 1
                _module_logger.warning(
                    f"[补丁入队][298刀1] 片段→完整形态拒绝入队: "
                    f"file={patch.get('file','')} method={_method_name} "
                    f"(original无def modified有def={_def_name!r})")
                return
        # ★第280批刀2：高危族按 issue_type 强制重定级（覆盖 LLM 自评分）。
        #   烛微279批发现：48条risk_level全为"低"，其中 sql_injection/lock_with_emit/
        #   non_atomic_write 共8条高危被误判低风险。高危族禁入免签视野、必须专项验证。
        # ★344批刀3：入队口**不再无条件覆盖** —— 显式 risk_level 优先，
        #   但与 issue_type 高危族取"较高档"（只许升不许降，安全面零放宽）。
        _orig_risk = str(patch.get("risk_level") or "")
        _m344_risk, _m344_risk_basis = PatchManager._m344_merge_risk(
            _orig_risk, patch.get("issue_type"))
        patch["risk_level"] = _m344_risk
        patch["risk_level_basis"] = _m344_risk_basis
        # 归一化后 `risk_level` 恒为中文档位；原文另存，便于追溯"上游写了什么"
        if _orig_risk and _orig_risk != _m344_risk:
            patch["risk_level_raw"] = _orig_risk
        if _m344_risk_basis == "escalated_by_issue_type":
            _module_logger.info(
                "[344刀3] 风险按 issue_type 上调: %s:%s 显式=%r → %r"
                "（issue_type=%s；280刀2 高危族禁降级保持有效）",
                patch.get("file", ""), patch.get("method", ""), _orig_risk,
                _m344_risk, patch.get("issue_type"))
        # ★第280批刀4：trust_score 恒70 零信息量 → 按 issue_type 重算有信息量分值。
        #   原恒70使 PATCH_AUTO_APPROVE_TRUST_THRESHOLD=40 形同虚设。
        if patch.get("trust_score") in (None, 70, 70.0):
            patch["trust_score"] = PatchManager._m280_trust_by_issue_type(
                patch.get("issue_type"))
        # ★372刀1（裁决 C-2 甲）：族登记状态**显式留痕**，不再靠"默认值猜"。
        #   未登记族已 fail-closed 至中风险（见 `_M372_DEFAULT_FAMILY_RISK`）；
        #   这里把判定依据落到条目上，事后可对账"这条为什么没进自动档"。
        #   布尔与说明分开两个字段（铁律153：显式布尔禁理由串）。
        _fam_registered = PatchManager._m372_family_registered(
            patch.get("issue_type"))
        patch["risk_family_registered"] = _fam_registered
        if not _fam_registered:
            patch["risk_family_note"] = (
                "族未登记 ⇒ fail-closed 中风险，不进自动档（372刀1）")
            _module_logger.warning(
                "[372刀1] 未登记族 fail-closed: issue_type=%r "
                "file=%s method=%s ⇒ risk_level=%r（自动档关闭，转人工）",
                patch.get("issue_type"), patch.get("file", ""),
                patch.get("method", ""), patch.get("risk_level"))
        # ★338批刀4：**生成侧显式填充证据三要素**。
        #   337刀1 查出池里只剩 fcef 一条“唯一合格者”，而其余 8 条
        #   全是“值在、凭据不在”——没人能回答这个 risk/trust 凭什么定的。
        #   在入队唯一口一次补齐，之后派单侧证据门（build_reusable_candidates /
        #   select_for_auto_triage）可直接采信，不再反复推导。
        if not isinstance(patch.get("patch_evidence"), dict):
            patch["patch_evidence"] = PatchManager._m338_build_evidence(patch)
            _ev338 = patch["patch_evidence"]
            _miss338 = [k for k in ("risk", "trust", "blast")
                        if not isinstance(_ev338.get(k), dict)]
            if _miss338:
                # 不阻断入队（入队与派单是两件事），但必须可见：
                # 这类补丁进不了自动派单池，会进可复用清单。
                _module_logger.info(
                    "[338刀4] 补丁入队但证据不全（进不了自动派单池）: "
                    "file=%s method=%s 缺=%s",
                    patch.get("file", ""), patch.get("method", ""), ",".join(_miss338))
        # ★v23.0新增：补全元数据
        if "saved_at" not in patch:
            patch["saved_at"] = time.time()
        # ★第116批 T-116f：入队补记真实创建时间戳。此前 pending 全量 created_at=0，
        #   导致只能做集合差分、无法按时间排序/判龄（stale-audit 亦因此判不了超龄）。
        #   历史票仍为 0，需另做一次性回填（本批未做，避免追溯改写）。
        if not patch.get("created_at"):
            patch["created_at"] = time.time()
        if "status" not in patch:
            patch["status"] = "pending"
        if "reason" not in patch:
            patch["reason"] = patch.get("description", "未说明修改原因")
        if "source_diagnosis" not in patch:
            patch["source_diagnosis"] = patch.get("source", "未知诊断源")

        # ★主线第80批 T7 (P0-3)：顶部闸门——核心文件补丁禁止自动 approved。
        #   防御「反思直批」等旁路在入队前直接置 approved（main.py:3480 反射改进版、
        #   PulseCodeLearner apply_now 过滤均依赖此兜底）。已 verified 的保持 verified。
        if PatchManager._m80_is_core_file(patch.get("file", "")) and patch.get("status") == "approved":
            patch["status"] = "verified"
            _module_logger.warning(
                f"[补丁入队][T7] 核心文件补丁禁止自动approved，已回退verified: "
                f"{patch.get('file','')}:{patch.get('method','')}")

        # ★D4修复（P2，2026-09-05，星轨 PHASE6 列为第六批必做第 3 项）：
        #   规则式补丁（repair_source="local_rule"）从未经过 AestheticJudge 评分，
        #   aesthetic_score 字段**根本不存在**——注意是「缺失」，不是「0」。
        #   而 _composite_score 用 `patch.get("aesthetic_score") or {}` 取值，
        #   把缺失当成审美 0 分参与计算，于是 local_rule 补丁综合分恒为
        #       0.5×0 + 0.5×trust(30) = 15.0
        #   与同题的 LLM 补丁（有评分，如 94.5/87.8/66.9）比较时永远处于劣势，
        #   同题择优因此退化为「先到先得」。
        #   生产证据（星轨直接读取 data/patches/pending_patches.json）：
        #     补丁#4 PulseLiver、#5 PulseCortex 均为 local_rule，aesthetic_score 字段不存在；
        #     对应 pulse.log 12:20:29 / 12:20:53 两次
        #     「已有方案综合分 15.0 >= 新方案 15.0，保留已有」。
        #
        #   ★必须放在下面同题择优（:_composite_score 调用处）之前，
        #     否则择优时读到的仍是缺失值，补了等于没补。
        #   此处统一补评分：任何来源的补丁入队前都具备审美分，
        #   一处修改覆盖全部来源，比逐个改生成点更彻底、回归面更小。
        if patch.get("aesthetic_score") is None and patch.get("modified_code"):
            try:
                from nucleus.evolution.AestheticJudge import get_aesthetic_judge
                _aes = get_aesthetic_judge().score(patch.get("modified_code"))
                patch["aesthetic_score"] = _aes
                patch["aesthetic_grade"] = _aes.get("grade")
                if not _aes.get("syntax_valid", True):
                    # ★与 D5 联动：语法校验未通过的补丁不应被自动审批放行
                    _module_logger.warning(
                        f"[补丁入队] 审美评分拒绝(语法校验未通过): "
                        f"{patch.get('file','')}:{patch.get('method','')} "
                        f"reason={_aes.get('reason','')}")
            except Exception as _e:
                # 评分失败不阻塞入队（保证补丁不丢），但必须留痕，不再静默吞掉
                _module_logger.debug(f"[补丁入队] 审美评分失败(不影响入队): {_e}")

        # ★v25.1 P0修复: 低风险补丁自动审批，打通自主迭代应用闭环
        #   原逻辑：所有补丁status=pending，启动时只应用approved，导致补丁永不应用。
        #   新逻辑：risk_level<=1（极低/低风险，如添加日志、修复沉默异常）自动设为approved，
        #   启动时apply_all_pending自动应用；高风险补丁仍保持pending需人工审批。
        #   安全保障：补丁已通过verify_in_copy验证，且有重启计数器防无限循环。
        #
        # ★PHASE17-A1修复（2026-09-07）：入队门槛与 _check_patch_safety 同源。
        #   原缺陷1：风险阈值硬编码 `<=1`，而 config.auto_apply_max_risk=2 —— 两套门槛不一致，
        #            配置改了不生效（实测 pending 2 条 risk=低(2) 因此永远不自动审批）。
        #   原缺陷2：此处**只判风险不判信任分**，一旦按配置放宽到 risk<=2，
        #            trust=30 的补丁会被自动置 approved，而 _check_patch_safety 对
        #            status=approved 直接放行 → 信任分门槛被完全绕过（安全门失效）。
        #   修复：风险与信任双门槛均从 EVOLUTION_CONFIG 读取，与落地前安全门判据一致。
        _risk_raw = patch.get("risk_level", 99)
        _risk_map = {"极低": 1, "低": 2, "中等": 3, "高": 4}
        if isinstance(_risk_raw, str):
            _risk_val = _risk_map.get(_risk_raw, 99)
        else:
            _risk_val = _risk_raw
        try:
            import config as _cfg_a1
            _evo_cfg_a1 = getattr(_cfg_a1, 'EVOLUTION_CONFIG', {})
        except Exception as _e_a1:
            _evo_cfg_a1 = {}
            _module_logger.debug(f"[补丁入队] 配置读取失败，用保守默认值: {_e_a1}")
        # ★第53批 T1（P0-补丁1/3）：阈值**运行时**从 config.EVOLUTION_CONFIG 读取
        #   （每次入队重新 import config → 改配置即时生效，无需重启框架）。
        #   ★同源约定：auto_apply_min_trust 必须与 config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD
        #   同值（门控测试 TestThresholdUnified 断言），避免两套机制再次漂移。
        _max_risk_a1 = _evo_cfg_a1.get("auto_apply_max_risk", 1)   # 默认保守：仅极低风险
        _min_trust_a1 = _evo_cfg_a1.get("auto_apply_min_trust", 60)
        try:
            _trust_val_a1 = float(patch.get("trust_score", 0) or 0)
        except (TypeError, ValueError) as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::save_pending_patch L3081", level="debug")
            _trust_val_a1 = 0.0
        # ★M85-4（第85批 T-85d / D84-4）：本地低风险补丁放行自动应用。
        #   三道既有门槛（风险/信任/免签总开关）对本地补丁放宽，
        #   但核心文件红线与 LLM 审批要求**不变**。
        _m85_allow = PatchManager._m85_local_low_risk_auto_apply(patch, _evo_cfg_a1)
        _risk_ok_a1 = isinstance(_risk_val, (int, float)) and (
            _risk_val <= _max_risk_a1 or _m85_allow)
        _trust_ok_a1 = (_trust_val_a1 >= _min_trust_a1) or _m85_allow
        # ★176批段3 B1 进化保守·类型白名单守门（SAFE_TO_AUTO_FIX）：
        #   当列表非空时，仅 issue_type ∈ SAFE_TO_AUTO_FIX 的补丁可免签自动审批，
        #   其余一律转人工（即使 risk/trust 达标也不再自动写入源码）。
        #   默认空列表 ⇒ 不施加限制（零回归）。
        _safe_list_a1 = list(_evo_cfg_a1.get("SAFE_TO_AUTO_FIX", []) or [])
        _issue_type_a1 = str(patch.get("issue_type", "") or "")
        _whitelist_ok_a1 = (not _safe_list_a1) or (_issue_type_a1 in _safe_list_a1)
        # ★383刀5：三级族分级。开关关（默认）⇒ `_triage_a1 == "off"`，
        #   下面这段一行都不执行 ⇒ 逐字回到 176 批口径。
        #   ★零放宽：只在旧判定**已通过**的基础上再收紧，
        #     旧判定拒绝的补丁，三级绝不会把它放行。
        _triage_a1 = PatchManager._m383_family_triage(patch)
        if _triage_a1 != "off":
            _whitelist_ok_a1 = _whitelist_ok_a1 and (_triage_a1 == "safe")
            if _triage_a1 in ("review", "blocked"):
                patch["family_triage"] = _triage_a1
            if _triage_a1 == "blocked":
                _module_logger.warning(
                    f"[补丁自动审批][383刀5] 高危族禁用自动档: "
                    f"{patch.get('file', '')}:{patch.get('method', '')} "
                    f"issue_type={_issue_type_a1} ⇒ 转人工（BLOCKED）")
        # ★第280批刀3：llm 补丁永不自动审批（即使 risk/trust/whitelist 达标也转人工）。
        _source_is_llm_a1 = str(patch.get("source", "")) == "llm"
        if (_risk_ok_a1 and _trust_ok_a1 and _whitelist_ok_a1
                and not _source_is_llm_a1):
            # ★主线第80批 T7 (P0-3)：免签改写闸门——verified 或核心文件或总开关关闭，
            #   禁止升 approved；核心/关闭场景下未 verified 的补丁强制置 verified 待人工。
            # ★M85-4：总开关关闭时，本地低风险补丁仍放行（_m85_allow 认定）；
            #   核心文件由 _m85_allow 内部已排除，此处再叠加一次显式红线判定。
            _blocked = (patch.get("status") == "verified") or (
                PatchManager._m80_gate_blocked(patch, _evo_cfg_a1)
                and not _m85_allow) or PatchManager._m80_is_core_file(
                    str(patch.get("file", "")))
            if _blocked:
                if patch.get("status") != "verified":
                    patch["status"] = "verified"
                _module_logger.info(
                    f"[补丁自动审批][T7] 免签改写闸门拦截(未升approved): "
                    f"{patch.get('file','')}:{patch.get('method','')} "
                    f"status={patch.get('status')} "
                    f"(verified={patch.get('status')=='verified'}, "
                    f"core={PatchManager._m80_is_core_file(patch.get('file',''))}, "
                    f"auto_apply={PatchManager._m80_auto_apply_enabled(_evo_cfg_a1)})")
            else:
                if PatchManager._approve_via(patch, "save", None):
                    patch["auto_approved"] = True
                # 否则 fail-closed：保持原 status，不置 auto_approved
                _module_logger.info(
                    f"[补丁自动审批] 低风险补丁已自动审批: "
                    f"{patch.get('file','')}:{patch.get('method','')} "
                    f"risk={_risk_raw}(<={_max_risk_a1}), trust={_trust_val_a1}(>={_min_trust_a1})")
        else:
            _module_logger.debug(
                f"[补丁入队] 未自动审批(转人工): {patch.get('file','')}:{patch.get('method','')} "
                f"risk={_risk_raw}(需<={_max_risk_a1}, {'通过' if _risk_ok_a1 else '超限'}), "
                f"trust={_trust_val_a1}(需>={_min_trust_a1}, {'通过' if _trust_ok_a1 else '不足'}), "
                f"白名单={'通过' if _whitelist_ok_a1 else '不在SAFE_TO_AUTO_FIX'}")

        # ★第101批 T-101a（P0）：低风险可放行路径——打通进化闭环最后一环。
        #   沿革：18 条补丁（多为 LLM 来源）曾因 T7 闸门 + auto_apply_enabled=False
        #   全部 pending/verified，0 批准 0 落盘（烛微第3期 N2），本通道即为此而开。
        #   ★372刀2（裁决 C-1 甲）**改了本通道的两条口径**，旧注释已作废，如实记：
        #     ① 旧：「比 auto_apply_enabled 更严格，故即使总开关关闭也放行」
        #        ⇒ 现：**总开关关闭即拒**（绕总开关被认定为安全面裂缝，已收口）；
        #     ② 旧：「该路径允许 LLM 来源（与 _m85 不同）」
        #        ⇒ 现：**source=llm 一律排除**（与 A 通道对齐；账本实测 12/12 全 llm）。
        #   现放行判据（全部满足）：总开关 True ∧ source≠llm ∧ risk 低
        #   ∧ 嵌套 runtime_verify_result.verified=True ∧ baseline_errors>0
        #   ∧ 非核心文件 ∧ 方法不在护栏禁改清单。
        #   标记 auto_released=True（非 auto_approved），使 apply_all_pending
        #   入口收口不将其退回；落地核心门仍拦截核心文件。
        # ★第105批 T-105a：复用统一放行助手（与 verify_submitted_patches 补回边同源）。
        # ★372刀2：本处把已读到的 `_evo_cfg_a1` 透传下去，避免同一次判定两次读 config。
        self._m105_try_release_low_risk(patch, _evo_cfg_a1)

        # ★352刀2②：入池口强制 `dedup_key`。放在所有其他判定**之后**、
        #   载入队列**之前**：前面路径可能还会回写 `method`（_m323 真值归一），
        #   键必须用**归一后**的 file/method 算；而在落盘前才能保证写进去的每一条
        #   都带键（否则又是一批“入了池却对不上账”）。
        try:
            _m352_dk = PatchManager._m352_ensure_dedup_key(patch)
            patch["dedup_key_status"] = _m352_dk["action"]
            _m352_pid = str(patch.get("id") or "")[:32]
            if not _m352_dk["ok"]:
                _module_logger.warning(
                    "[352去重] 拒收：无法定位时不入池 id=%s file=%r method=%r why=%s",
                    _m352_pid, patch.get("file"), patch.get("method"), _m352_dk["why"])
                return
            if _m352_dk["action"] == "filled":
                _module_logger.info(
                    "[352去重] 补齐 dedup_key id=%s file=%s method=%s key=%s%s",
                    _m352_pid, patch.get("file"), patch.get("method"),
                    _m352_dk["key"], "(退化：无 issue_type)" if _m352_dk["degraded"] else "")
            elif _m352_dk["action"] == "recomputed":
                _module_logger.warning(
                    "[352去重] dedup_key 不自洽，已按可复算值重算 id=%s "
                    "原值=%s 新值=%s（原值留 dedup_key_raw）",
                    _m352_pid, _m352_dk["raw"][:16], _m352_dk["key"])
        except Exception as _m352_e:
            # 异常不阻断入队（不能因新闸门丢真补丁），但**必须可见**
            silent_exc(_m352_e, where="PatchManager::save_pending_patch 352去重键",
                       level="warning")

        # ★384刀4：v2 键（按 modified_code 里的真实方法名）—— v1 双写，存量不迁移。
        #   放在 v1 之后：v1 已是「入池必有」的稳定键，v2 只是叠一层可对账的真实名。
        try:
            _m384_v2 = PatchManager._m384_ensure_dedup_key_v2(patch)
            patch["dedup_key_v2_status"] = _m384_v2["action"]
            if _m384_v2["action"] == "differs":
                _module_logger.info(
                    "[384去重] dedup_key_v2 与 v1 不同（method 字段非真实 def 名）:"
                    " id=%s method=%r real=%r",
                    str(patch.get("id") or "")[:32], patch.get("method"),
                    _m384_v2["real_method"])
        except Exception as _m384_e:
            # 异常不阻断入队（与 352 同纪律：不能因新键丢真补丁），但必须可见
            silent_exc(_m384_e, where="PatchManager::save_pending_patch 384v2键",
                       level="warning")

        pending = self._load_patch_list(self._pending_file)
        history = self._load_patch_list(self._history_file)

        # ★v24.0增强：去重检查——相同文件+相同original_code的补丁不重复添加，且检查历史
        # ★多候选择优合并(E6·深化)：同题去重从「先到先得」升级为「按审美+信任综合分择优」——
        #   同一问题可能同时产生本地规则补丁与 LLM 补丁（original_code 相同），
        #   不再以到达时序丢弃一方，而是保留综合分更高的方案（保优去劣），
        #   与 apply_all_pending 的审美降序应用形成「入队择优 + 应用排序」双层择优。
        # ★7-3修复（P2，2026-09-05，星轨 PHASE7 第七批 7-3）：去重键增强。
        #   原键为 (file, original_code)，实际漏拦了补丁#1与#2——
        #   二者同文件(PulseCodeLearner.py)、同方法(_generate_organ_handbook)、
        #   同来源(llm_multi_file)、同描述(为静默异常添加日志记录)，
        #   只因 LLM 两次生成时的变量名/日志格式略有差异，
        #   original_code 与 modified_code 均不完全相同，原键因此失配。
        #   original_code 是「代码文本」，天生易受上下文提取差异影响；
        #   method 是「结构性标识」，稳定得多。
        #   新键改为 (file, method, repair_source) 三元组：
        #     · 同来源才判重复 → 保住 llm_multi_file 与 local_rule 的择优空间
        #     · 不同来源同方法 → 视为不同候选，双双保留用于择优（符合原设计）
        #   原键**保留**而非替换：两者取「或」，只增拦截面、不减既有能力，
        #   符合「不删除原有逻辑，只增强」的改动规范。
        _new_key = self._patch_dedup_key(patch)
        # 新键退化标记：method 缺失时三元组退化为 (file, "", source)，
        # 区分能力不足，此时才启用原键兜底（见下）。
        _new_key_degraded = not patch.get("method")
        # ★9-问题2修复（2026-09-05）：相似度兜底去重。
        #   7-3 的 (file, method, repair_source) 键能拦住「同文件同方法同来源」，
        #   但当 repair_source 不同（如 local_rule vs llm 反复生成同一处改动）或
        #   method 字段缺失时，键会失配。星轨 7 小时日志实测：PulseLiver /
        #   PulseCortex 的两个 silent_exception 补丁被反复生成约 8 次，每次都
        #   走「同题择优保留」，白白浪费 8 轮 LLM 调用。
        #   此处补一层：同 file + 同 method 且 modified_code 相似度 >0.9 时，
        #   判为实质重复直接跳过（不进入择优，也不重复入队）。
        _new_code = patch.get("modified_code", "") or ""
        for _existing in pending:
            # 相似度兜底：仅当键失配时才启用，避免误伤「同方法不同来源」的择优空间
            _sim_dup = False
            if self._patch_dedup_key(_existing) != _new_key:
                _exist_code = _existing.get("modified_code", "") or ""
                if (
                    _new_code and _exist_code
                    and _existing.get("file") == patch.get("file")
                    and _existing.get("method") == patch.get("method")
                    and patch.get("method")  # 两条都有 method 才比（否则退化场景不适用）
                ):
                    try:
                        _ratio = difflib.SequenceMatcher(
                            None, _exist_code, _new_code).ratio()
                        _sim_dup = _ratio > 0.9
                    except Exception as _m428_e:
                        silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::save_pending_patch L3258", level="debug")
                        _sim_dup = False
            if _sim_dup:
                _module_logger.debug(
                    f"[补丁入队] 相似度去重跳过(>0.9): "
                    f"{patch.get('file','')}:{patch.get('method','')} "
                    f"source={patch.get('repair_source','?')}")
                return
            _same_topic = (
                self._patch_dedup_key(_existing) == _new_key
                or (
                    # 原键**仅在 new_key 退化时**兜底。
                    #   不能无条件叠加：原键是 (file, original_code)，不含来源维度，
                    #   会把「同方法不同来源」的两个补丁也判成同题，
                    #   从而误删掉本应保留用于择优的另一条候选——
                    #   这与 E6 多候选择优合并「llm_multi_file 与 local_rule
                    #   双双保留再择优」的设计意图直接冲突。
                    #   实测即触发：local_rule 补丁被判「同题择优保留」而丢弃。
                    #
                    # ★收窄为「且」：仅当**新旧两条都缺 method** 时才回落原键。
                    #   原先用「或」，意味着只要旧补丁缺 method，就会拿粗粒度
                    #   的 (file, original_code) 去比对新补丁——结果是：一条带
                    #   method 的 llm_multi_file 新补丁，会被一条同文件同原始码
                    #   但缺 method 的旧 local_rule 补丁判成同题，进而在「择优」
                    #   中被静默丢弃。这与 E6 多候选择优的设计意图直接冲突。
                    #   待审队列的取舍原则是：宁可多留一条候选，不可误删一条修复。
                    (_new_key_degraded and not _existing.get("method"))
                    and _existing.get("file") == patch.get("file")
                    and _existing.get("original_code") == patch.get("original_code")
                )
            )
            if _same_topic:
                _new_score = self._composite_score(patch)
                _old_score = self._composite_score(_existing)
                if _new_score > _old_score:
                    # 择优替换：保留已有补丁的 saved_at，覆盖为新方案
                    _kept_at = _existing.get("saved_at", patch.get("saved_at", time.time()))
                    _existing.update(patch)
                    _existing["saved_at"] = _kept_at
                    _existing["updated_at"] = time.time()
                    self._save_patch_list(self._pending_file, pending)
                    _module_logger.info(
                        f"同题择优替换: {patch.get('file','')}:{patch.get('method','')} "
                        f"综合分 {_old_score:.1f}→{_new_score:.1f} (保留更优方案)")
                else:
                    _existing["updated_at"] = time.time()
                    _existing["saved_at"] = time.time()
                    self._save_patch_list(self._pending_file, pending)
                    _module_logger.warning(
                        f"同题择优保留: {patch.get('file','')}:{patch.get('method','')} "
                        f"已有方案综合分 {_old_score:.1f} >= 新方案 {_new_score:.1f}，保留已有")
                return
        for _hist in history:
            # ★7-3：历史查重同样叠加新键（与上方 pending 查重保持同一判定口径，
            #   否则会出现「pending 里判为重复、历史里判为不重复」的口径错位）
            _hist_same = (
                self._patch_dedup_key(_hist) == _new_key
                or (
                    # 与上方 pending 查重同口径：仅当两条都缺 method 时才回落原键
                    (_new_key_degraded and not _hist.get("method"))
                    and _hist.get("file") == patch.get("file")
                    and _hist.get("original_code") == patch.get("original_code")
                )
            )
            if _hist_same:
                # 检查历史中该补丁是否曾经失败，如果失败则不再重复添加
                if not _hist.get("applied", False):
                    # ★第22批 T5/P2-123：相同补丁历史上失败过 → 去重跳过，属**正常
                    #   去重行为**（实测 915 次/1.5h），降为 DEBUG；关开关时回 WARNING。
                    (_module_logger.debug if _noise_reduce()
                     else _module_logger.warning)(
                        f"去重跳过: 相同补丁已在历史中失败过 "
                        f"(原错误: {_hist.get('apply_error', '未知')})")
                    return

        # ★438刀2：入队口写「修复前基线」——没有基线的 verified 是自证。
        #   为什么在入队口而不是验证时：验证发生在修复**之后**，那时日志里已经
        #   看不到"修复前那 24 小时"了（只能靠历史日志考古，而日志会轮转）。
        #   取不到 ⇒ baseline_errors=None + source="unavailable"，**绝不填 0**。
        try:
            from nucleus.evolution.baseline_probe import apply_to_patch as _m438_bp
            _m438_bpr = _m438_bp(patch)
            if _m438_bpr.get("applied"):
                _module_logger.info(
                    "[438刀2] 基线探测 %s:%s → baseline=%s source=%s shadow=%s (%s)",
                    patch.get("file", ""), patch.get("method", ""),
                    patch.get("baseline_errors"), patch.get("baseline_source"),
                    patch.get("baseline_shadow"), patch.get("baseline_detail", ""))
        except Exception as _m438_bp_e:
            silent_exc(_m438_bp_e,
                       where="nucleus.reasoning.PatchManager::save_pending_patch 438基线探测",
                       level="warning")
        # ★448刀2：先把 problem_gone 带到顶层（八闸⑤只认显式布尔）
        try:
            _m448_pg = self._m448_sync_problem_gone(patch)
            if _m448_pg.get("changed"):
                _module_logger.info(
                    "[448刀2] problem_gone=%r（来源=%s）%s:%s",
                    _m448_pg.get("problem_gone"), _m448_pg.get("source"),
                    patch.get("file", ""), patch.get("method", ""))
        except Exception as _m448_pge:
            silent_exc(_m448_pge, where="PatchManager::save_pending_patch 448 problem_gone",
                       level="warning")
        # ★448刀1：再按证据算信任分（要看上一步刚写进来的 problem_gone）
        try:
            _m448_tc = self._m448_calc_trust(patch)
            if _m448_tc.get("applied"):
                _module_logger.info(
                    "[448刀1] trust %r → %r（加分 %.0f；明细=%s）%s:%s",
                    _m448_tc.get("before"), _m448_tc.get("after"),
                    _m448_tc.get("score"),
                    ",".join(f"{i['rule']}{'' if i['ok'] else '(未过)'}"
                             for i in _m448_tc.get("items") or []),
                    patch.get("file", ""), patch.get("method", ""))
        except Exception as _m448_tce:
            silent_exc(_m448_tce, where="PatchManager::save_pending_patch 448 trust",
                       level="warning")
        # ★446刀2：四个基线字段**随 patch 落 pending**（缺则补 None，不改值）。
        #   位置：必须紧贴 append —— 上面任何一步早退（284作废/352拒收/280错配）
        #   都不会走到这里，写进去也无意义；这里写进去的，就一定在待审池里。
        try:
            _m446_fill = PatchManager._m446_ensure_baseline_fields(patch)
            if _m446_fill.get("filled"):
                _module_logger.info(
                    "[446刀2] 基线字段补齐（原路径未写）: id=%s 补=%s "
                    "file=%s method=%s",
                    str(patch.get("id") or "")[:16],
                    ",".join(_m446_fill["filled"]),
                    patch.get("file", ""), patch.get("method", ""))
        except Exception as _m446_e:
            silent_exc(_m446_e, where="PatchManager::save_pending_patch 446基线字段",
                       level="warning")
        pending.append(patch)
        self._save_patch_list(self._pending_file, pending)
        PatchManager._enqueue_count += 1
        _module_logger.info(
            f"[补丁入队] {patch.get('id','')[:16]} {patch.get('file','')}:"
            f"{patch.get('method','')} trust={patch.get('trust_score','?')} "
            f"src={patch.get('repair_source','?')}")
        # ★405刀3：入队 = 一次"重大变更提议" ⇒ 发 ConsentEvent.PROPOSE。
        #   位置刻意放在 `_save_patch_list` **之后**：只有真落了盘才算提议成立，
        #   入队被拒/写盘失败的路径不该让共同决策那边看到幽灵提议。
        try:
            from nucleus.const import ConsentEvent as _CE405p
            _emit_consent(_CE405p.PROPOSE, _m405_proposal_payload(patch),
                          priority=5)
        except Exception as _e405p:
            silent_exc(_e405p,
                       where="PatchManager::save_pending_patch 405提议发射",
                       level="debug")

    def _composite_score(self, patch: dict[str, Any]) -> float:
        """★多候选择优合并：综合分 = 0.4*审美 + 0.3*信任 + 0.3*价值（0~100 同量纲）。

        用于同题多候选补丁（本地规则 vs LLM）的择优保留。
        ★价值内化(护城河·山3)：框架决策带「我珍视什么」的价值印迹——补丁与主导价值
          契合度（value_alignment）参与择优。无价值信号时退化为 0.5/0.5（零冲突）。
        ★D4修复（P2，2026-09-05）：审美分**缺失**时不再按 0 计分。
            原实现用 `patch.get("aesthetic_score") or {}`，把「字段不存在」与
            「评了 0 分」混为一谈。规则式补丁（local_rule）从未评分、字段缺失，
            于是综合分恒为 0.5×0 + 0.5×trust(30) = 15.0，
            在同题择优中被系统性压低，择优退化为「先到先得」。
            正确语义应当是：**未评分 = 中性，不构成负面信号**，
            故缺失时退化为「仅信任分（+价值分）」，与已评分补丁保持同量纲可比。
        """
        try:
            _aes = patch.get("aesthetic_score")
            _trust_c = max(0.0, min(100.0, float(patch.get("trust_score", 0) or 0)))
            _val = self._value_alignment_score(patch)
            _val_c = max(0.0, min(100.0, _val))
            if _aes is None:
                # 未评分：跳过审美维度，不按 0 拉低
                return round(_trust_c if _val <= 0.0
                             else 0.5 * _trust_c + 0.5 * _val_c, 1)
            _aes_total = float(_aes.get("total", 0) if isinstance(_aes, dict) else 0)
            _aes_c = max(0.0, min(100.0, _aes_total))
            if _val > 0.0:
                return round(
                    0.4 * _aes_c
                    + 0.3 * _trust_c
                    + 0.3 * _val_c, 1)
            return round(0.5 * _aes_c + 0.5 * _trust_c, 1)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_composite_score L1235", level="debug")
            return 0.0

    def _value_alignment_score(self, patch: dict[str, Any]) -> float:
        """★价值内化(护城河·山3): 补丁与框架主导价值的契合度（0~100）。

        取 ValuePreference 主导价值（strength 降序），检查其语义触发词在补丁文本
        （diff_summary + description + issue_type）中的命中；命中按价值强度加权。
        无价值数据/未命中 → 0（退化为原 0.5/0.5 择优，零冲突）；任何异常降级 0 不误伤。
        """
        try:
            from nucleus.ValuePreference import (
                _POSITIVE_VALUE_WORDS,
                get_value_preference,
            )
            _vp = get_value_preference()
            _dom = _vp.get_dominant(3) if _vp else None
            if not _dom:
                return 0.0
            _text = " ".join([
                str(patch.get("diff_summary") or ""),
                str(patch.get("description") or ""),
                str(patch.get("issue_type") or ""),
            ])
            _hits, _n = 0.0, 0
            for _d in _dom:
                _v = _d.get("value", "")
                _s = float(_d.get("strength", 0) or 0)
                _words = _POSITIVE_VALUE_WORDS.get(_v, [_v])
                if any(w in _text for w in _words):
                    _hits += _s
                    _n += 1
            if _n == 0:
                return 0.0
            return round(min(100.0, 100.0 * _hits / _n), 1)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_value_alignment_score L1270", level="debug")
            return 0.0

    def list_pending_patches(self) -> list[dict[str, Any]]:
        """列出待审批补丁（★FIX: 提供人工审批入口的数据来源）"""
        return self._load_patch_list(self._pending_file)

    def has_pending_patch_for(self, file_path: str, method_name: str) -> bool:
        """★P1-1修复（第十批）：查询是否已有同文件同方法的待审批补丁。

        供补丁生成前（SafeEvolutionExecutor._generate_patch）预检使用，
        避免「同一处改动反复生成 + LLM 反复调用」，从源头减少重复补丁。
        判定口径：file 与 method 双匹配即视为「已有待审批补丁」，
        不区分 repair_source —— 只要同位置已有补丁待审批，就跳过再次生成。
        """
        if not file_path or not method_name:
            return False
        try:
            for _p in self._load_patch_list(self._pending_file):
                if (
                    _p.get("file") == file_path
                    and _p.get("method") == method_name
                ):
                    return True
        except Exception as e:
            # 读取失败不阻断生成（保守放行，交给入队阶段的去重兜底）
            silent_exc(e, where="nucleus.reasoning.PatchManager::has_pending_patch_for L1294", level="debug")
            return False
        return False

    def find_blocking_pending_patch(self, file_path: str, method_name: str,
                                    now: float | None = None) -> dict | None:
        """★T-94a.3：返回真正**阻塞再生**的待审批补丁（按补丁活性判定）。

        任务书 §T-94a.3：「『已有待审批』计时器改为按补丁而非按问题位置」。
        实现语义（★与 `has_pending_patch_for` 的差别）：

        * 老化开关**关闭**（默认）→ 与 `has_pending_patch_for` **完全一致**
          （任一 file+method 匹配即算阻塞），零行为变化；
        * 老化开关**开启** → 仅在补丁仍处**老化窗口内**（未超过
          ``PENDING_AGING_MAX_AGE_HOURS``）时才视为阻塞。超时未裁决的补丁不再永久冻结同位置的
          再生 —— 这正是第93批测得的「已有待审批」死循环（pending 只增不减、
          同位置问题每轮重复发现）。

        ★注意：本方法**只解除阻塞**，**不做放行**；放行仍必须走
        `_m94_aging_eligible` 的严格判据（`_m94_apply_pending_aging`）。
        ★取不到时间戳的补丁：视为仍在窗口内（保守，不误伤）。
        """
        if not file_path or not method_name:
            return None
        try:
            _aging = self._m94_pending_aging_on()
            _maxh = self._m94_aging_max_hours()
            for _p in self._load_patch_list(self._pending_file):
                if not isinstance(_p, dict):
                    continue
                if (_p.get("file") != file_path
                        or _p.get("method") != method_name):
                    continue
                if not _aging:
                    return _p
                _age = self._m94_patch_age_hours(_p, now)
                if _age is None or _age < _maxh:
                    return _p
        except Exception as e:
            silent_exc(e, "nucleus/reasoning/PatchManager.py:1332:find_blocking_pending_patch", level="warning")
            return None
        return None

    def _is_stale(self, patch: dict[str, Any]) -> bool:
        """★第54批 T3.2：补丁是否已过期（超过 PATCH_AUTO_APPROVE_STALE_DAYS 天）。

        ★背景：PatchAutoApprover 里早有 is_stale/_is_stale_by_age 实现，
        但该类 deprecated 且**零生产调用** → 过期检测此前从未真正生效。
        ★无可用时间戳 → 视为未过期（不误伤历史补丁）。
        """
        _now = time.time()
        for _k in ("created_at", "submitted_at", "timestamp", "generated_at"):
            _t = patch.get(_k)
            if isinstance(_t, (int, float)) and _t > 0:
                return (_now - _t) / 86400.0 > _m54_stale_days()
        return False

    def approve_patch_by_id(self, patch_id: str, by: str = "human:unknown",
                            reason: str = "",
                            basis: dict | None = None,
                            judged_by: str | None = None) -> dict[str, Any]:
        """★480 刀5：审批入口 —— 整段「读-定位-改-写」在 pending 唯一写口锁内。

        为什么必须整段加锁（而不是只锁写）：`write_pending` 已把**写**串行化，
        但本方法的流程是「读 pending → 算下标 → approve_patch（内部**再读一次**）
        → 改 → 写」。整段在锁外时，任何并发写者（入队/收敛/归档）都能用旧快照
        把它盖回去 —— 活体 2026-11-09 实测「同一件批准成功 3 次、落盘仍是
        verified」，3 次把当日额度吃光，当天其余补丁全部 daily_cap。

        锁是 `RLock`：`approve_patch → _save_patch_list → write_pending`
        会再次获取同一把锁，同线程重入不会死锁。

        开关 `M480_APPROVE_LOCK`（缺省开，显式 False ⇒ 逐字回到加锁前）。
        """
        if not PatchManager._m480_approve_lock_on():
            return self._m480_approve_by_id_unlocked(
                patch_id, by=by, reason=reason, basis=basis, judged_by=judged_by)
        try:
            from nucleus.evolution.pending_write import PENDING_WRITE_LOCK as _lk480
        except Exception as _e480lk:
            silent_exc(_e480lk,
                       where="PatchManager.approve_patch_by_id 480锁",
                       level="debug")
            return self._m480_approve_by_id_unlocked(
                patch_id, by=by, reason=reason, basis=basis, judged_by=judged_by)
        with _lk480:
            return self._m480_approve_by_id_unlocked(
                patch_id, by=by, reason=reason, basis=basis, judged_by=judged_by)

    @staticmethod
    def _m480_approve_lock_on() -> bool:
        """★480 刀5 灰度（缺省开；显式 False ⇒ 逐字回到加锁前）。"""
        try:
            import config as _c480l
            _v = getattr(_c480l, "M480_APPROVE_LOCK", True)
            if _v is None:
                return True
            if isinstance(_v, str):
                return _v.strip().lower() in ("1", "true", "yes", "on")
            return bool(_v)
        except Exception as _e480lo:
            silent_exc(_e480lo,
                       where="PatchManager._m480_approve_lock_on",
                       level="debug")
            return True

    def _m480_approve_by_id_unlocked(self, patch_id: str, by: str = "human:unknown",
                                     reason: str = "",
                                     basis: dict | None = None,
                                     judged_by: str | None = None) -> dict[str, Any]:
        """★439刀1 的**内部实现**（★480 刀5 起由 `approve_patch_by_id` 加锁后调用）。

        ★439刀1：**按 id** 审批 —— pending 状态变更的**唯一写口**。

        为什么加这个而不是改造 `approve_patch`：既有 `approve_patch(index, signer, ...)`
        被 CLI / 测试多处按**下标**调用，改签名等于改契约（铁律 117）。这里只做
        「id → 下标」的定位，然后**逐字委托**给既有实现 —— 治理门禁
        （`_approve_via` 的 human 签名 + 字段契约）、O3 四审、审美打分、stale 标记、
        `auto_apply_requested` 全部继承，本方法不新增任何放行路径。

        调用方（`nucleus/evolution/approval_bridge.maybe_approve`）只负责判条件，
        **状态只由这里改** —— 这样「桥」改不了状态，「闸」也写不了盘。

        Args:
            patch_id: pending 条目的 `id`。
            by: 签名者，形如 ``human:name`` / ``auto:bridge``（治理门禁会校验前缀）。
            reason: 审批理由（落进返回值，便于事后追溯）。
            basis: O3 四项审核依据（O3 条目必传）。
            judged_by: 判定来源标记。

        Returns:
            ``{"ok": bool, "reason": str, "patch_id": str, ...}``
        """
        _pid = str(patch_id or "").strip()
        if not _pid:
            return {"ok": False, "reason": "patch_id 为空", "patch_id": _pid}
        try:
            _pending = self._load_patch_list(self._pending_file)
        except Exception as _e:
            silent_exc(_e, where="PatchManager.approve_patch_by_id 读 pending", level="warning")
            return {"ok": False, "reason": f"读 pending 失败: {_e}", "patch_id": _pid}
        if not isinstance(_pending, list):
            return {"ok": False, "reason": "pending 不是列表", "patch_id": _pid}
        for _i, _p in enumerate(_pending):
            if not isinstance(_p, dict):
                continue
            if str(_p.get("id") or "") != _pid:
                continue
            # ★440刀2：`human:*` → human 口；其余（auto:bridge / machine:*）→ auto 口。
            #   不猜、不冒充：人工签名只能由 human 前缀走 human 口。
            _src = "human" if str(by).startswith("human:") else "auto"
            _r = self.approve_patch(_i, signer=by, basis=basis,
                                    judged_by=judged_by or "approve_patch_by_id",
                                    source=_src)
            if isinstance(_r, dict):
                _r["patch_id"] = _pid
                if reason:
                    _r["reason_detail"] = reason
            return _r
        return {"ok": False, "reason": f"pending 中找不到 id={_pid}", "patch_id": _pid}

    def approve_patch(self, index: int, signer: str = "human:unknown",
                      basis: dict | None = None,
                      judged_by: str | None = None,
                      source: str = "human") -> dict[str, Any]:
        """将指定待审批补丁的 status 从 verified/pending 提升为 approved（★FIX: 人工审批闭环）

        ★339刀1（E裁决）：O3 条目必须带**四项审核 basis**（缺 ⇒ fail-closed
        拒绝，不静默降级成"普通审批"——那等于把自动派单的前置条件偷偷去掉）。
        非 O3 条目（llm / local_rule）行为**与改前一致**（280刀3：llm 永不自动）。
        """
        pending = self._load_patch_list(self._pending_file)
        if not (0 <= index < len(pending)):
            return {"ok": False, "reason": f"索引越界，共{len(pending)}个待审批补丁"}
        _patch = pending[index]
        if _patch.get("status") == "approved":
            return {"ok": False, "reason": "该补丁已批准"}
        # ★339刀1：O3 走四项审核道（缺 basis ⇒ 直接拒，不降级）
        if PatchManager._m339_is_o3(_patch):
            _r_ok, _r_why, _r_miss = PatchManager._m339_review_items_ok(basis)
            if not _r_ok:
                return {"ok": False, "reason": _r_why, "missing": _r_miss,
                        "hint": "O3 条目请用 approve_o3_patch(basis={...}) 四审核项"}
            _patch["o3_auto_dispatch_ready"] = True
            _patch["requires_confirmation"] = False
        # ★440刀2：source 由调用方给（缺省 human，逐字保持旧行为）。
        #   为什么必须参数化：`_boundary_check` 规定 **human 口 signer 必须以 "human:" 开头**，
        #   且**非 human 口 signer 不得冒充人工** ⇒ `auto:bridge` 走 human 口必被拒。
        #   机器审批只有走 `auto` 口才合法（source ∈ {auto, aging, save, human}）。
        if not PatchManager._approve_via(_patch, source, signer,
                                         judged_by=judged_by, basis=basis):
            return {"ok": False, "reason": "审批被治理门禁拦截（需 human:<名> 签名且字段契约完整）"}
        # ★第54批 T3.2（P1）：过期检测 —— 超过阈值天数的补丁打 stale 标记。
        if _m54_stale_mark_on() and self._is_stale(_patch):
            _patch["stale"] = True
            _module_logger.warning(
                f"[补丁审批] 补丁已过期(>{_m54_stale_days()}天)，标记 stale: "
                f"{_patch.get('file','')}:{_patch.get('method','')}")
        # ★v9.5审美判据（登顶路线图 第四条路）：审批时计算补丁质量分并记录档案（不阻断闭环）
        try:
            from nucleus.evolution.AestheticJudge import get_aesthetic_judge
            _aesthetic = get_aesthetic_judge().score(_patch.get("modified_code"))
            _patch["aesthetic_score"] = _aesthetic
            _patch["aesthetic_grade"] = _aesthetic.get("grade")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ★第53批 T2（P0-补丁2）：检查写入结果 —— 写盘失败（如被 WriteGuard 拦截）
        #   不得返回 ok=True，否则用户误以为审批成功、而后续应用全部跳过。
        _save_ok = self._save_patch_list(self._pending_file, pending)
        if _m53_write_check_on() and not _save_ok:
            _module_logger.error(
                f"[补丁审批] 写入失败，审批未持久化: "
                f"{_patch.get('file','')}:{_patch.get('method','')} path={self._pending_file}")
            return {"ok": False, "approved": 0,
                    "reason": "写入失败（可能被 WriteGuard 拦截）",
                    "error": "写入失败（可能被 WriteGuard 拦截）"}
        # ★480 刀4：**读回目标条目**——只比条数会放过"条数没变、内容被覆盖"。
        #   假成功会让上游记一次额度、而 pending 里 status 仍是 verified，
        #   下一轮重投再"成功"一次 ⇒ 额度被同一件空耗干净（活体 11-09 实测 3 次）。
        if _save_ok and PatchManager._m480_readback_on():
            _rb_ok, _rb_why = self._m480_verify_approved_on_disk(_patch)
            if not _rb_ok:
                _module_logger.error(
                    "[480刀4][审批读回] 落盘后校验未通过，按失败返回（不记额度）: "
                    "%s:%s why=%s", _patch.get("file", ""),
                    _patch.get("method", ""), _rb_why)
                return {"ok": False, "approved": 0,
                        "reason": f"落盘读回校验失败: {_rb_why}",
                        "error": "write_readback_mismatch"}
        _module_logger.info(
            f"[补丁审批] index={index} {_patch.get('file','')}:{_patch.get('method','')} "
            f"grade={_patch.get('aesthetic_grade','?')}")
        _reason = _patch.get("reason", "")
        _grade = _patch.get("aesthetic_grade")
        if _grade in ("C", "D"):
            _reason = f"{_reason}（审美判定:{_grade}）"
        # ★第54批 T3.3（P1）：auto_apply_enabled 在审批侧生效 ——
        #   False（默认）→ 只审批不应用；True → 置 auto_apply_requested，
        #   ★真正改写源码仍由 SafeEvolutionExecutor 安全门决定，此处不越权。
        _auto_apply = _m54_auto_apply_on()
        if _auto_apply:
            _patch["auto_apply_requested"] = True
        # ★405刀3：审批通过 = 对那条提议的"决定" ⇒ 发 ConsentEvent.DECIDE。
        #   放在这里而不是 `_approve_via` 内部：`_approve_via` 是治理门禁的底层
        #   实现，批量/单条/O3 三条道都会经过它，在那里发会重复计数；此处只覆盖
        #   "单条审批确实成功"这一条路径，与 `approve_all_patches` 各自记账。
        try:
            from nucleus.const import ConsentEvent as _CE405d
            _emit_consent(_CE405d.DECIDE, {
                "proposal_id": str(_patch.get("id") or ""),
                "decision": "accept",
                "signer": str(signer or ""),
                "source": "PatchManager.approve_patch",
            }, priority=5)
        except Exception as _e405d:
            silent_exc(_e405d,
                       where="PatchManager::approve_patch 405决策发射",
                       level="debug")
        return {"ok": True, "file": os.path.basename(_patch.get("file", "")),
                "reason": _reason, "auto_apply": _auto_apply,
                "stale": bool(_patch.get("stale"))}

    def approve_all_patches(self, signer: str = "human:unknown") -> dict[str, Any]:
        """批准所有待审批补丁（★FIX: 人工审批闭环）"""
        pending = self._load_patch_list(self._pending_file)
        _count = 0
        for _p in pending:
            if _count >= 10:
                break  # ★第129批 T-129a：单次≤10硬上限，堵「一次点名全放行」
            if _p.get("status") != "approved":
                # ★339刀1：批量审批**跳过 O3**——四项审核必须逐条给底座，
                #   "一次点名全放行"在 O3上不成立（与 129批 单次≤10 同向）。
                if PatchManager._m339_is_o3(_p):
                    _module_logger.info(
                        "[339审批] 批量审批跳过 O3 条目（四项审核须逐条裁决）: %s",
                        str(_p.get("id") or "")[:24])
                    continue
                if not PatchManager._approve_via(_p, "human", signer):
                    continue
                # ★405刀3：批量道同样逐条发 DECIDE（与单条道同一口径，
                #   区别只在 signer 来源与触发入口）。
                try:
                    from nucleus.const import ConsentEvent as _CE405b
                    _emit_consent(_CE405b.DECIDE, {
                        "proposal_id": str(_p.get("id") or ""),
                        "decision": "accept",
                        "signer": str(signer or ""),
                        "source": "PatchManager.approve_all_patches",
                    }, priority=5)
                except Exception as _e405b:
                    silent_exc(_e405b,
                               where="PatchManager::approve_all_patches 405决策发射",
                               level="debug")
                # ★v9.5审美判据：批量审批同样记录质量分（不阻断闭环）
                try:
                    from nucleus.evolution.AestheticJudge import get_aesthetic_judge
                    _aesthetic = get_aesthetic_judge().score(_p.get("modified_code"))
                    _p["aesthetic_score"] = _aesthetic
                    _p["aesthetic_grade"] = _aesthetic.get("grade")
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                _count += 1
        if _count:
            # ★第53批 T2（P0-补丁2）：检查写入结果（同 approve_patch）
            _save_ok = self._save_patch_list(self._pending_file, pending)
            if _m53_write_check_on() and not _save_ok:
                _module_logger.error(
                    f"[补丁审批] 批量审批写入失败，审批未持久化: "
                    f"count={_count} path={self._pending_file}")
                return {"ok": False, "approved": 0,
                        "reason": "写入失败（可能被 WriteGuard 拦截）",
                        "error": "写入失败（可能被 WriteGuard 拦截）"}
        return {"ok": True, "approved": _count}

    def _check_patch_path(self, patch: dict[str, Any]) -> tuple[bool, str]:
        """★7-2 / P1-8修复（P1，2026-09-05）：补丁目标路径沙箱（CWE-22 路径穿越防护）。

        背景：
            patch["file"] 在整条补丁链路上被**直接使用、全程零校验**——
                :311  shutil.copy2(patch["file"], tmp_target)   验证侧复制
                :317  open(patch["file"])                       读取源文件
                :1236 shutil.copy2(patch["file"], backup_path)  应用前备份
                :1282 写回 modified_code 到源文件               真正落地
                :1468 shutil.copy2(backup_path, patch["file"])  失败回退
            而 :308 的 `os.path.relpath()` **不会消除 `..`**：
                relpath("../../etc/passwd", "/proj") → "../../etc/passwd"
                join(tmp_dir,   "../../etc/passwd") → 跳出临时目录
            于是补丁只要把 file 写成越界路径，就能读写项目目录外的任意文件。

        防护策略（三重）：
            ① 扩展名白名单：仅允许 .py，杜绝写配置/数据/凭证文件
            ② realpath 归一化：消除 `..`、`.` 与符号链接后再判定
            ③ 项目根前缀校验：commonpath 必须等于项目根，越界即拒绝

        为什么只在此处拦截：
            _verify_in_copy 是补丁**落地前的唯一必经关卡**
            （apply_all_pending:1212 与对外契约 verify_in_copy:1511 都汇聚于此），
            在此单点拦截即可覆盖全部四处危险使用点，无需改动任何既有写文件逻辑，
            符合最小侵入原则。

        边界与容错：
            · Windows 跨盘符时 commonpath 抛 ValueError，按越界拒绝
            · 任何异常一律「拒绝」而非「放行」，失败即闭合（fail-closed）
        """
        _raw = str(patch.get("file", "") or "")
        if not _raw:
            return False, "补丁缺少 file 字段"

        # ① 扩展名白名单
        if not _raw.lower().endswith(".py"):
            return False, f"非法扩展名(仅允许 .py): {_raw}"

        # ② Windows 盘符显式比对（跨平台处理）。
        #    补丁的 file 字段可能由 LLM 生成而写成 Windows 风格（X:\...）。
        #    在 Linux 上 os.path.isabs("C:\\...") 返回 False，它会被当成
        #    普通相对文件名挂进项目根从而绕过越界判定；在 Windows 上虽会走
        #    绝对路径分支，但提前显式比对可让判定与运行平台无关。
        import re as _re
        _m = _re.match(r"^([A-Za-z]):[\\/]", _raw)
        if _m:
            _rm = _re.match(r"^([A-Za-z]):[\\/]", str(self._project_root))
            if not _rm or _m.group(1).lower() != _rm.group(1).lower():
                return False, f"路径越界(跨盘符): {_raw}"

        try:
            _root = os.path.realpath(self._project_root)
            # ② realpath 归一化（绝对路径直接解析，相对路径先挂到项目根）
            _abs = os.path.realpath(
                _raw if os.path.isabs(_raw) else os.path.join(self._project_root, _raw))
            # ③ 必须落在项目根目录内
            if os.path.commonpath([_abs, _root]) != _root:
                return False, f"路径越界(不在项目根目录内): {_raw} → {_abs}"
        except ValueError as _m428_e:
            # Windows 跨盘符（C:\ vs D:\）无法求公共路径
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_check_patch_path L3696", level="warning")
            return False, f"路径越界(跨盘符): {_raw}"
        except Exception as _e:
            # 判定失败一律拒绝，绝不因异常而放行
            silent_exc(_e, where="nucleus/reasoning/PatchManager.py::_check_patch_path L3699", level="warning")
            return False, f"路径校验异常(已拒绝): {_raw} ({_e})"

        return True, ""

    def _verify_in_copy(self, patch: dict[str, Any]) -> dict[str, Any]:
        """在项目副本中测试补丁，完全隔离不影响主框架"""
        import tempfile

        result = {
            "passed": False,
            "stage": "init",
            "errors": [],
            "logs": "",
        }

        # ★7-2：路径沙箱——补丁链路上所有写操作的第一道闸。
        #   必须在任何 open()/copy2() 之前执行，否则越界补丁已经完成读写。
        _path_ok, _path_reason = self._check_patch_path(patch)
        if not _path_ok:
            result["errors"].append(f"补丁路径校验失败: {_path_reason}")
            result["stage"] = "path_check_failed"
            result["failed_check"] = "path"
            # ★第87批 T-87b：原告警只有原因，无法从日志判断是「哪一类补丁」出的问题
            #   —— 实测 09-18~09-20 共 19 条**完全相同**的文案，定位一次要翻整条
            #   补丁链路。补上来源/类型/方法/file 实值/键名清单，一眼可辨
            #   「字段缺失（file=''）」还是「值非法（路径越界/扩展名）」。
            _module_logger.warning(
                f"[补丁验证] 路径沙箱拒绝: {_path_reason} "
                f"（source={patch.get('source', '')!r}, "
                f"issue_type={patch.get('issue_type', '')!r}, "
                f"method={patch.get('method', '')!r}, "
                f"file={patch.get('file', '')!r}, "
                f"keys={sorted(str(_k) for _k in patch)}）")
            return result

        # ★469刀1：验证侧的同一道核对（与 apply_all_pending 同判据）。
        #   本方法还有第二个入口 `verify_in_copy`（公开封装，供 SafeEvolutionExecutor /
        #   代码学习 在**生成侧**调用）⇒ 在生成侧就能把「补丁对不上现源码」拦下来，
        #   不消耗一次「建临时目录 + 整文件 copy2 + 语法 + 导入」的完整验证。
        _m469_vpre = _m469_source_precheck(patch, self._project_root)
        if _m469_vpre.get("kind") not in ("ok", "guard_off"):
            result["errors"].append(
                _m469_vpre.get("detail") or "源码一致性核对不通过")
            result["stage"] = "source_precheck_failed"
            result["failed_check"] = _m469_vpre.get("kind")
            result["source_precheck"] = _m469_vpre
            print(f"[469刀1] 副本验证前源码核对不通过({_m469_vpre.get('kind')}): "
                  f"{os.path.basename(str(patch.get('file', '')))}."
                  f"{patch.get('method', '')}")
            return result

        # ★策略自进化扩展(长期深化): 验证深度按历史策略效果 + 风险分级动态标记。
        #   - 历史规则策略失败率高（rule_bad）或核心文件 → verify_depth="deep"（深验证）；
        #   - 其余 → "standard"（标准验证）。
        #   deep 仅影响验证强度标记/行为等价强制/审计，不改标准验证链判定（零冲突）。
        _verify_depth = "standard"
        try:
            _hist = self.get_strategy_effectiveness(patch.get("issue_type", ""))
            _norm_f = (patch.get("file") or "").replace("\\", "/")
            _core_markers = ("main.py", "config.py", "base/", "nucleus/pulse/",
                             "nucleus/field/", "nucleus/mnemosyne/", "nucleus/reasoning/")
            _is_core = any(m in _norm_f for m in _core_markers)
            if _hist.get("rule_bad") or _is_core:
                _verify_depth = "deep"
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_verify_in_copy L3749", level="warning")
            _verify_depth = "standard"
        result["verify_depth"] = _verify_depth

        # ★PULSE-DEFECT-20260901-01 方案B：LLM 补丁完整性护栏（验证第一关）。
        # 从源头拦截残缺/截断的补丁（如仅剩方法开头 2%~12%、被截断的代码），
        # 避免「语法恰好合法但内容严重残缺」的补丁通过后续语法/导入关混入队列。
        _completeness = self._check_llm_patch_completeness(patch)
        if not _completeness["complete"]:
            # ★427刀3：生成侧拒收也要计入 (file, method) 冷却（详见
            #   `_m427_note_method_fail` docstring 的「真实缺口」段）。
            if _m427_cooldown_wire_on():
                _m427_note_method_fail(patch, _completeness["reason"])
            result["errors"].append(f"补丁完整性检查失败: {_completeness['reason']}")
            result["stage"] = "completeness_check_failed"
            result["failed_check"] = "completeness"
            return result

        tmp_dir = tempfile.mkdtemp(prefix="tongtong_patch_test_")
        try:
            # 1. 创建副本
            target_file_rel = os.path.relpath(patch["file"], self._project_root)
            tmp_target = os.path.join(tmp_dir, target_file_rel)
            os.makedirs(os.path.dirname(tmp_target), exist_ok=True)
            shutil.copy2(patch["file"], tmp_target)

            # 2. 应用补丁到副本
            with open(patch["file"], encoding='utf-8') as orig:
                full_content = orig.read()

            if patch["original_code"] not in full_content:
                result["errors"].append("目标代码片段在源文件中未找到，无法应用补丁")
                result["stage"] = "code_not_found"
                result["failed_check"] = "in_copy_match"
                return result

            # ★第92批 T-92c（防御性，默认关闭）：验证侧「基础缩进相等」结构化关。
            #   原位整段替换对缩进一字不改 ⇒ base 不一致会把类体/函数体提前终止，
            #   且替换后**语法仍合法**（既有语法/编译/导入关全部放行）。
            #   第91批只在构造侧对齐，本关是验证侧兜底。
            #   开关 ENABLE_M92_PATCH_BASE_INDENT_GUARD（默认关闭，先观察一批）。
            if _m92_base_indent_guard_on():
                _bo92 = _m92_base_indent(patch.get("original_code", ""))
                _bm92 = _m92_base_indent(patch.get("modified_code", ""))
                if _bo92 >= 0 and _bm92 >= 0 and _bm92 != _bo92:
                    result["errors"].append(
                        f"基础缩进不一致（未替换）: original={_bo92} modified={_bm92} "
                        f"（原位替换会把类体/函数体提前终止，且语法仍合法）")
                    result["stage"] = "base_indent_guard_failed"
                    result["failed_check"] = "indent"
                    PatchManager._m92_rejected_count += 1
                    _module_logger.warning(
                        f"[补丁验证] 基础缩进结构化关拒绝: original={_bo92} "
                        f"modified={_bm92}（source={patch.get('source', '')!r}, "
                        f"file={patch.get('file', '')!r}, "
                        f"method={patch.get('method', '')!r}）")
                    return result

            modified_full = full_content.replace(patch["original_code"], patch["modified_code"])

            # ★第92批 T-92d（防御性，默认关闭）：验证侧「AST 结构不变量」关。
            #   第91d 事故：LLM 补丁把类方法改写成模块级函数（base 8 → 0），
            #   ast.parse / compile / py_compile / import **全部放行**
            #   ——「语法合法 ≠ 结构未退化」。
            #   判据：类方法总数缩水 > _M92_STRUCT_SHRINK_RATIO（10%）即拒绝。
            #   开关 ENABLE_M92_PATCH_AST_STRUCT_GUARD（默认关闭，先观察一批）。
            if _m92_ast_struct_guard_on():
                _st92 = _m92_ast_struct_guard(full_content, modified_full)
                if _st92 and not _st92["ok"]:
                    result["errors"].append(_st92["reason"])
                    result["stage"] = "ast_structure_guard_failed"
                    result["failed_check"] = "ast_structure"
                    PatchManager._m92_rejected_count += 1
                    _module_logger.warning(
                        f"[补丁验证] 结构不变量关拒绝: 类方法 {_st92['before']} → "
                        f"{_st92['after']}（-{_st92['ratio']:.1%}）（"
                        f"file={patch.get('file', '')!r}, "
                        f"method={patch.get('method', '')!r}）")
                    return result

            with open(tmp_target, 'w', encoding='utf-8') as f:
                f.write(modified_full)

            result["stage"] = "patch_applied_to_copy"

            # 3. 语法校验
            try:
                with open(tmp_target, encoding='utf-8') as f:
                    _src = f.read()
                ast.parse(_src, filename="<llm-patch>")
                # ★多入口验证: compile() 走完整编译，捕获 ast.parse 不报但编译期
                #   报错的情况（future 语句位置/编码/重复等），作为第二道语法入口。
                compile(_src, tmp_target, 'exec')
                result["stage"] = "syntax_check_passed"
            except SyntaxError as e:
                # ★第92批 T-92a（同批一致性，_m92_t92a_step3_label）：补齐异常类名，
                #   与关2 文案同口径 —— 否则整份副本语法失败仍无法从日志区分
                #   `TabError` / `IndentationError` / 其他。
                result["errors"].append(f"语法错误({type(e).__name__}): {e}")
                result["stage"] = "syntax_check_failed"
                result["failed_check"] = "syntax"
                return result

            # 4. 编译验证（py_compile，不执行模块级代码，避免框架文件模块级副作用导致误判）
            # ★2026-09-03修复：原exec(open(file).read())会执行整个文件，
            #   框架文件有大量模块级导入/初始化代码，在临时目录中必然失败，
            #   导致所有补丁验证都不通过。改为py_compile只做字节码编译检查。
            import py_compile
            try:
                py_compile.compile(tmp_target, doraise=True)
                result["stage"] = "compile_check_passed"
                result["passed"] = True
            except py_compile.PyCompileError as _ce:
                result["errors"].append(f"编译失败: {_ce}")
                result["stage"] = "compile_check_failed"
                result["failed_check"] = "syntax"
                return result
            except Exception as _e:
                result["errors"].append(f"编译验证异常: {_e}")
                result["stage"] = "compile_check_error"
                result["failed_check"] = "syntax"
                return result

            # 5. ★T5(PHASE9) import 检查——补丁修改后的副本能否正常 import（沙箱联动）。
            #    星轨 PHASE9 要求第 2/5 项：副本验证必须含 import 检查，且用
            #    execute_code_in_subprocess() 执行（CPU 30s / 内存 512MB 限制），
            #    不得在主进程直接执行补丁代码。
            #    语义：import「应用了补丁的副本」(tmp_target)，而非原文件。
            #    补丁只改单个文件的方法体，import 该副本即同时验证了「语法 +
            #    顶层可执行 + 依赖可解析」，比 py_compile 深一层（真实触发 import 链）。
            #    失败即 fail-closed：passed=False，标记 stage，不进入待审批。
            _import_check = self._verify_import_in_subprocess(patch, tmp_target)
            result["import"] = _import_check
            if not _import_check.get("ok"):
                result["passed"] = False
                result["stage"] = "import_check_failed"
                result["failed_check"] = "import"
                result["errors"].append(
                    f"import检查失败: {_import_check.get('reason', '未知')}")
                _module_logger.warning(
                    f"[补丁验证] import检查失败({patch.get('file','')}): "
                    f"{_import_check.get('reason', '')}")
                return result

            # 6. ★进化增强：真实回归测试（轻量验证脚本，退出码 0=通过）
            #    原验证仅做语法+导入，可能放行「能导入但行为损坏」的补丁。
            #    此处运行项目自带的轻量验证脚本，作为行为回归的兜底。
            #    ★策略自进化扩展: deep 验证对 LLM 补丁做二次完整性复核（防深验证放行残缺补丁）。
            if _verify_depth == "deep" and patch.get("repair_source") == "llm":
                try:
                    _recheck = self._check_llm_patch_completeness(patch)
                    if not _recheck.get("complete"):
                        result["passed"] = False
                        result["stage"] = "completeness_check_failed"
                        result["failed_check"] = "completeness"
                        result["errors"].append(
                            f"深验证-二次完整性复核失败: {_recheck.get('reason', '')}")
                        return result
                    result["deep_recheck"] = "completeness_ok"
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _regression = self._run_regression_tests()
            if _regression["passed"]:
                result["passed"] = True
                result["stage"] = "regression_test_passed"
                result["regression"] = _regression["summary"]
                # ★验证深度升级(黄项收敛): 行为等价探针——纯函数场景运行前后对比。
                #   非纯函数/无法生成输入 → checked=False（跳过，不影响原验证链，零误伤）。
                #   行为不等价 → 判定失败（stage=behavior_mismatch），交自我反思闭环换策略重试。
                # ★策略自进化扩展: deep 验证时若无法行为等价（非纯函数），追加深验证说明
                #   （供人工审计关注；判定本身不受影响，零冲突）。
                _behavior = self._behavior_equivalence_probe(patch)
                result["behavior"] = _behavior
                if _behavior.get("checked") and not _behavior.get("equivalent"):
                    result["passed"] = False
                    result["stage"] = "behavior_mismatch"
                    result["failed_check"] = "behavior"
                    result["errors"].append(
                        f"行为等价验证失败: {_behavior.get('reason', '')}")
                    return result
                if _verify_depth == "deep" and not _behavior.get("checked"):
                    result["deep_notes"] = (
                        "深验证: 非纯函数无法行为等价，补丁行为安全依赖"
                        "回归+完整性验证，建议人工关注")
                # ★多入口验证(跨版本/结构护栏): 方法签名一致性——补丁声称修改的
                #   method 在修改后仍存在且参数签名与修改前一致，防止补丁误改签名
                #   导致调用方崩溃（被修改方法缺失 → 判定失败）。
                _sig = self._check_method_signature_consistency(patch, _src)
                result["signature"] = _sig
                if _sig.get("checked") and not _sig.get("consistent"):
                    result["passed"] = False
                    result["stage"] = "signature_mismatch"
                    result["failed_check"] = "signature"
                    result["errors"].append(
                        f"方法签名一致性验证失败: {_sig.get('reason', '')}")
                    return result
            else:
                # ★B156-4 修正（补丁验证空转·断链点②）：原实现在回归验证**失败**时
                #   只记 warning、绝不改判 result["passed"]（即"0通过/1失败 仅记录不影响判定"），
                #   导致「验证工具本身损坏/缺失」也被静默放行 → 补丁在**未经真实验证**下被批准，
                #   与 _clean_llm_code 38 次语法错 / avg_fix_rate=0.0039 共同构成"进化空转"。
                #   改判规则（接线点：regression_verified）：
                #   ① 脚本**运行崩溃（非功能性失效：Traceback / ModuleNotFoundError）**
                #      → 验证工具本身损坏，fail-closed 改判 passed=False
                #      （绝不能基于"未被验证"批准补丁）。注意：脚本**缺失(missing)** 不在此列——
                #      缺失属于配置/可选态（如隔离测试用的伪 _project_root 下本就无该脚本），
                #      按 PHASE9「全局脚本不影响具体补丁」语义归入 ② 分支（记录+信号、不阻断），
                #      否则会误杀「开关全关」红测试（test_m92 G1）所依赖的零回归契约；
                #   ② 脚本**缺失或跑通但发现无关回归** → 保留 PHASE9 语义，仍不改判 passed，
                #      但显式标记 regression_verified=False 并经报告总线发出
                #      PATCH_REAL_FIX_RATE_LOW（"补丁验证仍处空转"），结束静默遮掩。
                result["regression"] = _regression["summary"]
                result["regression_verified"] = False
                _module_logger.warning(
                    f"[补丁验证] 全局回归脚本失败(仅记录，不影响判定): {_regression['summary']}")
                _non_functional = "ModuleNotFoundError" in _regression["summary"] \
                    or "Traceback" in _regression["summary"]
                if _non_functional:
                    # 验证工具运行崩溃：fail-closed，不得基于未验证状态批准补丁。
                    result["passed"] = False
                    result["stage"] = "regression_unavailable"
                    result["failed_check"] = "regression"
                    result["errors"].append(
                        f"补丁验证脚本运行崩溃(不可用)，无法验证补丁: {_regression['summary']}")
                else:
                    # 脚本缺失或跑通但发现无关回归：保留 PHASE9 语义（不改判 passed）。
                    result["regression_warning"] = (
                        f"全局回归脚本有失败({_regression['failed']}个{'/缺失' if _regression.get('missing') else ''})，"
                        f"与具体补丁无关，仅记录参考: {_regression['summary']}")
                # ★B156-4：补丁验证失败 → 经报告总线发出 PATCH_REAL_FIX_RATE_LOW 出口，
                #   由 evolution_anomaly_consumer 写 alerts.jsonl(needs_human=True)，
                #   使"验证空转"从静默日志变为可观测告警。
                try:
                    from nucleus.reporting.publishers import publish_patch_verification_failed
                    publish_patch_verification_failed(
                        _regression["summary"],
                        generator="PatchManager._run_regression_tests",
                        severity="P0" if _non_functional else "P1",
                        extra={"non_functional": _non_functional,
                               "failed": _regression.get("failed", 0),
                               "missing": _regression.get("missing", [])})
                except Exception as _e:
                    _module_logger.debug(
                        f"PATCH_REAL_FIX_RATE_LOW 出口失败(忽略): {type(_e).__name__}: {_e}")

            # ★多入口验证: 汇总本补丁实际执行的验证入口（体现验证深度，供审计）。
            _entries = [k for k in ("completeness", "syntax", "compile", "import", "regression")
                        if result.get(k) is not None or k in ("syntax", "import")]
            result["verify_entries"] = _entries + (["behavior"] if result.get("behavior") else []) \
                + (["signature"] if result.get("signature") else [])
            result["cross_version_note"] = (
                "当前环境仅 Python 3.12 单解释器，多环境/跨版本 CI 受环境限制未落地；"
                "以 compile 编译验证 + 方法签名结构护栏作为本地跨入口替代。")
            if result.get("passed"):
                # ★日志补全: 补丁验证通过摘要进后台日志（深度+入口），便于离线审计实际运行。
                _module_logger.info(
                    f"[补丁验证] {patch.get('file','')}:{patch.get('method','')} "
                    f"{result.get('stage','')} depth={result.get('verify_depth','')} "
                    f"entries={result.get('verify_entries')}")
        except Exception as e:
            result["errors"].append(f"验证异常: {e}")
            result["stage"] = "exception"
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return result

    def _verify_import_in_subprocess(self, patch: dict[str, Any],
                                     copy_path: str) -> dict[str, Any]:
        """★T5(PHASE9)：import 检查——补丁修改后的副本能否正常 import（沙箱联动）。

        星轨 PHASE9 要求第 2/5 项：
          - 副本验证必须包含 import 检查（补丁修改的文件能正常 import）；
          - 必须用 execute_code_in_subprocess() 执行，CPU 30s / 内存 512MB 限制；
          - 不得直接在主进程执行补丁代码。

        实现语义：
          - import 的目标是「应用了补丁的副本文件」(copy_path)，而非原文件；
          - 用 importlib.util.spec_from_file_location 从副本路径加载模块，
            并把项目根注入 sys.path，使副本里的框架依赖（from nucleus.xxx import）
            能正常解析——既验证补丁文件本身，也验证其依赖可导入性；
          - import 成功 + 子进程 stderr 无 traceback/ERROR → ok=True；
          - 资源超限（wall_clock/cpu/memory）→ ok=False（fail-closed）；
          - 任何异常 → ok=False，绝不放行。

        与「无新增ERROR」的联动：
          import 会触发副本文件及其依赖的模块级代码，若补丁引入运行时错误
          （如模块级 NameError / 类型错误），会在子进程 stderr 留下 traceback，
          此处检测 stderr 中的 Traceback/Error 关键字作为「无新增ERROR」判据。
        """
        try:
            from nucleus.security.sandbox_limits import execute_code_in_subprocess
        except Exception as _imp_err:
            silent_exc(_imp_err, where="nucleus/reasoning/PatchManager.py::_verify_import_in_subprocess L4044", level="warning")
            return {"ok": False, "reason": f"沙箱模块不可用: {_imp_err}"}

        # 副本文件必须真实存在（_verify_in_copy 已生成 tmp_target）。
        if not os.path.isfile(copy_path):
            return {"ok": False, "reason": f"副本文件不存在: {copy_path}"}

        # 模块名：用副本文件的绝对路径推导，保证 spec_from_file_location 的 name 稳定唯一。
        # 例：/tmp/tongtong_patch_test_xxx/nucleus/reasoning/PatchManager.py
        #     → 模块名取文件名（去掉 .py），避免与真实模块冲突。
        _module_name = "patch_copy_" + os.path.basename(copy_path).replace(".py", "")

        # 子进程执行代码：注入项目根（绝对路径）到 sys.path（副本里 from nucleus.xxx /
        # import config 等依赖），再用 spec_from_file_location 从副本路径加载模块并 exec。
        # ★关键：self._project_root 可能是相对路径（如 '.'），而子进程 cwd 是沙箱临时
        # 目录，相对路径会指向错误位置。必须用 os.path.abspath 归一化为绝对路径，
        # 否则副本里 `import config` 会 ModuleNotFoundError，正常补丁被误拒。
        _proj_root_abs = os.path.abspath(self._project_root)
        # 副本路径也归一化为绝对路径（spec_from_file_location 需要真实可定位的路径）。
        _copy_path_abs = os.path.abspath(copy_path)
        # 顶层 try/except 捕获所有异常并 exit(3)，把失败信息写入 stderr。
        _code = (
            "import sys, importlib.util\n"
            f"sys.path.insert(0, {_proj_root_abs!r})\n"
            "try:\n"
            f"    _spec = importlib.util.spec_from_file_location({_module_name!r}, {_copy_path_abs!r})\n"
            "    _mod = importlib.util.module_from_spec(_spec)\n"
            "    _spec.loader.exec_module(_mod)\n"
            "    print('IMPORT_OK', flush=True)\n"
            "except Exception as _e:\n"
            "    import traceback\n"
            "    traceback.print_exc()\n"
            "    sys.stderr.write('IMPORT_FAIL: ' + repr(_e) + '\\n')\n"
            "    sys.exit(3)\n"
        )

        try:
            _res = execute_code_in_subprocess(
                _code,
                timeout_seconds=30,          # 墙上时钟 30s（CPU 硬上限另设 30s）
                # ★472刀2：白名单环境。不传时 subprocess 会继承父进程全部环境，
                #   其中 PULSE_FRAMEWORK=1 会让 write_guard 把这批「被验证的陌生代码」
                #   当可信写入者（可写 data/）。对齐 ScriptExecutor.py 的做法。
                child_env=_m472_build_child_env(),
                allow_full_builtins=True,    # import 需要完整 builtins + import 能力
                source_label=f"patch_import_check:{_module_name}",
            )
        except Exception as _e:
            silent_exc(_e, where="nucleus/reasoning/PatchManager.py::_verify_import_in_subprocess L4087", level="warning")
            return {"ok": False, "reason": f"沙箱执行异常: {_e}"}

        # 资源超限 → fail-closed。
        if _res.limit_hit:
            _limit_reason = {
                "wall_clock": "墙上时钟超时(30s)",
                "cpu": "CPU 时间超限(30s)",
                "memory": "内存超限(512MB)",
            }.get(_res.limit_hit, _res.limit_hit)
            return {"ok": False, "reason": f"import 检查资源超限: {_limit_reason}",
                    "sandbox": {"limit_hit": _res.limit_hit,
                                "peak_memory_bytes": _res.peak_memory_bytes,
                                "duration_ms": _res.duration_ms}}

        _stderr = _res.stderr or ""
        _stdout = _res.stdout or ""

        # 判定：returncode==0 且 stdout 有 IMPORT_OK 标记。
        _import_ok = _res.returncode == 0 and "IMPORT_OK" in _stdout

        # 「无新增ERROR」判据：子进程 stderr 出现 Traceback 或 ERROR 级日志 → 视为新增错误。
        # ★主线第30批 T2 评估：此处解析的是**子进程 stderr 原始输出**（无 `[器官] LEVEL:`
        #   格式保证），**不复用** `extract_log_level`——宽松子串判据在此是**保守正确**的
        #   （宁可误判为"有错误"而拒绝补丁，也不放过真实错误）。保留原实现。
        _has_traceback = "Traceback" in _stderr
        _has_error = "ERROR" in _stderr.upper()

        if _import_ok and not _has_traceback and not _has_error:
            return {"ok": True, "reason": f"副本 {_module_name} import 成功",
                    "sandbox": {"duration_ms": _res.duration_ms,
                                "peak_memory_bytes": _res.peak_memory_bytes}}

        # 失败路径：尽量给出精确原因。
        if not _import_ok:
            _tail = (_stderr or _stdout)[-400:]
            return {"ok": False,
                    "reason": f"副本 {_module_name} import 失败: {_tail}",
                    "sandbox": {"returncode": _res.returncode}}
        if _has_traceback or _has_error:
            _tail = _stderr[-400:]
            return {"ok": False,
                    "reason": f"副本 {_module_name} import 产生新ERROR: {_tail}"}
        return {"ok": False, "reason": f"副本 {_module_name} import 结果异常"}

    def _check_method_signature_consistency(self, patch: dict[str, Any],
                                           modified_source: str) -> dict[str, Any]:
        """★多入口验证(结构护栏): 补丁声称修改的 method 在修改后仍存在且签名一致。

        用 AST 提取修改前后目标方法/函数的 name + 参数（位置/关键字/默认值数量），
        前后不一致 → consistent=False（说明补丁误改了签名，调用方可能崩溃）。
        无 method 字段 / 方法在修改前不存在 / 解析异常 → checked=False（跳过，零冲突）。
        """
        try:
            _method = patch.get("method") or ""
            _orig = patch.get("original_code") or ""
            if not _method or not _orig or not modified_source:
                return {"checked": False, "consistent": True, "reason": "无需校验"}
            import ast as _ast
            import textwrap as _tw

            def _sig(txt):
                # 带缩进的方法体片段直接 ast.parse 会报 IndentationError，
                # 解析前先 dedent 去统一缩进（与完整性检查一致的处理）。
                _t = _ast.parse(_tw.dedent(txt),
                                filename="<llm-patch>")
                for _n in _ast.walk(_t):
                    if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                            and _n.name == _method:
                        return (_n.name,
                                len(_n.args.posonlyargs),
                                len(_n.args.args),
                                len(_n.args.kwonlyargs),
                                _n.args.vararg is not None,
                                _n.args.kwarg is not None)
                return None

            _before = _sig(_orig)
            _after = _sig(modified_source)
            if _before is not None and _after is None:
                # 修改前存在、修改后消失 → 方法被删除/改名，调用方必然崩溃 → 判不一致。
                return {"checked": True, "consistent": False,
                        "reason": f"方法 {_method} 在修改后不存在（被删除或改名），调用方可能崩溃"}
            if _before is None or _after is None:
                return {"checked": False, "consistent": True,
                        "reason": f"方法 {_method} 在修改前/后无法定位，跳过签名校验"}
            if _before != _after:
                return {"checked": True, "consistent": False,
                        "reason": f"方法 {_method} 签名被修改: {_before} → {_after}"}
            return {"checked": True, "consistent": True,
                    "reason": f"方法 {_method} 签名一致"}
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_check_method_signature_consistency L4178", level="warning")
            return {"checked": False, "consistent": True, "reason": "签名校验异常，跳过"}

    def _check_llm_patch_completeness(self, patch: dict[str, Any]) -> dict[str, Any]:
        """★PULSE-DEFECT-20260901-01 方案B：LLM 补丁完整性静态护栏。

        在副本验证之前，先做四关静态检查，从源头拦截残缺/截断的补丁：
          0) 字段契约（★第90批 T-90a 新增）：original_code / modified_code 必须存在
             且非空。缺失时报**字段名**，不再由关3 以「相似度过低(0.00)」伪装成
             「内容残缺」；且本关与灰度开关无关，结构性生效；
          1) 修改内容非空、且与原文有实质差异；
          3) 与原文的相似度不低于阈值（第89批 T-89a 起默认 0.3，可开关回退 0.5），
             拦截「仅剩 2%~12%」的严重残缺；
          3b) 长度下限：修改后代码不得短于原文的 1/3（第89批 T-89a 新增，
             与关3 并列的独立判据，防阈值下调放行「长方法被删掉大半」。

        返回 {"complete": bool, "reason": str}。complete=False 时 reason 说明失败原因。
        """
        _original = patch.get("original_code", "")
        _modified = patch.get("modified_code", "")
        # ★PULSE-DEFECT-20260902-01 修复: 真实补丁的 original/modified 是「带缩进的方法体片段」
        #   （get_method_body 返回原样缩进），直接 ast.parse 会报 unexpected indent 误拦全部
        #   真实补丁，导致自主修复验证链在完整性第一关被卡死。
        #   修复: 语法解析与相似度对比前统一 textwrap.dedent（缩进仅结构上下文，不影响判定）。
        import textwrap as _tw
        # ★427刀1：顺序改为「先剥围栏行 → 再 dedent → 最后 strip」。
        #   421 版是「先 dedent → 再剥围栏」，实测失效机制：围栏行通常顶格，
        #   `textwrap.dedent` 的公共缩进前缀被它拉成空串 ⇒ 整段代码保持原缩进、
        #   dedent 等于没做（65 条 2a 标本里 52 条正文带 ``` 围栏）。
        #   ★零回归：`_m427_strip_fence_lines` 对无围栏输入是恒等变换，
        #     故无围栏时新旧两式都等价于 `dedent(text).strip()`，逐字节相同。
        _orig_norm = _tw.dedent(_m427_strip_fence_lines(_original or "")).strip()
        _mod_norm = _tw.dedent(_m427_strip_fence_lines(_modified or "")).strip()

        # 关0：字段契约（★第90批 T-90a 新增）
        #   把「字段缺失」与「内容残缺」分开报因 —— 文案把「字段没传」伪装成
        #   「LLM 生成残缺」，是本项目反复踩过的「错理由掩盖真因」模式
        #   （同型处置见第87批 `_llm_no_file`、第88批 `_llm_bad_material`）。
        #   ★零回归证明：空字段在原实现下**必然被拒**（关3 的
        #     ratio(("", mod)) == 0.0 恒小于任何启用阈值 0.3/0.5），
        #     本关只改「拒绝的理由」，不改任何通过/拒绝结论。
        if _m90_patch_field_contract_on():
            _miss = _m90_missing_patch_fields(patch)
            if _miss:
                return {
                    "complete": False,
                    "reason": (f"补丁缺少必填字段 {'、'.join(_miss)}"
                               "（补丁字段契约不完整，非内容残缺；"
                               "请检查补丁构造点是否传入 original_code/modified_code）"),
                }


        # 关1：非空 + 实质差异
        if not _mod_norm or _mod_norm == _orig_norm:
            return {"complete": False, "reason": "修改内容为空或与原文完全一致"}

        # 关2a：try 块平衡（★376刀4）—— 摆在关2 之前，比 ast.parse 更早给出
        #   **可执行**的原因（缺几处、缺在第几行、片段末行长什么样）。
        #   只拒「try 多于 handler」与「有 handler 无 try」两种**必然语法非法**形态，
        #   故对既有全部通过的补丁零影响（裁决等价性见 tests/test_m376_d4_try_balance.py）。
        _tb376 = _m376_try_balance(_mod_norm)
        # ★427刀2：先判「片段本身能不能解析」，再谈 try 平不平衡。
        #   为什么必须前移：`data/audit/failed_patches/` 65 条 2a 标本里 49 条的
        #   根因是 `SyntaxError: expected 'except' or 'finally' block`
        #   （生成器把片段截断在 try 中间），可日志只写「try 块不平衡」——
        #   读起来像"补丁逻辑写错了"，实际要查的是上下文窗口/输出上限。
        #   两者处置完全不同，故把真因写在 reason **最前面**。
        #   措辞策略：try 统计降级成括号里的补充诊断（原文案保留 ⇒ 既有断言
        #   不破、信息不丢），只在**确实也不平衡**时才附上。
        if _m427_parse_precheck_on():
            _parse427 = _m427_parse_fragment(_mod_norm)
            if not _parse427["ok"]:
                _why427 = f"生成片段不完整（围栏/截断）：{_parse427['detail']}"
                if _tb376["state"] in ("unbalanced", "orphan"):
                    # ★427刀2 修（回归 m376 暴露）：这里必须接 376 的**整段**富文案，
                    #   不能只抄两个计数 —— 那样拒收理由有了"真因"却丢了"定位"
                    #   （第几行、缺几处收尾、拦截口径全没了），等于从一个误导
                    #   换到另一个误导。信息只增不减：先真因，后定位。
                    _why427 += f"（另：{_m376_try_balance_reason(_tb376)}）"
                _dump427 = _m377_dump_failed_patch(
                    patch, _why427, stage="2a",
                    root=getattr(self, "_project_root", None), tb=_tb376)
                if _dump427.get("written"):
                    _module_logger.info(
                        "[427刀2] 片段不完整已留档（不再由 try 平衡闸背锅）: "
                        "patch=%s file=%s method=%s detail=%s",
                        str(patch.get("id") or "")[:24], patch.get("file"),
                        patch.get("method"), _parse427["detail"][:90])
                else:
                    _module_logger.warning(
                        "[427刀2] 片段不完整留档未成功（拒收结论不受影响）: "
                        "patch=%s why=%s", str(patch.get("id") or "")[:24],
                        _dump427.get("why"))
                return {"complete": False, "reason": _why427}
        if _tb376["state"] in ("unbalanced", "orphan"):
            _why376 = _m376_try_balance_reason(_tb376)
            # ★377刀3：正文落独立审计件（只加写盘，**不改裁决**）——
            #   返回值只用于留痕，判据分支与 reason 一字不变。
            _dump377 = _m377_dump_failed_patch(
                patch, _why376, stage="2a",
                root=getattr(self, "_project_root", None), tb=_tb376)
            if _dump377.get("written"):
                _module_logger.info(
                    "[377刀3] 关2a 失败补丁正文已留档: patch=%s file=%s method=%s "
                    "bytes=%s truncated=%s",
                    str(patch.get("id") or "")[:24], patch.get("file"),
                    patch.get("method"), _dump377.get("bytes"),
                    _dump377.get("truncated"))
            else:
                # 留档失败必须可观测，否则「有没有留档」与「没走到这一关」同形
                _module_logger.warning(
                    "[377刀3] 关2a 失败补丁正文留档未成功（拒收结论不受影响）: "
                    "patch=%s why=%s", str(patch.get("id") or "")[:24],
                    _dump377.get("why"))
            return {"complete": False,
                    "reason": _why376}

        # 关2：语法可解析（dedent + 虚拟函数包装，兼容带缩进/含 return 的方法体片段）
        #   ★PULSE-DEFECT-20260902-01: 方法体片段含 return，dedent 后顶层 return 非法，
        #   需包装进虚拟函数才能正确解析；完整方法（def 开头）包装后为嵌套 def 亦合法。
        # ★427刀2：判据改由 `_m427_parse_fragment` 统一实现（与上面预检同一份）。
        #   预检开着时本关必然通过（同输入同结论），属**幂等防御**；
        #   预检关掉（灰度回退）时本关就是唯一入口，文案格式与 421 版逐字一致
        #   （★第92批 T-92a 的 `语法错误(<类名>): <msg>` 未变）。
        _parse427b = _m427_parse_fragment(_mod_norm)
        if not _parse427b["ok"]:
            return {"complete": False, "reason": _parse427b["detail"]}

        # 关3：相似度阈值（拦截严重残缺/截断；dedent 后对比保证可比）
        #   ★PULSE-DEFECT-20260902-01: 用整字符串对比（不用 splitlines——单行方法体
        #   时 SequenceMatcher 对 [A] vs [B] 恒为 0.0，误拦所有单行补丁）。
        #   ★第89批 T-89a: 阈值由硬编码 0.5 改为 _m89_min_similarity()（默认 0.3，
        #   开关 ENABLE_M89_PATCH_SIM_THRESHOLD 关闭 → 0.5，逐字回到改造前）。
        _min_sim = _m89_min_similarity()
        try:
            import difflib
            _ratio = difflib.SequenceMatcher(None, _orig_norm, _mod_norm).ratio()
            if _ratio < _min_sim:
                return {
                    "complete": False,
                    "reason": f"与原文相似度过低({_ratio:.2f} < {_min_sim:g})，疑似严重残缺/截断",
                }
        except Exception as e:
            silent_exc(e, "nucleus/reasoning/PatchManager.py:1944", level="warning")
            # 相似度计算失败不阻断验证（保守放行，交给后续语法/导入关）

        # 关3b：长度下限（★第89批 T-89a 新增，与关3 并列的**独立**判据）
        #   为什么必须补：补丁是**整段替换**（PatchManager._verify_in_copy 的
        #       modified_full = full_content.replace(original_code, modified_code)），
        #   而关3 的相似度是**对称**指标——modified 远短于 original 时
        #       ratio ≈ 2*len(mod) / (len(orig)+len(mod))
        #   ⇒ 只看相似度无法区分「合法的局部改写」与「被截断的残缺」。
        #   实测（2026-09-20 15:25:47）被判 ratio=0.09 的失败样本，
        #   按上式反推 modified 仅剩原文 **4.7%**（= 真截断）。
        #   阈值降到 0.3 后，len(mod) ≥ 3/17*len(orig)（≈17.6%）即可过闸：
        #   对长方法（PulseLiver._periodic_purity_check 17089 字符）等于放行
        #   「删掉约 82% 方法体」的补丁 ⇒ 必须补一条显式长度下限。
        #   ★零安全回归证明：原 0.5 阈值下任何过闸补丁恒满足
        #       len(mod) ≥ len(orig)/3
        #   （M ≤ len(mod) ⇒ ratio ≤ 2*len(mod)/(len(orig)+len(mod))，
        #     令其 ≥ 0.5 即得 len(mod) ≥ len(orig)/3）
        #   ⇒ 本关**不会**拒绝任何「原阈值下可通过」的补丁，只堵住阈值下调新开的口子；
        #     对「同长度但重叠较少」的合法改写则完全放行（这正是 T-89a 的目的）。
        #   开关关闭时 _m89_len_floor() 返回 0.0 → 本关不生效。
        _len_floor = _m89_len_floor()
        if _len_floor > 0 and _orig_norm:
            if len(_mod_norm) < len(_orig_norm) * _len_floor:  # _M89_LEN_FLOOR_FIXED
                return {
                    "complete": False,
                    "reason": (
                        f"修改后代码长度不足({len(_mod_norm)} < "
                        f"{len(_orig_norm)} * {_len_floor:.3f})，疑似截断/残缺"),
                }

        return {"complete": True, "reason": ""}

    def _behavior_equivalence_probe(self, patch: dict[str, Any]) -> dict[str, Any]:
        """★验证深度升级(黄项收敛): 补丁前后方法行为等价探针。

        仅对「无外部依赖的纯函数」方法生效（零副作用、无 self 依赖、仅内置函数）：
          1) 从源文件解析目标方法完整定义（含签名）；
          2) 分别用 original_code / modified_code 替换方法体，构造前后两个可调用版本；
          3) 用代表性输入实际运行，比较返回值（浮点 isclose，其余严格 ==）。

        非纯函数 / 无法生成输入 / 执行异常 → checked=False（跳过，不影响原验证链，零误伤）。
        行为不等价 → {"checked": True, "equivalent": False, "reason": 差异说明}。

        Returns: {"checked": bool, "equivalent": bool, "reason": str, "samples": int}
        """
        try:
            _file = patch.get("file", "")
            _method = patch.get("method", "")
            _orig = patch.get("original_code", "")
            _mod = patch.get("modified_code", "")
            if not (_file and _method and _orig and _mod):
                return {"checked": False, "equivalent": True, "reason": "缺方法信息"}

            with open(_file, encoding="utf-8") as _f:
                _src = _f.read()
            _tree = ast.parse(_src, filename="<llm-patch>")
            _target = None
            for _n in ast.walk(_tree):
                if isinstance(_n, ast.FunctionDef) and _n.name == _method:
                    _target = _n
                    break
            if _target is None:
                return {"checked": False, "equivalent": True, "reason": "目标方法不存在"}
            if isinstance(_target, ast.AsyncFunctionDef):
                return {"checked": False, "equivalent": True, "reason": "异步方法跳过"}

            # ---- 纯函数判定 ----
            # 收集参数名
            _params = {_a.arg for _a in (_target.args.posonlyargs + _target.args.args
                                         + _target.args.kwonlyargs)}
            # 收集局部名（赋值/for/import/def/class/with/except 目标）
            _locals = set(_params)

            def _add_locals(_node: ast.AST) -> None:
                for _c in ast.walk(_node):
                    if isinstance(_c, ast.Name) and isinstance(_c.ctx, (ast.Store, ast.Del)):
                        _locals.add(_c.id)
                    elif isinstance(_c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        _locals.add(_c.name)
                    elif isinstance(_c, (ast.Import, ast.ImportFrom)):
                        for _a in _c.names:
                            _locals.add(_a.asname or _a.name.split(".")[0])
                    elif isinstance(_c, ast.ExceptHandler) and _c.name:
                        _locals.add(_c.name)

            for _s in _target.body:
                _add_locals(_s)

            # 检测 self 属性访问（依赖实例状态 → 非纯）
            for _c in ast.walk(_target):
                if isinstance(_c, ast.Attribute) and isinstance(_c.value, ast.Name)                         and _c.value.id == "self":
                    return {"checked": False, "equivalent": True, "reason": "依赖 self 状态，非纯函数"}

            # 收集自由名称（Load 上下文，排除参数/局部/builtin）
            _builtins_set = set(dir(builtins))
            _free = set()
            for _c in ast.walk(_target):
                if isinstance(_c, ast.Name) and isinstance(_c.ctx, ast.Load):
                    if _c.id not in _locals and _c.id not in _builtins_set:
                        _free.add(_c.id)
            if _free:
                return {"checked": False, "equivalent": True,
                        "reason": f"依赖外部名称: {sorted(_free)[:5]}"}

            # ---- 构造前后两个可调用版本 ----
            _sig = ast.unparse(_target.args)
            _orig_def = f"def {_method}({_sig}):\n" + "\n".join(
                f"    {_l}" for _l in _orig.splitlines()) if _orig else ""
            _mod_def = f"def {_method}({_sig}):\n" + "\n".join(
                f"    {_l}" for _l in _mod.splitlines()) if _mod else ""
            if not _orig_def or not _mod_def:
                return {"checked": False, "equivalent": True, "reason": "方法体为空"}
            _ns_o: dict[str, Any] = {"__builtins__": builtins.__dict__}
            _ns_m: dict[str, Any] = {"__builtins__": builtins.__dict__}
            try:
                # 安全说明: 此处 exec 对象为框架自身补丁代码(original/modified)，非外部输入；
                #   纯函数判定已拦截一切外部名称依赖(os/sys/...)，命名空间仅含 builtins，
                #   全异常捕获兜底 —— 属受控隔离执行。
                # 受控隔离执行框架自身代码（命名空间仅含 builtins，见上方安全说明）
                exec(compile(_orig_def, "<orig>", "exec"), _ns_o)  # noqa: S102
                exec(compile(_mod_def, "<mod>", "exec"), _ns_m)  # noqa: S102
            except Exception as _e:
                silent_exc(_e, where="nucleus/reasoning/PatchManager.py::_behavior_equivalence_probe L4443", level="debug")
                return {"checked": False, "equivalent": True,
                        "reason": f"构造探针失败(签名引用外部类型?): {_e}"}
            _f_orig = _ns_o[_method]
            _f_mod = _ns_m[_method]

            # ---- 生成代表性输入 ----
            _all_args = _target.args.posonlyargs + _target.args.args
            _defaults = [None] * (len(_all_args) - len(_target.args.defaults))                 + list(_target.args.defaults)

            def _value_candidates(_name: str, _default: Any) -> list[Any]:
                _nm = _name.lower()
                if isinstance(_default, bool):
                    return [_default, not _default]
                if isinstance(_default, int):
                    return [_default, _default + 1, 0]
                if isinstance(_default, float):
                    return [_default, _default * 1.5, 0.0]
                if isinstance(_default, str):
                    return [_default, _default + "x", ""]
                if isinstance(_default, (list, tuple)):
                    return [list(_default), []]
                if isinstance(_default, dict):
                    return [dict(_default), {}]
                # 复数/集合类名词优先 list（避免 values/items/seq 被"val"误判为数值）
                if any(_k in _nm for _k in
                       ("values", "items", "seq", "list", "vec", "arr", "cand",
                        "ids", "keys", "data", "batch", "samples")):
                    return [[], [1.0, 2.0], [1, 2, 3]]
                if any(_k in _nm for _k in
                       ("count", "num", "n_", "_n", "idx", "index", "dim", "size",
                        "times", "round", "len", "loop", "depth", "max", "min")):
                    return [0, 1, 3]
                if any(_k in _nm for _k in
                       ("ratio", "score", "weight", "val", "value", "prob", "temp",
                        "alpha", "beta", "threshold", "rate", "pct")):
                    return [0.0, 0.5, 1.0]
                if any(_k in _nm for _k in
                       ("text", "str", "name", "msg", "query", "word", "token", "keyword")):
                    return ["", "abc", "测试"]
                if any(_k in _nm for _k in ("flag", "enable", "ok", "bool", "active")):
                    return [True, False]
                return [0, 1.0, ""]

            _candidates: list[list[Any]] = []
            for _a, _d in zip(_all_args, _defaults):
                _vals = _value_candidates(_a.arg, _d)
                if not _vals:
                    return {"checked": False, "equivalent": True, "reason": "无法生成代表输入"}
                _candidates.append(_vals)
            _combo: list[tuple] = [tuple(_c[0] for _c in _candidates)]
            for _idx in range(len(_all_args)):
                if len(_candidates[_idx]) > 1:
                    _row = list(_combo[0])
                    _row[_idx] = _candidates[_idx][1]
                    _combo.append(tuple(_row))
            if not _combo:
                return {"checked": False, "equivalent": True, "reason": "无输入样本"}

            # ---- 运行对比 ----
            def _values_equal(_a: Any, _b: Any) -> bool:
                # 数值统一比较：int/float 均按浮点容差（bool 除外，视为类别而非数值）
                if isinstance(_a, (int, float)) and isinstance(_b, (int, float)) \
                        and not isinstance(_a, bool) and not isinstance(_b, bool):
                    return abs(_a - _b) <= 1e-9 or abs(_a - _b) <= 1e-6 * max(1.0, abs(_a), abs(_b))
                if isinstance(_a, (list, tuple)) and isinstance(_b, (list, tuple)):
                    return len(_a) == len(_b) and all(
                        _values_equal(x, y) for x, y in zip(_a, _b))
                if isinstance(_a, dict) and isinstance(_b, dict):
                    return _a.keys() == _b.keys() and all(
                        _values_equal(_a[k], _b[k]) for k in _a)
                return _a == _b

            _checked = 0
            for _args in _combo:
                try:
                    _r_o = _f_orig(*_args)
                except Exception as _e_o:
                    # 原方法抛异常：若修改后也抛 → 行为一致；否则不一致
                    try:
                        _f_mod(*_args)
                    except Exception as e:
                        silent_exc(e, "nucleus/reasoning/PatchManager.py:2215:_behavior_equivalence_probe", level="warning")
                        continue
                    return {"checked": True, "equivalent": False,
                            "reason": f"原抛异常但修改后不抛: {_e_o}"}
                try:
                    _r_m = _f_mod(*_args)
                except Exception as _e_m:
                    silent_exc(_e_m, where="nucleus/reasoning/PatchManager.py::_behavior_equivalence_probe L4531", level="debug")
                    return {"checked": True, "equivalent": False,
                            "reason": f"原正常但修改后抛异常: {_e_m}"}
                _checked += 1
                if not _values_equal(_r_o, _r_m):
                    return {"checked": True, "equivalent": False,
                            "reason": f"返回值不一致: {_r_o!r} vs {_r_m!r}"}

            if _checked == 0:
                # 所有样本均双方抛异常（输入类型与签名不符等）→ 无法验证，不误判等价
                return {"checked": False, "equivalent": True,
                        "reason": "无有效样本(输入类型不匹配)，无法验证"}
            return {"checked": True, "equivalent": True,
                    "reason": "行为等价", "samples": _checked}
        except Exception as _e:
            silent_exc(_e, where="nucleus/reasoning/PatchManager.py::_behavior_equivalence_probe L4545", level="debug")
            return {"checked": False, "equivalent": True, "reason": f"探针异常: {_e}"}

    def _load_history_experience(self, issue_type: str, limit: int = 3) -> str:
        """★上下文记忆(黄项收敛): 从补丁历史加载同类经验（跨会话学习）。

        成功范例（applied/approved）→ 正向参考；失败（verification_failed/rejected）→ 负向警示。
        无同类历史时回退最近历史；无历史/异常 → 返回空串（不影响生成，零冲突）。

        Returns:
            纯文本经验摘要（供 LLM 补丁生成 few-shot 注入）
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return ""
            _refs = [p for p in _history if (p.get("issue_type") or "") == issue_type]
            _same_type = bool(_refs)
            if not _refs:
                _refs = _history  # 无同类 → 回退最近历史（标题用"经验"而非"同类"）
            _ok: list[str] = []
            _bad: list[str] = []
            for _p in reversed(_refs):
                _diff = (_p.get("diff_summary") or "").strip()[:110]
                if not _diff:
                    continue
                _st = _p.get("status", "")
                _err = (_p.get("apply_error") or _p.get("reason") or "")[:90]
                if _p.get("applied") or _st == "approved":
                    _ok.append(f"[成功] {_diff}")
                elif _st in ("verification_failed", "rejected_verify", "rejected_safety") or _err:
                    _bad.append(f"[失败] {_diff} | {_err}")
                if len(_ok) + len(_bad) >= limit * 2 + 1:
                    break
            _lines: list[str] = []
            _ok_label = "历史同类成功修复参考" if _same_type else "历史经验-成功范例"
            _bad_label = "历史同类失败警示" if _same_type else "历史经验-失败警示"
            if _ok:
                _n_ok = min(len(_ok), limit)
                _lines.append(f"{_ok_label}(前{_n_ok}条):\n- "
                              + "\n- ".join(_ok[:_n_ok]))
            if _bad:
                _n_bad = min(len(_bad), limit)
                _lines.append(f"{_bad_label}(前{_n_bad}条):\n- "
                              + "\n- ".join(_bad[:_n_bad]))
            return "\n\n".join(_lines)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_load_history_experience L2284", level="debug")
            return ""

    def get_history_failure_stats(self, limit: int = 100) -> dict[str, Any]:
        """★元认知: 统计历史补丁验证失败 stage 分布（供反思改进与人工审计）。

        Returns: {"total": int, "stages": {stage: count}}（按次数降序）
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return {"total": 0, "stages": {}}
            _stages: dict[str, int] = {}
            _total = 0
            for _p in _history[-limit:]:
                _v = _p.get("verification") or {}
                _st = _v.get("stage", "") or (_p.get("status") or "unknown")
                _stages[_st] = _stages.get(_st, 0) + 1
                _total += 1
            return {"total": _total,
                    "stages": dict(sorted(_stages.items(), key=lambda kv: -kv[1]))}
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::get_history_failure_stats L4613", level="debug")
            return {"total": 0, "stages": {}}

    _STRATEGY_HALF_LIFE = 14 * 86400  # 策略效果时间衰减半衰期（14天）

    @staticmethod
    def _type_family(issue_type: str) -> str:
        """issue_type → 类型家族（跨类型先验分组）。

        家族按关键词分组：exception/resource/timeout/duplicate/syntax/thread/other。
        无历史的目标类型可用同家族其他类型的强信号做冷启动先验（元学习泛化）。
        """
        _t = (issue_type or "").lower()
        _families = (
            ("exception", ("exception", "silent", "as_e", "bare_except", "catch", "raise")),
            ("resource", ("resource", "leak", "unbounded", "buffer", "memory", "deque")),
            ("timeout", ("timeout", "http", "request", "connect")),
            ("duplicate", ("duplicate", "repeat", "idempot")),
            ("syntax", ("syntax", "indent", "parse")),
            ("thread", ("thread", "daemon", "race", "lock", "concurrent")),
        )
        for _fam, _keys in _families:
            if any(k in _t for k in _keys):
                return _fam
        return "other"

    def _aggregate_strategy_buckets(
        self, _history: list[Any], issue_type: str,
        use_family_prior: bool = False) -> tuple[dict[str, Any], str]:
        """聚合策略桶（含时间衰减权重）；返回 (agg[type], prior_source)。

        ★策略自进化深化(元学习): 
          - 时间衰减：新近补丁权重高（半衰期14天，指数衰减）；旧信号弱化——
            框架自我修复后不再误判 rule_bad；
          - 跨类型先验：目标类型无历史时，用同家族其他类型的强信号冷启动（use_family_prior）。
        """
        _now = time.time()
        _agg: dict[str, dict[str, dict[str, Any]]] = {}
        _target_key = issue_type
        for _p in _history:
            _it = _p.get("issue_type") or ""
            _match = (_it == _target_key) or (
                use_family_prior and _it and issue_type
                and self._type_family(_it) == self._type_family(issue_type)
                and _it != issue_type)
            if not _match:
                continue
            if not _it:
                _it = "未标注类型"
            _rs = _p.get("repair_source") or ""
            _bucket = "rule" if _rs in ("local_rule", "llm_upgrade") else                           ("llm" if _rs == "llm" else "other")
            _v = _p.get("verification") or {}
            _passed = bool(_v.get("passed"))
            _st = _p.get("status", "")
            _final_ok = bool(_p.get("applied")) or _st in ("approved", "applied")
            _failed = (not _passed) or _st in ("verification_failed", "rejected_verify",
                                               "rejected_safety")
            # 时间衰减权重（无时间字段→权重1不衰减）
            _w = 1.0
            _ts = _p.get("saved_at") or _p.get("applied_at") or _p.get("updated_at") or 0
            if _ts:
                _dt = _now - float(_ts)
                if _dt > 0:
                    _w = 0.5 ** (_dt / self._STRATEGY_HALF_LIFE)
            _bucket_map = _agg.setdefault(_it, {}).setdefault(
                _bucket, {"total": 0.0, "passed": 0.0, "failed": 0.0, "applied": 0.0,
                          "raw_total": 0, "raw_passed": 0})
            _bucket_map["total"] += _w
            if _passed:
                _bucket_map["passed"] += _w
            if _failed:
                _bucket_map["failed"] += _w
            if _final_ok:
                _bucket_map["applied"] += _w
            _bucket_map["raw_total"] += 1
            if _passed:
                _bucket_map["raw_passed"] += 1
        _prior = ""
        if use_family_prior and issue_type:
            _prior = f"家族[{self._type_family(issue_type)}]先验"
            # 家族先验：合并家族内所有类型的策略桶（跨类型求和），而非只取目标类型
            _merged: dict[str, dict[str, float]] = {}
            for _type_agg in _agg.values():
                for _b_name, _b in _type_agg.items():
                    _m = _merged.setdefault(
                        _b_name,
                        {"total": 0.0, "passed": 0.0, "failed": 0.0, "applied": 0.0,
                         "raw_total": 0, "raw_passed": 0})
                    for _k in _m:
                        _m[_k] = float(_m[_k]) + float(_b.get(_k, 0))
            return (_merged, _prior)
        return (_agg.get(_target_key, {}), _prior)

    def get_strategy_effectiveness(self, issue_type: str = "") -> dict[str, Any]:
        """★策略自进化(元学习·长期深化): 聚合同类问题的「策略-效果」关联。

        按 issue_type 聚合历史补丁的修复策略桶（rule=local_rule/llm_upgrade、llm=llm）：
          - 各策略桶的 total/passed/failed/rate（验证通过率，时间衰减加权）与 applied；
          - rule_bad：规则策略加权 total>=3 且加权失败率>=67% → 建议跳过规则直接 LLM 升级；
          - llm_proven：LLM 策略有 >=1 成功记录（成功证据不衰减）→ 可靠的升级目标。
        ★策略自进化深化(元学习): 目标类型无历史时启用「跨类型家族先验」——用同家族其他
          类型的强信号冷启动（标记 prior 来源，供审计/回退；仅强信号生效，零冲突）。

        Returns:
            {"issue_type", "no_history", "rule_bad", "llm_proven", "strategies",
             "prior", "summary"}
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return {"issue_type": issue_type, "no_history": True, "rule_bad": False,
                        "llm_proven": False, "strategies": {}, "prior": "", "summary": "无补丁历史"}
            _target, _prior = self._aggregate_strategy_buckets(_history, issue_type)
            _rule = _target.get("rule", {})
            _llm = _target.get("llm", {})
            _rule_bad = bool(_rule) and _rule.get("total", 0) >= 2.0                 and _rule.get("failed", 0) / _rule.get("total", 1) >= 0.67
            _llm_proven = bool(_llm) and _llm.get("raw_passed", _llm.get("passed", 0)) >= 1
            # 目标类型无历史 → 跨类型家族先验（冷启动，仅强信号生效）
            if not _target and issue_type:
                _fam_agg, _fam_prior = self._aggregate_strategy_buckets(
                    _history, issue_type, use_family_prior=True)
                if _fam_agg:
                    _frule = _fam_agg.get("rule", {})
                    _fllm = _fam_agg.get("llm", {})
                    _fam_rule_bad = bool(_frule) and _frule.get("total", 0) >= 2.0                     and _frule.get("failed", 0) / _frule.get("total", 1) >= 0.67
                    _fam_llm_proven = bool(_fllm) and _fllm.get("raw_passed", _fllm.get("passed", 0)) >= 1
                    if _fam_rule_bad or _fam_llm_proven:
                        _target = _fam_agg
                        _prior = _fam_prior
                        _rule_bad = _fam_rule_bad
                        _llm_proven = _fam_llm_proven
            _parts: list[str] = []
            for _b_name, _b in sorted(_target.items()):
                _rate = (_b["passed"] / _b["total"]) if _b["total"] else 0.0
                _raw_t = _b.get("raw_total", 0)
                _parts.append(f"{_b_name}:{_raw_t}条({_rate:.0%},应用{_b['applied']:.0f})")
            return {
                "issue_type": issue_type,
                "no_history": not bool(_target),
                "rule_bad": _rule_bad,
                "llm_proven": _llm_proven,
                "strategies": _target,
                "prior": _prior,
                "summary": ("；".join(_parts) if _parts else "无同类历史")
                           + (f"（{_prior}）" if _prior else ""),
            }
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::get_strategy_effectiveness L4759", level="debug")
            return {"issue_type": issue_type, "no_history": True, "rule_bad": False,
                    "llm_proven": False, "strategies": {}, "prior": "", "summary": "异常"}

    def generate_patch_regression_records(self) -> dict[str, Any]:
        """★测试生成(黄项收敛·轻量): 为已应用补丁生成针对性回归测试记录。

        对每个已应用补丁（applied 或 approved）：
          1) modified_code 在源文件中的存在性校验——补丁是否仍生效（未被后续覆盖/回退）；
          2) 若方法为纯函数 → 附加行为等价校验（补丁不应改变方法行为，只修复问题）。

        返回 {"total": int, "passed": int, "records": [...], "summary": str}。
        无历史/无已应用补丁/异常 → 零记录返回（不影响调用方，零冲突）。
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return {"total": 0, "passed": 0, "records": [], "summary": "无补丁历史"}
            _applied = [p for p in _history
                        if p.get("applied") or p.get("status") in ("approved", "applied")]
            if not _applied:
                return {"total": 0, "passed": 0, "records": [],
                        "summary": "无已应用补丁（等待补丁落地后生成回归记录）"}
            _records: list[dict[str, Any]] = []
            _passed = 0
            for _p in _applied:
                _file = _p.get("file", "")
                _mod = _p.get("modified_code", "")
                _rec: dict[str, Any] = {
                    "id": _p.get("id", ""),
                    "file": _file,
                    "method": _p.get("method", ""),
                    "issue_type": _p.get("issue_type", ""),
                    "diff_summary": (_p.get("diff_summary") or "")[:80],
                    "applied_at": _p.get("applied_at") or _p.get("saved_at") or 0,
                    "checks": [],
                }
                # 1) modified_code 存在性（补丁未失效）
                _exists = False
                if _file and _mod and os.path.exists(_file):
                    try:
                        with open(_file, encoding="utf-8") as _f:
                            _content = _f.read()
                        _exists = _mod.strip() in _content
                    except Exception as _m428_e:
                        silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::generate_patch_regression_records L4803", level="debug")
                        _exists = False
                _rec["checks"].append({"name": "modified_code_present", "passed": _exists})
                # 2) 纯函数行为等价（附加，仅 checked=True 时纳入判定）
                if _file and _mod and os.path.exists(_file):
                    try:
                        _behavior = self._behavior_equivalence_probe(_p)
                        _rec["behavior"] = _behavior
                        if _behavior.get("checked"):
                            _rec["checks"].append({
                                "name": "behavior_equivalence",
                                "passed": bool(_behavior.get("equivalent")),
                            })
                    except Exception as e:
                        _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                _ok = bool(_rec["checks"]) and all(_c["passed"] for _c in _rec["checks"])
                if _ok:
                    _passed += 1
                _rec["passed"] = _ok
                _records.append(_rec)
            _summary = f"{_passed}/{len(_records)} 个已应用补丁通过回归"
            return {"total": len(_records), "passed": _passed,
                    "records": _records, "summary": _summary}
        except Exception as _e:
            silent_exc(_e, where="nucleus/reasoning/PatchManager.py::generate_patch_regression_records L4826", level="debug")
            return {"total": 0, "passed": 0, "records": [],
                    "summary": f"生成回归记录异常: {_e}"}

    def _run_regression_tests(self) -> dict[str, Any]:
        """
        ★进化增强：运行项目自带的轻量回归测试，作为补丁行为验证的兜底。

        只运行「快速、有明确退出码、不依赖长期运行」的验证脚本，
        避免每个补丁都触发长耗时压力测试。
        任一脚本退出码非 0 即判定回归失败。

        返回 {"passed": bool, "summary": str, "failed": int}
        """
        # 轻量回归测试清单（相对路径 + 超时秒数）
        # ★第九批 5.2（星轨指出）：原清单指向 tools/test_parquet_stage_a.py 与
        #   tools/verify_patch_coverage.py —— 这两个脚本在仓库里**根本不存在**，
        #   而旧代码遇到不存在就 `continue`，于是结果恒为「0通过/0失败」，
        #   看起来像「回归挂了」，实际是「一个都没跑」。
        #   修复：① 清单改为仓库真实存在的回归脚本，并支持 config 覆盖；
        #         ② 脚本缺失时不再静默跳过，而是显式记进 missing 明细。
        #   （第五批 任务2B 同步清理了 TestGenerator._run_regression_tests 中
        #    指向同一对不存在脚本的清单，两个回归入口现已统一为真实脚本。）
        _default_scripts = ["tools/verify_phase17_1_5.py"]
        try:
            import config as _cfg_mod
            _test_scripts = list(getattr(
                _cfg_mod, 'EVOLUTION_CONFIG', {}).get(
                    "regression_scripts", _default_scripts) or _default_scripts)
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_run_regression_tests L4855", level="debug")
            _test_scripts = list(_default_scripts)
        _timeout = 300  # 主回归脚本较重，60s 不够
        try:
            import config as _cfg_mod2
            _timeout = int(getattr(_cfg_mod2, 'EVOLUTION_CONFIG', {}).get(
                "regression_timeout", _timeout) or _timeout)
        except Exception as e:
            silent_exc(e, "nucleus/reasoning/PatchManager.py:2483", level="warning")
        _passed = 0
        _failed = 0
        _missing = []
        _fail_details = []

        for _rel in _test_scripts:
            _script = os.path.join(self._project_root, _rel)
            if not os.path.exists(_script):
                _missing.append(os.path.basename(_rel))
                continue
            try:
                # ★E1修复（P2，2026-09-05）：子进程 stdio 强制 UTF-8。
                #   本文件的 encoding="utf-8" 只决定**父进程如何解码**收到的字节，
                #   管不到**子进程自己的 stdout 编码**——那由子进程启动时的
                #   locale 决定，Windows 中文环境默认是 GBK。
                #   于是脚本里任何 emoji（✅/❌，见 tools/verify_patch_coverage.py:53）
                #   一 print 就 UnicodeEncodeError 崩溃、退出码非 0。
                #   生产实测：5/5 个补丁的 regression 恒为「0通过/2失败」，
                #   错误信息与本地复现逐字一致——
                #   UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
                #   结果就是整条回归验证数据全废，分不清「补丁有问题」和
                #   「测试脚本打不出 emoji」。此处透传一份改过编码的环境变量即可，
                #   不改动子进程任何业务行为。
                _env = dict(os.environ)
                _env["PYTHONIOENCODING"] = "utf-8"
                # ★第九批 5.2：`python` 未必在 PATH 上（Windows 常见），旧代码
                #   会因此 FileNotFoundError → 计入 failed。改用当前解释器。
                _proc = subprocess.run(  # 有意不检查子进程退出码
                    [sys.executable or "python", _script],
                    capture_output=True, text=True, timeout=_timeout,
                    encoding="utf-8", errors="replace",
                    cwd=self._project_root, env=_env,
                    check=False,   # ★421：显式声明（默认即 False，零行为变化）
                )
                if _proc.returncode == 0:
                    _passed += 1
                else:
                    _failed += 1
                    _tail = (_proc.stderr or _proc.stdout or "")[-200:]
                    _fail_details.append(f"{os.path.basename(_script)}: {_tail}")
            except subprocess.TimeoutExpired:
                _failed += 1
                _fail_details.append(f"{os.path.basename(_script)}: 超时")
            except Exception as _e:
                _failed += 1
                _fail_details.append(f"{os.path.basename(_script)}: {_e}")

        _summary = f"{_passed}通过/{_failed}失败"
        if _missing:
            # 显式暴露「清单里的脚本不存在」，避免再次出现 0通过/0失败 的假象
            _summary += f" | 脚本缺失: {', '.join(_missing)}"
        if _fail_details:
            _summary += " | " + "; ".join(_fail_details[:2])
        return {
            "passed": _failed == 0 and (_passed > 0),
            "summary": _summary,
            "failed": _failed,
            "missing": _missing,
        }

    def _check_patch_safety(self, patch: dict[str, Any]) -> dict[str, Any]:
        """落地前校验信任分/风险等级/冷却时间（A3：与自动审批共用安全门）。
        ★FIX(2026-09-07): 人工批准(status=approved)绕过自动安全门。

        ★第96批 T-96b（N1-④ 烛微第1期）：原实现对 ``status=="approved"``
        **无条件返回 safe**（三关全跳）。问题是 ``approved`` 有两个来源：
          · 人工批准（``approve_patch`` / ``approve_all_patches``）
          · **机器自动批准**（``patch["auto_approved"]=True``，本文件 :861）
        后者本应受信任/风险/冷却三关约束，却因同一个 status 被一并放行。
        ⇒ 按星轨裁决「区分机器/人工」：**仅（无 ``auto_approved`` 且无 ``auto_released`` 标记的）纯人工批准**沿用原放行语义；
          **机器自动批准**（``auto_approved=True``）与**机器放行**（``auto_released=True``）
          均须走完三关（★T-113e② 堵「auto_released 借道」：其无 auto_approved 键，曾借「人工批准」捷径绕过安全门）。
          ★判据来源：``auto_approved`` 在本文件 :861 是**唯一写入点**，
            人工批准路径不写该键 ⇒ 区分度可靠（历史 67 条：5 True / 62 无）。
        """
        try:
            # ★T-123b（僵尸拆弹）：冻结补丁硬门禁——fail-closed，优先于一切安全校验。
            if patch.get("frozen") or patch.get("status") == "frozen":
                return {"safe": False, "reason": "补丁已冻结(T-123b)，禁止自动应用"}
            # ★T-113e②（2026-09-23）：堵「auto_released 借道」。
            #   仅「无任何 auto_* 标记」的纯人工批准才放行；
            #   auto_approved / auto_released 一律走信任/风险/冷却三关。
            if (patch.get("status") == "approved"
                    and not patch.get("auto_approved")
                    and not patch.get("auto_released")):
                return {"safe": True, "reason": "人工批准"}
            import config
            _evo_cfg = getattr(config, 'EVOLUTION_CONFIG', {})
            _trust = patch.get("trust_score", 0)
            _min_trust = _evo_cfg.get("auto_apply_min_trust", 60)
            # ★第九批 B-4（星轨要求）：门槛一刀切 60 时，「加日志/改注释/参数微调」
            #   这类低风险补丁（信任分普遍 30 左右）永远过不去 → 60 个问题 0 修复。
            #   改为按风险分级：低风险走 40 门槛，其余维持 60（开关关闭时行为不变）。
            if getattr(config, "ENABLE_EVOLUTION_EFFECT_VERIFY", False):
                try:
                    from nucleus.evolution.EvolutionEffectVerifier import (
                        EvolutionEffectVerifier as _EV,
                    )
                    # 阈值从 config.EVOLUTION_EFFECT_VERIFY_CONFIG 读（低风险40/高风险60）
                    _min_trust = _EV.trust_threshold_for(patch)
                except Exception as e:
                    silent_exc(e, "nucleus/reasoning/PatchManager.py:2584", level="warning")
                    # 判定失败则维持原门槛，绝不放宽
            if _trust < _min_trust:
                return {"safe": False, "reason": f"信任分数不足({_trust}<{_min_trust})"}
            _risk = patch.get("risk_level", "低")
            _risk_map = {"极低": 1, "低": 2, "中等": 3, "高": 4}
            _max_risk = _evo_cfg.get("auto_apply_max_risk", 2)
            if _risk_map.get(str(_risk), 3) > _max_risk:
                return {"safe": False, "reason": f"风险等级过高({_risk})"}
            _cooldown = _evo_cfg.get("auto_apply_cooldown", 86400)
            _last = getattr(self, "_last_apply_time", 0.0)
            if time.time() - _last < _cooldown:
                return {"safe": False, "reason": f"冷却中({int(_cooldown)}s)"}
        except Exception as _e:
            silent_exc(_e, where="nucleus/reasoning/PatchManager.py::_check_patch_safety L4978", level="warning")
            return {"safe": False, "reason": f"安全校验异常: {_e}"}
        return {"safe": True, "reason": ""}

    def apply_all_pending(self, only_approved: bool = False) -> dict[str, Any]:
        """应用所有待处理补丁，返回应用结果。
        
        Args:
            only_approved: 仅应用 status == "approved" 的补丁。
                           若为 True，则跳过 pending/未审批 的补丁。

        ★第96批 T-96b（N1-③）：**机器自动批准**的补丁在入口统一查总开关。
        落地侧原有三个触发口，均只读 ``status==approved``、不读总开关 ⇒
        侧门得以绕过 ``auto_apply_enabled=False``。此处**一处收口**：
        凡 ``auto_approved=True``（机器批准）的补丁，总开关关闭即拒、
        命中核心文件即拒 ⇒ 三个触发口同时被覆盖。
        ★人工批准（无 ``auto_approved`` 标记）不受影响：按星轨裁决，
          人类的显式批准即为最高授权，不得被自动化开关反向否决
          （否则历史上 62 条人工批准记录将全部不可落地，进化闭环归零）。
        """
        pending = self._load_patch_list(self._pending_file)
        # ★第96批 T-96b：入口收口（机器自动批准专用）
        try:
            _machines = [p for p in pending
                         if isinstance(p, dict) and p.get("auto_approved")
                         and not p.get("auto_released")]
            if _machines:
                _evo96 = getattr(_c96_import(), "EVOLUTION_CONFIG", {})
                if not PatchManager._m80_auto_apply_enabled(_evo96):
                    _dropped = 0
                    for _mp in _machines:
                        _mp["status"] = "pending"
                        _mp["reject_stage"] = "m96_auto_apply_gate"
                        _mp["reject_reason"] = "机器自动批准：总开关关闭"
                        _dropped += 1
                    if _dropped:
                        _module_logger.warning(
                            "[自动审批治理][M96] 总开关关闭，%d 条机器自动批准"
                            "已退回待人工审批", _dropped)
                else:
                    for _mp in _machines:
                        if PatchManager._m80_is_core_file(str(_mp.get("file", ""))):
                            _mp["status"] = "pending"
                            _mp["reject_stage"] = "m96_core_file_gate"
                            _mp["reject_reason"] = "机器自动批准：核心文件禁止"
                            _module_logger.warning(
                                "[自动审批治理][M96] 核心文件机器自动批准已拦截: %s",
                                _mp.get("file", ""))
        except Exception as _g96:
            _module_logger.debug(
                "[自动审批治理][M96] 入口收口异常，按保守处理（不改写队列）: %s: %s",
                type(_g96).__name__, _g96)
        if not pending:
            # ★FIX(问题12): 无待应用补丁时重置重启计数器，避免无意义累计导致永久锁死
            self.reset_restart_counter()
            return {"applied": 0, "failed": 0, "details": [],
                    "stale": 0, "safety_blocked": 0}

        # ★主线第94批 T-94a（P0）：待审批队列老化策略——在审批过滤**之前**执行，
        #   使老化放行的补丁（status→approved）能被下面的 only_approved 过滤捞到。
        #   开关默认关闭 ⇒ `_m94_apply_pending_aging` 立即返回、不触碰任何补丁。
        self._m94_apply_pending_aging(pending)
        # ★339刀1：O3 类老化（降级不删除）——**独立一条**，不靠 M94 总开关
        #   （M94 总开关关着时连函数都不进，把 O3 降级挂在里面就会永远不生效）。
        try:
            _o3_ag = self._m339_o3_aging(pending)
            if isinstance(_o3_ag, dict) and _o3_ag.get("expired"):
                _module_logger.info("[339审批][O3老化] 本轮降级 %s 条",
                                    _o3_ag.get("expired"))
        except Exception as _o3e:
            _module_logger.warning("[339审批][O3老化] 异常（不阻断主流程）: %s: %s",
                                   type(_o3e).__name__, _o3e)
        # ★v24.0新增：审批过滤
        if only_approved:
            _approved_pending = []
            _skipped_count = 0
            _skipped_list = []
            for _p in pending:
                if _p.get("status") == "approved":
                    _approved_pending.append(_p)
                else:
                    _skipped_count += 1
                    _skipped_list.append(_p)
            if _skipped_count > 0:
                _module_logger.info(f"审批过滤：跳过 {_skipped_count} 个未审批补丁")
            pending = _approved_pending
            if not pending:
                # ★FIX(A4): 全部未审批时也重置计数，避免未审批队列导致永久锁死
                self.reset_restart_counter()
                return {"applied": 0, "failed": 0, "details": [], "skipped_unapproved": _skipped_count}
        # ===== 审批过滤结束 =====

        # ★v16.0新增：防循环重启检测（仅在有可应用补丁时自增，A4修复）
        # ★T-113e①（2026-09-23）：棘轮改「冷却可重试 + 遥测」。
        #   原逻辑：计数 > 上限即永久锁死（须人工 reset_restart_counter 才解开）。
        #   现逻辑：超过上限后进入 N 小时冷却窗口；窗口期满放行「重试一次」
        #           （重置棘轮并继续应用），窗口内仍锁死并记 WARNING + 事件遥测。
        #   这样既不撤销「防循环重启」保护，又避免结构性锁死导致补丁永远卡死。
        self._restart_counter = self._load_restart_counter() + 1
        if not self._save_restart_counter(self._restart_counter):
            # ★FIX: 计数写失败时 fail-closed，拒绝应用补丁，避免防循环重启失效导致无限重启
            _module_logger.error("重启计数持久化失败，拒绝自动应用补丁以保证安全")
            return {"applied": 0, "failed": 0, "details": [], "loop_protection": True}
        if self._restart_counter > self._max_restart_count:
            _cooldown_h = self._m113e_restart_cooldown_hours()
            _blocked_at = self._load_restart_blocked_at()
            _now = time.time()
            _cooldown_sec = _cooldown_h * 3600.0
            _elapsed = (_now - _blocked_at) if _blocked_at > 0 else float("inf")
            if _blocked_at > 0 and _elapsed >= _cooldown_sec:
                # ★T-113e① 冷却期满：放行本轮「重试一次」，并重置棘轮
                self.reset_restart_counter()
                _module_logger.warning(
                    f"[棘轮冷却] 重启计数曾达{self._max_restart_count}次上限，"
                    f"冷却窗口{_cooldown_h}h已期满（实隔{int(_elapsed) // 3600}h），"
                    f"放行本轮重试一次并重置棘轮")
                # 不 return —— 继续向下应用补丁
            else:
                # ★T-113e① 仍处冷却/锁死：记 WARNING + 事件遥测，拒绝
                # ★第159批 D-1（T-棘轮冷却续期-1）：仅首次锁死写入冷却起点，
                #   后续拒绝不再刷新 _blocked_at → 冷却倒计时可正常推进至期满放行。
                #   修复前每次拒绝都写 now，导致 _elapsed 恒近 0、冷却永不满期（现网活锁）。
                if _blocked_at == 0:
                    self._save_restart_blocked_at(_now)
                _remain = (int(_cooldown_sec - _elapsed)
                           if _blocked_at > 0 else int(_cooldown_sec))
                _module_logger.warning(
                    f"重启次数已达{self._max_restart_count}次上限，停止自动应用"
                    f"（冷却剩余约{_remain // 3600}h{(_remain % 3600) // 60}m，"
                    f"请手动检查补丁）")
                self._m113e_emit_restart_telemetry(_cooldown_h, _remain)
                return {"applied": 0, "failed": 0, "details": [], "loop_protection": True}

        results = {"applied": 0, "failed": 0, "details": [],
                   "stale": 0, "safety_blocked": 0}
        history = self._load_patch_list(self._history_file)

        # ★v23.0新增：按文件分组，从后往前应用（避免多补丁行号偏移影响）
        _file_groups = {}
        for _p in pending:
            _file = _p.get("file", "")
            if _file not in _file_groups:
                _file_groups[_file] = []
            _file_groups[_file].append(_p)

        # ★审美判据深化（第四条路·E6）：不同文件间按最高审美分降序应用
        # （先应用审美质量高的补丁，让演化优先沉淀优质代码）；同文件内仍按
        # 行号从后往前，保证多补丁行号偏移正确性不受影响。
        def _patch_aesthetic(p):
            _v = p.get("aesthetic_score")
            if isinstance(_v, dict):
                try:
                    return float(_v.get("total", 0) or 0)
                except (TypeError, ValueError) as e:
                    silent_exc(e, where="nucleus.reasoning.PatchManager::_patch_aesthetic L2806", level="debug")
                    return 0.0
            try:
                return float(_v or 0)
            except (TypeError, ValueError) as e:
                silent_exc(e, where="nucleus.reasoning.PatchManager::_patch_aesthetic L2810", level="debug")
                return 0.0

        _sorted_pending = []
        for _file, _patches in sorted(
                _file_groups.items(),
                key=lambda kv: max(_patch_aesthetic(p) for p in kv[1]),
                reverse=True):
            # 按original_code在文件中的位置排序（从后往前应用）
            _sorted_patches = sorted(_patches, key=lambda p: p.get("line", 0), reverse=True)
            _sorted_pending.extend(_sorted_patches)

        for patch in _sorted_pending:
            try:
                # ★T-123b（僵尸拆弹）：冻结补丁硬门禁——fail-closed 跳过，绝不自动应用。
                if patch.get("frozen") or patch.get("status") == "frozen":
                    _module_logger.info(
                        "[T-123b冻结] 跳过冻结补丁（不自动应用），待人工/烛微预审: %s",
                        patch.get("id"))
                    continue
                # ★主线第80批 T7 (P0-3)：落地前双判定——核心文件且核心红线开关未开，
                #   即使已被标 approved 也拒绝自动应用（防御 legacy/旁路漏网）。
                #   用 allow_core_auto_apply 专用红线（默认 False），而非总开关 auto_apply_enabled。
                # ★471刀1（裁决①）：先算人工豁免，再决定是否拦。
                _m471_core = PatchManager._m80_is_core_file(patch.get("file", ""))
                _m471_exempt = bool(
                    _m471_core
                    and not PatchManager._m80_allow_core_auto_apply()
                    and PatchManager._m471_core_human_exempt(patch))
                if _m471_exempt:
                    _module_logger.info(
                        "[471刀1] 核心文件人工批准豁免(落地): id=%s file=%s method=%s " 
                        "approved_source=%s signer=%s",
                        patch.get("id"), patch.get("file"), patch.get("method"),
                        patch.get("approved_source"), patch.get("approved_signer"))
                if (_m471_core
                        and not PatchManager._m80_allow_core_auto_apply()
                        and not _m471_exempt):
                    patch["applied"] = False
                    patch["apply_error"] = "核心文件免签改写闸门拦截(落地)"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch.get("id"), "file": patch.get("file"),
                        "status": "rejected_core_guard",
                        "reason": "核心文件禁止自动应用，需人工审批",
                    })
                    history.append(patch)
                    continue
                # ★v22.0 P2新增：应用前先通过人格基线校验
                _baseline_check = self._check_personality_baseline(patch)
                if not _baseline_check["safe"]:
                    patch["applied"] = False
                    patch["apply_error"] = f"人格基线校验不通过: {_baseline_check['reason']}"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_by_baseline",
                        "reason": _baseline_check["reason"],
                        "violations": _baseline_check.get("violations", []),
                    })
                    continue
                # ★v22.0 P2新增结束
                # ★FIX(A3): 落地前做信任/风险/冷却三道安全校验（此前只在死代码 _auto_apply_patch 中）
                _safety_check = self._check_patch_safety(patch)
                if not _safety_check["safe"]:
                    patch["applied"] = False
                    patch["apply_error"] = _safety_check["reason"]
                    # ★469刀3：被安全门槛（信任分/风险等级/冷却）永久拒绝的补丁
                    #   不再只是"一次 failed" —— 留下结构化标记与计数，供下游
                    #   一眼分清「补丁烂」「等额度」「安全口径不允许」三类。
                    #   绝不改判据本身：本处只记不改，安全口径零变化。
                    patch["safety_blocked"] = True
                    patch["safety_blocked_reason"] = str(_safety_check["reason"])[:120]
                    results["safety_blocked"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_safety",
                        "reason": _safety_check["reason"],
                    })
                    history.append(patch)
                    continue
                # ★469刀1：应用前「源码一致性核对」。
                #   必须排在 _verify_in_copy 与 create_backup **之前**：
                #   过期的补丁（生成后源码被改过）连验证和备份都不该做。
                #   判定不看字符串，看 original_code 在当前文件中的**实际出现次数**，
                #   因此不受错误文案改版影响（_OBSOLETE_MARKERS 那套是事后按字面匹配）。
                _m469_pre = _m469_source_precheck(patch, self._project_root)
                if _m469_pre.get("kind") not in ("ok", "guard_off"):
                    patch["applied"] = False
                    patch["apply_error"] = _m469_pre.get("detail") or "源码一致性核对不通过"
                    patch["stale_source"] = (_m469_pre.get("kind") == "stale")
                    patch["source_precheck"] = _m469_pre
                    results["stale"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": f"rejected_{_m469_pre.get('kind')}",
                        "reason": patch["apply_error"],
                    })
                    _module_logger.warning(
                        "[469刀1] 应用前源码核对不通过(%s): id=%s file=%s method=%s "
                        "occurrences=%d drift=%s",
                        _m469_pre.get("kind"), str(patch.get("id"))[:16],
                        os.path.basename(str(patch.get("file", ""))),
                        patch.get("method", ""), _m469_pre.get("occurrences", 0),
                        _m469_pre.get("snapshot_drift"))
                    history.append(patch)
                    continue
                if _m469_pre.get("snapshot_drift"):
                    # 文件有其他改动但 original_code 仍在 ⇒ 照常应用，只留痕归因
                    patch["source_drift"] = True
                    _module_logger.info(
                        "[469刀1] 源码指纹已变但目标片段仍在，照常应用: "
                        "id=%s file=%s method=%s",
                        str(patch.get("id"))[:16],
                        os.path.basename(str(patch.get("file", ""))),
                        patch.get("method", ""))
                # ★v24.0新增：应用前强制在副本中验证补丁
                _verify_result = self._verify_in_copy(patch)
                if not _verify_result.get("passed", False):
                    patch["applied"] = False
                    patch["apply_error"] = f"副本验证失败: {_verify_result.get('errors', ['未知错误'])}"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_verify",
                        "reason": patch["apply_error"],
                    })
                    history.append(patch)
                    continue
                # ★v24.0新增结束
                # 1. 备份原始文件（集中式备份）
                _backup_dir = self._backup_manager.create_backup(
                    files=[patch["file"]],
                    reason=patch.get("reason", "自动补丁应用"),
                    source=patch.get("source_diagnosis", "未知来源"),
                )
                if _backup_dir:
                    patch["backup_path"] = _backup_dir
                else:
                    # 备份失败时使用单文件备份兜底
                    backup_path = patch["file"] + f".patch_backup_{int(time.time())}"
                    shutil.copy2(patch["file"], backup_path)
                    patch["backup_path"] = backup_path

                # 2. 应用补丁
                with open(patch["file"], encoding='utf-8') as f:
                    full_content = f.read()

                # ★修复: str.replace 默认替换所有出现位置——original_code 若出现多次
                # 会被静默全部改写。改为校验唯一出现 + 单次替换。
                _occurrences = full_content.count(patch["original_code"])
                if _occurrences > 1:
                    patch["applied"] = False
                    patch["apply_error"] = f"原始代码在文件中出现{_occurrences}次，为避免误改已拒绝（请提供更长上下文唯一定位）"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_ambiguous",
                        "reason": patch["apply_error"],
                    })
                    history.append(patch)
                    continue
                elif _occurrences == 1:
                    modified_full = full_content.replace(patch["original_code"], patch["modified_code"], 1)

                    if patch["file"].endswith(".py"):
                        # ★第93批 T-93a（Q92-3 星轨裁决「确认需要」）：写盘点补结构关。
                        #   第92批的结构关只在 `_verify_in_copy`（**沙箱副本**）生效；
                        #   补丁若绕过沙箱直接落到本函数（写活文件），结构关不生效
                        #   ⇒ 本关是**写盘侧兜底**，与验证侧构成「双闸」。
                        #   ★判据与验证侧**完全一致**：同一对纯函数 + 同一对开关
                        #     （`ENABLE_M92_PATCH_BASE_INDENT_GUARD` /
                        #      `ENABLE_M92_PATCH_AST_STRUCT_GUARD`，均默认关闭）。
                        #     仅 `status` 用 `rejected_*` 前缀，以区分拒绝发生在写盘点。
                        #   T-93c 基础缩进关（同验证侧 T-92c 判据）：
                        if _m92_base_indent_guard_on():
                            _bo93 = _m92_base_indent(patch.get("original_code", ""))
                            _bm93 = _m92_base_indent(patch.get("modified_code", ""))
                            if _bo93 >= 0 and _bm93 >= 0 and _bm93 != _bo93:
                                patch["applied"] = False
                                patch["apply_error"] = (
                                    f"基础缩进不一致（未写入）: original={_bo93} "
                                    f"modified={_bm93}（原位替换会把类体/函数体提前终止，"
                                    f"且语法仍合法）")
                                results["failed"] += 1
                                results["details"].append({
                                    "id": patch["id"], "file": patch["file"],
                                    "status": "rejected_base_indent_guard",
                                    "reason": patch["apply_error"],
                                })
                                _module_logger.warning(
                                    f"[补丁写盘] 基础缩进结构化关拒绝: original={_bo93} "
                                    f"modified={_bm93}（source={patch.get('source', '')!r}, "
                                    f"file={patch.get('file', '')!r}, "
                                    f"method={patch.get('method', '')!r}）")
                                history.append(patch)
                                continue

                        # ★修复: 写入活文件前先做语法复验，避免坏补丁导致下次启动即崩
                        try:
                            ast.parse(modified_full, filename="<llm-patch>")
                        except SyntaxError as _se:
                            patch["applied"] = False
                            patch["apply_error"] = f"补丁应用后语法校验失败（未写入）: {_se}"
                            results["failed"] += 1
                            results["details"].append({
                                "id": patch["id"], "file": patch["file"],
                                "status": "rejected_syntax",
                                "reason": patch["apply_error"],
                            })
                            history.append(patch)
                            continue

                        # ★第93批 T-93a：AST 结构不变量关（同验证侧 T-92d 判据）。
                        #   第91d 事故形态：类方法被 dedent 成模块级函数，
                        #   `ast.parse` / `compile` / `import` **全部放行**
                        #   ——「语法合法 ≠ 结构未退化」。
                        if _m92_ast_struct_guard_on():
                            _st93 = _m92_ast_struct_guard(full_content, modified_full)
                            if _st93 and not _st93["ok"]:
                                patch["applied"] = False
                                patch["apply_error"] = (
                                    f"补丁应用后结构校验失败（未写入）: "
                                    f"{_st93['reason']}")
                                results["failed"] += 1
                                results["details"].append({
                                    "id": patch["id"], "file": patch["file"],
                                    "status": "rejected_ast_structure_guard",
                                    "reason": patch["apply_error"],
                                })
                                _module_logger.warning(
                                    f"[补丁写盘] 结构不变量关拒绝: 类方法 {_st93['before']} → "
                                    f"{_st93['after']}（-{_st93['ratio']:.1%}）（"
                                    f"file={patch.get('file', '')!r}, "
                                    f"method={patch.get('method', '')!r}）")
                                history.append(patch)
                                continue

                    # ★FIX: mkstemp 随机名，避免预测名 TOCTOU/符号链接重定向
                    import tempfile
                    _fd, _tmp_path = tempfile.mkstemp(  # type: ignore[possibly-unbound]
                        dir=os.path.dirname(patch["file"]) or ".",
                        prefix=".patch_", suffix=".tmp")
                    try:
                        with os.fdopen(_fd, 'w', encoding='utf-8') as f:
                            f.write(modified_full)
                        os.replace(_tmp_path, patch["file"])  # type: ignore[possibly-unbound]
                    finally:
                        if os.path.exists(_tmp_path):  # type: ignore[possibly-unbound]
                            try:
                                os.remove(_tmp_path)  # type: ignore[possibly-unbound]
                            except OSError as e:
                                silent_exc(e, "nucleus/reasoning/PatchManager.py:3003:apply_all_pending", level="warning")

                    patch["applied"] = True
                    patch["applied_at"] = time.time()
                    # ★372刀3（裁决 A3）：**真落地**标记。全仓只有"文件已写盘"的
                    #   成功点才置这一个字段：本处（os.replace 已落目标文件）
                    #   与 O3 双标确认通道；沙箱 success **不置**（沙箱绝不 apply 源码）。
                    #   派单侧的 set_status(FIXED) 已改为只认本字段。
                    patch["landed"] = True
                    patch["landed_at"] = patch["applied_at"]
                    # ★447刀2：落地即留痕 —— 修改前/后**完整内容** + 备份/回滚路径。
                    #   位置刻意选在这里：`os.replace` 已落盘（真落地），
                    #   而 `full_content` / `modified_full` 两个对象都还在作用域里
                    #   —— 事后想复算"到底改了什么"，只有这一刻能拿到两侧全文。
                    PatchManager._m447_note_apply(
                        patch, full_content, modified_full,
                        line=patch.get("line"),
                        applied_at=patch["applied_at"])
                    # ★375刀5：可回滚标记改为**实测**后再置。存量实测（375 刀5）：
                    #   applied=76 条**全部**标 True、**全部**有 backup_path，
                    #   但备份物理可读只有 20 条 —— 56 条早被 code_backups 保留策略清掉。
                    #   标记是「写入那一刻的真话」，坏在没有复核口 ⇒ 备份被清后仍标 True，
                    #   把 patch_lifecycle「无回滚能力不得进 activated」的硬点架空。
                    #   这里在置标记的同一时刻判一次物理可读性，新产出的记录不再有假可回滚；
                    #   存量历史账本刀不动（任务书：不改审计池数据）。
                    try:
                        from nucleus.evolution.rollback_audit import (
                            F_UNAVAILABLE_REASON as _M375_RB_REASON,
                        )
                        from nucleus.evolution.rollback_audit import (
                            backup_present as _m375_bp,
                        )
                        _m375_rb_ok, _m375_rb_why = _m375_bp(patch)
                    except Exception as _m375_rbe:
                        # 审计件不可用不得反过来把应用判失败：退回原语义并留痕
                        silent_exc(
                            _m375_rbe,
                            where="PatchManager::apply_all_pending 375刀5 回滚实测",
                            level="warning")
                        _m375_rb_ok, _m375_rb_why = True, "audit_unavailable"
                    patch["rollback_available"] = bool(_m375_rb_ok)
                    if not _m375_rb_ok:
                        patch[_M375_RB_REASON] = _m375_rb_why
                        _module_logger.warning(
                            "[375刀5] 补丁%s 备份不可读（%s），rollback_available 置 False：%s",
                            str(patch.get("id", ""))[:12], _m375_rb_why,
                            str(patch.get("backup_path", ""))[-60:])
                    # ★372刀3（裁决 A3）：FIXED 改由**落地成功**触发 —— 就是这个时刻。
                    #   改前挂在派单循环的沙箱 success 上（沙箱不碰源码），
                    #   审计"已修"提前于落地。这里 os.replace 已落盘，才是真落地。
                    #   复用既有唯一闭环封装（内部只有一个 set_status 调用点），
                    #   不新开调用点、不放宽 _LEGAL_TRANSITIONS、不改 applied 三通道语义。
                    #   闭环写不进去不得反过来把应用判失败（观测不阻断主流程）。
                    try:
                        from nucleus.evolution.patch_execution_dispatch import (
                            _m356_close_loop_fixed as _m372_close_loop,
                        )
                        _m372_close_loop(patch)
                    except Exception as _m372_e:
                        silent_exc(
                            _m372_e,
                            where="nucleus.reasoning.PatchManager::apply_all_pending 372刀3 闭环回写",
                            level="warning")
                    self._last_apply_time = time.time()
                    results["applied"] += 1
                    # ★第159批上A 刀3（O-B7）：补丁生命周期表登记激活态
                    try:
                        from nucleus.evolution.patch_lifecycle import record_activation
                        record_activation(
                            patch.get("id"),
                            risk_level=patch.get("risk_level", "低"),
                            file=patch.get("file", ""),
                            backup_path=patch.get("backup_path", ""),
                            rollback_available=bool(patch.get("rollback_available")),
                            owner=patch.get("owner")
                            or (patch.get("meta") or {}).get("owner", ""),
                            approver=patch.get("approver")
                            or (patch.get("meta") or {}).get("approver", ""),
                            approved_source=patch.get("approved_source", ""),
                            approved_signer=patch.get("approved_signer", ""),
                            approved_at=patch.get("approved_at", 0.0) or 0.0,
                            meta=patch.get("meta"),
                        )
                    except Exception as _pl_e:
                        _module_logger.warning(
                            "[补丁生命周期] 登记激活失败（已忽略，不影响应用）: %s",
                            _pl_e)
                    # ★第96批 T-96c（N2-① 烛微第1期）：applied 前**磁盘复核**。
                    #   账本曾出现 applied=True / effect_verified=True /
                    #   effectiveness=1.0，但目标文件与落地前备份**逐字节相同**
                    #   （2 条假成功）。根因：``applied`` 置位只代表「写盘动作
                    #   已发出」，**从不回读校验** ⇒ 任何静默失败都记为成功。
                    #   ⇒ 此处写盘后立即回读，确认 modified_code 已在磁盘生效。
                    #   ★只读复核，绝不因复核失败回滚已落地内容；
                    #   ★异常一律按「未通过」处理（不静默放行）。
                    patch["disk_verified"] = False
                    try:
                        with open(patch["file"], "r", encoding="utf-8",
                                  errors="ignore") as _df96:
                            _disk96 = _df96.read().replace("\r\n", "\n")
                        _mc96 = str(patch.get("modified_code") or "").replace(
                            "\r\n", "\n")
                        if _mc96 and _mc96 in _disk96:
                            patch["disk_verified"] = True
                            patch["disk_verify_reason"] = ""
                        else:
                            patch["disk_verify_reason"] = (
                                "落地后磁盘未命中 modified_code" if _mc96
                                else "补丁缺 modified_code，无法复核")
                    except Exception as _dve96:
                        silent_exc(_dve96, where="nucleus/reasoning/PatchManager.py::apply_all_pending L5433", level="debug")
                        patch["disk_verify_reason"] = (
                            f"磁盘复核异常: {type(_dve96).__name__}: {_dve96}")
                    if not patch.get("disk_verified"):
                        patch["effect_verified"] = False
                        _module_logger.warning(
                            "[落地复核][M96] 补丁 %s 写盘后磁盘无对应内容 "
                            "⇒ effect_verified=False（%s）",
                            str(patch.get("id", ""))[:12],
                            patch.get("disk_verify_reason", ""))
                    # ★M85-2（第85批 T-85b / D84-1）：应用后**运行时**主动复现。
                    #   第51批设计的最高优先级判据 `reprobe_verdict`（代码级、不受
                    #   日志轮转影响）此前**运行时从未写入**，只被一次性工具与只读
                    #   脚本调用 → `problem_fixed` 因 `baseline_errors=0` 恒 None。
                    #   此处补上运行时接线：写入本字段后，下游 `_apply_split`
                    #   （同函数 :1960）会优先采用它，`problem_fixed` 即可判定。
                    #   ★不阻塞主流程：任何异常只记 DEBUG，应用判定不受影响。
                    try:
                        from nucleus.evolution.patch_active_reprobe import (
                            apply_reprobe as _m85_apply_reprobe,
                        )
                        _m85_rp = _m85_apply_reprobe(patch)
                        _module_logger.debug(
                            "[运行时复现] 补丁%s verdict=%s（作用域=%s）",
                            str(patch.get("id", ""))[:12],
                            (_m85_rp or {}).get("reprobe_verdict", ""),
                            (_m85_rp or {}).get("reprobe_scope", ""))
                    except Exception as _m85_rpe:
                        _module_logger.debug(
                            "[运行时复现] 失败（已忽略，不影响应用）: %s: %s",
                            type(_m85_rpe).__name__, _m85_rpe)
                    # ★第九批 B-4（星轨要求）：补丁落地后做**针对该问题**的效果验证。
                    #   此前只有 compile_check + 副本验证，等于「代码能跑就算修好」，
                    #   问题是否真的消失、功能是否被带坏、性能是否退化，全都没人管。
                    try:
                        import config as _cfg_eff
                        if getattr(_cfg_eff, "ENABLE_EVOLUTION_EFFECT_VERIFY", False):
                            from nucleus.evolution.EvolutionEffectVerifier import (
                                EvolutionEffectVerifier as _EV,
                            )
                            from nucleus.evolution.EvolutionEffectVerifier import (
                                get_effect_verifier,
                            )
                            _verifier = get_effect_verifier()
                            # ① 问题是否消失：以「副本验证通过 + 补丁已落地」为基线信号；
                            #    真正的业务探针由调用方（SafeEvolutionExecutor）补充上报。
                            # ★★主线第47批 T1（P0-2）：语义拆分 —— 不再把
                            #   No-Regression 当成 Problem-Fixed。
                            #   原实现 `problem_gone=bool(passed)` 与
                            #   `function_ok=bool(passed)` **共用同一个值**：
                            #   ``passed`` 只证明"改完没弄坏"（语法/导入/回归），
                            #   却被同时当成"问题真的消失了"。
                            #   → 此处 `problem_gone=None`（**不可判定**），
                            #     真正的业务探针由调用方补充上报后回填。
                            # ★375刀4：`problem_gone` 不再恒 None —— 同一次应用里
                            #   `apply_reprobe()` 刚把复现结论原地写进 patch（上方
                            #   `_m85_apply_reprobe`），那就是真实的问题复查信号。
                            #   `false_pass`（修复前复现不出）映射为 None：无基线≠问题仍在。
                            _m375_pg, _m375_pg_note = _EV.problem_gone_from_reprobe(patch)
                            _m375_func_ok = bool(_verify_result.get("passed"))
                            _verdict = _verifier.verify(
                                patch,
                                {"type": patch.get("issue_type") or patch.get("type", ""),
                                 "file": patch.get("file", ""),
                                 "method": patch.get("method", "")},
                                problem_gone=_m375_pg,
                                function_ok=_m375_func_ok,
                                perf_delta_ms=_m412_perf_delta_ms(patch, self._project_root),
                                # ★428刀3：把 P95 / 内存 / A/B 模式一并带进去存盘。
                                #   为什么单独算一次而不是复用上面那个 float：
                                #   上面那个为兼容旧契约只返回 delta，丢了 P95 与内存。
                                perf_detail=_m428_perf_detail(patch, self._project_root),
                            )
                            # ★428刀3：原文案硬写"perf=未测量"—— 412 之后已经会供数了，
                            #   这句会让读日志的人以为第三维永远是空的。
                            _module_logger.debug(
                                "[375刀4] 效果验证入参 problem_gone=%s（%s）"
                                "function_ok=%s perf=%s",
                                _m375_pg, _m375_pg_note, _m375_func_ok,
                                ("未测量" if _verdict.get("perf_delta_ms") is None
                                 else (f"{_verdict.get('perf_delta_ms'):+.3f}ms/"
                                       f"p95={_verdict.get('perf_p95_ms')}/"
                                       f"mem={_verdict.get('perf_mem_mb')}")))
                            _verifier.apply_trust_adjustment(patch, _verdict)
                            # ★第47批：写入语义拆分字段（no_regression / problem_fixed /
                            #   verification_granularity / effectiveness），
                            #   `verified` 保留为 no_regression（deprecated）。
                            try:
                                from nucleus.evolution.patch_verification_split import (
                                    apply_split as _apply_split,
                                )
                                from nucleus.evolution.patch_verification_split import (
                                    is_split_override_locked,
                                )
                                # ★165批B1：幂等保护 —— 既有修正标记为真时，
                                #   不让我方 apply_split 覆盖其 problem_fixed 判定。
                                if is_split_override_locked(patch):
                                    _module_logger.debug(
                                        "[语义拆分] 跳过写回（problem_fixed_corrected 为真，幂等保护）"
                                        f": {patch.get('id', '')[:12]}")
                                else:
                                    _apply_split(patch)
                            except Exception as _se:  # 拆分失败不阻断应用闭环
                                _module_logger.warning(
                                    f"[语义拆分] 写入失败（已忽略）: {_se}")
                            # ② 修复登记：供后续复发检测比对
                            _verifier.mark_fixed(_EV.issue_signature(
                                {"type": patch.get("issue_type") or patch.get("type", ""),
                                 "file": patch.get("file", ""),
                                 "method": patch.get("method", "")}))
                            _module_logger.info(
                                f"[效果验证] 补丁{patch['id'][:12]} 已应用并通过验证: "
                                f"{_verdict.get('reason', '')}")
                    except Exception as e:
                        # ★411刀2：这条分支就是任务书说的"吞异常"处 —— 实测它**不是静默**
                        #   （本来就在打 warning），但**没有上下文**：不知道是哪个补丁、
                        #   断在哪一步。加 patch_id + 阶段 + 异常类型，才能与
                        #   `patch_history.effect_verify_state="missing"` 对上账。
                        _pid_411 = str(patch.get("id") or "")[:24]
                        _module_logger.warning(
                            "[411刀2][效果验证失败] patch_id=%s stage=verify_or_writeback "
                            "err=%s: %s ⇒ 该补丁将保持 effect_verify_state=missing"
                            "（这是**验证未完成**，不是「验证不通过」）",
                            _pid_411, type(e).__name__, str(e)[:200])
                        try:
                            patch["effect_verify_error"] = f"{type(e).__name__}: {str(e)[:180]}"
                        except Exception as _e_mark:
                            silent_exc(_e_mark,
                                       where="PatchManager::apply effect_verify_error标记",
                                       level="debug")
                    # ★审美判据深化（E6）：C/D级补丁附审美提示，不阻断闭环
                    _aes_note = None
                    if patch.get("aesthetic_grade") in ("C", "D"):
                        _aes_note = (
                            f"审美{patch.get('aesthetic_grade')}级(评分{_patch_aesthetic(patch):.0f})，"
                            f"已应用但建议后续优化代码质量"
                        )
                    _apply_detail = {
                        "id": patch["id"], "file": patch["file"],
                        "status": "applied", "backup_path": patch.get("backup_path", ""),
                        # ★N-3①②（157批）：透出验证结论供 main 重启闸门判定
                        "effect_verified": bool(patch.get("effect_verified")),
                        "reprobe_verdict": patch.get("reprobe_verdict"),
                    }
                    if _aes_note:
                        _apply_detail["aesthetic_note"] = _aes_note
                    results["details"].append(_apply_detail)
                else:
                    patch["applied"] = False
                    patch["apply_error"] = "原始代码未找到，可能已被修改"
                    results["failed"] += 1

                # ★446刀2：基线字段随 patch 带进 history（并留 baseline_carried_at）
                try:
                    _m446_c = PatchManager._m446_carry_baseline_to_history(patch)
                    if _m446_c.get("carried"):
                        _module_logger.info(
                            "[446刀2] baseline_source 已带入 history: id=%s src=%s "
                            "file=%s method=%s",
                            str(patch.get("id") or "")[:16], _m446_c["baseline_source"],
                            patch.get("file", ""), patch.get("method", ""))
                except Exception as _m446_ce:
                    silent_exc(_m446_ce, where="PatchManager::apply 446带基线入账",
                               level="warning")
                history.append(patch)

            except Exception as e:
                print(f"[WARNING] PatchManager.py:1764: {type(e).__name__}: {e}")
                patch["applied"] = False
                patch["apply_error"] = str(e)
                results["failed"] += 1

        # ★Kimi 设计参考借鉴（§9.1/§12.3）：失败计数器——按「文件+类型」累计失败，3 次升级
        # 绝不原样重复同一失败动作：同一问题反复失败即标记需升级（换策略/人工介入）。
        # 零冲突：仅在应用循环结束后统一记账，不改变任何应用/验证/回滚逻辑。
        try:
            from nucleus.reasoning.FailureTracker import get_failure_tracker
            _ft = get_failure_tracker(self._project_root)
            for _p in history:
                _file = _p.get("file", "")
                _ptype = _p.get("type") or _p.get("source_type") or "unknown"
                _sig = _ft.build_signature(_file, str(_ptype))
                if _p.get("applied"):
                    _ft.record_success(_sig)
                else:
                    _ft.record_failure(_sig, _p.get("apply_error") or _p.get("reason") or "应用失败")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★第九批 5.1（星轨要求）：不可恢复的失败补丁就地归档为 obsolete。
        #   背景：apply_all_pending 末尾会把 pending 清空、全部塞进 history，
        #   于是「目标代码片段在源文件中未找到」这类**结构性失效**补丁
        #   （源码早已被后续批次改写，补丁永远不可能对上）会永久躺在
        #   history 里，每次统计修复率都被算成「失败」，60 个问题 0 修复。
        #   处理：命中失效标记的补丁打 obsolete=True 后移入独立归档文件，
        #   不删除（可审计可回滚），也不再计入修复率。
        try:
            _arch = self.archive_obsolete_patches(history)
            if _arch.get("archived", 0):
                _module_logger.info(
                    f"[补丁归档] 已归档 {_arch['archived']} 条失效补丁"
                    f"（原因: {_arch.get('reason_top') or '目标片段不存在'}）")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★165批B1：同-verdict 一致性校验 —— 落盘前检查每个补丁的
        #   problem_fixed 是否与当前 split 规则重算一致，不一致 WARNING + 计数
        #   （此前 43 True / 11 None 的静默不一致由此暴露）。
        try:
            from nucleus.evolution.patch_verification_split import (
                audit_verdict_consistency,
            )
            _inc_cnt, _inc_samples = audit_verdict_consistency(history)
            if _inc_cnt:
                _module_logger.warning(
                    f"[165批B1 一致性校验] 发现 {_inc_cnt} 条 problem_fixed 与重算不一致"
                    f"（示例: {_inc_samples[:5]}），建议核查")
            else:
                _module_logger.info(
                    "[165批B1 一致性校验] 0 条不一致（同 verdict 不再落两值）")
        except Exception as e:  # 校验失败不阻断落盘
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._save_patch_list(self._history_file, history)
        # ★T-131a②：仅应用 approved 时，未审批补丁须写回 pending，
        #   绝不能被 [] 清空（否则未审批补丁在 apply 后永久丢失）。
        if only_approved:
            self._save_patch_list(self._pending_file, _skipped_list)
        else:
            self._save_patch_list(self._pending_file, [])
        # ★v23.0新增：写入人类可读的修改日志
        self._write_readable_log(results, history)
        # ★N-3①②（157批）：不可判定场景禁止整机自重启。
        #   任一已应用补丁缺乏明确效果验证(effect_verified 非真)即视为不可判定，
        #   不再触发 3 秒后整机重启，避免无效/未证实变更引发重启抖动。
        _restart_allowed = results["applied"] == 0 or all(
            (d.get("status") != "applied") or bool(d.get("effect_verified"))
            for d in results.get("details", [])
        )
        results["restart_allowed"] = _restart_allowed
        if not _restart_allowed:
            _module_logger.warning(
                "[N-3①②] 存在验证不可判定的已应用补丁，抑制整机自重启（待人工/活体确认）"
            )
        # ★第302批 刀2（dead_code_after_return · 逻辑未闭环）：
        #   原 `_reflect_on_failed_patches(...)` 调用写在 `return results` **之后** ⇒ 永不执行，
        #   即 196刀2 补的这半根刀实际从未通电。按「逻辑未闭环 ⇒ 补完而非删除」原则前移到
        #   return 之前；开关 ENABLE_PATCH_FAILURE_REFLECTION 默认未设置(=False) ⇒ 移动后仍零行为变化。
        _reflect_on_failed_patches(results.get("details", []), project_root=self._project_root)
        # ★469刀1/刀3：一行看清本轮去向 —— 以前只有 applied/failed 两个数，
        #   「过期」与「安全门槛拒绝」都混进 failed，看不出到底是哪一类卡住。
        _m469_sum = (int(results.get("applied", 0)), int(results.get("failed", 0)),
                     int(results.get("stale", 0)), int(results.get("safety_blocked", 0)))
        if any(_m469_sum):
            _module_logger.info(
                "[469汇总] 落地=%d 失败=%d 源码过期=%d 安全门槛拒绝=%d（共处理 %d 条）",
                _m469_sum[0], _m469_sum[1], _m469_sum[2], _m469_sum[3],
                len(results.get("details", [])))
        return results

    # 结构性失效（重试一万次也不会成功）的失败原因特征串
    _OBSOLETE_MARKERS = (
        "目标代码片段在源文件中未找到",
        "原始代码未找到",
        "目标代码片段未找到",
        "源文件不存在",
        "目标文件不存在",
    )

    def _is_obsolete_patch(self, patch: dict[str, Any]) -> bool:
        """判定补丁是否为结构性失效（非临时失败，重试无意义）。"""
        if patch.get("applied"):
            return False
        if patch.get("obsolete"):
            return False  # 已归档过，避免重复搬运
        # ★469刀1：结构化判据优先 —— `stale_source` 由应用前源码核对直接置位，
        #   不依赖错误文案（文案改一个字就会让字符串匹配失效，铁律 9 同型）。
        if patch.get("stale_source") is True:
            return True
        _err = f"{patch.get('apply_error') or ''}{patch.get('reason') or ''}"
        return any(_m in _err for _m in self._OBSOLETE_MARKERS)

    def archive_obsolete_patches(self, history: list[dict[str, Any]] | None = None
                                 ) -> dict[str, Any]:
        """把结构性失效补丁从 history 移入 patch_history_obsolete.json。

        Args:
            history: 内存中的历史列表；为 None 时从磁盘读一次并回写。
                     传入列表时**原地过滤**（调用方随后落盘），返回归档统计。

        Returns: {"archived": int, "kept": int, "reason_top": str}
        """
        _in_memory = history is not None
        if history is None:
            history = self._load_patch_list(self._history_file) or []
        _kept: list[dict[str, Any]] = []
        _obsolete: list[dict[str, Any]] = []
        _reasons: dict[str, int] = {}
        for _p in (history or []):
            if self._is_obsolete_patch(_p):
                _p["obsolete"] = True
                _p["obsolete_at"] = time.time()
                _p["obsolete_reason"] = (
                    _p.get("apply_error") or _p.get("reason") or "结构性失效")
                _key = str(_p["obsolete_reason"])[:60]
                _reasons[_key] = _reasons.get(_key, 0) + 1
                _obsolete.append(_p)
            else:
                _kept.append(_p)
        if _obsolete:
            _arch_path = os.path.join(
                os.path.dirname(self._history_file), "patch_history_obsolete.json")
            _existing = self._load_patch_list(_arch_path) or []
            # 按 id 去重，避免重复归档
            _seen = {str(_e.get("id")) for _e in _existing}
            for _p in _obsolete:
                if str(_p.get("id")) not in _seen:
                    _existing.append(_p)
                    _seen.add(str(_p.get("id")))
            _existing = _existing[-2000:]  # 归档上限，防止无限膨胀
            self._save_patch_list(_arch_path, _existing)
        if _in_memory:
            history[:] = _kept
        else:
            self._save_patch_list(self._history_file, _kept)
        _top = ""
        if _reasons:
            _top = max(_reasons.items(), key=lambda kv: kv[1])[0]
        return {"archived": len(_obsolete), "kept": len(_kept),
                "reason_top": _top, "reasons": _reasons}
    def _check_personality_baseline(self, patch: dict[str, Any]) -> dict[str, Any]:
        """
        ★v22.0 P2新增：调用人格内核进行基线校验。
        
        在应用任何补丁之前，检查提议的修改是否触及核心身份基线。
        如果补丁试图修改核心锚点、使命、关系或L4本能，直接拒绝。
        
        Returns:
            {"safe": bool, "reason": str, "violations": [...]}
        """
        try:
            # 动态导入，避免循环依赖
            import sys as _sys
            _project_root = self._project_root
            if _project_root not in _sys.path:
                _sys.path.insert(0, _project_root)

            from organs.identity.PulsePersonalityKernel import PulsePersonalityKernel
            _kernel = PulsePersonalityKernel("补丁校验")

            _proposed_content = patch.get("modified_code", "")
            _proposed_file = patch.get("file", "")

            return _kernel.check_modification_baseline(
                proposed_content=_proposed_content,
                proposed_file=_proposed_file,
            )
        except Exception as _e:
            # ★修复: 安全门必须 fail-closed。校验自身出错时若放行，
            # 等于人格基线保护在任何异常下自动失效。
            # 如需临时放行，可在 config 中显式设置 PATCH_BASELINE_FAIL_OPEN=True。
            try:
                import config as _cfg
                if getattr(_cfg, "PATCH_BASELINE_FAIL_OPEN", False):
                    return {
                        "safe": True,
                        "reason": f"基线校验异常（显式配置放行）: {_e}",
                        "violations": [],
                    }
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return {
                "safe": False,
                "reason": f"基线校验异常（安全起见拒绝应用）: {_e}",
                "violations": ["baseline_check_error"],
            }
    def _write_readable_log(self, results: dict, history: list):
        """
        ★v23.0新增：将补丁应用结果写入人类可读的修改日志。
        
        日志文件: data/patches/change_log.md
        每次应用补丁都追加一条完整记录，包含：
        - 修改时间
        - 目标文件
        - 风险等级
        - 修改原因
        - 来源诊断
        - 修改前代码片段
        - 修改后代码片段
        - 应用结果
        """
        _log_path = os.path.join(self._patch_dir, "change_log.md")
        _now = time.strftime("%Y-%m-%d %H:%M:%S")

        _lines = []
        # 首次创建时写入标题
        if not os.path.exists(_log_path):
            _lines.append("# 曈曈自我修改日志\n")
            _lines.append("记录所有自动修改操作的完整信息。\n")
            _lines.append("---\n")

        _lines.append(f"## 修改时间: {_now}\n")

        for _patch in history[-len(results.get("details", [])):]:
            _file = _patch.get("file", "未知文件")
            _risk = _patch.get("risk_level", "未标注")
            _reason = _patch.get("reason", _patch.get("description", "未说明"))
            _source = _patch.get("source_diagnosis", _patch.get("source", "未知"))
            _status = "✅ 成功" if _patch.get("applied") else f"❌ 失败: {_patch.get('apply_error', '未知错误')}"

            _lines.append(f"### 文件: `{os.path.basename(_file)}`\n")
            _lines.append(f"- **风险等级**: {_risk}")
            _lines.append(f"- **修改原因**: {_reason}")
            _lines.append(f"- **来源诊断**: {_source}")
            _lines.append(f"- **应用结果**: {_status}")
            _lines.append(f"- **完整路径**: `{_file}`")

            _original = _patch.get("original_code", "")
            _modified = _patch.get("modified_code", "")
            if _original:
                _lines.append(f"\n**修改前代码**:\n```python\n{_original[:500]}\n```")
            if _modified:
                _lines.append(f"\n**修改后代码**:\n```python\n{_modified[:500]}\n```")

            _backup = _patch.get("backup_path", "")
            if _backup:
                _lines.append(f"- **备份文件**: `{os.path.basename(_backup)}`")

            _lines.append("---\n")

        # 追加写入
        # ★第44批 T4（P2-290）：写盘守卫（change_log.md 在 data/patches/ 下）
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(_log_path, component="PatchManager.change_log"):
                return
        except ImportError as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_write_readable_log L3364", level="debug")
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(_lines))

        _module_logger.info(f"修改日志已更新: {_log_path}")
    @staticmethod
    def _m80_safe_target_path(target_file: str, project_root: str) -> bool:
        """★T7-4：回滚目标路径必须在项目根内，禁止越界(呼应 SSL 路径沙箱教训)。"""
        if not target_file or not project_root:
            return False
        try:
            _abs = os.path.abspath(target_file)
            _root = os.path.abspath(project_root)
            return os.path.commonpath([_abs, _root]) == _root
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m80_safe_target_path L3379", level="debug")
            return False

    def _m346_record_rollback(self, patch: dict, reason: str) -> dict:
        """★346刀2：回退动作**记账**（patch_history status + effect_verify 联动）。

        只写台账字段，不碰文件恢复——调用点都在"文件已经回滚完"之后。
        记账失败只告警，**绝不影响回退本身**：回退是安全动作，不能被记账拖住。
        """
        _out = {"history": None, "effect": None}
        try:
            from nucleus.evolution.patch_lifecycle import mark_effect_verify_rolled_back, mark_history_rolled_back
            _out["history"] = mark_history_rolled_back(
                patch, reason=reason, source="PatchManager.rollback")
            if _out["history"].get("ok"):
                _module_logger.info(
                    "[346回退] 台账记账: id=%s status→rolled_back reason=%s",
                    str(patch.get("id") or "")[:24], reason)
            else:
                _module_logger.warning(
                    "[346回退] 台账记账被拒: id=%s %s",
                    str(patch.get("id") or "")[:24], _out["history"].get("reason", ""))
            # ★346刀 2：必须把**本实例的**台账目录传进去。
            #   不传就走 `EvolutionEffectVerifier` 的默认目录（生产）——
            #   隔离盘上的回滚会去改**生产**的效果验证台账
            #   （本批隔离验收实测捕到这一点：d2 首轮 FAIL）。
            _out["effect"] = mark_effect_verify_rolled_back(
                str(patch.get("id") or ""), reason=reason,
                base_dir=os.path.join(str(self._project_root), "data", "evolution"))
        except Exception as _e:
            silent_exc(_e, where="PatchManager._m346_record_rollback", level="warning")
        return _out

    def rollback_last(self) -> bool:
        """回滚最近一次应用的补丁"""
        history = self._load_patch_list(self._history_file)
        for patch in reversed(history):
            if patch.get("applied") and patch.get("rollback_available"):
                # ★主线第80批 T7-4：回滚目标路径越界拒绝（防补丁 file 逃逸出项目根）
                if not self._m80_safe_target_path(patch.get("file", ""), self._project_root):
                    _module_logger.warning(
                        f"[补丁回滚][T7-4] 目标路径越界，拒绝回滚: {patch.get('file','')}")
                    continue
                backup_path = patch.get("backup_path", "")
                if os.path.isdir(backup_path):
                    # ★v23.0新增：使用集中备份恢复
                    _restore_result = self._backup_manager.restore_backup(backup_path)
                    if _restore_result["success"]:
                        patch["applied"] = False
                        patch["rollback_available"] = False
                        patch["rolled_back_at"] = time.time()
                        self._m346_record_rollback(patch, "rollback_last")
                        self._save_patch_list(self._history_file, history)
                        self._write_rollback_log(patch)
                        # ★第159批上A 刀3（O-B7）：登记回滚（失败不置位由上方保证）
                        try:
                            from nucleus.evolution.patch_lifecycle import record_rollback
                            record_rollback(patch.get("id"), reason="rollback_last")
                        except Exception as _pl_rb:
                            _module_logger.warning(
                                "[补丁生命周期] 登记回滚失败（已忽略）: %s", _pl_rb)
                        return True
                elif os.path.exists(backup_path):
                    # 兼容旧版单文件备份
                    shutil.copy2(backup_path, patch["file"])
                    patch["applied"] = False
                    patch["rollback_available"] = False
                    patch["rolled_back_at"] = time.time()
                    self._m346_record_rollback(patch, "rollback_last")
                    self._save_patch_list(self._history_file, history)
                    self._write_rollback_log(patch)
                    # ★第159批上A 刀3（O-B7）：登记回滚（失败不置位由上方保证）
                    try:
                        from nucleus.evolution.patch_lifecycle import record_rollback
                        record_rollback(patch.get("id"), reason="rollback_last")
                    except Exception as _pl_rb:
                        _module_logger.warning(
                            "[补丁生命周期] 登记回滚失败（已忽略）: %s", _pl_rb)
                    return True
        return False

    def rollback_patch(self, patch_id: str) -> dict[str, Any]:
        """★A2修复（主线A）：按补丁 ID 精确回滚单个已应用补丁。

        区别于 rollback_last（只能回滚「最近一个」）：
        运行时验证失败的是「特定补丁」，可能不是最近应用的，
        故需按 ID 精确定位并回滚，避免误回滚其他成功补丁。

        返回: {"ok": bool, "reason": str}
        """
        if not patch_id:
            return {"ok": False, "reason": "补丁 ID 为空"}
        history = self._load_patch_list(self._history_file)
        for patch in history:
            if patch.get("id") != patch_id:
                continue
            # ★主线第80批 T7-4：回滚目标路径越界拒绝（防补丁 file 逃逸出项目根）
            if not self._m80_safe_target_path(patch.get("file", ""), self._project_root):
                return {"ok": False, "reason": f"补丁 {patch_id} 目标路径越界，拒绝回滚"}
            if not patch.get("applied"):
                return {"ok": False, "reason": f"补丁 {patch_id} 未处于已应用状态，无需回滚"}
            if not patch.get("rollback_available"):
                return {"ok": False, "reason": f"补丁 {patch_id} 无可用备份，无法回滚"}
            backup_path = patch.get("backup_path", "")
            _restored = False
            try:
                if os.path.isdir(backup_path):
                    _restore_result = self._backup_manager.restore_backup(backup_path)
                    _restored = bool(_restore_result.get("success"))
                elif os.path.exists(backup_path):
                    shutil.copy2(backup_path, patch["file"])
                    _restored = True
            except Exception as _e:
                silent_exc(_e, where="nucleus/reasoning/PatchManager.py::rollback_patch L5973", level="warning")
                return {"ok": False, "reason": f"回滚补丁 {patch_id} 失败: {_e}"}
            if not _restored:
                return {"ok": False, "reason": f"补丁 {patch_id} 备份恢复失败"}
            # 回滚成功，更新状态
            patch["applied"] = False
            patch["rollback_available"] = False
            patch["rolled_back_at"] = time.time()
            patch["rolled_back_reason"] = "runtime_verify_failed"
            self._m346_record_rollback(patch, "runtime_verify_failed")
            self._save_patch_list(self._history_file, history)
            self._write_rollback_log(patch)
            # ★第159批上A 刀3（O-B7）：登记回滚（失败不置位由上方保证）
            try:
                from nucleus.evolution.patch_lifecycle import record_rollback
                record_rollback(patch_id, reason="runtime_verify_failed")
            except Exception as _pl_rb:
                _module_logger.warning(
                    "[补丁生命周期] 登记回滚失败（已忽略）: %s", _pl_rb)
            _module_logger.info(
                f"[补丁回滚] {patch_id} 已回滚: "
                f"{os.path.basename(patch.get('file',''))} 运行时验证失败自动撤销")
            return {"ok": True, "reason": f"补丁 {patch_id} 已回滚"}
        return {"ok": False, "reason": f"未找到补丁 {patch_id}"}
    def get_latest_applied_backup_paths(self, count: int = 5) -> list[str]:
        """★v23.0新增：获取最近应用的补丁备份路径"""
        history = self._load_patch_list(self._history_file)
        backups = []
        for patch in reversed(history):
            if patch.get("applied") and patch.get("backup_path"):
                backups.append(patch.get("backup_path"))
                if len(backups) >= count:
                    break
        return backups
    def _write_rollback_log(self, patch: dict):
        """
        ★v23.0新增：将回退操作写入修改日志。
        """
        _log_path = os.path.join(self._patch_dir, "change_log.md")
        _now = time.strftime("%Y-%m-%d %H:%M:%S")
        _file = patch.get("file", "未知文件")

        _lines = [
            f"\n## 回退时间: {_now}\n",
            f"### 文件: `{os.path.basename(_file)}`\n",
            "- **回退原因**: 修改后验证失败或手动回退",
            f"- **完整路径**: `{_file}`",
            "- **回退到**: 修改前版本\n",
            "---\n",
        ]

        # ★第44批 T4（P2-290）：写盘守卫
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(_log_path, component="PatchManager.rollback_log"):
                return
        except ImportError as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_write_rollback_log L3493", level="debug")
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(_lines))

        _module_logger.info(f"回退日志已记录: {os.path.basename(_file)}")

    # ========== 公开契约（规则14：消除跨模块私有穿透） ==========
    def verify_in_copy(self, patch: dict[str, Any]) -> dict[str, Any]:
        """公开封装 _verify_in_copy，供 SafeEvolutionExecutor/代码学习 调用"""
        return self._verify_in_copy(patch)

    def load_json(self, path, default):
        """公开封装 _load_json，供内在世界读取待处理补丁"""
        return self._load_json(path, default)

    def get_project_root(self) -> str:
        """公开只读访问项目根目录"""
        return self._project_root

    def get_pending_file(self) -> str:
        """公开只读访问待处理补丁文件路径"""
        return self._pending_file

    def get_history_file(self) -> str:
        """★A2修复（主线A）：公开只读访问补丁历史文件路径。"""
        return self._history_file

    # ===== ★PHASE17-A4（2026-09-07）：补丁路径跨平台归一化 =====
    #   问题：patch_history.json 中 file 字段写的是 Windows 绝对路径
    #         （实测 22 条全为「<项目根>\...」）。
    #         在 Linux/沙箱下 _check_patch_path 的跨盘符分支一律判越界 →
    #         补丁既不能应用也不能回滚，跨平台验证与迁移全部失效（P2-5）。
    #
    #   方案（最小侵入，下游零改动）：
    #         磁盘表示 = 相对项目根的 POSIX 路径（organs/body/PulseLiver.py）
    #         内存表示 = 绝对路径（与改动前完全一致，所有 open/copy 代码不用动）
    #   即：_save_patch_list 落盘时归一化，_load_patch_list 读取时解析回绝对。
    #   两者都不动任何业务判据，异常时原样返回（保守降级，绝不改坏数据）。

    def _normalize_patch_file(self, file_path: str) -> str:
        """绝对路径（Windows 或 POSIX）→ 相对项目根的 POSIX 路径；非绝对路径原样返回。

        跨平台难点：Windows 绝对路径（「<项目根>\\organs\\...」）
        无法直接 relpath 到本地项目根（Linux 上是 `/workspace/tongtong-pulse-v9`）——
        简单去掉盘符会得到 `workspace/tongtong-pulse-v9/...`（错误，多保留了上层目录）。
        因此用**项目根目录名做锚点**：在路径片段中找最后一个与项目根目录同名的片段，
        取其之后的部分作为相对路径。
        """
        if not file_path or not isinstance(file_path, str):
            return file_path
        try:
            import re as _re_n
            _is_win_abs = bool(_re_n.match(r"^[A-Za-z]:[/\\]", file_path))
            _is_posix_abs = os.path.isabs(file_path.replace("\\", "/"))
            if not (_is_win_abs or _is_posix_abs):
                return file_path  # 本来就是相对路径，原样返回

            _root = os.path.realpath(self._project_root)
            _p = file_path.replace("\\", "/")

            # ① 本机同源 POSIX 绝对路径：直接 relpath（最准确）
            if not _is_win_abs:
                _abs = os.path.realpath(_p)
                if os.path.commonpath([_abs, _root]) == _root:
                    return os.path.relpath(_abs, _root).replace("\\", "/")

            # ② Windows 或其它机器路径：用项目根目录名锚定
            _base = os.path.basename(_root)
            if _base:
                _parts = [x for x in _p.split("/") if x not in ("", ".")]
                _base_l = _base.lower()
                _idx = -1
                for _i in range(len(_parts) - 1, -1, -1):
                    if _parts[_i].lower() == _base_l:
                        _idx = _i
                        break
                if _idx >= 0 and _idx + 1 < len(_parts):
                    _rel = "/".join(_parts[_idx + 1:])
                    if _rel and not _rel.startswith(".."):
                        return _rel
            return file_path  # 锚定失败，不敢动，原样返回
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_normalize_patch_file L6111", level="debug")
            return file_path  # 任何异常都不改坏数据

    def _resolve_patch_file(self, file_path: str) -> str:
        """相对路径 → 项目根下的绝对路径；已是绝对路径则原样返回。"""
        if not file_path or not isinstance(file_path, str):
            return file_path
        try:
            import re as _re_r
            if _re_r.match(r"^[A-Za-z]:[/\\]", file_path) or os.path.isabs(file_path):
                return file_path
            return os.path.join(self._project_root, file_path.replace("/", os.sep))
        except Exception as _m428_e:
            silent_exc(_m428_e, where="nucleus/reasoning/PatchManager.py::_resolve_patch_file L6123", level="debug")
            return file_path

    @staticmethod
    def _m480_readback_on() -> bool:
        """★480 刀4 灰度开关（缺省开；显式 False ⇒ 逐字回到刀4 之前）。"""
        try:
            import config as _c480
            _v = getattr(_c480, "M480_APPROVE_READBACK", True)
            if _v is None:
                return True
            if isinstance(_v, str):
                return _v.strip().lower() in ("1", "true", "yes", "on")
            return bool(_v)
        except Exception as _e480:
            silent_exc(_e480,
                       where="PatchManager._m480_readback_on",
                       level="debug")
            return True

    def _m480_verify_approved_on_disk(self, patch: dict) -> tuple[bool, str]:
        """★480 刀4：读回 pending，确认**这一条**真的落成了 approved。

        为什么不复用 `_save_patch_list` 的返回值：那是条数比对（7==7），
        内容被并发写者用旧快照覆盖时同样返回 True —— 正是本刀要堵的假成功。

        匹配口径：优先 `id`（唯一）；无 id 的老条目退化为 (basename(file), method)。

        Returns: `(ok, why)`；why 只在 ok=False 时有意义（给日志/留痕用）。
        """
        _pid = str(patch.get("id") or "")
        _base = os.path.basename(str(patch.get("file") or ""))
        _meth = str(patch.get("method") or "")
        try:
            _rows = self._load_patch_list(self._pending_file)
        except Exception as _e:
            silent_exc(_e, where="PatchManager._m480_verify_approved_on_disk 读回",
                       level="warning")
            return False, f"读回异常: {type(_e).__name__}"
        if not isinstance(_rows, list):
            return False, "读回不是列表"
        for _r in _rows:
            if not isinstance(_r, dict):
                continue
            if _pid:
                if str(_r.get("id") or "") != _pid:
                    continue
            else:
                if (os.path.basename(str(_r.get("file") or "")) != _base
                        or str(_r.get("method") or "") != _meth):
                    continue
            _st = str(_r.get("status") or "")
            if _st == "approved":
                return True, ""
            return False, f"落盘后 status={_st!r}（应为 approved）"
        return False, "读回找不到该条（疑被并发写者覆盖/出队）"

    def _load_patch_list(self, path, default=None):
        """读取补丁列表（pending/history）并把 file 解析为绝对路径。"""
        _items = self._load_json(path, default if default is not None else [])
        if isinstance(_items, list):
            for _it in _items:
                if isinstance(_it, dict) and isinstance(_it.get("file"), str):
                    _it["file"] = self._resolve_patch_file(_it["file"])
            # ★433刀3：读入即归一 applied_at 哨兵（0/"0"/"" ⇒ None）。
            _m433_normalize_applied_at_in(_items)
            # ★433刀2：history 读入按 id 合并（同 id 多快照只留一条）。
            if path == self._history_file:
                _items = _m433_upsert_history(_items, where="_load_patch_list")
        return _items

    def dedup_history_rows(self, rows):
        """★433刀2：读入端按 id 合并 history（薄封装，纯函数在 patch_dedup）。

        给不走 `_load_patch_list` 的读点用（它们直接 `load_json(get_history_file())`）；
        与落盘端同一实现，避免"写进去 1 行、读出来 2 条"。
        """
        return _m433_upsert_history(rows, where="dedup_history_rows")

    def _save_patch_list(self, path, data):
        """保存补丁列表（pending/history）并把 file 归一化为相对路径落盘。"""
        try:
            if isinstance(data, list):
                for _it in data:
                    if isinstance(_it, dict) and isinstance(_it.get("file"), str):
                        _it["file"] = self._normalize_patch_file(_it["file"])
        except Exception:
            _module_logger.debug("补丁路径归一化失败（不影响保存）")
        # ★433刀3：落盘前把 applied_at 哨兵（0/"0"/""）归一为 None。
        try:
            _m433_normalize_applied_at_in(data)
        except Exception as _na433:
            silent_exc(_na433, where="PatchManager._save_patch_list applied_at归一",
                       level="debug")
        # ★433刀2：history 按 id upsert（同一补丁多个生命周期快照只留一行，
        #   并合并 status_history / wont_fix_* 审计信息）。**只对 history 生效**：
        #   pending 的唯一写口是 write_pending，不在本刀范围内。
        if path == self._history_file:
            data = _m433_upsert_history(data, where="_save_patch_list")
        # ★427刀5：落盘前收敛「状态说已验证、真值说失败」的矛盾（只治 is False）。
        try:
            if isinstance(data, list) and _m427_status_guard_on():
                data = _m427_converge_status(data)
        except Exception as _g427:
            silent_exc(_g427, where="PatchManager._save_patch_list 427状态守卫",
                       level="warning")
        # ★第280批刀4：pending 队列落盘前做状态机收敛（applied即出队 + 删顶层runtime_verified）。
        if path == self._pending_file:
            # ★461 刀1：同类错误冷却硬闸 —— 挂在 pending 的**唯一收口**。
            #   实盘 9 个写点（入队/审批/收敛/归档/AutoApprover 直写/TTL 回写）
            #   都会经过这里；逐个生成路径接必漏（445 的反馈就只接了单补丁路径，
            #   而作废来自 patch_multi_ 多文件路径，59 次重复作废由此而来）。
            #   ★只剔除、不改保留条目字段；失败不影响落盘。
            try:
                from nucleus.evolution import lesson_cooldown as _m461_lc
                _m461_data, _m461_blocked, _m461_rows = _m461_lc.filter_pending(data)
                if _m461_blocked:
                    data = _m461_data
                    _module_logger.info(
                        "[461刀1] 冷却拦截 %d 条: %s",
                        _m461_blocked, [str(r.get("method")) for r in _m461_rows[:3]])
            except Exception as _e:
                silent_exc(_e, where="PatchManager::_save_patch_list 461冷却闸",
                           level="warning")
            data = self._m280_converge_pending(data)
            # ★356刀11（洞鉴DJ4-03）：pending **必须走唯一写口**。
            #   本方法背后是 9 个写点（入队/审批/收敛/归档…），加上 AutoApprover
            #   直写与 dispatch TTL 回写，共 3 写者 11 写点，原先没有共享锁：
            #   A 读[1,2] → B 读[1,2] → A 写[1,2,3] → B 写[1,2,4] ⇒ 条目3 无声丢失。
            try:
                from nucleus.evolution.pending_write import write_pending
                _w = write_pending(data, path=path, writer="PatchManager")
                return bool(_w.get("ok"))
            except Exception as _pwe:
                silent_exc(_pwe, "PatchManager._save_patch_list 单写口", level="warning")
                return self._save_json(path, data)
        return self._save_json(path, data)

    def _load_json(self, path, default):
        if not os.path.exists(path): return default
        try:
            # ★第146批 T146-7：``utf-8-sig`` —— 透明剥离 UTF-8 BOM。
            #   带 BOM 的补丁文件用 strict utf-8 能解码但 json.loads 会报
            #   "Unexpected UTF-8 BOM"，表现为持续 ERROR + 一律回落默认值；
            #   无 BOM 时 utf-8-sig 行为与 utf-8 完全一致。
            with open(path, encoding='utf-8-sig') as f:
                _content = f.read()
            if not _content.strip():
                # ★P3修复：空文件/仅空白视为合法空状态（如用户手动清空补丁），
                # 不报 ERROR，直接返回默认值；只对真实损坏保留 ERROR 告警。
                _module_logger.debug(f"JSON空文件视为空状态: {path}")
                return default
            return json.loads(_content)
        except Exception as e:
            _module_logger.error(f"JSON加载失败 {path}: {e}，返回默认值")
            return default
    def _load_restart_counter(self) -> int:
        """从持久化文件加载重启计数"""
        path = os.path.join(self._patch_dir, "restart_count.txt")
        try:
            with open(path, encoding='utf-8') as f:
                return int(f.read().strip())
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_load_restart_counter L3634", level="debug")
            return 0

    def _save_restart_counter(self, count: int) -> bool:
        """持久化重启计数（★FIX: fail-closed——写失败返回 False 并告警，避免防循环重启失效）"""
        path = os.path.join(self._patch_dir, "restart_count.txt")
        # ★第44批 T4（P2-290）：写盘守卫
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(path, component="PatchManager.restart_counter"):
                return False
        except ImportError:
            pass
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(str(count))
            return True
        except Exception as _e:
            _module_logger.error(f"重启计数写入失败（防循环重启可能失效）: {path}: {_e}")
            return False

    def reset_restart_counter(self):
        """人工确认后重置重启计数"""
        self._save_restart_counter(0)

    # ============ ★第114批 T-114b③a：启动即锁死 WARNING ============
    def _m114b_emit_ratchet_lock_warning(self) -> None:
        """★第114批 T-114b③a：启动即重报棘轮锁死状态，治"重启后失忆"。

        棘轮锁死 WARNING 此前只在 ``apply_all_pending`` 运行时落日志；若框架
        重启后尚未触发 apply，该锁死状态不会在任何日志出现 → 运维"失忆"。
        本方法在启动健康诊断中主动读取棘轮持久化状态并重报 WARNING（锁死中）/
        INFO（冷却已期满待重试），确保重启后立即可见。只读、零副作用。
        """
        try:
            _counter = self._load_restart_counter()
            if _counter <= self._max_restart_count:
                return  # 未达上限，无锁死风险
            _cooldown_h = self._m113e_restart_cooldown_hours()
            _cooldown_sec = _cooldown_h * 3600.0
            _blocked_at = self._load_restart_blocked_at()
            _now = time.time()
            _elapsed = (_now - _blocked_at) if _blocked_at > 0 else float("inf")
            if _blocked_at > 0 and _elapsed < _cooldown_sec:
                _remain = int(_cooldown_sec - _elapsed)
                _module_logger.warning(
                    f"[棘轮锁死·启动即] 重启计数={_counter}（上限"
                    f"{self._max_restart_count}），仍处于冷却窗口，自动应用已锁死"
                    f"（剩余约{_remain // 3600}h{(_remain % 3600) // 60}m，"
                    f"请人工检查补丁队列）")
                self._m113e_emit_restart_telemetry(_cooldown_h, _remain)
            else:
                _module_logger.info(
                    f"[棘轮锁死·启动即] 重启计数={_counter}（上限"
                    f"{self._max_restart_count}），冷却窗口已期满，下轮"
                    f"apply_all_pending 将放行重试一次")
        except Exception as _e:
            _module_logger.debug(
                f"[棘轮锁死·启动即] 状态读取失败（已忽略）: "
                f"{type(_e).__name__}: {_e}")

    # ============ ★T-113e① 棘轮冷却 + 遥测辅助 ============
    def _m113e_restart_cooldown_hours(self) -> float:
        """棘轮冷却窗口（小时）。默认 24h，可由 config / 环境变量覆盖（只读取，不改写开关）。"""
        try:
            _env = os.environ.get("PULSE_PATCH_RESTART_COOLDOWN_HOURS")
            if _env and _env.strip():
                try:
                    return float(_env)
                except ValueError as e:
                    silent_exc(e, where="nucleus.reasoning.PatchManager::_m113e_restart_cooldown_hours L3703", level="debug")
            import config as _cfg
            _v = getattr(_cfg, "PATCH_RESTART_COOLDOWN_HOURS", None)
            if isinstance(_v, (int, float)):
                return float(_v)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_m113e_restart_cooldown_hours L3709", level="debug")
        return 24.0

    # ============ ★176批段2 内存自动重启·共用 3 次上限计数桶 ============
    def memory_restart_bucket_state(self) -> dict:
        """176批段2：内存自动重启与补丁重启共用同一持久化计数桶(restart_count.txt)。
        返回桶状态供内存重启判定互斥/冷却锁；读失败返回未锁默认态。"""
        try:
            _counter = self._load_restart_counter()
            _cooldown_h = self._memory_restart_cooldown_hours()
            _blocked_at = self._load_restart_blocked_at()
            _now = time.time()
            _cooldown_sec = _cooldown_h * 3600.0
            _elapsed = (_now - _blocked_at) if _blocked_at > 0 else float("inf")
            _locked = False
            _remain = int(_cooldown_sec)
            if _counter > self._max_restart_count:
                if _blocked_at > 0 and _elapsed >= _cooldown_sec:
                    # 冷却期满：不锁（与 apply_all_pending 冷却期满放行一致）
                    _locked = False
                    _remain = 0
                else:
                    _locked = True
                    _remain = int(_cooldown_sec - _elapsed) if _blocked_at > 0 else int(_cooldown_sec)
            return {
                "counter": _counter,
                "max": self._max_restart_count,
                "cooldown_h": _cooldown_h,
                "locked": bool(_locked),
                "remain_sec": max(0, _remain),
            }
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::memory_restart_bucket_state", level="debug")
            return {"counter": 0, "max": self._max_restart_count,
                    "cooldown_h": 24.0, "locked": False, "remain_sec": 0}

    def _memory_restart_cooldown_hours(self) -> float:
        """176批段2：内存重启冷却窗口（小时）。优先 MEMORY_RESTART_COOLDOWN_HOURS，
        未设置回退到与补丁重启同一桶的 _m113e_restart_cooldown_hours（共享冷却口径）。"""
        try:
            _env = os.environ.get("PULSE_MEMORY_RESTART_COOLDOWN_HOURS")
            if _env and _env.strip():
                try:
                    return float(_env)
                except ValueError as e:
                    silent_exc(e, where="nucleus.reasoning.PatchManager::_memory_restart_cooldown_hours env", level="debug")
            import config as _cfg
            _v = getattr(_cfg, "MEMORY_RESTART_COOLDOWN_HOURS", None)
            if isinstance(_v, (int, float)):
                return float(_v)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_memory_restart_cooldown_hours", level="debug")
        # 回退：与补丁重启同桶冷却口径
        return self._m113e_restart_cooldown_hours()

    def bump_restart_counter_for_memory_restart(self) -> int:
        """176批段2：内存重启与补丁重启共用同一持久化计数桶(restart_count.txt)。
        返回递增后的计数值；写失败 fail-closed 返回 -1（调用方据此抑制重启）。
        越上限首次锁死时写入冷却起点（与 apply_all_pending 一致，避免冷却倒计时被刷新）。"""
        try:
            _c = self._load_restart_counter() + 1
            if not self._save_restart_counter(_c):
                return -1
            if _c > self._max_restart_count:
                _blocked_at = self._load_restart_blocked_at()
                if _blocked_at == 0:
                    self._save_restart_blocked_at(time.time())
            return _c
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::bump_restart_counter_for_memory_restart", level="debug")
            return -1

    def _load_restart_blocked_at(self) -> float:
        """读取上次棘轮锁死的 epoch（无记录返回 0）。"""
        _path = os.path.join(self._patch_dir, "restart_blocked_at.txt")
        try:
            with open(_path, encoding="utf-8") as _f:
                return float(_f.read().strip() or 0)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.PatchManager::_load_restart_blocked_at L3719", level="debug")
            return 0.0

    def _save_restart_blocked_at(self, ts: float) -> bool:
        """持久化本次棘轮锁死时刻（用于冷却窗口计时）。"""
        _path = os.path.join(self._patch_dir, "restart_blocked_at.txt")
        try:
            from nucleus.data.write_guard import guard_write as _gw
            if not _gw(_path, component="PatchManager.restart_blocked_at"):
                return False
        except ImportError:
            pass
        try:
            with open(_path, "w", encoding="utf-8") as _f:
                _f.write(str(ts))
            return True
        except Exception as _e:
            _module_logger.error(f"棘轮锁死时刻写入失败: {_path}: {_e}")
            return False

    def _m113e_emit_restart_telemetry(self, cooldown_hours: float, remain_sec: int) -> None:
        """★T-113e① 棘轮锁死事件遥测：WARNING + 事件总线信号（best-effort，失败不影响锁死）。"""
        try:
            from nucleus.events.EventBus import get_event_bus
            get_event_bus().publish(
                name="evolution.patch_restart_limit",
                payload={
                    "restart_counter_max": self._max_restart_count,
                    "cooldown_hours": cooldown_hours,
                    "remain_seconds": remain_sec,
                    "source": "PatchManager.apply_all_pending",
                },
                source="PatchManager",
            )
        except Exception as _e:
            global _publish_fail_count
            _publish_fail_count += 1
            _module_logger.warning(f"[棘轮遥测] 事件总线发布失败（仅影响遥测，不影响锁死）: {_e}")
        self._restart_counter = 0
    def _save_json(self, path, data):
        """保存 JSON 并**返回是否写入成功**（bool）。

        ★第53批 T2（P0-补丁2）缺陷修复：本方法原本**只有守卫拒绝分支显式
        ``return False``**，成功路径与异常路径都**隐式返回 None** ——
        调用方若写 ``if not _save_ok:``，则「成功(None)」与「失败(False)」同为
        falsy，会把成功误判为失败。现统一为显式 bool：

        * 写入成功 → ``True``
        * 写盘守卫拒绝 → ``False``（并记 WARNING 留痕）
        * 写盘异常 → ``False``（并记 ERROR 留痕）

        兼容性（已实测）：既有调用方（SafeEvolutionExecutor x2 忽略返回值、
        tests/test_write_guard_m44 仅判 ``!= False`` / ``assertFalse``）均不受影响。

        Args:
            path: 目标 JSON 路径（会被写盘守卫检查，测试环境不得写生产 data/）。
            data: 可 JSON 序列化的对象。

        Returns:
            bool: 是否写入成功。
        """
        # ★第44批 T4（P2-290）：写盘守卫 —— 测试环境不得写生产 data/
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(path, component="PatchManager._save_json"):
                # ★第53批 T2：守卫拒绝必须留痕（原先静默返回 False，无法区分
                #   「被守卫拦下」与「写盘异常」）
                _module_logger.warning(f"[补丁落盘] 写盘守卫拒绝写入: {path}")
                return False
        except ImportError:
            pass
        # ★FIX: mkstemp 随机名，避免预测名 TOCTOU
        import tempfile
        _dir = os.path.dirname(path) or "."
        tmp_path = ""                # ★第53批 T2b：预置，避免 mkstemp 自身失败时清理块 UnboundLocalError
        try:
            _fd, tmp_path = tempfile.mkstemp(dir=_dir, prefix=".json_", suffix=".tmp")  # type: ignore[possibly-unbound]
            with os.fdopen(_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)  # type: ignore[possibly-unbound]
            return True                      # ★第53批 T2：成功路径显式 True
        except Exception as e:
            _module_logger.error(f"JSON保存失败 {path}: {e}")
            try:
                if tmp_path and os.path.exists(tmp_path):  # ★第53批 T2b：真值前置判断
                    os.remove(tmp_path)
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return False                     # ★第53批 T2：异常路径显式 False（原隐式 None）
