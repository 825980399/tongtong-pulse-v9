# -*- coding: utf-8 -*-
"""
EvolutionLoop.py —— 进化循环

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 自主进化主循环与状态管理
机制: 基于EvolutionLoop类实现，包含10个核心方法
定位: 进化核心层
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.evolution.DiffArchiver import DiffArchiver
from nucleus.evolution.EvolutionDriver import EvolutionDriver
from nucleus.evolution.LogAnalyzer import LogAnalyzer
from nucleus.evolution.TestGenerator import TestGenerator

# ★P0-1补漏（2026-09-06）：本类有 2 处 self._log 调用但自身未定义 _log，
#   其中 1 处位于 except 块内（:391）——一旦触发会在异常处理时再抛
#   AttributeError，把真正的原始异常完全掩盖，是本类最难排查的失败模式。
#   补 SilentLogMixin 兜底，与全项目其余 32 个类保持同一修复口径。
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底

_module_logger = logging.getLogger("EvolutionLoop")

# ===== ★325批刀0：需求源（growth_intake）可观测计数 =====
# 为什么要有：旧实现 `_file, _method = "", ""` 恒空，而缺陷源里大量日志类条目
# (file="", method="") ⇒ ("","") 必在 _conflict ⇒ 需求源**结构性 100% 让位**。
# 烛微325§0-5 实测佐证：台账 trigger 84 条全 None、growth_intake 恒 0。
# 计数只为"看得见"，不参与任何判罚。
_GROWTH_INTAKE_STATS: dict = {
    "calls": 0,          # 进入采集的次数（有 hub 料才算）
    "yielded": 0,        # 因**非空**冲突键让位
    "admitted": 0,       # 真正入队
    "empty_key_hits": 0,  # 存在空键冲突候选（**不再让位**，仅留痕）
    "no_material": 0,    # hub 里没有 growth 料
    # ★330批刀4（326 前置§0-3）：让位要看得出**是哪种**冲突 —— 只有一句日志不算留痕。
    "last_conflict_key": None,   # 最近一次让位的 {"file","method","why"}，可复算
    "yielded_nonempty": 0,       # 与 yielded 同值（语义名，报表直读不猜）
    "empty_key_ignored": 0,      # 与 empty_key_hits 同值（语义名）
    # ★330批刀4：料龄 —— 与 hub **上次跨阈消费**的时刻对表。
    #   None = hub 从未消费过 ⇒ 不可算（**不填 0**：0 秒会被读成"刚刚产出的新料"）。
    "hub_consume_age_sec": None,
    "stale_material": 0,         # 料龄超过跨阈节奏的拍数
}

#: ★330批刀4：hub 跨阈消费节奏（≈33 分钟/轮）。料龄超过它，说明这一拍读到的
#: 是上一轮**残留**而不是"刚产出的料"——标 stale，不冒充新鲜数据。
_M330_HUB_CADENCE_SEC = 33 * 60.0

#: 入料摘要落盘位置（唯一可观测口——trigger_counts 过去只活在内存返回值里）
_M325_INTAKE_SUMMARY_REL = os.path.join(
    "data", "reports", "evolution", "intake_summary_latest.json")

# ===== ★326批刀5：需求入队**逐拍台账**（A5 断言的唯一数据源）=====
# 为什么必须新增：325刀0 只落"最新一拍的汇总计数"，**没有条目身份**⇒
#   "某条需求驱动条目在 N 拍内是否被承接/作废"在数据上根本无法判定，
#   A5 只能写成"永远 UNAVAILABLE"。故本批补一个 append-only 台账：
#   每拍一行，行内带本拍入队条目明细（key/file/method/message）与 tick 序。
_M326_INTAKE_JOURNAL_REL = os.path.join(
    "data", "reports", "evolution", "intake_journal.jsonl")
#: 拍序计数器（进程内；重启后由台账里最大 tick 续号，绝不复用旧号）
_GROWTH_INTAKE_TICKS: dict = {"ticks": 0}


def _m326_intake_journal_path(root: str | None = None) -> str:
    return os.path.join(root or os.getcwd(), _M326_INTAKE_JOURNAL_REL)


def _m326_item_key(item: dict) -> str:
    """需求条目身份键：优先 file::method（可比对 patch_history），退化为 message 前缀。"""
    _f = str((item or {}).get("file") or "").strip()
    _m = str((item or {}).get("method") or "").strip()
    if _f or _m:
        return "%s::%s" % (_f, _m)
    _msg = str((item or {}).get("message") or "").strip()
    return "msg::" + _msg[:80]


def _m326_next_tick(root: str | None = None) -> int:
    """取下一拍号：进程内计数 + 台账最大值 取大者 +1（重启后接续，不回头）。"""
    _last = 0
    try:
        _p = _m326_intake_journal_path(root)
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        import json as _json
                        _last = max(_last, int((_json.loads(_line) or {}).get("tick", 0) or 0))
                    except (ValueError, TypeError):
                        continue
    except OSError as _e:
        silent_exc(_e, where="EvolutionLoop::_m326_next_tick", level="debug")
    _cur = max(int(_GROWTH_INTAKE_TICKS.get("ticks", 0)), _last)
    _GROWTH_INTAKE_TICKS["ticks"] = _cur + 1
    return _cur + 1


def _m326_append_intake_journal(rec: dict, root: str | None = None) -> str:
    """追加一行入队台账（失败只留痕，绝不影响发现池）。返回路径或空串。"""
    _p = _m326_intake_journal_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        import json as _json
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="EvolutionLoop::_m326_append_intake_journal", level="warning")
        return ""
    return _p


def get_intake_journal_stats(root: str | None = None) -> dict:
    """★326批刀5：台账读数（只读），供 A5 断言与日报取数。"""
    _p = _m326_intake_journal_path(root)
    _out = {"path": _p, "exists": os.path.isfile(_p), "ticks": 0, "lines": 0,
            "growth_items": 0}
    try:
        if _out["exists"]:
            import json as _json
            with open(_p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    _out["lines"] += 1
                    _d = _json.loads(_line)
                    _out["ticks"] = max(_out["ticks"], int(_d.get("tick", 0) or 0))
                    _out["growth_items"] += len(_d.get("growth_items") or [])
    except Exception as _e:
        silent_exc(_e, where="EvolutionLoop::get_intake_journal_stats", level="warning")
        _out["error"] = type(_e).__name__
    return _out


def get_growth_intake_stats() -> dict:
    """★325批刀0：需求源计数（只读拷贝），供压测报告/日报取数。"""
    return dict(_GROWTH_INTAKE_STATS)


def _m325_persist_intake_summary(summary: dict) -> str:
    """把本拍入料摘要原子落盘；失败只留痕，绝不影响发现池。"""
    _p = os.path.join(os.getcwd(), _M325_INTAKE_SUMMARY_REL)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _tmp = _p + ".tmp"
        _body = dict(summary or {})
        _body["written_at"] = time.time()
        _body["written_str"] = time.strftime("%Y-%m-%d %H:%M:%S")
        import json as _json
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            _json.dump(_body, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _p)
        return _p
    except Exception as _e:
        silent_exc(_e, where="EvolutionLoop::_m325_persist_intake_summary",
                   level="warning")
        return ""


class EvolutionLoop(SilentLogMixin):
    """完整进化闭环编排器。"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._log_analyzer = LogAnalyzer(project_root)
        self._diff_archiver = DiffArchiver(project_root)
        self._test_generator = TestGenerator(project_root)
        self._evolution_driver = EvolutionDriver(project_root)
        # CodeReviewEngine: 代码审查引擎作为额外问题发现源
        try:
            from nucleus.review import get_code_review_engine
            self._code_review = get_code_review_engine()
        except Exception as e:
            silent_exc(e, "nucleus/evolution/EvolutionLoop.py:47:进化循环异常", level="warning")
            self._code_review = None

    # ========== 阶段 1+2：发现问题 + 精准定位 ==========

    def discover_issues(self, log_file: str | None = None,
                        max_issues: int = 30) -> dict[str, Any]:
        """
        阶段 1：运行日志动态归因，发现问题并精准定位。

        返回 LogAnalyzer.analyze 的结果（含 issues 清单与 summary）。
        """
        return self._log_analyzer.analyze(
            log_file=log_file, include_runtime_metrics=True, max_issues=max_issues
        )

    def discover_code_issues(self, incremental=True, max_issues=20):
        """使用代码审查引擎发现代码问题（P0/P1级）。"""
        if self._code_review is None:
            return {"issues": [], "summary": {"total": 0, "source": "unavailable"}}
        try:
            result = self._code_review.review(incremental=incremental, use_pyright=False)
            critical = result.get_p0_issues() + result.get_p1_issues()
            critical = critical[:max_issues]
            issues = []
            for issue in critical:
                issues.append({
                    "type": f"code_review.{issue.rule}",
                    "message": issue.message,
                    "file": issue.file_path,
                    "line": issue.line,
                    "severity": issue.severity,
                    "source": "code_review",
                    "fix_available": issue.fix_available,
                })
            return {
                "issues": issues,
                "summary": {
                    "total": result.total_issues,
                    "critical": len(critical),
                    "p0": len(result.get_p0_issues()),
                    "p1": len(result.get_p1_issues()),
                    "source": "code_review",
                }
            }
        except Exception as e:
            return {"issues": [], "summary": {"total": 0, "error": str(e)}}

    # ★T3修复（N1/P1）：问题字段契约归一化。
    #   本链路有两条来源，产出的键名**互不一致**，而下游统一按 file/type/organ/method 读取：
    #     - LogAnalyzer（日志分析）：产出 file_path / error_type，没有 file / type
    #       （见 nucleus/analysis/LogAnalyzer.py:180-192，_get_or_create 的键名）
    #     - CodeReview（代码审查）：产出 file / type，没有 organ / method
    #       （见上方 discover_code_issues:77-85）
    #   消费方 SafeEvolutionExecutor 在 :578-580 读 type/organ/method，
    #   于是日志类问题的 type 与 method **恒为空字符串**，
    #   导致 :586 的 `if self_inspector and _organ and _method` 不成立，
    #   代码片段取不到 → 整个修复流程被静默跳过，只剩一行 DEBUG。
    #   本方法把两条来源统一到同一套键名，不动各自的产出端（避免扩大回归面）。
    ISSUE_KEY_ALIASES = {
        "file": ("file", "file_path", "path"),
        "type": ("type", "error_type", "issue_type", "rule"),
        "method": ("method", "func", "function"),
        "organ": ("organ", "organ_name", "class_name"),
        "line": ("line", "line_no", "lineno"),
        "description": ("description", "message", "msg", "detail"),
    }

    @classmethod
    def _normalize_issue(cls, raw: dict) -> dict:
        """把任意来源的 issue 归一化为下游契约 {file,type,organ,method,line,description}。"""
        _n = dict(raw or {})
        for _canon, _aliases in cls.ISSUE_KEY_ALIASES.items():
            if _n.get(_canon):
                continue
            for _alias in _aliases:
                _v = raw.get(_alias)
                if _v not in (None, "", 0):
                    _n[_canon] = _v
                    break
        for _canon in cls.ISSUE_KEY_ALIASES:
            _n.setdefault(_canon, "" if _canon != "line" else 0)
        # 原始键全部保留，不破坏既有调用方
        return _n

    def discover_all_issues(self, log_file=None, max_issues=30, include_code_review=True):
        """综合发现问题（运行日志 + 代码审查 + self_inspector 全量扫描），去重后返回。

        ★9-问题1修复（2026-09-05）：原实现只有两个问题源——
          ① LogAnalyzer（日志 ERROR/CRITICAL，运行健康时恒为 0）
          ② CodeReviewEngine.review(incremental=True)（只查最近 1 小时改动的文件，
             框架稳定运行时代码不再改动 → 恒为 0）
        于是「12 轮自主进化发现 0 问题」，与健康诊断的「412 个代码问题」矛盾。
        根因：健康诊断用的 self_inspector.detect_code_issues()（全量静态扫描），
        这条最重要的代码问题源**从未接入进化循环**。
        现增加第三源：self_inspector 全量扫描，但**限流**——只取 high/medium 级
        且每次最多 max_issues 条，避免 412 个一次性塞爆修复队列。
        """
        log_result = self.discover_issues(log_file=log_file, max_issues=max_issues)
        all_issues = list(log_result.get("issues", []))
        code_count = 0
        if include_code_review:
            code_result = self.discover_code_issues(max_issues=max_issues // 2)
            all_issues.extend(code_result.get("issues", []))
            code_count = len(code_result.get("issues", []))
        # ★9-问题1：接入 self_inspector 全量扫描作为第三问题源（限流）
        inspector_count = 0
        try:
            from nucleus.self_inspector import get_self_inspector
            _inspector = get_self_inspector()
            if _inspector is not None:
                # ★9-问题3修复（2026-09-06）：把「只取 high/medium」与
                #   「跳过 dead_code」下沉到 detect_code_issues 内部执行。
                #   原写法先全量 detect_code_issues() 再在本地过滤，实测代价是：
                #     417 个问题中 362 个（87%）是 dead_code 且 severity 恒为 low，
                #     而这 362 条本检测器耗时约占整轮 9 分钟的绝大部分
                #     （O(方法数×全库字符数) 双重正则，累计扫描 13.2G 字符）。
                #   即：每轮进化花近 10 分钟算出一批 100% 会被丢弃的结果，
                #   真正要修的 20 个 high/medium 问题反而被堵在后面。
                #   下沉后跳过该检测器，耗时与噪声同时消除。
                #   兼容：旧版 self_inspector 无这两个参数 → TypeError 时降级原路径。
                try:
                    # ★往期批次 相关任务：进化循环同时跳过两个**重扫描**检测器
                    #   （dead_code 历史 9 分钟级；cc_growth 全树 radon 实测 ~5.9s）——
                    #   二者均为全库级静态扫描，与进化修复队列低相关，不应挤占循环预算。
                    _inspector_issues = _inspector.detect_code_issues(
                        skip_detectors=["dead_code", "cc_growth"],
                        min_severity="medium",
                    )
                except TypeError:
                    _inspector_issues = _inspector.detect_code_issues()
                if _inspector_issues:
                    # 只取 high/medium 级（low/info 是 long_method/print_debug 等
                    # 低价值问题，不应进入修复队列），且按 severity 优先级排序。
                    # 注：新路径下 detect_code_issues 已完成该过滤，此处保留是
                    # 为了兼容降级路径（旧版签名）以及未来新增的 high/medium 检测器。
                    _prio = {"high": 0, "medium": 1, "low": 2, "info": 3}
                    _filtered = [
                        _i for _i in _inspector_issues
                        if _i.get("severity", "medium") in ("high", "medium")
                    ]
                    _filtered.sort(
                        key=lambda _i: _prio.get(_i.get("severity", "medium"), 9)
                    )
                    # 限流：self_inspector 源最多贡献 max_issues 条
                    _filtered = _filtered[:max_issues]
                    # self_inspector 的问题字段已含 file/organ/method/line/type，
                    # 与下游契约一致，标记来源后并入
                    for _i in _filtered:
                        _i = dict(_i)
                        _i.setdefault("source", "self_inspector")
                        all_issues.append(_i)
                    inspector_count = len(_filtered)
        except Exception as _insp_err:
            # self_inspector 不可用时静默降级（不因问题源失败而中断进化循环）
            _module_logger.debug(
                f"[自主进化] self_inspector 问题源不可用: {_insp_err}")
        # ★T3：归一化必须在去重之前，否则去重键会用到错误的字段名。
        all_issues = [self._normalize_issue(_i) for _i in all_issues
                      if isinstance(_i, dict)]
        seen = set()
        unique = []
        for issue in all_issues:
            # ★T3：原去重键 f"{file}:{line}:{type}" 对日志类问题而言
            #   file 为空、line 为 0，于是所有日志问题的键都退化成同一个
            #   ":{line}:{type}" → N 个问题被吞成 1 个
            #   （生产实测：4 条 ERROR 日志去重后只剩 1 个，与日志逐字吻合）。
            #   判据应使用「file 与 line 是否缺失」，而非整个 key 是否等于某字面量——
            #   type 被归一化填上值之后，key 不再形如 ":0:"，
            #   若按字面量判断会漏掉这一分支（首版实现即犯此错，已修正）。
            _fkey = str(issue.get("file", "") or "")
            _lkey = issue.get("line", 0) or 0
            if not _fkey and not _lkey:
                # 无代码位置锚点：改用 organ + type + 消息前 80 字符区分，
                # 保证「同一模块的不同报错」不会被误合并为一条。
                key = (f"nolocation:{issue.get('organ', '')}:"
                       f"{issue.get('type', '')}:"
                       f"{str(issue.get('description', ''))[:80]}")
            else:
                key = f"{_fkey}:{_lkey}:{issue.get('type', '')}"
            if key not in seen:
                seen.add(key)
                unique.append(issue)
        # ★第311批 刀1：**受控入料**（≤1 条）放在 max_issues 截断**之后**并入——
        #   否则池子填满时这一条会被 [:max_issues] 直接切掉，「池 0→有料」永远是假的。
        #   这些条目的 severity 仍是 info、review_only=True，
        #   由下游按 intake 标记区分「判死产物」与「待复核产物」。
        _intake = self._m311_collect_review_intake()
        # ★316批刀3：需求源入队（growth 内省并轨）——缺陷优先、需求补空窗。
        #   仅当缺陷源本拍未取到料时，才补一条需求源；继承同一硬上限（每拍≤1）。
        _growth_intake = self._m316_collect_growth_intake(
            _defect_taken=len(_intake), _existing=unique)
        _final = unique[:max_issues] + _intake + _growth_intake
        # ★316批刀3：给所有入池条目补可观测 trigger 字段（defect / growth_need），
        #   不新开第三字段；需求驱动补丁占比 := count(growth_need)/count(all) 可算。
        for _i in _final:
            _i.setdefault("trigger", "defect")
        # ★325批刀0：sum 落盘（唯一可观测口）——旧实现 trigger_counts 只活在
        #   内存返回值里，压测报告/日报/外部复算全都读不到，需求源"有没有产出"
        #   根本无法验收（这正是 刀5 三处字段映射缺源的同一个坑）。
        # ★326批刀5：本拍需求条目明细（A5 判"某条是否被承接/作废"的靶子）
        _m326_growth_items = [{
            "key": _m326_item_key(_i),
            "file": str(_i.get("file") or ""),
            "method": str(_i.get("method") or ""),
            "message": str(_i.get("message") or "")[:120],
            "organ": str(_i.get("organ") or ""),
        } for _i in (_growth_intake or []) if isinstance(_i, dict)]
        _m326_append_intake_journal({
            "tick": _m326_next_tick(),
            "ts": time.time(),
            "ts_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(_final),
            "review_intake": len(_intake),
            "growth_intake": len(_growth_intake),
            "trigger_counts": {
                "defect": sum(1 for _i in _final if _i.get("trigger") == "defect"),
                "growth_need": sum(1 for _i in _final
                                   if _i.get("trigger") == "growth_need"),
            },
            "growth_items": _m326_growth_items,
        })
        _m325_persist_intake_summary({
            "total": len(_final),
            "review_intake": len(_intake),
            "growth_intake": len(_growth_intake),
            "trigger_counts": {
                "defect": sum(1 for _i in _final if _i.get("trigger") == "defect"),
                "growth_need": sum(1 for _i in _final
                                   if _i.get("trigger") == "growth_need"),
            },
            "growth_stats": dict(_GROWTH_INTAKE_STATS),
            # ★326批刀5：明细也进最新快照（台账是主源，快照带一份便于人读）
            "growth_items": _m326_growth_items,
            "tick": int(_GROWTH_INTAKE_TICKS.get("ticks", 0)),
        })
        return {
            "issues": _final,
            "summary": {
                "total": len(_final),
                "from_log": len(log_result.get("issues", [])),
                "from_code_review": code_count,
                "from_self_inspector": inspector_count,
                "review_intake": len(_intake),
                "growth_intake": len(_growth_intake),
                "trigger_counts": {
                    "defect": sum(1 for _i in _final if _i.get("trigger") == "defect"),
                    "growth_need": sum(1 for _i in _final
                                       if _i.get("trigger") == "growth_need"),
                },
            }
        }

    def _m311_collect_review_intake(self) -> list:
        """★第311批 刀1：待复核清单的受控入料（单批 ≤1 条；配置 0 即关）。

        为什么不在这里现跑 dead_code 检测器：它的耗时是**分钟级**（历史实测 9 分钟），
        塞进每轮进化发现等于把循环预算全吃掉。改为读 self_inspector 最近一次
        全量扫描留下的统计明细（`get_dead_code_review_stats()["items"]`），
        没有明细就如实返回 0 条并在 summary 里可见。
        """
        try:
            import config as _cfg311
            _lim = int(getattr(_cfg311, "REVIEW_INTAKE_MAX_PER_BATCH", 1) or 0)
        except Exception as _ie:
            _module_logger.debug("[311刀1] 读 REVIEW_INTAKE_MAX_PER_BATCH 失败，按 1: %s", _ie)
            _lim = 1
        if _lim <= 0:
            return []
        try:
            from nucleus.self_inspector import build_review_intake
            return build_review_intake(limit=min(1, _lim))
        except Exception as _e:
            _module_logger.debug("[311刀1] 待复核入料失败（不影响发现池）: %s: %s",
                                 type(_e).__name__, _e)
            return []

    def _m316_collect_growth_intake(self, _defect_taken=0, _existing=None) -> list:
        """★316批刀3：需求源入队（growth 内省并轨 · 最小落地）。

        路由：复用同一硬上限（REVIEW_INTAKE_MAX_PER_BATCH，每拍≤1）；**缺陷优先**——
        缺陷源本拍已取到料（_defect_taken ≥ 上限）时不补，仅填空窗。
        冲突：同一 (file, method) 若缺陷源已命中，需求源让位。
        优先级：一律 requires_confirmation=True，不进 auto_apply。
        五段留痕之「补丁」段：[316刀3][补丁] ... 可 grep。
        """
        try:
            import config as _cfg316
            _lim = int(getattr(_cfg316, "REVIEW_INTAKE_MAX_PER_BATCH", 1) or 0)
        except Exception as _e:
            _module_logger.debug("[316刀3] 读 REVIEW_INTAKE_MAX_PER_BATCH 失败，按 1: %s", _e)
            _lim = 1
        if _lim <= 0 or _defect_taken >= _lim:
            return []
        _topic = ""
        _hyp = ""
        _hub_consume_ts = None
        # ★331批刀8：growth 条目本体（用于取需求落点 file/method）
        _hyp_entry: dict = {}
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            _hub = get_verification_learning_hub()
            # ★330批刀4：hub 上次跨阈消费时刻（用于算料龄，不参与让位判定）
            _hub_consume_ts = getattr(_hub, "_last_consume_ts", None)
            for _entry in reversed(list(getattr(_hub, "_entries", []) or [])):
                if (str(_entry.get("task_type", "")) == "growth_hypothesis"
                        or str(_entry.get("origin", "")) == "growth"):
                    _hyp = str(_entry.get("input_summary", ""))[:200]
                    _lr = _entry.get("local_result") or {}
                    _topic = str(_lr.get("topic", "") or "")[:80]
                    # ★331批刀8：留住条目本体 —— 旧实现只抽了两个字符串就把条目丢了，
                    #   于是位置信息无从取，`_file/_method` 只能硬编码空 ⇒
                    #   非空冲突分支**结构性不可达**（330 刀4 如实申报的那条）。
                    _hyp_entry = _entry if isinstance(_entry, dict) else {}
                    break
        except Exception as _e:
            _module_logger.debug("[316刀3] 读 hub growth 料失败（不影响发现池）: %s", _e)
        if not _topic and not _hyp:
            _GROWTH_INTAKE_STATS["no_material"] = int(
                _GROWTH_INTAKE_STATS.get("no_material", 0)) + 1
            return []
        # ★331批刀8：需求源**真实来源赋值** —— 不再是硬编码空串。
        #   取值顺序：条目本体 → local_result（需求落点）→ 兜底空串。
        #   ★料本身没带位置时空串仍在，但那是"这条料没有位置"，不是"代码写死为空"：
        #     两者在留痕上能分开（一个是有来源但为空，一个是压根没来源）。
        _src_lr = _hyp_entry.get("local_result") or {}
        _file = str(_hyp_entry.get("file") or _hyp_entry.get("target_file")
                    or _src_lr.get("file") or _src_lr.get("target_file") or "")
        _method = str(_hyp_entry.get("method") or _hyp_entry.get("target_method")
                      or _src_lr.get("method") or _src_lr.get("target_method") or "")
        # ★325批刀0：**空键不得参与冲突判定**。
        #   旧实现 _file/_method 恒 ""（需求源本就没有具体代码位置），而缺陷源里
        #   大量 {file:"", method:""} 的日志类条目 ⇒ ("","") 必在 _conflict ⇒
        #   需求源**结构性必然让位**（烛微325§0-5；台账 trigger 84 条全 None 与此一致）。
        #   修法：只有**非空** (file, method) 才判冲突；空键仅计数留痕，不拦。
        #   ★331批刀8：现在 _file/_method 有真实来源 ⇒ 非空分支**可达**（料带位置时命中）。
        _conflict = {(str(_i.get("file") or ""), str(_i.get("method") or ""))
                     for _i in (_existing or [])}
        _GROWTH_INTAKE_STATS["calls"] = int(_GROWTH_INTAKE_STATS.get("calls", 0)) + 1
        _conflict_key = None
        if _file or _method:
            if (_file, _method) in _conflict:
                _conflict_key = {"file": _file, "method": _method,
                                 "why": "nonempty_key_conflict"}
        elif ("", "") in _conflict:
            # 空键：**不判冲突**，只记录"候选里确实有空键"，与"没判"可区分
            _GROWTH_INTAKE_STATS["empty_key_hits"] = int(
                _GROWTH_INTAKE_STATS.get("empty_key_hits", 0)) + 1
            # ★330批刀4：与 yielded（真同文件冲突让位）**分开计数**，
            #   否则"空键被忽略"与"真冲突让位"会混成一个数，看不出是哪一类。
            _GROWTH_INTAKE_STATS["empty_key_ignored"] = int(
                _GROWTH_INTAKE_STATS.get("empty_key_ignored", 0)) + 1
        if _conflict_key:
            _GROWTH_INTAKE_STATS["yielded"] = int(
                _GROWTH_INTAKE_STATS.get("yielded", 0)) + 1
            _GROWTH_INTAKE_STATS["yielded_nonempty"] = int(
                _GROWTH_INTAKE_STATS.get("yielded_nonempty", 0)) + 1
            # ★330批刀4：把**具体冲突键**落进统计（只有日志的话，复算要翻日志文件）
            _GROWTH_INTAKE_STATS["last_conflict_key"] = dict(_conflict_key)
            _module_logger.info(
                "[316刀3][补丁][330刀4] 需求源让位: topic=%s key_kind=%s "
                "conflict_key=%s（另一类 empty_key 冲突只计数不让位）",
                _topic, _conflict_key.get("why"), _conflict_key)
            return []
        _rec = {
            "type": "growth_need",
            "message": (_hyp or _topic)[:300],
            "file": _file,
            "line": 0,
            "method": _method,
            "organ": "潜意识",
            "severity": "info",
            "source": "subconscious",
            "intake": "growth_intake",
            "intake_reason": "growth_introspection",
            "requires_confirmation": True,
            "trigger": "growth_need",
        }
        # ★325批刀0：留痕带上冲突判定结论，便于复核"为什么这拍进来了"
        _rec["conflict_key"] = None
        _rec["conflict_key_why"] = "none"
        _rec["m325_empty_key_ignored"] = bool(_file == "" and _method == "")
        # ★330批刀4：料龄与 hub 消费时刻对表 —— 差值超过跨阈节奏 ⇒ 这料是上一轮残留，
        #   如实标 stale。绝不用 0 冒充"刚产出的新料"。
        _age = None
        if isinstance(_hub_consume_ts, (int, float)) and float(_hub_consume_ts) > 0:
            _age = max(0.0, time.time() - float(_hub_consume_ts))
            _GROWTH_INTAKE_STATS["hub_consume_age_sec"] = round(_age, 1)
        _stale = bool(_age is not None and _age > _M330_HUB_CADENCE_SEC)
        if _stale:
            _GROWTH_INTAKE_STATS["stale_material"] = int(
                _GROWTH_INTAKE_STATS.get("stale_material", 0)) + 1
        _rec["material_age_sec"] = (round(_age, 1) if _age is not None else None)
        _rec["material_stale"] = _stale
        _GROWTH_INTAKE_STATS["admitted"] = int(
            _GROWTH_INTAKE_STATS.get("admitted", 0)) + 1
        _module_logger.info(
            "[316刀3][补丁] growth_need 入队(requires_confirmation=True, 不自动应用): "
            "topic=%s", _topic or (_hyp[:80]))
        return [_rec]

    # ========== 阶段 3+4：修改代码 + diff 归档 ==========

    def archive_patch(self, patch: dict[str, Any],
                      issue: dict[str, Any] | None = None) -> str:
        """
        阶段 4：为补丁生成 unified diff 归档 + 全链路溯源。

        返回 diff 文本。
        """
        _patch_id = patch.get("id", f"patch_{int(time.time())}")
        _file = patch.get("file", "")
        _method = patch.get("method", "")
        _diff = self._diff_archiver.generate_diff(
            _patch_id, _file,
            patch.get("original_code", ""),
            patch.get("modified_code", ""),
        )
        self._diff_archiver.record_trace(_patch_id, {
            "file": _file,
            "method": _method,
            "issue": {
                "type": (issue or {}).get("type", patch.get("issue_type", "")),
                "description": (issue or {}).get("message", patch.get("description", "")),
                "source": (issue or {}).get("error_type", "self_inspect"),
            },
            "location": {
                "file": _file,
                "line": (issue or {}).get("line", patch.get("line", 0)),
                "method": _method,
            },
            "change": {
                "diff_file": f"{_patch_id}.diff",
                "summary": patch.get("diff_summary", ""),
            },
        })
        return _diff

    # ========== 阶段 5：动态测试 ==========

    def test_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """阶段 5：为补丁动态生成冒烟测试并运行。"""
        return self._test_generator.generate_and_run(patch)

    # ========== 阶段 6+7：本地应用 + 自应用重启 ==========

    def request_apply(self, approved_count: int) -> bool:
        """阶段 6/7：写入「立即应用」请求标记，由主循环检测后应用+重启。"""
        return self._evolution_driver.request_apply_now(approved_count)

    # ========== 阶段 8：健康度比较 ==========

    def judge_evolution(self, baseline_score: float | None = None,
                        runtime_metrics: dict[str, Any] | None = None,
                        system_snapshot: dict[str, Any] | None = None,
                        log_file: str | None = None) -> dict[str, Any]:
        """阶段 8：对比应用前后健康度，判定进化是否有效。"""
        return self._evolution_driver.judge_evolution(
            baseline_score=baseline_score,
            runtime_metrics=runtime_metrics,
            system_snapshot=system_snapshot,
            log_file=log_file,
        )

    # ========== 全流程串联（一键闭环） ==========

    def run_full_loop(self, patches: list[dict[str, Any]],
                      issues: list[dict[str, Any]] | None = None,
                      auto_apply: bool = True) -> dict[str, Any]:
        """
        一键执行完整进化闭环：对每个补丁走「归档 → 测试 → 应用请求」流水线。

        返回:
            {"archived": N, "tested": N, "test_passed": N, "apply_requested": bool,
             "details": [每补丁的阶段结果]}
        """
        _details = []
        _archived = 0
        _tested = 0
        _test_passed = 0
        _approved = []

        _issues = issues or []

        # ★P3 补丁间零冲突检查：同一文件的多个补丁修改区域不重叠
        _conflicts = self._check_patch_conflicts(patches)
        if _conflicts:
            for _c in _conflicts:
                _details.append({
                    "patch_id": _c.get("patch_id", ""),
                    "file": _c.get("file", ""),
                    "error": f"补丁冲突: {_c.get('reason', '')}",
                    "skipped": True,
                })
            # 过滤掉冲突补丁
            _conflict_ids = {_c.get("patch_id") for _c in _conflicts}
            patches = [p for p in patches if p.get("id") not in _conflict_ids]

        for _patch in patches:
            _detail = {"patch_id": _patch.get("id", ""), "file": _patch.get("file", "")}
            try:
                # 阶段 4：diff 归档
                _issue = _issues[0] if _issues else None
                self.archive_patch(_patch, _issue)
                _detail["archived"] = True
                _archived += 1

                # 阶段 5：动态测试
                _test = self.test_patch(_patch)
                _detail["test"] = _test
                _tested += 1
                if _test.get("passed"):
                    _test_passed += 1

                # 记录测试结果到溯源
                _patch_id = _patch.get("id", "")
                self._diff_archiver.record_trace(_patch_id, {
                    "file": _patch.get("file", ""),
                    "method": _patch.get("method", ""),
                    "test": {
                        "script": _test.get("script", ""),
                        "passed": _test.get("passed", False),
                        "output": _test.get("output", "")[:500],
                    },
                })

                # 阶段 6：测试通过的补丁，标记为待应用
                if _test.get("passed") and _patch.get("status") == "approved":
                    _approved.append(_patch)
            except Exception as _e:
                _detail["error"] = str(_e)

            _details.append(_detail)

        # 阶段 7：写入应用请求
        _apply_requested = False
        if auto_apply and _approved:
            _apply_requested = self.request_apply(len(_approved))

        # ★P1补强：阶段8——运行时效果验证与前后对比
        _runtime_verification = None
        if _apply_requested and _approved:
            try:
                _runtime_verification = self._verify_runtime_effect(_approved)
                self._log(LogLevel.INFO,
                         f"运行时效果验证: {_runtime_verification.get('status', 'unknown')}, "
                         f"健康度变化={_runtime_verification.get('health_delta', 'N/A')}")
            except Exception as _ve:
                self._log(LogLevel.DEBUG, f"运行时效果验证异常: {_ve}")

        # ★P4 自动生成落地记录
        _result = {
            "archived": _archived,
            "tested": _tested,
            "test_passed": _test_passed,
            "apply_requested": _apply_requested,
            "approved_count": len(_approved),
            "details": _details,
            "runtime_verification": _runtime_verification,  # ★P1: 运行时效果验证结果
        }
        if _approved:
            _record = self.generate_evolution_record(_approved, _result)
            _record_path = self.write_evolution_record(_record)
            _result["evolution_record"] = _record_path
        return _result

    def _verify_runtime_effect(self, approved_patches: list[dict]) -> dict[str, Any]:
        """★P1补强：运行时效果验证——补丁应用后对比前后健康度。

        流程:
        1. 记录应用前的基线健康度
        2. 等待补丁应用（由主循环检测apply_request后执行）
        3. 收集应用后的运行时指标
        4. 对比前后健康度，判定进化是否有效
        5. 如果效果不佳，建议回滚或调整

        注意: 本方法在补丁应用请求发出后立即调用，实际效果验证
        由框架重启后的健康度比较完成。此处记录验证计划和基线。
        """
        import time as _time
        _verification = {
            "status": "pending",
            "baseline_health": None,
            "post_health": None,
            "health_delta": None,
            "patches_verified": [],
            "verification_plan": {
                "wait_seconds": 60,  # 等待框架重启并稳定运行
                "metrics_to_collect": ["error_rate", "response_time", "knowledge_quality"],
                "comparison_method": "before_after",
            },
            "timestamp": _time.time(),
        }

        try:
            # 1. 记录应用前的基线健康度
            if hasattr(self, '_health_scorer') and self._health_scorer:
                _baseline = self._health_scorer.get_current_score()
                _verification["baseline_health"] = _baseline
            elif hasattr(self, '_evolution_driver') and self._evolution_driver:
                _baseline = getattr(self._evolution_driver, '_last_health_score', None)
                _verification["baseline_health"] = _baseline

            # 2. 记录待验证的补丁
            for _patch in approved_patches:
                _verification["patches_verified"].append({
                    "patch_id": _patch.get("id", ""),
                    "file": _patch.get("file", ""),
                    "method": _patch.get("method", ""),
                    "expected_improvement": _patch.get("expected_improvement", ""),
                })

            # 3. 尝试立即进行健康度比较（如果框架已重启）
            try:
                _judge_result = self.judge_evolution(
                    baseline_score=_verification["baseline_health"],
                    log_file="logs/pulse.log",
                )
                if _judge_result and _judge_result.get("post_score") is not None:
                    _verification["post_health"] = _judge_result.get("post_score")
                    if _verification["baseline_health"] is not None:
                        _verification["health_delta"] = round(
                            _verification["post_health"] - _verification["baseline_health"], 2)
                    _verification["status"] = "completed"
                    _verification["evolution_effective"] = _judge_result.get("effective", False)
                else:
                    _verification["status"] = "scheduled"
                    _verification["note"] = "框架重启后将自动进行健康度比较"
            except Exception as _je:
                _verification["status"] = "scheduled"
                _verification["note"] = f"健康度比较将在框架重启后执行: {_je}"

            # 4. 如果效果不佳，记录回滚建议
            if (_verification.get("health_delta") is not None and
                    _verification["health_delta"] < -5):
                _verification["rollback_recommended"] = True
                _verification["rollback_reason"] = f"健康度下降{abs(_verification['health_delta'])}分，建议回滚"

        except Exception as e:
            _verification["status"] = "error"
            _verification["error"] = str(e)

        return _verification


    # ========== ★P1 参数补丁自动应用 ==========

    def auto_apply_param_patches(self, max_apply: int = 3) -> dict[str, Any]:
        """★P1: 自动应用待处理的参数补丁（类似代码补丁的自动应用流程）。

        流程:
        1. 获取待处理的参数补丁
        2. 验证补丁（安全范围/类型/幅度）
        3. 应用验证通过的补丁（写入config_override.json，触发热加载）
        4. 调度运行时效果验证
        5. 效果不佳的补丁自动回滚

        Args:
            max_apply: 本次最多应用的补丁数（压力均衡）

        Returns:
            应用结果统计
        """
        try:
            from nucleus.evolution.ParamPatchManager import get_param_patch_manager
            ppm = get_param_patch_manager()

            # 1. 自动应用待处理补丁
            apply_result = ppm.auto_apply_pending(max_apply=max_apply, auto_verify=True)

            # 2. 验证已应用补丁的效果
            effect_result = ppm.verify_applied_patches_effect()

            # 3. 自动回滚效果不佳的补丁
            rollback_result = ppm.auto_rollback_ineffective()

            # 4. 获取统计
            stats = ppm.get_stats()

            return {
                "apply": apply_result,
                "effect_verification": effect_result,
                "rollback": rollback_result,
                "stats": stats,
            }
        except Exception as e:
            return {"error": str(e), "apply": {"attempted": 0, "applied": 0}}

    # ========== ★P3 补丁间零冲突检查 ==========

    def _check_patch_conflicts(self, patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """检查同一文件的多个补丁是否修改了重叠区域（零冲突显式检查）。

        通过 original_code 的前 50 字符作为区域键，同一文件的多个补丁
        如果区域键重叠，则判定为冲突，避免应用时互相覆盖。
        """
        _conflicts: list[dict[str, Any]] = []
        _file_regions: dict[str, list[tuple[str, str]]] = {}  # file -> [(patch_id, region_key)]

        for _patch in patches:
            _file = _patch.get("file", "")
            _orig = (_patch.get("original_code", "") or "").strip()[:50]
            if not _file or not _orig:
                continue
            _file_regions.setdefault(_file, []).append((_patch.get("id", ""), _orig))

        for _file, _regions in _file_regions.items():
            if len(_regions) <= 1:
                continue
            # 检查区域键是否有重叠（完全相同或一个包含另一个）
            for i in range(len(_regions)):
                for j in range(i + 1, len(_regions)):
                    _id1, _r1 = _regions[i]
                    _id2, _r2 = _regions[j]
                    if _r1 == _r2 or _r1 in _r2 or _r2 in _r1:
                        _conflicts.append({
                            "patch_id": _id2,  # 跳过后一个
                            "file": _file,
                            "reason": f"与补丁 {_id1} 修改区域重叠",
                        })
        return _conflicts

    # ========== ★P4 记录沉淀自动化 ==========

    def generate_evolution_record(self, patches: list[dict[str, Any]],
                                   test_results: dict[str, Any]) -> str:
        """★P4：补丁应用成功后自动生成落地记录草稿。

        生成结构化的变更记录，供人工审核后追加到落地记录文档。
        """
        if not patches:
            return ""
        _lines = [
            "## 自主迭代记录（自动生成）",
            "",
            f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 补丁数量: {len(patches)}",
            f"- 测试通过: {test_results.get('test_passed', 0)}/{test_results.get('tested', 0)}",
            "",
            "### 变更清单",
            "",
        ]
        for _p in patches:
            _lines.append(f"- **{_p.get('file', '')}** `{_p.get('method', '')}`")
            _lines.append(f"  - 类型: {_p.get('issue_type', '')}")
            _lines.append(f"  - 描述: {_p.get('description', '')}")
            _lines.append(f"  - 改动比例: {_p.get('change_ratio', 'N/A')}")
            _lines.append(f"  - 唯一定位: {_p.get('original_unique', 'N/A')}")
            _lines.append("")
        _lines.append("### 验证结果")
        _lines.append("")
        _lines.append(f"- 归档: {test_results.get('archived', 0)}")
        _lines.append(f"- 测试: {test_results.get('tested', 0)}")
        _lines.append(f"- 通过: {test_results.get('test_passed', 0)}")
        _lines.append(f"- 应用请求: {test_results.get('apply_requested', False)}")
        _lines.append("")
        return "\n".join(_lines)

    def write_evolution_record(self, record: str, record_dir: str | None = None) -> str:
        """★P4：将自动生成的落地记录写入文件。

        写入到 data/evolution/records/ 目录，文件名含时间戳。
        """
        if not record:
            return ""
        _dir = record_dir or os.path.join(self._project_root, "data", "evolution", "records")
        os.makedirs(_dir, exist_ok=True)
        _filename = f"evolution_{time.strftime('%Y%m%d_%H%M%S')}.md"
        _path = os.path.join(_dir, _filename)
        try:
            with open(_path, "w", encoding="utf-8") as _f:
                _f.write(record)
            return _path
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.EvolutionLoop::write_evolution_record L614")
            return ""

    def update_panorama_status(self, completed_items: list[dict[str, Any]],
                                panorama_file: str | None = None) -> str:
        """★P4：生成全景盘点状态更新建议（JSON格式，供人工审核后同步到HTML）。

        不直接修改 HTML（避免破坏格式），而是生成结构化的更新建议，
        写入 data/evolution/panorama_updates/ 目录。
        """
        if not completed_items:
            return ""
        import json as _json
        _dir = os.path.join(self._project_root, "data", "evolution", "panorama_updates")
        os.makedirs(_dir, exist_ok=True)
        _update = {
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "completed_count": len(completed_items),
            "items": [
                {
                    "category": _item.get("category", ""),
                    "name": _item.get("name", ""),
                    "status": "completed",
                    "description": _item.get("description", ""),
                    "verification": _item.get("verification", ""),
                }
                for _item in completed_items
            ],
            "action": "请将以上项在全景盘点HTML中标记为已完成",
        }
        _filename = f"panorama_update_{time.strftime('%Y%m%d_%H%M%S')}.json"
        _path = os.path.join(_dir, _filename)
        try:
            with open(_path, "w", encoding="utf-8") as _f:
                _json.dump(_update, _f, ensure_ascii=False, indent=2)
            return _path
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.EvolutionLoop::update_panorama_status L650")
            return ""


# ========== 便捷函数 ==========

def get_evolution_loop(project_root: str) -> EvolutionLoop:
    """获取 EvolutionLoop 实例。"""
    return EvolutionLoop(project_root)


if __name__ == "__main__":
    # 自测：发现日志问题 + 归档 + 测试一个示例补丁
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _loop = EvolutionLoop(_root)

    # 阶段 1：发现问题
    _r = _loop.discover_issues()
    print(f"发现问题: {_r['summary']['unique_locations']} 个唯一定位, "
          f"Traceback={_r['summary']['tracebacks']}")

    # 阶段 4+5：归档 + 测试示例补丁
    _patch = {
        "id": "loop_smoke",
        "file": "nucleus/evolution/HealthScore.py",
        "method": "compute_health_score",
        "original_code": "def compute_health_score():\n    return None\n",
        "modified_code": "def compute_health_score(project_root):\n    return {'score': 100}\n",
        "diff_summary": "示例：修正 compute_health_score 签名",
        "status": "approved",
    }
    _full = _loop.run_full_loop([_patch], auto_apply=False)
    print(f"全流程: 归档={_full['archived']}, 测试={_full['tested']}, "
          f"通过={_full['test_passed']}")
