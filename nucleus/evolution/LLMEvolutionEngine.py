# -*- coding: utf-8 -*-
"""
LLMEvolutionEngine.py —— LLM进化引擎

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 基于大模型的代码进化与优化
机制: 基于LLMEvolutionEngine类实现，包含10个核心方法
定位: 进化执行层
"""

from __future__ import annotations

import ast
import json
import os
import re
import textwrap
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.llm.call_recorder import trace_evolution_call
from nucleus.LLMDependencyMetrics import SCENE_EVOLUTION, record_llm_call
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底

# ===== ★428刀1：生成侧截断治理 =====
# 【T0 实测，非推测】67 条关2a 拒收标本（data/audit/failed_patches/）用正确管线
#   （先剥围栏再 dedent）跑 `ast.parse`：**0/67 通过**，其中 66 条报
#   `expected 'except' or 'finally' block`。逐件对账后定位到四个独立缺陷
#   （见 _m428_strip_fence_lines / _m428_self_contained_prefix / _call_llm
#    的 finish_reason 检测 / 本文件 prompt 要求6），四者互不替代。
#
# token 预算：原 4096。86 批实测同类推理模型 8000 档才拿到 stop/content 1311 字
#   （reasoning 约 1.8 万字）；此处取 8192，不追到 16384 是因为跨文件补丁包
#   正常只需 3~6k tokens，过高预算只会延长单次耗时。
_M428_MULTI_PATCH_MAX_TOKENS = 8192
#: 续写上限（**有界**：铁律「必配重试上限」，防"每次都被截断"变成死循环）
_M428_MAX_CONTINUATIONS = 1
#: 续写时回灌的原文尾部长度（字符）
_M428_CONTINUE_TAIL_CHARS = 2000
#: 喂给模型的原文上限。**与改造前同为 3000**，改动只在"切在哪"（行边界+语法自洽）
_M428_PROMPT_ORIG_LIMIT = 3000
#: 语法自洽回退的最大行数（防止整段原文都解析不了时退化成全量重试）
_M428_BACKTRACK_LINES = 80

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*$")


def _m428_strip_fence_lines(text) -> str:
    """剥掉 markdown 围栏行（含 ```` ```python ````）。**围栏不是代码。**

    为什么必须在**解析侧**剥而不是只靠 prompt 禁：实测 67 条拒收标本里 54 条的
    `original_code`/`modified_code` 首行就是 ```` ```python ````——模型把围栏写进了
    JSON 字符串值**内部**，而原解析侧只剥回复最外层的围栏。
    后果有两层：① 片段第 1 行不是代码 ⇒ `ast.parse` 必报 invalid syntax；
    ② `original_code` 在目标文件里永远定位不到 ⇒ 唯一性检查恒 False。
    """
    _s = str(text or "")
    _out = []
    for _ln in _s.splitlines():
        if _FENCE_RE.match(_ln):
            continue
        _out.append(_ln)
    _joined = "\n".join(_out)
    # ★保留原有结尾换行： 会被当作**精确锚点**去目标文件里做替换，
    #   少一个结尾 \n 会让锚点退化成某行前缀，替换后残留一个空行。
    if _s.endswith(("\n", "\r")) and not _joined.endswith("\n"):
        _joined += "\n"
    return _joined


def _m428_self_contained_prefix(text, limit=_M428_PROMPT_ORIG_LIMIT):
    """在 `limit` 内取**语法自洽**的前缀，返回 `(片段, 是否被截断)`。

    为什么不能直接 `text[:limit]`：3000 字硬切会切在语句中间（实测标本里出现
    半截字面量 `...["trust_penalty"`）；模型看到半个 `try` 就照着写半个
    `try` —— 这在关2a 表现为「try 块不平衡」，但**真因是喂进去的原文不完整**。

    做法：先剥围栏；长度够就整段（若整段本身解析不了再回退）；
    长度超了就从 `limit` 往前逐行回退，取**第一个能通过 `ast.parse` 的前缀**。
    回退超过 `_M428_BACKTRACK_LINES` 行仍不行 ⇒ 按行边界硬切并如实标"已截断"
    （不返回原文，避免把 3000 字上限变成无上限）。

    注意：方法体片段本身缩进，需 `dedent` 后才是合法模块，故探测用 dedent 后的文本；
    返回值仍是**原缩进**，调用方无需再处理。
    """
    _s = str(text or "")
    _s = _m428_strip_fence_lines(_s)
    if not _s.strip():
        return "", False
    if len(_s) <= limit:
        try:
            ast.parse(textwrap.dedent(_s))
            return _s, False
        except SyntaxError:
            pass  # 原文自身不完整 ⇒ 落到下面回退（不谎报"完整"）
    _ends = [i for i, _ch in enumerate(_s[:limit]) if _ch == "\n"]
    for _end in reversed(_ends[-_M428_BACKTRACK_LINES:]):
        _cand = _s[:_end]
        if not _cand.strip():
            continue
        try:
            ast.parse(textwrap.dedent(_cand))
        except SyntaxError:
            continue
        return _cand, True
    _cut = _s.rfind("\n", 0, limit)
    return (_s[:_cut] if _cut > 0 else _s[:limit]), True


class LLMEvolutionEngine(SilentLogMixin):
    """LLM 主导的进化引擎（跨文件 + 多步规划）。"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        # ★428刀1：截断可见化状态。`finish_reason` 此前在本模块**零读取**
        #   ⇒ 模型没写完时 content 照旧被当"完整回复"返回，一路流到关2a
        #   才以「try 块不平衡」被拒 —— 拒收理由描述的是**现象**，
        #   真因（模型没写完）在生成侧一个字都没留。
        self._m428_last_finish_reason = ""
        self._m428_truncated = False
        self._m428_continuations = 0

    # ========== LLM 调用（复用 ssrf_guard 安全网络层） ==========

    @trace_evolution_call(prompt_pos=2, version="evolution.engine.v1")
    def _call_llm(self, system: str, prompt: str,
                  model: str | None = None,
                  max_tokens: int = 2048,
                  temperature: float = 0.2) -> str | None:
        """统一 LLM 调用（复用 SSRF 防护网络层）。
        
        优化（2026-09-10 内部协作者）：
        1. 超时重试机制：偶发网络超时自动重试1次，避免进化失败
        2. 用框架日志系统代替print，确保写入日志文件
        """
        record_llm_call(SCENE_EVOLUTION)
        # ★340批刀4：每次调用先清空，避免沿用上一次的模型名（错账比漏账更难查）。
        self._m44_last_model = ""
        try:
            import config
            # ★352刀9⑧：改走**渠道池**（REMOTE_API_CHANNELS 按优先级免费优先），
            #   不再直连 api.deepseek.com。三元组成对取自同一渠道（见
            #   `config.get_llm_call_target` 的注释：advanced_model 与渠道 URL 不成对）。
            _tgts = config.get_llm_call_targets(model)
            _tgt = _tgts[0] if _tgts else {}
            _api_url = _tgt.get("api_url", "")
            _api_key = _tgt.get("api_key", "")
            if not _api_url or not _api_key:
                self._m44_last_error = "missing_api_config: api_url/api_key 未配置"
                return None
            _model = _tgt.get("model") or "deepseek-flash"
            # ★340批刀4：把**实际请求的模型名**写回取数口 `_m44_last_model`。
            #   该属性此前全库**无生产者** ⇒ `trace_evolution_call` 装饰器恒取到空串
            #   ⇒ `_compute_cost_estimate` 查不到单价 ⇒ `cost_estimate` 恒 null
            #   ⇒ 预算熔断器永不跳闸（烛微340前置§四：cost 非空 = 0 条）。
            self._m44_last_model = str(_model or "")
            _payload = {
                "model": _model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            _payload_bytes = json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': 'Bearer ' + _api_key,
            }
            from nucleus.api_rate_limiter import api_rate_limited, get_llm_call_config
            from nucleus.ssrf_guard import safe_http_json
            _cfg = get_llm_call_config()
            _timeout = _cfg["timeout_by_purpose"]["general"]

            # ★352刀9⑧：超时重试 + **渠道失效转移**。两类失败分开处置（不同事实不同对待）：
            #   · 超时/连接异常 = 网络偶发 ⇒ 原地重试 1 次；
            #   · 429 / 5xx = **这个渠道现在不行** ⇒ 原地重试没用（免费渠道 conc=1，
            #     d9c 实测连续 3 次里前两次全 429），必须换下一个渠道（任务书"收费兜底"）。
            _max_retries = 2
            _ok = False
            _data = None
            _last_exc = None
            for _tgt in _tgts:
                _api_url = _tgt.get("api_url", "")
                _api_key = _tgt.get("api_key", "")
                if not _api_url or not _api_key:
                    continue
                _model = _tgt.get("model") or "deepseek-flash"
                self._m44_last_model = str(_model or "")
                # ★352刀9⑧：路由留痕（验收要求"实际路由渠道可 grep"）。
                #   「不再直连 api.deepseek.com」必须能从日志里看见，否则只是口头声明。
                try:
                    _host = _api_url.split("//", 1)[-1].split("/", 1)[0]
                    import logging as _lg
                    _lg.getLogger("pulse").info(
                        "[352接线] engine=evolution source=%s channel=%s model=%s host=%s",
                        _tgt.get("source"), _tgt.get("channel"), _model, _host)
                except Exception as _le:
                    silent_exc(_le, where="LLMEvolutionEngine::_call_llm route_log", level="debug")
                _payload_bytes = json.dumps({
                    "model": _model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }, ensure_ascii=False).encode('utf-8')
                _headers = {
                    'Content-Type': 'application/json; charset=utf-8',
                    'Authorization': 'Bearer ' + _api_key,
                }
                for _attempt in range(_max_retries):
                    try:
                        with api_rate_limited(enabled=_cfg.get('enable_rate_limit', True)):
                            _ok, _data = safe_http_json(
                                _api_url, method='POST', data=_payload_bytes, headers=_headers,
                                timeout=_timeout,
                            )
                        if _ok and isinstance(_data, dict):
                            break
                        # 渠道侧限流/故障 ⇒ 换渠道，不必原地重试
                        if isinstance(_data, str) and ("429" in _data or "HTTP Error 5" in _data):
                            break
                    except Exception as _retry_err:
                        _last_exc = _retry_err
                        if _attempt < _max_retries - 1:
                            self._log(LogLevel.DEBUG,
                                f"LLM调用超时/异常，重试中（{_attempt+1}/{_max_retries}）: {type(_retry_err).__name__}")
                            time.sleep(1.0)
                            continue
                        break
                if _ok and isinstance(_data, dict):
                    break
                if len(_tgts) > 1:
                    self._log(LogLevel.DEBUG,
                        f"渠道 {_tgt.get('channel')} 未成功，转向下一渠道")

            if not _ok or not isinstance(_data, dict):
                # ★352刀9⑧：异常语义保持不变（外层 except 记 "Type: msg"）；
                #   服务端有原文时把它带出来（"HTTP Error 429" 比"请求失败"有用得多）。
                if _last_exc is not None and _data is None:
                    raise _last_exc
                if isinstance(_data, str) and _data:
                    self._m44_last_error = "http_failed: " + _data[:200]
                    return None
                self._m44_last_error = "http_failed: 请求失败或响应非 JSON 对象"
                return None
            # ★第94批 相关任务：暂存 usage 供 `trace_evolution_call` 装饰器留存
            self._last_llm_usage = _data.get("usage")  # _m94_extract_usage_marker
            # ★340批刀4：服务端回执的 model 才是**权威**值（网关可能路由到别处）
            _m340_srv = str(_data.get("model") or "")
            if _m340_srv:
                self._m44_last_model = _m340_srv
            _choices = _data.get("choices", [])
            if _choices:
                _c0 = _choices[0] if isinstance(_choices[0], dict) else {}
                # ★428刀1：`finish_reason` 首次被读取。取值语义：
                #   "stop"   模型自己收尾 ⇒ 内容完整；
                #   "length" 撞 max_tokens ⇒ 内容**必然**是半截；
                #   "content_filter" / 其他 ⇒ 内容可能被外部截断。
                #   两种非 stop 都置 _m428_truncated，供调用方决策（续写/丢弃）。
                self._m428_last_finish_reason = str(_c0.get("finish_reason") or "")
                self._m428_truncated = (
                    self._m428_last_finish_reason not in ("", "stop"))
                _txt = str((_c0.get("message") or {}).get("content") or "")
                if self._m428_last_finish_reason == "length":
                    _usage = _data.get("usage") or {}
                    self._log(
                        LogLevel.WARNING,
                        "[428刀1][生成截断] finish_reason=length ⇒ 模型没写完："
                        f"content={len(_txt)} 字 "
                        f"completion_tokens={_usage.get('completion_tokens')} "
                        f"(prompt_tokens={_usage.get('prompt_tokens')}) "
                        "｜ 该回复**不可当完整结果使用**")
                return _txt
            return None
        except Exception as e:
            # 修复：用框架日志系统代替print，确保写入日志文件
            self._log(LogLevel.WARNING, f"LLM调用失败: {type(e).__name__}: {e}")
            # ★第44批 T1：把失败明细留给埋点装饰器（此前只进日志，留存里恒空）
            self._m44_last_error = f"{type(e).__name__}: {e}"
            return None

    # ========== 阶段B核心1：跨文件补丁包 ==========

    def generate_multi_file_patch(self, issue: dict[str, Any],
                                  original_code: str,
                                  file_path: str,
                                  method_name: str,
                                  call_chain: dict[str, Any] | None = None,
                                  self_inspector=None) -> list[dict[str, Any]]:
        """
        让 LLM 生成跨文件补丁包（改接口时同步迁移调用方）。

        Args:
            issue: 问题字典（type/description 等）
            original_code: 问题方法的原始代码
            file_path: 问题方法所在文件
            method_name: 问题方法名
            call_chain: 调用链上下文（get_call_chain 的结果，含 called_methods/called_by）
            self_inspector: SelfInspector 实例（用于读取调用方代码）

        返回:
            补丁列表（每个含 file/method/original_code/modified_code），
            可能包含多个文件（主文件 + 调用方文件）。
            失败或无 API 时返回空列表。
        """
        # 构建调用链上下文文本
        _call_ctx = self._format_call_chain(call_chain)

        # 读取调用方代码（供 LLM 同步修改）
        _callers_code = self._read_callers_code(call_chain, self_inspector)

        # ★428刀1：原文必须**语法自洽**地进 prompt。
        #   原实现 `{original_code[:3000]}` 是字面硬切 —— 实测标本里切出了
        #   半截字面量 `...["trust_penalty"`。模型看到半个 `try` 就写半个
        #   `try`，于是在关2a 报「try 块不平衡」；把账记在模型头上是错的。
        _orig_capped, _orig_cut = _m428_self_contained_prefix(
            original_code, _M428_PROMPT_ORIG_LIMIT)
        _cut_notice = ""
        if _orig_cut:
            _cut_notice = (
                "★注意：上面的原始代码因长度限制**只给出了前一部分**"
                "（已在完整的语句块边界处截断），后面部分**未提供**。\n"
                "因此：①不要声称自己看到了完整方法；②如果你必须改动截断点之后的内容，"
                "请把 original_code 收敛到**你确实看到的**那个完整语句块内；"
                "③无论如何**不得产出半截语句块**（`try` 必须有 `except`/`finally`，"
                "`if`/`for`/`while`/`def` 必须有完整块体）——"
                "宁可缩小改动范围，也不要留下未闭合的块。\n\n")
        _prompt = (
            f"你是曈曈的架构级代码重构助手。请对下面的问题生成「跨文件补丁包」。\n\n"
            f"问题类型: {issue.get('type', '')}\n"
            f"问题描述: {issue.get('description', '')}\n"
            f"目标方法: {method_name}（位于 {file_path}）\n\n"
            f"目标方法原始代码:\n```python\n{_orig_capped}\n```\n\n"
            f"{_cut_notice}"
            f"{_call_ctx}"
            f"{_callers_code}"
            f"要求：\n"
            f"1. 若修改会改变方法签名/接口，必须同步修改所有调用方。\n"
            f"2. ★最小化改动：只修改解决问题所必需的代码行，优先局部修改而非整体重写；"
            f"保留原有逻辑、注释和未要求改动的部分。\n"
            f"3. 输出严格的 JSON，结构为 {{\"patches\": ["
            f"{{\"file\": \"文件路径\", \"method\": \"方法名\", "
            f"\"original_code\": \"修改前完整代码\", \"modified_code\": \"修改后完整代码\"}}]}}。\n"
            f"4. 每个补丁的 original_code 必须能在对应文件中唯一定位（含足够上下文）。\n"
            f"5. 只输出 JSON，不要输出任何解释或 markdown 代码块标记。\n"
            # ★428刀1 要求6：实测 54/67 拒收标本的根因就是"锚点不自洽" ——
            #   模型按原要求4只保证"能唯一定位"，于是给出从 `try:` 起、
            #   到 `except` 前止的最小锚点，替换后天然缺 handler。
            f"6. ★original_code 与 modified_code 必须是**语法自洽的完整语句块**："
            f"try 必须连同它的 except/finally 一起给出，if/for/while/def/with "
            f"必须连同完整块体一起给出；**严禁**把一个复合语句从中间切开。"
            f"两个字段的值内**不得**出现 ``` 围栏或任何 markdown 标记。\n"
            f"\n{self._aesthetic_guidance()}"
        )
        _sys = "你是曈曈的架构级代码重构引擎，输出严格 JSON 结构。"
        self._m428_truncated = False
        self._m428_last_finish_reason = ""
        self._m428_continuations = 0
        _response = self._call_llm(
            system=_sys,
            prompt=_prompt,
            model=None,  # 用 advanced_model（复杂重构需强模型）
            max_tokens=_M428_MULTI_PATCH_MAX_TOKENS,
        )
        # ★428刀1：被截断就发**一次**「继续」续写。
        #   为什么不直接丢弃：截断发生在 JSON 中途时 `json.loads` 整条失败，
        #   一条只差尾部 200 字的补丁会变成**零产出**（"全损"与"被判不合格"
        #   是两件事，前者连审计都没有）。续写一次能把"差一点"救回来。
        #   为什么上限 1 次：铁律「重试必须配上限」——若模型每轮都截断，
        #   无上限续写会把一次生成放大成 N 次计费调用。
        while (self._m428_truncated and _response
               and self._m428_continuations < _M428_MAX_CONTINUATIONS):
            self._m428_continuations += 1
            _tail = _response[-_M428_CONTINUE_TAIL_CHARS:]
            _more = self._call_llm(
                system=_sys,
                prompt=(
                    _prompt
                    + "\n\n【已生成的部分输出（因长度被截断，直接接着写即可）】\n"
                    + _tail
                    + "\n\n【续写指令】请**只输出紧接上面的剩余内容**："
                      "不要重复已有部分，不要重新输出 JSON 开头，不要解释，"
                      "不要输出 markdown 围栏。"
                ),
                model=None,
                max_tokens=_M428_MULTI_PATCH_MAX_TOKENS,
            )
            if not _more:
                self._log(
                    LogLevel.WARNING,
                    "[428刀1][续写失败] 续写调用无返回，保留被截断的原文"
                    f"（finish_reason={self._m428_last_finish_reason or '?'}，"
                    f"共尝试 {self._m428_continuations} 次）")
                break
            _response = _response + _more
        return self._parse_multi_file_patch(_response, issue)

    def _aesthetic_guidance(self) -> str:
        """获取框架审美偏好指引（生成回流），异常降级为空串（不阻断生成）。"""
        try:
            from nucleus.evolution.AestheticJudge import get_aesthetic_judge
            return get_aesthetic_judge().feedback_guidance()
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.LLMEvolutionEngine::_aesthetic_guidance L175", level="debug")
            return ""

    def _format_call_chain(self, call_chain: dict[str, Any] | None) -> str:
        """格式化调用链为文本。"""
        if not call_chain:
            return ""
        _parts = []
        if call_chain.get("called_methods"):
            _parts.append(f"该方法调用了: {call_chain['called_methods']}")
        if call_chain.get("called_by"):
            _parts.append(f"被以下方法调用: {call_chain['called_by']}")
        if not _parts:
            return ""
        return "调用链上下文:\n" + "\n".join(_parts) + "\n\n"

    def _read_callers_code(self, call_chain: dict[str, Any] | None,
                           self_inspector) -> str:
        """读取调用方代码，供 LLM 同步修改。"""
        if not call_chain or not self_inspector:
            return ""
        _called_by = call_chain.get("called_by", [])
        if not _called_by:
            return ""
        _snippets = []
        for _caller_key in _called_by[:3]:  # 最多读3个调用方
            if "." in _caller_key:
                _organ, _method = _caller_key.split(".", 1)
                try:
                    _detail = self_inspector.get_method_body(_organ, _method)
                    if _detail:
                        _body = _detail.get("body", "")
                        _snippets.append(f"调用方 {_caller_key} 代码:\n```python\n{_body[:1500]}\n```\n")
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        if not _snippets:
            return ""
        return "调用方代码（需同步修改）:\n" + "\n".join(_snippets) + "\n"

    def _parse_multi_file_patch(self, response: str | None,
                                issue: dict[str, Any]) -> list[dict[str, Any]]:
        """解析 LLM 输出的 JSON 补丁包。"""
        if not response:
            return []
        try:
            # 清理可能的 markdown 代码块标记
            _clean = response.strip()
            if _clean.startswith("```"):
                _clean = _clean.strip("`")
                _clean = _clean.removeprefix("json")
            _data = json.loads(_clean)
            _patches = _data.get("patches", [])
            _result = []
            for _p in _patches:
                if not _p.get("file") or not _p.get("modified_code"):
                    continue
                if not _p.get("original_code"):
                    continue
                # ★428刀1：**值内围栏必须在这里剥**。实测 54/67 拒收标本的
                #   original_code/modified_code 首行是 ```python —— 模型把围栏
                #   写进了 JSON 字符串值内部，而原实现只剥回复最外层围栏。
                #   围栏不是代码：留着它 ⇒ ast 必报 invalid syntax，且
                #   original_code 在目标文件里永远定位不到（唯一性恒 False）。
                _p["original_code"] = _m428_strip_fence_lines(_p.get("original_code"))
                _p["modified_code"] = _m428_strip_fence_lines(_p.get("modified_code"))
                # ★P3 最小化改动检查
                _change_ratio = self._calc_change_ratio(_p.get("original_code", ""), _p.get("modified_code", ""))
                # ★P3 零冲突检查：original_code 在文件中唯一存在
                _unique = self._check_original_unique(_p.get("file", ""), _p.get("original_code", ""))
                _patch = {
                    "id": f"patch_multi_{int(time.time())}_{hash(_p['original_code']) & 0xFFFF:04x}",
                    "file": _p["file"],
                    "method": _p.get("method", ""),
                    "issue_type": issue.get("type", "unknown"),
                    "risk_level": "中等",
                    "description": issue.get("description", ""),
                    "original_code": _p["original_code"],
                    "modified_code": _p["modified_code"],
                    "diff_summary": f"跨文件重构: {_p.get('method', '')}",
                    "trust_score": 50,  # 跨文件补丁信任分保守
                    "confidence": "medium",
                    "change_ratio": round(_change_ratio, 3),  # ★P3 改动比例（0-1，越低越精准）
                    "original_unique": _unique,  # ★P3 original_code 是否在文件中唯一
                    "repair_source": "llm_multi_file",
                    # ★428刀1：本次回复是否被模型/网关截断（"只加不改"的观测口）。
                    #   下游据此可把"生成不完整"与"生成完整但质量差"分开归因。
                    "m428_gen_truncated": bool(self._m428_truncated),
                    "generated_at": time.time(),
                    "status": "pending",
                    "verification": None,
                    "applied": False,
                    "applied_at": None,
                }
                _result.append(_patch)
            return _result
        except Exception as e:
            # ★428刀1：原为 DEBUG ⇒ 生产日志里等于不存在。
            #   而"整条回复解析不出来"= **本次生成全损**，与"解析成功但补丁
            #   被判不合格"是两种完全不同的事实，必须能分开看见。
            self._log(
                LogLevel.WARNING,
                f"[428刀1][补丁包解析失败] {type(e).__name__}: {str(e)[:200]}"
                f" ｜ 回复 {len(str(response or ''))} 字"
                f" finish_reason={self._m428_last_finish_reason or '?'}"
                " ⇒ 本条生成全损（不是补丁被判不合格）")
            return []

    # ========== 阶段B核心2：多步规划 ==========

    def plan_multi_step(self, issue: dict[str, Any],
                        call_chain: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        让 LLM 把复杂任务拆成多步计划，每步含目标+验证标准。

        返回:
            [{"step": 1, "goal": str, "verification": str, "depends_on": [0...]}, ...]
        """
        _call_ctx = self._format_call_chain(call_chain)
        _prompt = (
            f"你是曈曈的架构规划助手。请把下面的复杂重构任务拆成「多步计划」。\n\n"
            f"问题类型: {issue.get('type', '')}\n"
            f"问题描述: {issue.get('description', '')}\n"
            f"{_call_ctx}"
            f"要求：\n"
            f"1. 输出严格 JSON：{{\"steps\": ["
            f"{{\"step\": 序号, \"goal\": \"本步目标\", "
            f"\"verification\": \"本步验证标准\", \"depends_on\": [依赖的前序步骤序号]}}]}}。\n"
            f"2. 每步必须是可独立验证的最小改动。\n"
            f"3. 步骤数不超过5。\n"
            f"4. 只输出 JSON，不要输出解释。\n"
        )
        _response = self._call_llm(
            system="你是曈曈的架构规划引擎，输出严格 JSON 结构。",
            prompt=_prompt,
            model=None,
            max_tokens=2048,
        )
        return self._parse_multi_step_plan(_response)

    def _parse_multi_step_plan(self, response: str | None) -> list[dict[str, Any]]:
        """解析 LLM 输出的多步计划 JSON。"""
        if not response:
            return []
        try:
            _clean = response.strip()
            if _clean.startswith("```"):
                _clean = _clean.strip("`")
                _clean = _clean.removeprefix("json")
            _data = json.loads(_clean)
            _steps = _data.get("steps", [])
            return [s for s in _steps if s.get("goal")]
        except Exception as e:
            # 修复：用框架日志系统代替print
            self._log(LogLevel.DEBUG, f"多步计划JSON解析失败: {type(e).__name__}: {e}")
            return []


    # ========== ★P3 最小化改动 + 零冲突检查 ==========

    @staticmethod
    def _calc_change_ratio(original: str, modified: str) -> float:
        """计算改动比例（modified 中与 original 不同的行数占比）。

        返回 0-1，0 表示无改动，1 表示完全重写。
        用于约束 LLM 生成最小化改动，避免整体重写。
        """
        if not original or not modified:
            return 1.0
        _orig_lines = set(original.splitlines())
        _mod_lines = modified.splitlines()
        if not _mod_lines:
            return 1.0
        _changed = sum(1 for l in _mod_lines if l not in _orig_lines)
        return _changed / len(_mod_lines)

    def _check_original_unique(self, file_path: str, original_code: str) -> bool:
        """检查 original_code 在目标文件中是否唯一存在（零冲突基础）。

        如果 original_code 在文件中出现多次，补丁应用时可能定位错误，
        导致修改错误的代码块。
        """
        if not file_path or not original_code:
            return False
        _abs = os.path.join(self._project_root, file_path)
        if not os.path.exists(_abs):
            return False
        try:
            with open(_abs, encoding='utf-8', errors='ignore') as _f:
                _content = _f.read()
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.LLMEvolutionEngine::_check_original_unique L347", level="debug")
            return False
        # 统计出现次数（取 original_code 的前 50 字符作为匹配键，避免完全匹配失败）
        _key = original_code.strip()[:50]
        if not _key:
            return False
        _count = _content.count(_key)
        return _count == 1


# ========== 便捷函数 ==========

def get_llm_evolution_engine(project_root: str) -> LLMEvolutionEngine:
    """获取 LLMEvolutionEngine 实例。"""
    return LLMEvolutionEngine(project_root)


if __name__ == "__main__":
    # 自测：无 API key 时优雅降级
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _engine = LLMEvolutionEngine(_root)
    _issue = {"type": "refactor", "description": "测试多步规划"}
    _result = _engine.plan_multi_step(_issue, None)
    print(f"无 API key 时多步规划返回: {_result} (应为空列表，优雅降级)")
    _patches = _engine.generate_multi_file_patch(_issue, "def x():\n    pass\n", "organs/test.py", "x", None, None)
    print(f"无 API key 时跨文件补丁返回: {_patches} (应为空列表，优雅降级)")
