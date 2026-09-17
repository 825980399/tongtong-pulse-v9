# -*- coding: utf-8 -*-
"""
PulseGlobalLearner —— 全域自学习循环器官（v24.0新增）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 在心跳上周期性审计全域样本，检测能力漂移与系统性偏差并产出改进结论，驱动全局自学习闭环，而非纠正单点错误。
机制: _on_heartbeat → _run_audit_cycle，先 _check_cost_limit 控制审计预算，_collect_and_sample 经 _collect_semantic_samples / _collect_experience_samples / _collect_code_samples / _collect_narrative_samples 抽样；_check_drift 判定漂移后由 _handle_drift 处理、_classify 归类，_build_conclusion 生成结论，_publish_conclusion 发布；_execute_comparisons 做对照验证，_record_audit_experience 回写经验池；_load_config / _is_enabled 控制开关与配置。
定位: v24.0 引入的「全域自我审计官」，站在系统之上审视整体学习质量。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import hashlib
import json
import random
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import Event, HeartEvent, LogLevel, SystemEvent


class PulseGlobalLearner(BasePulseOrgan):
    """
    全域自学习循环器官（v24.0新增）
    """

    def __init__(self, organ_name: str = "全局学习器"):
        super().__init__(organ_name)

        # 依赖引用
        self.experience_pool = None         # 体验池
        self.semantic_comprehension = None  # 语义理解器（抽样数据源）
        self.self_inspector = None          # 代码审视器（趋势数据源）
        self.code_learner = None            # 代码学习器（学习能力）
        self.lung = None                    # 大模型调用（比对用）
        # ★A-8（2026-09-08）：叙事自我引用（周期报告/人生教训/行为指导 → 学习输入）
        self.narrative_self = None
        # ★P3-1：只读状态 provider 回调（替代 self.semantic_comprehension.get_lessons 直调）
        self._lessons_provider = None       # (limit=...) -> list[dict]

        # 成本控制
        self._hourly_api_calls = 0
        self._api_window_start = time.time()
        self._max_hourly_calls = 30
        self._similar_case_cache: dict[str, float] = {}  # 内容哈希 → 上次比对时间
        self._similar_case_ttl = 3600  # 相似案例去重窗口（秒）

        # 抽样体检
        self._sample_counter = 0
        self._sample_interval = 7  # 每7次高置信度判断抽样一次（约15%）
        self._sample_results: list[dict[str, Any]] = []  # 抽样结果历史
        self._max_sample_results = 50

        # 漂移检测
        self._drift_threshold = 0.3  # 准确率低于此值触发深度审查
        self._drift_alert_count = 0

        # 循环控制
        # ★v30.0负载均衡修复：随机错峰初始化，避免与其他器官取模任务同点共振
        self._heartbeat_count = random.randint(1, 49)
        self._audit_interval = 50  # 每50次心跳运行一次审计（基础值，运行时乘 tempo）
        self._last_audit_time = 0.0

        # 学习记录
        self._learning_count = 0

        # ★阶段三·任务1（2026-09-08）：内部降级样本源 + 结构化学习结论
        self._max_samples_per_round = 10        # 单轮样本上限（防过载）
        self._code_source_min_interval_sec = 600  # 代码学习器样本源最小拉取间隔（该源较重）
        self._last_code_source_time = 0.0
        self._conclusions: list[dict[str, Any]] = []  # 结构化学习结论历史（可审计，上限50）
        self._max_conclusions = 50

        # 线程安全
        self._lock = threading.Lock()

        # 从配置加载
        self._load_config()

        # ★属性初始化完整性补全（自动审查添加）
        self._api_calls_this_cycle = False

    def _load_config(self):
        """从config加载配置"""
        try:
            import config
            cfg = getattr(config, 'GLOBAL_LEARNER_CONFIG', {})
            self._max_hourly_calls = cfg.get("max_hourly_calls", 30)
            self._audit_interval = cfg.get("audit_interval_beats", 50)
            self._sample_interval = cfg.get("sample_interval", 7)
            self._drift_threshold = cfg.get("drift_threshold", 0.3)
            # ★阶段三·任务1：仅在 ENABLE_GLOBAL_LEARNER 打开时有意义
            self._max_samples_per_round = int(cfg.get("max_samples_per_round", 10))
            self._code_source_min_interval_sec = int(
                cfg.get("code_source_min_interval_sec", 600))
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")

    def _is_enabled(self) -> bool:
        """★阶段三·任务1：读取灰度开关（每次审计读一次，支持运行期热切换）。

        关闭（默认 False）时：_collect_and_sample 返回 []、_execute_comparisons 返回 0，
        与开关存在前的行为**完全一致**（抽样=0 / 学习=0），零回退。
        """
        try:
            import config
            return bool(getattr(config, 'ENABLE_GLOBAL_LEARNER', False))
        except Exception:
            return False

    # ========== 框架注入接口 ==========

    def set_experience_pool(self, pool):
        self.experience_pool = pool

    def set_semantic_comprehension(self, sc):
        self.semantic_comprehension = sc
        # ★P3-1：同步注入学习记录 provider 回调（替代 get_lessons 直调）
        if sc is not None and hasattr(sc, 'get_lessons'):
            self._lessons_provider = sc.get_lessons

    def set_lessons_provider(self, provider):
        """★P3-1：注入学习记录 provider 回调（规则14 依赖注入+回调）。"""
        self._lessons_provider = provider

    def set_self_inspector(self, inspector):
        self.self_inspector = inspector

    def set_code_learner(self, learner):
        self.code_learner = learner

    def set_lung(self, lung):
        self.lung = lung

    def set_narrative_self(self, narrative_self):
        """★A-8：注入叙事自我引用（周期报告/人生教训/行为指导 → 学习输入信号）。

        由 organ_assembler 经 ORGAN_META.post_wiring 按 organ_name="叙事自我" 软装配。
        """
        self.narrative_self = narrative_self

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HeartEvent.BEAT:
            return self._on_heartbeat(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._heartbeat_count += 1
        now = time.time()
        # ★v29/14.45：动态间隔——无对话+硬件充裕时加速审计，有对话时减速
        _audit_interval = self._audit_interval
        try:
            from nucleus.runtime_tempo import get_runtime_tempo
            _tempo = get_runtime_tempo().get_background_tempo()
            _audit_interval = max(1, round(self._audit_interval * _tempo))
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if self._heartbeat_count % _audit_interval == 0:
            if now - self._last_audit_time >= 10:  # 防止同一秒内重复触发
                self._last_audit_time = now
                return self._run_audit_cycle()

        return {"status": "waiting", "next_audit_in": _audit_interval - (self._heartbeat_count % _audit_interval)}

    def _run_audit_cycle(self) -> dict[str, Any]:
        """执行一次全域自学习审计（增强版：整体异常保护）"""
        result = {
            "status": "audit_completed",
            "drift_detected": False,
            "samples_taken": 0,
            "lessons_learned": 0,
            "api_calls_this_cycle": 0,
        }

        try:
            # 1. 检查漂移
            drift_result = self._check_drift()
            result["drift_detected"] = drift_result.get("drift", False)
            if drift_result.get("drift"):
                self._handle_drift(drift_result)

            # 2. 抽样体检
            samples = self._collect_and_sample()
            result["samples_taken"] = len(samples)

            # 3. 执行比对并记录学习
            lessons = self._execute_comparisons(samples)
            result["lessons_learned"] = lessons
            result["api_calls_this_cycle"] = self._api_calls_this_cycle if hasattr(self, '_api_calls_this_cycle') else 0

            # 4. 记录审计体验
            self._record_audit_experience(result)
            self._log(LogLevel.INFO,
                 f"全域自学习审计: 漂移={result['drift_detected']}, "
                 f"抽样={result['samples_taken']}, 学习={result['lessons_learned']}")

        except Exception as _e:
            self._log(LogLevel.ERROR, f"全局学习器审计异常: {_e}")

        return result

    def _check_drift(self) -> dict[str, Any]:
        """基于抽样结果历史检测知识漂移"""
        with self._lock:
            if len(self._sample_results) < 5:
                return {"drift": False, "reason": "样本不足"}

            recent = self._sample_results[-10:]
            correct = sum(1 for r in recent if r.get("match", False))
            accuracy = correct / len(recent) if recent else 0.0

            drift = accuracy < self._drift_threshold
            return {
                "drift": drift,
                "accuracy": round(accuracy, 2),
                "sample_count": len(recent),
                "threshold": self._drift_threshold,
            }

    def _handle_drift(self, drift_result: dict):
        """漂移检测触发深度审查"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._drift_alert_count += 1
        self._log(LogLevel.WARNING,
                 f"漂移检测: 准确率={drift_result['accuracy']}, "
                 f"触发深度审查（第{self._drift_alert_count}次）")

        # 发射漂移告警
        self._emit(Event.GLOBAL_LEARNER_DRIFT_DETECTED, {
            "accuracy": drift_result["accuracy"],
            "threshold": self._drift_threshold,
            "alert_count": self._drift_alert_count,
        }, priority=7, layer="L0")

        # 写入体验池作为负面体验
        if self.experience_pool:
            self.experience_pool.record_experience(
                motivation="维持判断准确性",
                motivation_intensity=0.6,
                process_pressure=0.7,
                pressure_type="cognitive",
                reward_type="cognitive",
                reward_intensity=0.1,
                emotion_tags=["担忧", "反思"],
                emotion_intensity=0.6,
                content=f"发现知识漂移迹象，准确率降至{drift_result['accuracy']}"
            )

    # ========== ★阶段三·任务1：样本分类与内部降级样本源 ==========
    # 【为什么改】原 _collect_and_sample 的样本**唯一来源**是 _lessons_provider，
    #   而 main.py 从未调用 set_lessons_provider → provider=None → 样本恒为 []
    #   → 抽样恒为 0 → _execute_comparisons 直接 return 0 → "学习"恒为 0。
    #   （星轨观察到的「4次审计全0」即此。）
    # 【怎么改】provider 未注入时**内部降级自取**（不动 main.py，符合红线），
    #   三路备用源各自 try/except 互不干扰，单轮样本上限 max_samples_per_round。

    # 样本分类规则：顺序敏感，先命中者优先
    #   （"检索质量"含"质量"二字，故检索规则必须排在消化规则之前）
    CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
        ("检索策略调整", ("检索", "召回", "相似度", "匹配", "重排", "top-", "topk",
                     "搜索", "关键词", "索引", "上下文", "信任度", "共振", "查询")),
        ("消化质量改进", ("消化", "提炼", "压缩", "摘要", "吸收", "去重", "质量信号")),
        ("运行参数优化", ("线程池", "池大小", "超时", "timeout", "并发", "批次", "batch",
                     "tempo", "间隔", "阈值", "参数", "内存", "队列", "日志量")),
    ]
    DEFAULT_CATEGORY = "综合优化"

    def _classify(self, text: str) -> str:
        """把一条样本归入「运行参数/检索策略/消化质量」三类之一（无法归类则fallback）。"""
        _t = str(text or "")
        for _cat, _kws in self.CATEGORY_RULES:
            if any(_k in _t for _k in _kws):
                return _cat
        return self.DEFAULT_CATEGORY

    def _collect_semantic_samples(self, limit: int) -> list[dict[str, Any]]:
        """样本源1（优先级最高）：语义理解器学习记录。

        provider 已注入 → 走 provider（规则14 依赖注入+回调，首选路径）；
        provider 未注入 → **降级直调** semantic_comprehension.get_lessons（本轮新增）。
        """
        _lessons: list = []
        # ⚠️ _call_provider 签名是 (provider, *args, default=None)，不支持 limit= 关键字
        #   （原代码 limit=10 会 TypeError 且被吞掉 → 即使注入了 provider 样本也恒为 0，
        #    这是"空转"的第二重根因）。此处改用**位置传参**：get_lessons(limit) 语义不变。
        try:
            if self._lessons_provider is not None:
                _lessons = self._call_provider(self._lessons_provider, limit, default=[])
            elif self.semantic_comprehension is not None and hasattr(self.semantic_comprehension, 'get_lessons'):
                _lessons = self._call_provider(
                    self.semantic_comprehension.get_lessons, limit, default=[])
        except Exception as e:
            self._log(LogLevel.DEBUG, f"[全局学习器] 源1(语义课程) 采集异常已忽略（{type(e).__name__}: {e}）")

        _out: list[dict[str, Any]] = []
        for _l in _lessons or []:
            if not isinstance(_l, dict):
                continue
            _content = str(_l.get("content", ""))[:200]
            _out.append({
                "source": "semantic",
                "content": _content,
                "local_confidence": float((_l.get("local") or {}).get("confidence", 0) or 0),
                "llm_result": _l.get("llm", {}),
                "suggestion": "",
                "category": self._classify(_content),
            })
        return _out

    def _collect_experience_samples(self, limit: int) -> list[dict[str, Any]]:
        """样本源2：体验池近期经验（只取**负面/承压**类，作为待改进信号）。

        判负依据：pressure_type ∈ {frustration, resource, retrieval} 或情绪标签含负面词。
        正常（低压强）体验不构成改进信号，避免噪声淹没。
        """
        if self.experience_pool is None:
            return []
        _exps: list = []
        try:
            _fn = getattr(self.experience_pool, 'get_experiences_for_narrative', None)
            if _fn is not None:
                _exps = self._call_provider(_fn, limit, default=[])  # 位置传参（勿用 limit=）
        except Exception as e:
            self._log(LogLevel.DEBUG, f"[全局学习器] 源2(体验池) 采集异常已忽略（{type(e).__name__}: {e}）")

        _NEG_PRESSURE = {"frustration", "resource", "retrieval"}
        _NEG_TAGS = {"担忧", "警觉", "挫败", "困惑", "烦躁", "失望"}
        _out: list[dict[str, Any]] = []
        for _e in _exps or []:
            if not isinstance(_e, dict):
                continue
            _ptype = str(_e.get("pressure_type", "") or "")
            _tags = set(_e.get("emotion_tags") or [])
            if _ptype not in _NEG_PRESSURE and not (_tags & _NEG_TAGS):
                continue
            _content = str(_e.get("content", ""))[:200]
            _out.append({
                "source": "experience_pool",
                "content": _content,
                "local_confidence": 0.5,
                "suggestion": "",
                # 分类线索带上 pressure_type（如 retrieval → 检索策略）
                "category": self._classify(_content + " " + _ptype),
            })
        return _out

    def _collect_code_samples(self, limit: int) -> list[dict[str, Any]]:
        """样本源3：代码学习器建议（该源需要自我审视，较重 → 按最小间隔节流）。

        自带 suggestion 的条目直接复用为学习结论的"优化建议"候选，质量最高。
        """
        if self.code_learner is None:
            return []
        _now = time.time()
        if _now - self._last_code_source_time < self._code_source_min_interval_sec:
            return []  # 节流窗口内不重复拉取
        self._last_code_source_time = _now

        _issues: list = []
        try:
            _fn = getattr(self.code_learner, 'review_own_code_issues', None)
            if _fn is not None:
                _issues = self._call_provider(_fn, default=[])
        except Exception as e:
            self._log(LogLevel.DEBUG, f"[全局学习器] 源3(代码学习器) 采集异常已忽略（{type(e).__name__}: {e}）")

        _out: list[dict[str, Any]] = []
        for _it in (_issues or [])[:limit]:
            if not isinstance(_it, dict):
                continue
            _desc = str(_it.get("description", ""))[:160]
            _sugg = str(_it.get("suggestion", ""))[:160]
            if not (_desc or _sugg):
                continue
            _out.append({
                "source": "code_learner",
                "content": _desc,
                "local_confidence": 0.5,
                "suggestion": _sugg,
                "category": self._classify(_desc + " " + _sugg),
            })
        return _out

    def _collect_narrative_samples(self, limit: int) -> list[dict[str, Any]]:
        """样本源4（★A-8）：叙事自我产出 → 学习输入（灰度 ENABLE_NARRATIVE_CONSUMPTION）。

        消费三类产出（不改变产出逻辑，只读公开接口）：
            周期报告摘要 → "稳定积累期/探索期"等学习方向信号
            人生教训     → "经历→反思"信号
            行为指导     → 语气/关注领域偏好信号
        叙事自我未注入/未开启/任何异常 → 空列表（零回退）。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_NARRATIVE_CONSUMPTION', False):
                return []
        except Exception:
            return []
        _ns = self.narrative_self
        if _ns is None:
            return []
        _out: list[dict[str, Any]] = []
        # ① 周期报告 → 学习方向信号
        try:
            _report = _ns.get_latest_period_report()
            if _report:
                _summary = str(_report.get("summary", ""))[:160]
                if _summary:
                    _out.append({"source": "narrative_report", "content": _summary,
                                 "local_confidence": 0.6, "suggestion": "",
                                 "category": self._classify(_summary)})
        except Exception:
            pass
        # ② 人生教训 → 经历→反思信号
        try:
            for _ls in (_ns.get_life_lessons(limit=2) or [])[:2]:
                _out.append({"source": "narrative_lesson", "content": str(_ls)[:160],
                             "local_confidence": 0.55, "suggestion": "",
                             "category": self._classify(str(_ls))})
        except Exception:
            pass
        # ③ 行为指导 → 偏好信号
        try:
            _g = _ns.get_behavior_guidance("general")
            if _g and _g.get("focus_areas"):
                _content = f"行为指导: 语气={_g.get('tone_preference')}, " \
                           f"关注={', '.join(map(str, _g.get('focus_areas', []))[:3])}"
                _out.append({"source": "narrative_guidance", "content": _content[:160],
                             "local_confidence": 0.5, "suggestion": "",
                             "category": self._classify(_content)})
        except Exception:
            pass
        return _out[:limit]

    def _collect_and_sample(self) -> list[dict[str, Any]]:
        """收集决策样本（★阶段三·任务1：增加内部降级样本源），执行高置信度抽样。"""
        samples: list[dict[str, Any]] = []
        self._api_calls_this_cycle = 0

        # 灰度关闭 → 与开关存在前完全一致（抽样=0）
        if not self._is_enabled():
            return samples

        if self._lessons_provider is None:
            self._log(LogLevel.INFO,
                      "[全局学习器] lessons_provider未注入，降级到内部样本源")

        _limit = max(1, int(self._max_samples_per_round))
        _collectors = (
            self._collect_semantic_samples,     # 源1 语义课程（优先）
            self._collect_experience_samples,   # 源2 体验池近期负面经验
            self._collect_code_samples,         # 源3 代码学习器建议（节流）
            self._collect_narrative_samples,    # 源4 叙事自我产出（★A-8，灰度）
        )
        for _collector in _collectors:
            try:
                _got = _collector(_limit) or []
            except Exception as _ce:
                self._log(LogLevel.DEBUG, f"[全局学习器] 样本源异常已忽略: {_ce}")
                _got = []
            for _s in _got:
                if len(samples) >= _limit:
                    break
                samples.append(_s)
            if len(samples) >= _limit:
                break

        # 保留原有抽样语义：按 sample_interval 稀释，控制每轮比对密度
        if samples and self._sample_interval > 1:
            _sampled = [s for i, s in enumerate(samples) if i % self._sample_interval == 0]
            if _sampled:
                samples = _sampled

        return samples[:_limit]

    # 无自带建议时的兜底模板（按类别）
    _CATEGORY_ADVICE = {
        "运行参数优化": "复核该类参数的取值区间与触发条件，建议先小范围调整并观察一轮指标再决定是否固化",
        "检索策略调整": "复核召回路径与相似度门控对该类查询的适配性，建议先在候选集层面验证召回提升再改排序",
        "消化质量改进": "复核该类内容的提炼阈值与去重策略，建议抽样比对提炼前后信息保留率再调参",
        "综合优化": "样本证据尚分散，建议继续累积观测，暂不做策略调整",
    }

    def _build_conclusion(self, category: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """★阶段三·任务1：把一个类别的样本聚合成**结构化学习结论**。

        结论格式（星轨开工批准指定）：
            {来源, 问题描述, 优化建议, 置信度, 建议优先级, 时间戳}

        置信度 = **样本一致性代理指标**：
            同一类别的样本越多、来源越分散（多路交叉印证），指向同一问题的证据越强。
            若样本自带 local_confidence，则按 0.7:0.3 与之融合（保持不外包给单一通道）。
        """
        _n = len(items)
        _sources = sorted({str(_i.get("source", "unknown")) for _i in items})
        _conf = min(0.95, 0.40 + 0.08 * (_n - 1) + 0.05 * (len(_sources) - 1))
        _lcs = [float(_i.get("local_confidence", 0) or 0) for _i in items]
        _avg_lc = sum(_lcs) / len(_lcs) if _lcs else 0.0
        if _avg_lc > 0:
            _conf = round(min(0.95, _conf * 0.7 + _avg_lc * 0.3), 2)
        else:
            _conf = round(_conf, 2)

        _priority = "高" if _conf >= 0.70 else ("中" if _conf >= 0.55 else "低")

        # 优化建议：优先用样本自带的 suggestion（源3 通常自带），否则用类别兜底模板
        _sugg = ""
        for _i in items:
            _s = str(_i.get("suggestion", "") or "").strip()
            if _s:
                _sugg = _s
                break
        if not _sugg:
            _sugg = self._CATEGORY_ADVICE.get(category, self._CATEGORY_ADVICE["综合优化"])

        _headline = items[0].get("content", "") if items else ""
        return {
            "来源": "/".join(_sources),
            "问题描述": f"{category}：{_n}条样本指向同类改进点"
                        + (f"，代表信号「{str(_headline)[:40]}」" if _headline else ""),
            "优化建议": _sugg,
            "置信度": _conf,
            "建议优先级": _priority,
            "时间戳": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

    def _publish_conclusion(self, conclusion: dict[str, Any]) -> None:
        """把结构化学习结论写 INFO 日志 + 体验池（**不自动执行**建议）。"""
        _cat = str(conclusion.get("问题描述", "")).split("：")[0]
        self._log(LogLevel.INFO,
                  f"[全局学习器] 学习结论: 类别={_cat}, "
                  f"建议={str(conclusion.get('优化建议', ''))[:40]}, "
                  f"置信度={conclusion.get('置信度')}, "
                  f"优先级={conclusion.get('建议优先级')}")

        if self.experience_pool:
            try:
                self.experience_pool.record_experience(
                    motivation="全域自学习",
                    motivation_intensity=0.5,
                    process_pressure=0.3,
                    pressure_type="cognitive",
                    reward_type="cognitive",
                    reward_intensity=0.6,
                    emotion_tags=["成长", "学习"],
                    emotion_intensity=0.5,
                    content=json.dumps(conclusion, ensure_ascii=False),
                )
            except Exception:
                self._log(LogLevel.DEBUG, "[全局学习器] 结论写入体验池失败已忽略")

    def _execute_comparisons(self, samples: list[dict[str, Any]]) -> int:
        """★阶段三·任务1：按类别聚合样本 → 产出结构化学习结论。

        说明（星轨 Q3 拍板）：本轮**不调用大模型**。原因：
            1) 本轮目标是让学习器从"完全空转"变成"产出可审计结论"，这是质的飞跃；
            2) 真实 LLM 比对成本高、稳定性风险大，且当前样本量不足以支撑比对。
        结论写入日志 + 体验池，**不自动执行任何优化建议**（执行闭环属后续任务）。
        """
        if not samples:
            return 0
        if not self._is_enabled():
            return 0

        _groups: dict[str, list[dict[str, Any]]] = {}
        for _s in samples:
            _groups.setdefault(str(_s.get("category", self.DEFAULT_CATEGORY)), []).append(_s)

        lessons = 0
        for _cat, _items in _groups.items():
            _conclusion = self._build_conclusion(_cat, _items)
            # 成本控制：沿用"每小时上限 + 相似案例去重"（同一问题描述 1 小时内不重复产出）
            if not self._check_cost_limit(_conclusion.get("问题描述", "")):
                continue

            self._api_calls_this_cycle += 1
            lessons += 1
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._learning_count += 1

            self._publish_conclusion(_conclusion)
            self._conclusions.append(_conclusion)
            if len(self._conclusions) > self._max_conclusions:
                del self._conclusions[:len(self._conclusions) - self._max_conclusions]

        return lessons

    def _check_cost_limit(self, content: str) -> bool:
        """检查成本限制和相似案例去重"""
        now = time.time()
        with self._lock:
            # 每小时重置
            if now - self._api_window_start >= 3600:
                self._api_window_start = now
                self._hourly_api_calls = 0

            # 检查每小时上限
            if self._hourly_api_calls >= self._max_hourly_calls:
                self._log(LogLevel.WARNING, "全域自学习成本控制: 达到每小时上限")
                return False

            # 相似案例去重
            content_hash = hashlib.md5(content.encode()).hexdigest()[:10]
            last_time = self._similar_case_cache.get(content_hash, 0)
            if now - last_time < self._similar_case_ttl:
                return False  # 太相似，跳过

            # 通过检查
            self._hourly_api_calls += 1
            self._similar_case_cache[content_hash] = now
            return True

    def _record_audit_experience(self, result: dict):
        """将审计结果记录为体验"""
        if self.experience_pool:
            self.experience_pool.record_experience(
                motivation="自我审计",
                motivation_intensity=0.3,
                process_pressure=0.2,
                pressure_type="cognitive",
                reward_type="cognitive",
                reward_intensity=0.4 if not result["drift_detected"] else 0.1,
                emotion_tags=["平静"] if not result["drift_detected"] else ["警觉"],
                emotion_intensity=0.3,
                content=f"完成了一次全域自学习审计，学习到{result['lessons_learned']}条新经验"
            )

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_recent_conclusions(self, limit: int = 10) -> list[dict[str, Any]]:
        """★阶段三·任务1：取最近 N 条结构化学习结论（供后续任务2/3 的执行闭环消费）。

        格式见 _build_conclusion 文档串。开关关闭时恒为空列表。
        """
        with self._lock:
            return list(self._conclusions[-limit:])

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "heartbeat_count": self._heartbeat_count,
                "drift_alert_count": self._drift_alert_count,
                "learning_count": self._learning_count,
                "hourly_api_calls": self._hourly_api_calls,
                "sample_results_size": len(self._sample_results),
                "is_running": self.is_running,
                # ★阶段三·任务1
                "enabled": self._is_enabled(),
                "conclusions_size": len(self._conclusions),
                "max_samples_per_round": self._max_samples_per_round,
            }

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                HeartEvent.BEAT,
                SystemEvent.STATUS_REQUEST,
            ],
            "min_priority": 1,
        }]

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "全局学习器",
    "class_name": "PulseGlobalLearner",
    "attr_name": "global_learner",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "experience_pool", "setter": "set_experience_pool"},
        {"target": "语义理解器", "setter": "set_semantic_comprehension"},
        {"target": "self_inspector", "setter": "set_self_inspector"},
        {"target": "代码学习", "setter": "set_code_learner"},
        {"target": "肺", "setter": "set_lung"},
        {"target": "叙事自我", "setter": "set_narrative_self"},  # ★A-8：叙事产出→学习输入
    ],
}

if __name__ == "__main__":
    print("=== PulseGlobalLearner v24.0 自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockPulseCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
            return {"pulse_id": f"pulse:{source_organ}:{event_type}", "event_type": event_type, "source_organ": source_organ, "payload": payload}

    class MockExperiencePool:
        def __init__(self):
            self.experiences = []
        def record_experience(self, **kwargs):
            self.experiences.append(kwargs)

    class MockSemanticComprehension:
        def get_lessons(self, limit=10):
            return [
                {
                    "content": "测试问题",
                    "local": {"confidence": 0.8},
                    "llm": {"intent": "知识查询", "confidence": 0.9},
                }
            ]

    mock_field = MockInfoField()
    mock_pool = MockExperiencePool()
    mock_semantic = MockSemanticComprehension()

    learner = PulseGlobalLearner("全局学习器")
    learner.set_info_field(mock_field)
    learner.set_pulse_core(MockPulseCore())
    learner.set_experience_pool(mock_pool)
    learner.set_semantic_comprehension(mock_semantic)
    learner.start()

    # 模拟50次心跳触发审计
    for _ in range(50):
        learner.on_pulse({"event_type": HeartEvent.BEAT, "payload": {}, "priority": 5})

    stats = learner.get_stats()
    print(f"统计: 心跳={stats['heartbeat_count']}, 漂移告警={stats['drift_alert_count']}, "
          f"学习={stats['learning_count']}")
    print(f"体验池记录: {len(mock_pool.experiences)}条")

    learner.stop()
    print("\n=== 自测完成 ===")
