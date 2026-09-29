# -*- coding: utf-8 -*-
"""
run_self_awareness_analysis.py —— 手动触发自我认知全量分析（PHASE18 阶段一）

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 独立进程手动触发 SelfAwarenessEngine 的全量静态分析，产出**首份真实报告**
      并落盘（画像 / 文本报告 / 产出-消费明细 / 虚假闭环明细）。
机制: 注册 4 个分析器（产出-消费配对、虚假闭环检测、LogAnalyzer 整合、
      CodeReviewEngine 整合）→ run_all_analyses → generate_report → 落盘；
      首次运行自动把画像复制为基线（`baseline.json`），后续运行自动做趋势对比。
定位: PHASE18 阶段一收尾工具。★不修改 main.py 启动逻辑（启动仍只初始化不自动分析）；
      本脚本为**独立进程**，不影响框架主流程。

用法:
    python tools/run_self_awareness_analysis.py                  # 分析 + 基线对比
    python tools/run_self_awareness_analysis.py --update-baseline # 用本次结果覆盖基线
    python tools/run_self_awareness_analysis.py --skip-code-review # 跳过较慢的代码审查
    python tools/run_self_awareness_analysis.py --out-dir tmp/x   # 产物落指定目录
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.self_awareness.FakeLoopDetector import (  # noqa: E402
    FakeLoopDetector,
    analyze_fake_loops,
)
from nucleus.self_awareness.ProductionConsumptionMatcher import (  # noqa: E402
    ProductionConsumptionMatcher,
    analyze_production_consumption,
)
from nucleus.self_awareness.SelfAwarenessEngine import (  # noqa: E402
    SelfAwarenessEngine,
    get_self_awareness_engine,
    reset_self_awareness_engine,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts() -> str:
    """时间戳（YYYYMMDD_HHMMSS）。"""
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _out_dir(override: str | None = None) -> str:
    """输出目录（首次运行自动创建）。

    Args:
        override: 显式指定目录（`--out-dir`）；相对路径按项目根解析。
            ★测试用：把产物导向 `tmp/` 而不污染生产 `data/self_awareness/`。
    """
    if override:
        _p = override if os.path.isabs(override) \
            else os.path.join(_PROJECT_ROOT, override.replace("/", os.sep))
    else:
        _rel = str(getattr(config, "SELF_AWARENESS_OUTPUT_DIR",
                           "data/self_awareness"))
        _p = os.path.join(_PROJECT_ROOT, _rel.replace("/", os.sep))
    os.makedirs(_p, exist_ok=True)
    return _p


def _build_engine(skip_code_review: bool,
                  include_event_tap: bool = True,
                  include_call_graph: bool = True,
                  call_graph_only: bool = False,
                  include_knowledge_quality: bool = True) -> SelfAwarenessEngine:
    """构造引擎并注册分析器（每次全新单例，保证可重复运行）。

    ★主线第20批 T4：`include_event_tap`（默认 True）—— EventTap 运行时统计。
    ★主线第21批 T4：`include_call_graph`（默认 True）—— 跨文件调用图；
       `call_graph_only=True` 时**只注册调用图分析器**（快速调试用）。
    ★主线第23批 T5：`include_knowledge_quality`（默认 True）—— 知识质量（第五维，P3-4）；
       关闭时该维度不注册，报告显示「数据不可用」，其余四维完全不受影响（纯增量）。
    """
    reset_self_awareness_engine()
    _engine = get_self_awareness_engine()
    if call_graph_only:
        _engine.register_analyzer("call_graph",
                                  lambda _e: _e.integrate_call_graph(),
                                  "call_graph_health", replace=True)
        return _engine
    _engine.register_analyzer("production_consumption",
                              analyze_production_consumption,
                              "production_consumption", replace=True)
    _engine.register_analyzer("fake_loops", analyze_fake_loops,
                              "fake_loops", replace=True)
    _engine.register_analyzer("log_analyzer",
                              lambda _e: _e.integrate_log_analyzer(),
                              "runtime_health", replace=True)
    if not skip_code_review:
        _engine.register_analyzer("code_review",
                                  lambda _e: _e.integrate_code_review(),
                                  "code_health", replace=True)
    # ★主线第20批 T4：EventTap 运行时事件统计（动静结合）
    if include_event_tap:
        _engine.register_analyzer("event_tap",
                                  lambda _e: _e.integrate_event_tap(),
                                  "runtime_events", replace=True)
    # ★主线第21批 T4：跨文件调用图（代码结构健康度，P3-3）
    if include_call_graph:
        _engine.register_analyzer("call_graph",
                                  lambda _e: _e.integrate_call_graph(),
                                  "call_graph_health", replace=True)
    # ★主线第23批 T5：知识质量（自我认知引擎第五维，P3-4）
    if include_knowledge_quality:
        _engine.register_analyzer("knowledge_quality",
                                  lambda _e: _e.integrate_knowledge_quality(),
                                  "knowledge_health", replace=True)
    return _engine


def _dump_json(path: str, data: object) -> int:
    """落盘 JSON（返回字节数）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return os.path.getsize(path)


def _summary_lines(profile, elapsed_s: float, runs: dict) -> list:
    """生成控制台关键指标摘要。"""
    _pc = profile.production_consumption or {}
    _fl = profile.fake_loops or {}
    _rt = profile.runtime_health or {}
    _ch = profile.code_health or {}
    # ★主线第20批 T4：动静结合的两个动态维度
    _re = profile.runtime_events or {}
    _oa = profile.organ_activity or {}
    _cg = getattr(profile, "call_graph_health", None) or {}
    # ★主线第23批 T5：知识质量（第五维，P3-4）
    _kq = getattr(profile, "knowledge_health", None) or {}
    _pc_s = _pc.get("summary", {}) or {}
    _fl_s = _fl.get("summary", {}) or {}
    _sev = _ch.get("by_severity", {}) or {}
    _lines = [
        "",
        "【关键指标】",
        "  数据文件        : %s 个（疑似无消费 %s / 无产出 %s）"
        % (_pc_s.get("data_files", "-"), _pc_s.get("no_consumer", "-"),
           _pc_s.get("no_producer", "-")),
        "  动态路径调用    : %s 处（需运行时验证）" % _pc_s.get("dynamic_calls", "-"),
        "  虚假闭环候选    : %s 个（严重 %s）"
        % (_fl_s.get("candidates", "-"), _fl_s.get("critical", "-")),
        "  虚假闭环问题分布: %s" % (_fl_s.get("by_problem", {}) or "-"),
        "  运行时错误      : %s 次（严重 %s / Traceback %s）"
        % (_rt.get("errors", "-"), _rt.get("criticals", "-"),
           _rt.get("tracebacks", "-")),
        "  代码问题        : %s 个（%s）"
        % (_ch.get("total_issues", "-"),
           " / ".join("%s=%s" % (k, v) for k, v in sorted(_sev.items())) or "-"),
        "  运行时事件      : %s 个（种类 %s / 活跃来源 %s）"
        % (_re.get("total_events", "-"), _re.get("distinct_event_names", "-"),
           _re.get("active_sources", "-")),
        "  器官活跃度      : 沉默 %s / 过热 %s（集中度 %s）"
        % (len(_oa.get("silent_organs") or []) if _oa else "-",
           len(_oa.get("overactive_organs") or []) if _oa else "-",
           _oa.get("concentration_ratio", "-") if _oa else "-"),
        "  代码结构健康度  : %s/100 %s（孤立 %s / 循环 %s / 最深 %s 层 / 跨文件 %.2f%%）"
        % (_cg.get("score", "-"), _cg.get("grade", "-"),
           (_cg.get("isolated") or {}).get("count", "-"),
           (_cg.get("cyclic") or {}).get("count", "-"),
           (_cg.get("depth") or {}).get("deepest", "-"),
           float((_cg.get("coupling") or {}).get("cross_file_ratio", 0) or 0) * 100),
        "  知识质量健康度  : %s/100 %s（冲突 %s / 盲区 %s / 孤岛 %s / 老化 %s）"
        % (_kq.get("score", "-"), _kq.get("level", "-"),
           ((_kq.get("stats") or {}).get("conflicts") or {}).get("total", "-"),
           (_kq.get("stats") or {}).get("blind_spots", "-"),
           (_kq.get("stats") or {}).get("islands", "-"),
           (_kq.get("stats") or {}).get("aged", "-")),
        "",
        "【分析器耗时】",
    ]
    for _name, _info in (runs or {}).items():
        _lines.append("  %-22s %-8s %.2fs"
                      % (_name, _info.get("status", "?"),
                         float(_info.get("elapsed_ms", 0)) / 1000.0))
    _lines.append("  总计 %.2fs" % elapsed_s)
    return _lines


def main(argv: list | None = None) -> int:
    """入口。返回进程退出码（0=成功）。"""
    _ap = argparse.ArgumentParser(
        description="手动触发曈曈 PulseNet 自我认知全量分析")
    _ap.add_argument("--update-baseline", action="store_true",
                     help="用本次结果覆盖 data/self_awareness/baseline.json")
    _ap.add_argument("--skip-code-review", action="store_true",
                     help="跳过较慢的 CodeReviewEngine 全项目审查")
    _ap.add_argument("--out-dir", default=None,
                     help="产物输出目录（默认 config.SELF_AWARENESS_OUTPUT_DIR；"
                          "测试可指向 tmp/ 以隔离）")
    # ★主线第20批 T4：EventTap 运行时数据三开关
    _ap.add_argument("--include-event-tap", dest="include_event_tap",
                     action="store_true", default=True,
                     help="整合 EventTap 运行时事件统计（默认开启）")
    _ap.add_argument("--no-include-event-tap", dest="include_event_tap",
                     action="store_false",
                     help="关闭 EventTap 运行时数据整合")
    _ap.add_argument("--reset-event-tap", action="store_true", default=False,
                     help="分析前重置 EventTap 统计（此前数据将丢失，用于取干净窗口）")
    _ap.add_argument("--event-tap-window", type=int, default=0, metavar="N",
                     help="仅使用最近 N 秒事件（本批未实现过滤，传 >0 会提示）")
    # ★主线第21批 T4：调用图三开关
    _ap.add_argument("--include-call-graph", dest="include_call_graph",
                     action="store_true", default=True,
                     help="执行跨文件调用图分析（默认开启，P3-3）")
    _ap.add_argument("--no-include-call-graph", dest="include_call_graph",
                     action="store_false",
                     help="关闭调用图分析")
    _ap.add_argument("--call-graph-only", action="store_true", default=False,
                     help="只执行调用图分析（跳过其他分析器，快速调试）")
    # ★主线第23批 T5：知识质量分析（第五维，P3-4）
    _ap.add_argument("--include-knowledge-quality", dest="include_knowledge_quality",
                     action="store_true", default=True,
                     help="执行知识质量分析（默认开启，P3-4）")
    _ap.add_argument("--no-include-knowledge-quality", dest="include_knowledge_quality",
                     action="store_false",
                     help="关闭知识质量分析")
    _args = _ap.parse_args(argv)

    print("=" * 68)
    print("  曈曈 PulseNet · 自我认知全量分析（PHASE18 阶段一）")
    print("=" * 68)
    print("  项目根: %s" % _PROJECT_ROOT)
    print("  引擎开关: %s" % getattr(config, "ENABLE_SELF_AWARENESS_ENGINE", True))

    # ★主线第20批 T4：EventTap 重置（用户主动行为，必须在分析前）
    if _args.reset_event_tap:
        print("[警告] 将重置 EventTap 统计，此前数据将丢失")
        try:
            from nucleus.events.EventTap import get_event_tap
            get_event_tap().reset_stats()
            print("  [EventTap] 统计已重置（订阅保留）")
        except Exception as _e:  # 重置失败不阻断分析
            print("[警告] EventTap 重置失败: %s: %s" % (type(_e).__name__, _e))
    if _args.event_tap_window and int(_args.event_tap_window) > 0:
        print("[警告] 时间窗口过滤暂未实现，使用全量统计（参数已预留）")

    _engine = _build_engine(_args.skip_code_review, _args.include_event_tap,
                            _args.include_call_graph, _args.call_graph_only,
                            _args.include_knowledge_quality)
    print("  已注册分析器: %s" % ", ".join(_engine.list_analyzers()))
    print("-" * 68)

    _t0 = time.perf_counter()
    _profile = _engine.run_all_analyses(scope="all")
    _elapsed = time.perf_counter() - _t0

    # ★主线第20批 T4：器官活跃度分析（基于 runtime_events 的来源分布）
    if _args.include_event_tap:
        _oa = _engine.analyze_organ_activity(_profile.runtime_events or {})
        _profile.organ_activity = _oa
    _runs = _engine.get_stats().get("last_run", {})

    # ---- 报告 + 落盘 ----
    _dir = _out_dir(_args.out_dir)
    _stamp = _ts()
    _paths = {}
    _paths["profile"] = os.path.join(_dir, "profile_%s.json" % _stamp)
    _paths["report"] = os.path.join(_dir, "report_%s.txt" % _stamp)
    _paths["production_consumption"] = os.path.join(
        _dir, "production_consumption_%s.json" % _stamp)
    _paths["fake_loops"] = os.path.join(_dir, "fake_loops_%s.json" % _stamp)
    # ★主线第21批 T4：完整调用图数据（供后续可视化）
    _paths["call_graph"] = os.path.join(_dir, "call_graph_%s.json" % _stamp)

    _engine.save_profile(_paths["profile"])
    _report_text = _engine.generate_report(_paths["report"])
    if not _report_text:
        print("[警告] 文本报告为空")

    # 两份明细：复用分析器（扫描一次）后完整落盘
    if not _args.call_graph_only:
        _pc_detail = ProductionConsumptionMatcher().scan()
        _fl_detail = FakeLoopDetector().scan()
        _dump_json(_paths["production_consumption"], _pc_detail)
        _dump_json(_paths["fake_loops"], _fl_detail)
    # ★主线第21批 T4：调用图完整数据（nodes/edges/stats）
    if _args.include_call_graph:
        try:
            from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
            _cg_obj = CallGraphAnalyzer()
            _cg_obj.analyze()
            _dump_json(_paths["call_graph"], _cg_obj._graph_data)
        except Exception as _e:  # 落盘失败不影响主流程
            print("[警告] 调用图落盘失败: %s: %s" % (type(_e).__name__, _e))

    # ---- 基线（T5）----
    _baseline_p = os.path.join(_dir, "baseline.json")
    _cmp_result = {}
    _baseline = None
    if _args.update_baseline:
        _engine.save_profile(_baseline_p)
        print("[基线] 已用本次结果**更新**基线: %s" % _baseline_p)
    elif os.path.isfile(_baseline_p):
        _baseline = _engine.load_profile(_baseline_p)
        if _baseline is None:
            print("[基线] 现有基线无法解析，本次跳过对比（可 --update-baseline 重建）")
        else:
            _cmp_result = SelfAwarenessEngine.compare_profiles(_baseline, _profile)
    else:
        _engine.save_profile(_baseline_p)
        print("[基线] 首份报告已建立基线: %s" % _baseline_p)

    # ---- 对比结果追加到报告尾部 ----
    if _cmp_result:
        _tail = ["", "=" * 68, "【与基线的趋势对比】", _cmp_result.get("summary", "")]
        for _k, _v in (_cmp_result.get("metrics") or {}).items():
            _tail.append("  · %-16s %s → %s  (Δ%+d, %s)"
                         % (_k, _v["baseline"], _v["current"], _v["delta"],
                            _v["verdict"]))
        try:
            with open(_paths["report"], "a", encoding="utf-8") as f:
                f.write("\n".join(_tail) + "\n")
        except Exception as _e:  # 追加失败不影响主流程
            print("[警告] 趋势对比写入报告失败: %s: %s" % (type(_e).__name__, _e))
        print("\n".join(_tail))

    # ---- 控制台摘要 ----
    for _l in _summary_lines(_profile, _elapsed, _runs):
        print(_l)
    print("")
    print("【落盘文件】")
    for _k, _p in _paths.items():
        _size = os.path.getsize(_p) if os.path.isfile(_p) else 0
        _flag = "OK " if _size > 0 else "空!"
        print("  [%s] %-24s %8d B  %s" % (_flag, _k, _size, _p))
    print("")
    print("完成：总耗时 %.2fs%s"
          % (_elapsed, "" if _cmp_result else "（无基线对比）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
