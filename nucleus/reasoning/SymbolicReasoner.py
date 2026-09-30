# -*- coding: utf-8 -*-
"""
SymbolicReasoner.py —— 符号推理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 符号逻辑推理与形式化推导
机制: 大型模块（1380行），包含7个类、10个核心方法，采用分层架构实现
定位: 推理核心层
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


# ★第九批 B-3：符号推理的置信度不再硬编码——
#   confidence = 0.9 × 该类型历史成功率系数 × 证据强度系数（证据 = 已产出的推理步）。
#   开关关闭时 _evidence_conf 原值返回，行为与改动前逐字节一致。
from nucleus.reasoning.SelfCalibrator import evidence_confidence as _evidence_conf
from nucleus._silent_except import silent_exc


@dataclass
class SymbolicStep:
    """一步推理（轨迹单元）——对标人类解题时的「第一步得到什么、第二步排除什么」。"""
    step_id: int
    premise: list[str]          # 前提，如 ["A>B", "B>C"]
    conclusion: str             # 结论，如 "A>C"
    rule: str                   # 推理规则：传递闭包 / 变量代入 / 排除法
    confidence: float = 1.0


@dataclass
class SymbolicResult:
    """符号推理结果。"""
    answer: str | None = None           # 最终答案文本
    steps: list[SymbolicStep] = field(default_factory=list)
    solved: bool = False                # 是否真正解出
    confidence: float = 0.0
    task_type: str = "unknown"          # 推理类型：compare/arithmetic/csp/unknown


# ============================================================================
# 阶段5：统一形式化模型（Relation + 语义注册表 + 目标 + 问题）
# ============================================================================

@dataclass
class Relation:
    """
    一条形式化关系。对标顶级 AI 的「关系 / 谓词」抽象。

    kind 取值：
      - greater / less / equal   —— 传递关系（比较）
      - assign                    —— 变量赋值（算术）
      - functional                —— 函数关系（住/养/喝/开/吃/喜欢/是），
                                     用 attribute 承载具体属性名（房子/宠物/...）
    negated：否定（甲不住红房 / 不高于 / 不比…高）
    """
    kind: str
    subject: str
    object: str
    negated: bool = False
    attribute: str | None = None   # 仅 functional 关系使用


@dataclass
class RelationSemantics:
    """关系语义（决定推理算子，而非按题型硬编码）。"""
    transitive: bool = False   # 是否可传递（greater 是，lives_in 否）
    functional: bool = False   # 是否函数（每人住一房 / 每人养一宠物）
    negatable: bool = True     # 是否支持否定


@dataclass
class Goal:
    """
    求解目标。kind 取值：
      max / min / sort / pair  —— 比较类目标
      eval                     —— 算术求值（expression 承载表达式）
      query                    —— 条件排除查询（relation 承载属性，object 承载目标值）
    """
    kind: str
    relation: str | None = None      # query：属性名（房子/宠物/...）
    object: str | None = None        # query：目标值
    expression: str | None = None    # eval：算术表达式
    maximum: bool = True             # max/min


@dataclass
class SymbolicProblem:
    """统一形式化后的问题（关系 + 实体 + 值域 + 变量 + 目标）。"""
    relations: list[Relation] = field(default_factory=list)
    entities: set[str] = field(default_factory=set)
    domains: dict[str, set[str]] = field(default_factory=dict)
    variables: dict[str, float] = field(default_factory=dict)
    goal: Goal | None = None


# 语义注册表：关系类型 → 推理算子选择依据
RELATION_SEMANTICS: dict[str, RelationSemantics] = {
    "greater": RelationSemantics(transitive=True, functional=False, negatable=True),
    "less": RelationSemantics(transitive=True, functional=False, negatable=True),
    "equal": RelationSemantics(transitive=True, functional=False, negatable=False),
    "assign": RelationSemantics(transitive=False, functional=False, negatable=False),
    "functional": RelationSemantics(transitive=False, functional=True, negatable=True),
}


class SymbolicReasoner:
    """内部符号推理引擎（纯函数式，无框架依赖）。"""

    # 正向比较形容词（"X比Y高" → X > Y）
    _GT_ADJECTIVES = "高大多快重长贵强深老早"
    # 反向比较形容词（"X比Y矮" → X < Y）
    _LT_ADJECTIVES = "矮小少慢轻短便宜弱浅年轻晚低"

    _MAX_QUESTION = "最高|最大|最多|最快|最重|最长|最贵|最强|最深|最老|最早|更大|更快|更重"
    _MIN_QUESTION = "最矮|最小|最少|最慢|最轻|最短|最便宜|最弱|最浅|最年轻|最晚|最低|更小|更慢"

    # ★语义理解辅助：当前无法精确处理的复杂逻辑语义（识别后诚实降级，交给大模型）
    # 阶段5调整：
    #   - 移除 negated_adjective（否定形容词现已由统一关系模型求解）
    #   - 移除「两人|三人」裸词（"甲乙丙三人"只是实体描述，非量词约束，避免假阳性）
    # 阶段6调整：
    #   - 移除 conditional / quantifier（现已由命题逻辑 / 量词约束算子求解）
    _COMPLEX_SEMANTICS = {
        "probability": r"概率|可能性|百分之|比例",
        "temporal": r"先于|后于|之前|之后|先后顺序|第几",
    }

    # ========================================================================
    # 入口（三段式流水线：formalize → solve → verify）
    # ========================================================================

    def reason(self, question: str, semantic_hints: dict[str, Any] | None = None) -> SymbolicResult:
        """
        尝试解出符号推理题。解不出返回 solved=False。

        Args:
            question: 推理题原文。
            semantic_hints: ★QICA 语义理解辅助（可选）。可携带 QICA 已识别的
                实体等语义信号，作为正则解析的补充。当前支持：
                  - entities: list[str] —— 已识别的核心实体，并入形式化实体集合。

        三段式流水线：
            1. _formalize：自然语言 → SymbolicProblem（关系/约束/目标）
            2. _solve：按关系语义选择算子求解
            3. _verify：把答案代回约束自我验证，失败则诚实降级
        """
        if not question:
            return SymbolicResult()

        problem = self._formalize(question, semantic_hints)

        result = self._solve(problem)
        if result is not None:
            # 自我验证：解出后把答案代回约束复检，验证失败则诚实降级
            if result.solved and not self._verify(problem, result):
                result = SymbolicResult(
                    answer=None,
                    steps=result.steps,
                    solved=False,
                    task_type=result.task_type,
                )
            # 无论解出与否，只要「理解并尝试」了问题结构，就返回真实的任务类型，
            # 避免把「多解/信息不足」误标为 unsupported_*。
            return result

        # 阶段6：命题逻辑（如果…那么/则、只要…就、除非…否则）
        result = self._solve_propositional(question)
        if result is not None:
            return result

        # 阶段6：量词约束（至少/恰好/所有/只有）
        result = self._solve_quantifier(question)
        if result is not None:
            return result

        # 未形式化出可解结构 → 复杂语义识别（概率/时序，诚实降级）
        _sem_type = self._detect_unsupported_semantics(question)
        if _sem_type:
            return SymbolicResult(task_type=f"unsupported_{_sem_type}")

        return SymbolicResult()

    # ========================================================================
    # 阶段5：统一形式化 _formalize
    # ========================================================================

    def _formalize(self, question: str, semantic_hints: dict[str, Any] | None = None) -> SymbolicProblem:
        """把自然语言题面统一形式化为 SymbolicProblem（关系 + 实体 + 值域 + 变量 + 目标）。"""
        relations: list[Relation] = []
        entities: set[str] = set()
        variables: dict[str, float] = {}
        domains: dict[str, set[str]] = {}

        # 1. 比较关系（greater/less/equal，含否定 ≤/≥）
        comps, comp_entities = self._extract_comparison_relations(question)
        relations.extend(comps)
        entities |= comp_entities

        # 2. 变量赋值关系（assign）
        assigns, assign_vars = self._extract_assignment_relations(question)
        relations.extend(assigns)
        variables.update(assign_vars)

        # 3. 函数关系（functional：住/养/喝/开/吃/喜欢/是）
        funcs, func_entities, func_domains = self._extract_functional_relations(question)
        relations.extend(funcs)
        entities |= func_entities
        for _k, _v in func_domains.items():
            domains.setdefault(_k, set()).update(_v)

        # ★QICA 语义理解辅助：并入外部已识别实体（作为正则解析的补充）
        if semantic_hints:
            _hint_entities = semantic_hints.get("entities")
            if isinstance(_hint_entities, (list, tuple, set)):
                for _e in _hint_entities:
                    if isinstance(_e, str) and _e.strip():
                        entities.add(_e.strip())

        goal = self._detect_goal(question, entities)

        return SymbolicProblem(
            relations=relations,
            entities=entities,
            domains=domains,
            variables=variables,
            goal=goal,
        )

    # ========================================================================
    # 阶段5：统一求解 _solve（按语义注册表分派算子）
    # ========================================================================

    def _solve(self, problem: SymbolicProblem) -> SymbolicResult | None:
        """根据问题中的关系语义与目标，自动选择推理算子。"""
        goal = problem.goal
        if goal is None:
            return None

        # 传递关系（greater/less/equal）→ 传递闭包算子
        transitive = [r for r in problem.relations if RELATION_SEMANTICS[r.kind].transitive]
        # 函数关系（functional）→ 约束传播算子
        functional = [r for r in problem.relations if RELATION_SEMANTICS[r.kind].functional]

        if goal.kind in ("max", "min", "sort", "pair") and transitive:
            return self._solve_transitive(problem, transitive)
        if goal.kind == "eval" and problem.variables:
            return self._solve_eval(problem, goal)
        if goal.kind == "query" and functional:
            return self._solve_functional(problem, functional, goal)

        return None

    # ========================================================================
    # 阶段5：自我验证 _verify
    # ========================================================================

    def _verify(self, problem: SymbolicProblem, result: SymbolicResult) -> bool:
        """把答案代回约束复检（顶级 AI 的自我验证模式）。验证失败返回 False。"""
        if not result.solved or result.answer is None or problem.goal is None:
            return False

        goal = problem.goal

        # 算术：代入求值可复现
        if goal.kind == "eval":
            _val = self._safe_eval(goal.expression or "", problem.variables)
            return _val is not None and self._fmt_num(_val) == result.answer

        # 比较：无矛盾 + 答案确为极值/排序
        if goal.kind in ("max", "min"):
            transitive = [r for r in problem.relations if RELATION_SEMANTICS[r.kind].transitive]
            gt, geq, _eq, _ = self._build_order_closure(transitive)
            # 矛盾复检
            if any((b, a) in gt for (a, b) in gt if a != b):
                return False
            if any((b, a) in geq for (a, b) in gt):
                return False
            extremes = self._find_extremes(problem.entities, gt, maximum=(goal.kind == "max"), geq=geq)
            return result.answer in extremes and len(extremes) == 1

        if goal.kind == "pair":
            return result.answer in problem.entities and len(problem.entities) == 2

        if goal.kind == "sort":
            return bool(result.answer)

        # 条件排除：解满足全部正/负约束，且目标值唯一确定
        if goal.kind == "query":
            functional = [r for r in problem.relations if RELATION_SEMANTICS[r.kind].functional]
            constraints = [
                (r.subject, r.attribute, r.object, not r.negated)
                for r in functional if r.attribute
            ]
            domains = {k: sorted(v) for k, v in problem.domains.items()}
            solutions = self._solve_csp(sorted(problem.entities), domains, constraints)
            candidates = {
                ent for sol in solutions
                for (ent, attr) in sol
                if attr == goal.relation and sol[(ent, attr)] == goal.object
            }
            return len(candidates) == 1 and result.answer in candidates

        return True

    # ========================================================================
    # 解析层：比较关系（含否定 ≤/≥）
    # ========================================================================

    def _extract_comparison_relations(self, question: str) -> tuple[list[Relation], set[str]]:
        """
        从题面提取比较关系为 Relation 对象。

        支持：
          - 中文正向："甲比乙高"（greater）、"甲比乙矮"（less）
          - 中文否定："甲不高于乙"（greater.negated=≤）、"甲不低于乙"（less.negated=≥）
                     "甲不比乙高"（greater.negated=≤）、"甲不比乙矮"（less.negated=≥）
          - 符号："A > B"（greater）、"A < B"（less）、"A = B"（equal，排除赋值 A=5）
        """
        relations: list[Relation] = []
        entities: set[str] = set()

        def _add(rel: Relation) -> None:
            relations.append(rel)
            entities.add(rel.subject)
            entities.add(rel.object)

        # 1. 否定动词：不高于/不大于（≤）、不低于/不小于（≥）
        _neg_verb = re.compile(
            r'([^\s，。；,;？?]{1,8})(不高于|不大于|不小于|不低于)([^\s，。；,;？?]{1,8})'
        )
        for m in _neg_verb.finditer(question):
            a, word, b = m.group(1), m.group(2), m.group(3)
            if word in ("不高于", "不大于"):
                _add(Relation("greater", a, b, negated=True))   # a ≤ b
            else:
                _add(Relation("less", a, b, negated=True))      # a ≥ b

        # 2. 否定比较：不比X(形容词)
        _neg_bi = re.compile(
            r'([^\s，。；,;？?比]{1,8})不比([^\s，。；,;？?]{1,8})(['
            + self._GT_ADJECTIVES + self._LT_ADJECTIVES + r'])'
        )
        for m in _neg_bi.finditer(question):
            a, b, adj = m.group(1), m.group(2), m.group(3)
            if adj in self._GT_ADJECTIVES:
                _add(Relation("greater", a, b, negated=True))   # a ≤ b
            else:
                _add(Relation("less", a, b, negated=True))      # a ≥ b

        # 3. 正向比较："X比Y(形容词)"，用 (?<!不) 排除「不比」已被上一步处理
        _cn_pat = re.compile(
            r'([^\s，。；,;？?比]{1,8})(?<!不)比([^\s，。；,;？?]{1,8})(['
            + self._GT_ADJECTIVES + self._LT_ADJECTIVES + r'])'
        )
        for m in _cn_pat.finditer(question):
            a, b, adj = m.group(1), m.group(2), m.group(3)
            if adj in self._GT_ADJECTIVES:
                _add(Relation("greater", a, b))
            else:
                _add(Relation("less", a, b))

        # 4. 符号："X > Y" / "X < Y" / "X = Y"（排除 A=5 这类赋值）
        _sym_pat = re.compile(
            r'([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*([><=])\s*([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff]*)'
        )
        for m in _sym_pat.finditer(question):
            a, sym, b = m.group(1), m.group(2), m.group(3)
            if sym == "=" and (a.isdigit() or b.isdigit()):
                continue  # 跳过变量赋值 A=5
            if sym == ">":
                _add(Relation("greater", a, b))
            elif sym == "<":
                _add(Relation("less", a, b))
            elif sym == "=":
                _add(Relation("equal", a, b))

        return relations, entities

    # ========================================================================
    # 解析层：变量赋值关系
    # ========================================================================

    def _extract_assignment_relations(self, question: str) -> tuple[list[Relation], dict[str, float]]:
        """提取变量赋值为 assign 关系 + 变量字典：A=5 / A是5 / A等于5。"""
        relations: list[Relation] = []
        variables: dict[str, float] = {}

        # 英文/符号：A=5
        for m in re.finditer(r'([A-Za-z])\s*=\s*(-?\d+(?:\.\d+)?)', question):
            var, val = m.group(1).upper(), float(m.group(2))
            relations.append(Relation("assign", var, m.group(2)))
            variables[var] = val

        # 中文：A是5 / A等于5
        for m in re.finditer(r'([A-Za-z])\s*(?:是|等于)\s*(-?\d+(?:\.\d+)?)', question):
            var, val = m.group(1).upper(), float(m.group(2))
            relations.append(Relation("assign", var, m.group(2)))
            variables[var] = val

        return relations, variables

    # ========================================================================
    # 解析层：函数关系（条件排除 CSP）
    # ========================================================================

    def _extract_functional_relations(
        self, question: str,
    ) -> tuple[list[Relation], set[str], dict[str, set[str]]]:
        """复用 CSP 解析器，把约束统一成 functional 关系。"""
        parsed = self._parse_csp(question)
        if parsed is None:
            return [], set(), {}

        entities, attr_values, constraints, _goal = parsed
        relations: list[Relation] = []
        for ent, attr, val, pos in constraints:
            relations.append(Relation("functional", ent, val, negated=not pos, attribute=attr))

        domains = {k: set(v) for k, v in attr_values.items()}
        return relations, set(entities), domains

    # ========================================================================
    # 解析层：目标检测
    # ========================================================================

    def _detect_goal(self, question: str, entities: set[str]) -> Goal | None:
        """统一检测求解目标（max/min/sort/pair/eval/query）。"""
        # 1. 算术求值
        _expr = self._extract_expression(question)
        if _expr:
            return Goal(kind="eval", expression=_expr)

        # 2. 极值
        if re.search(self._MAX_QUESTION, question):
            return Goal(kind="max", maximum=True)
        if re.search(self._MIN_QUESTION, question):
            return Goal(kind="min", maximum=False)

        # 3. 排序
        if re.search(r"排序|排列|从高到低|从大到小|从低到高|从小到大|顺序", question):
            return Goal(kind="sort")

        # 4. 两实体比较（谁更X / 谁X）
        if re.search(r"谁(?:更)?[高大多快重长贵强深老]", question):
            return Goal(kind="pair")

        # 5. 条件排除查询（谁住Y / 谁养Y / 谁喝Y ...）
        _goal = re.search(r'谁(' + self._POS_WORDS + r')([^\s，。；,;？?]{1,3})', question)
        if _goal:
            g_verb, g_val = _goal.group(1), _goal.group(2)
            g_attr = self._ATTR_VERBS.get(g_verb)
            g_val = re.sub(r'(房|宠物|饮料|车|食物)$', '', g_val)
            if g_attr and g_val and g_val not in self._CSP_STOP_VALUES:
                return Goal(kind="query", relation=g_attr, object=g_val)

        return None

    # ========================================================================
    # 求解算子一：传递闭包（比较/排序，含非严格 ≤/≥）
    # ========================================================================

    def _build_order_closure(
        self, relations: list[Relation],
    ) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]], list[SymbolicStep]]:
        """
        对 greater/less/equal 关系做传递闭包，支持非严格不等式。

        返回 (gt, geq, eq, steps)：
          - gt(a,b)   = a 严格大于 b
          - geq(a,b)  = a 大于等于 b（含相等）
          - eq(a,b)   = a 等于 b
        """
        gt: set[tuple[str, str]] = set()
        geq: set[tuple[str, str]] = set()
        eq: set[tuple[str, str]] = set()
        steps: list[SymbolicStep] = []
        step_id = 0

        def _record(premise: list[str], conclusion: str) -> None:
            nonlocal step_id
            step_id += 1
            steps.append(SymbolicStep(
                step_id=step_id,
                premise=premise,
                conclusion=conclusion,
                rule="传递闭包",
            ))

        for r in relations:
            a, b = r.subject, r.object
            if r.kind == "greater":
                if r.negated:
                    geq.add((b, a))            # a ≤ b
                else:
                    gt.add((a, b))
                    geq.add((a, b))            # a > b ⇒ a ≥ b
            elif r.kind == "less":
                if r.negated:
                    geq.add((a, b))            # a ≥ b
                else:
                    gt.add((b, a))
                    geq.add((b, a))            # b > a ⇒ b ≥ a
            elif r.kind == "equal":
                eq.add((a, b))
                eq.add((b, a))
                geq.add((a, b))
                geq.add((b, a))

        # 传递闭包（四条规则，含严格/非严格混合）
        changed = True
        while changed:
            changed = False
            # 规则1：gt(a,b) ∧ gt(b,c) → gt(a,c)
            for (a, b) in list(gt):
                for (c, d) in list(gt):
                    if b == c and a != d and (a, d) not in gt:
                        gt.add((a, d))
                        _record([f"{a}>{b}", f"{b}>{d}"], f"{a}>{d}")
                        changed = True
            # 规则2：gt(a,b) ∧ geq(b,c) → gt(a,c)
            for (a, b) in list(gt):
                for (c, d) in list(geq):
                    if b == c and a != d and (a, d) not in gt:
                        gt.add((a, d))
                        _record([f"{a}>{b}", f"{b}≥{d}"], f"{a}>{d}")
                        changed = True
            # 规则3：geq(a,b) ∧ gt(b,c) → gt(a,c)
            for (a, b) in list(geq):
                for (c, d) in list(gt):
                    if b == c and a != d and (a, d) not in gt:
                        gt.add((a, d))
                        _record([f"{a}≥{b}", f"{b}>{d}"], f"{a}>{d}")
                        changed = True
            # 规则4：geq(a,b) ∧ geq(b,c) → geq(a,c)
            for (a, b) in list(geq):
                for (c, d) in list(geq):
                    if b == c and a != d and (a, d) not in geq:
                        geq.add((a, d))
                        changed = True

        return gt, geq, eq, steps

    def _find_extremes(
        self, entities: set[str], gt: set[tuple[str, str]], maximum: bool,
        geq: set[tuple[str, str]] | None = None,
    ) -> list[str]:
        """
        找极值实体。maximum=True 找「没有被任何其它实体严格超过」的；
        否则找「没有严格超过任何其它实体」的。非严格并列会返回多个候选。

        ★P3修复：引入 geq（非严格 ≥）参与候选排除。
        求最大值时，若 other ≥ e（即 (other, e) ∈ geq），则 e 只有当
        e ≥ other（即 (e, other) ∈ geq，两者相等）时才可能并列最大；
        否则 e 被 other 支配（e < other），应排除。
        求最小值对称处理。
        """
        geq = geq or set()
        candidates = []
        for e in entities:
            if maximum:
                # 排除被严格超过的
                if any((other, e) in gt for other in entities if other != e):
                    continue
                # 排除被「非严格支配」的：other ≥ e 但 e 不 ≥ other（即 e < other）
                if any(
                    (other, e) in geq and (e, other) not in geq
                    for other in entities if other != e
                ):
                    continue
                candidates.append(e)
            else:
                # 排除严格超过其它实体的（最小值：不应严格大于任何其它）
                if any((e, other) in gt for other in entities if other != e):
                    continue
                # 排除被「非严格支配」的：e ≥ other 但 other 不 ≥ e（即 other < e）
                if any(
                    (e, other) in geq and (other, e) not in geq
                    for other in entities if other != e
                ):
                    continue
                candidates.append(e)
        return sorted(candidates)

    def _topological_sort(self, entities: set[str], gt: set[tuple[str, str]]) -> list[str]:
        """按 gt 关系做拓扑排序（从大到小）。环内/无法确定的按原顺序。"""
        indegree = {e: 0 for e in entities}
        dependents: dict[str, list[str]] = {e: [] for e in entities}
        for (a, b) in gt:
            if a in entities and b in entities:
                indegree[b] += 1
                dependents[a].append(b)

        from collections import deque
        queue = deque(sorted([e for e in entities if indegree[e] == 0]))
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for dep in sorted(dependents[node]):
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    queue.append(dep)

        for e in sorted(entities):
            if e not in order:
                order.append(e)
        return order

    def _solve_transitive(self, problem: SymbolicProblem, relations: list[Relation]) -> SymbolicResult | None:
        gt, geq, _eq, steps = self._build_order_closure(relations)
        if not gt and not geq:
            return None

        entities = problem.entities
        goal = problem.goal

        # ★自我验证·矛盾检测：A>B 且 B>A，或 A>B 且 B≥A（即 A>B 且 A≤B）
        _contradiction = any((b, a) in gt for (a, b) in gt if a != b) or \
                         any((b, a) in geq for (a, b) in gt)
        if _contradiction:
            steps.append(SymbolicStep(
                step_id=len(steps) + 1,
                premise=["检测到比较关系矛盾"],
                conclusion="题面自相矛盾，无法确定答案",
                rule="矛盾检测",
            ))
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="compare")

        if goal is None:
            return None

        # 1. 最大值
        if goal.kind == "max":
            maxima = self._find_extremes(entities, gt, maximum=True, geq=geq)
            if len(maxima) == 1:
                steps.append(SymbolicStep(
                    step_id=len(steps) + 1,
                    premise=[f"无其它实体严格超过 {maxima[0]}"],
                    conclusion=f"{maxima[0]} 最大",
                    rule="极值判定",
                ))
                return SymbolicResult(answer=maxima[0], steps=steps, solved=True, confidence=_evidence_conf(0.9, "symbolic", steps), task_type="compare")
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="compare")

        # 2. 最小值
        if goal.kind == "min":
            minima = self._find_extremes(entities, gt, maximum=False, geq=geq)
            if len(minima) == 1:
                steps.append(SymbolicStep(
                    step_id=len(steps) + 1,
                    premise=[f"{minima[0]} 未严格超过其它实体"],
                    conclusion=f"{minima[0]} 最小",
                    rule="极值判定",
                ))
                return SymbolicResult(answer=minima[0], steps=steps, solved=True, confidence=_evidence_conf(0.9, "symbolic", steps), task_type="compare")
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="compare")

        # 3. 排序（仅在存在严格关系时给出，避免把并列关系臆断为顺序）
        if goal.kind == "sort":
            if not gt:
                return SymbolicResult(answer=None, steps=steps, solved=False, task_type="compare")
            order = self._topological_sort(entities, gt)
            if len(order) >= 2:
                steps.append(SymbolicStep(
                    step_id=len(steps) + 1,
                    premise=[f"{a}>{b}" for (a, b) in sorted(gt)],
                    conclusion=" > ".join(order),
                    rule="拓扑排序",
                ))
                return SymbolicResult(answer=" > ".join(order), steps=steps, solved=True, confidence=0.85, task_type="compare")
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="compare")

        # 4. 两实体比较（谁更X / 谁比谁）
        if goal.kind == "pair" and len(entities) == 2:
            e1, e2 = sorted(entities)
            if (e1, e2) in gt:
                return SymbolicResult(answer=f"{e1}", steps=steps, solved=True, confidence=_evidence_conf(0.9, "symbolic", steps), task_type="compare")
            if (e2, e1) in gt:
                return SymbolicResult(answer=f"{e2}", steps=steps, solved=True, confidence=_evidence_conf(0.9, "symbolic", steps), task_type="compare")
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="compare")

        return None

    # ========================================================================
    # 求解算子二：变量算术（代入求值）
    # ========================================================================

    def _extract_expression(self, question: str) -> str | None:
        """提取含变量的目标算术表达式（A+B / A+B=? / A加B / A乘以B）。"""
        _cn_map = {"乘以": "*", "乘": "*", "除以": "/", "除": "/",
                   "加上": "+", "加": "+", "减去": "-", "减": "-"}
        _q = question
        for _cn, _op in _cn_map.items():
            _q = _q.replace(_cn, _op)

        _m = re.search(r'([A-Za-z][A-Za-z0-9+\-*/().\s]*[A-Za-z0-9)])\s*(?:=\s*\?|等于多少|是多少|\?|$)', _q)
        if _m:
            return _m.group(1).strip()
        return None

    def _safe_eval(self, expr: str, variables: dict[str, float]) -> float | None:
        """变量代入 + AST 白名单安全求值。"""
        _subbed = expr
        for var, val in variables.items():
            _subbed = re.sub(r'\b' + re.escape(var) + r'\b', str(val), _subbed)

        _subbed = re.sub(r'[^0-9+\-*/().%\s]', '', _subbed)
        if not _subbed or not re.search(r'\d', _subbed):
            return None
        if not re.fullmatch(r'[\d+\-*/().%\s]+', _subbed):
            return None

        try:
            _node = ast.parse(_subbed, mode='eval')
            _allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub, ast.UAdd)
            for _n in ast.walk(_node):
                if not isinstance(_n, _allowed):
                    return None
            _result = eval(compile(_node, '<calc>', 'eval'), {"__builtins__": {}}, {})
            if isinstance(_result, (int, float)) and not isinstance(_result, bool):
                if isinstance(_result, float) and _result.is_integer():
                    return int(_result)
                return round(_result, 6) if isinstance(_result, float) else _result
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SymbolicReasoner::_safe_eval L733")
            return None
        return None

    def _solve_eval(self, problem: SymbolicProblem, goal: Goal) -> SymbolicResult | None:
        expr = goal.expression or ""
        variables = problem.variables

        _expr_vars = set(re.findall(r'[A-Za-z]', expr))
        if not _expr_vars or not _expr_vars.issubset(set(variables.keys())):
            return None

        result = self._safe_eval(expr, variables)
        if result is None:
            return None

        steps: list[SymbolicStep] = []
        _sid = 0
        for var, val in variables.items():
            _sid += 1
            steps.append(SymbolicStep(
                step_id=_sid,
                premise=["已知"],
                conclusion=f"{var}={self._fmt_num(val)}",
                rule="变量绑定",
            ))
        steps.append(SymbolicStep(
            step_id=_sid + 1,
            premise=[f"{v}={self._fmt_num(val)}" for v, val in variables.items()],
            conclusion=f"{expr}={self._fmt_num(result)}",
            rule="变量代入",
        ))

        return SymbolicResult(answer=self._fmt_num(result), steps=steps, solved=True, confidence=_evidence_conf(0.95, "symbolic", steps), task_type="arithmetic")

    @staticmethod
    def _fmt_num(x: float) -> str:
        """数值转字符串（整数不带小数点）。"""
        if isinstance(x, float) and x.is_integer():
            return str(int(x))
        return str(x)

    # ========================================================================
    # 求解算子三：条件排除（简单 CSP：回溯搜索 + 约束传播）
    # ========================================================================

    _ATTR_VERBS = {
        "住": "房子", "养": "宠物", "喝": "饮料", "开": "车", "吃": "食物",
        "喜欢": "颜色", "是": "身份",
    }
    _NEG_WORDS = "不住|不养|不喝|不开|不吃|不喜欢|不是"
    _POS_WORDS = "住|养|喝|开|吃|喜欢|是"
    _CSP_STOP_VALUES = {"三", "种", "住", "养", "喝", "开", "吃", "别", "分别", "问", "谁", "有"}

    def _parse_csp(self, question: str) -> tuple | None:
        """
        从标准「爱因斯坦谜题简版」题面解析：
          实体、属性值域、约束、目标。

        返回 (entities, attr_values, constraints, goal) 或 None（无法解析）。
        """
        entities: list[str] = []
        _ent = re.search(r'([甲乙丙丁戊己庚辛壬癸]{2,6})', question)
        if _ent:
            entities = list(_ent.group(1))
        else:
            _ent2 = re.search(r'([A-Z])\s*[、,，]\s*([A-Z])\s*[、,，]?\s*([A-Z])?', question)
            if _ent2:
                entities = [g for g in _ent2.groups() if g]
        if len(entities) < 2:
            return None

        attr_values: dict[str, set[str]] = {}
        _enum_char = r'[^\s，。；,;？?住养喝开吃喜欢是有各分别三一二三四五六七八九两\d]'
        for m in re.finditer(
            r'(' + _enum_char + r'{2,6}?)([一二三四五六七八九两\d])(?:种|色|个|只|辆|款)(房子|宠物|饮料|车|食物)',
            question,
        ):
            vals, attr = m.group(1), m.group(3)
            if attr in set(self._ATTR_VERBS.values()):
                attr_values[attr] = set(vals)

        _cons_pat = re.compile(
            r'(' + '|'.join(re.escape(e) for e in entities) + r')('
            + self._NEG_WORDS + r'|' + self._POS_WORDS + r')([^\s，。；,;？?]{1,3})'
        )
        constraints: list[tuple[str, str, str, bool]] = []
        for m in _cons_pat.finditer(question):
            ent, verb, val = m.group(1), m.group(2), m.group(3)
            is_neg = verb.startswith("不")
            base_verb = verb[1:] if is_neg else verb
            attr = self._ATTR_VERBS.get(base_verb)
            if attr is None:
                continue
            val = re.sub(r'(房|宠物|饮料|车|食物)$', '', val)
            if not val or val in self._CSP_STOP_VALUES:
                continue
            constraints.append((ent, attr, val, not is_neg))
            attr_values.setdefault(attr, set()).add(val)

        if not constraints:
            return None

        goal = None
        _goal = re.search(r'谁(' + self._POS_WORDS + r')([^\s，。；,;？?]{1,3})', question)
        if _goal:
            g_verb, g_val = _goal.group(1), _goal.group(2)
            g_attr = self._ATTR_VERBS.get(g_verb)
            g_val = re.sub(r'(房|宠物|饮料|车|食物)$', '', g_val)
            if g_attr and g_val and g_val not in self._CSP_STOP_VALUES:
                goal = (g_attr, g_val)

        return entities, {k: sorted(v) for k, v in attr_values.items()}, constraints, goal

    def _solve_csp(
        self,
        entities: list[str],
        attr_values: dict[str, list[str]],
        constraints: list[tuple[str, str, str, bool]],
    ) -> list[dict[tuple[str, str], str]]:
        """
        回溯搜索 + 约束传播求所有解（最多 2 个，用于唯一性判断）。

        约束：
          - 排列约束：同一属性下，各实体取值互不相同（仅当值数 >= 实体数时生效）
          - 赋值约束：正向（X.attr=val）/ 否定（X.attr≠val）
        """
        attrs = list(attr_values.keys())
        if not attrs:
            return []

        if len(entities) * len(attrs) > 36 or len(entities) > 6:
            return []

        assignment: dict[tuple[str, str], str] = {}
        solutions: list[dict[tuple[str, str], str]] = []

        def _consistent(entity: str, attr: str, val: str) -> bool:
            if len(attr_values[attr]) >= len(entities):
                for e in entities:
                    if e != entity and assignment.get((e, attr)) == val:
                        return False
            for (c_ent, c_attr, c_val, c_pos) in constraints:
                if c_ent == entity and c_attr == attr:
                    if c_pos and val != c_val:
                        return False
                    if not c_pos and val == c_val:
                        return False
            return True

        _vars = [(e, a) for e in entities for a in attrs]

        def _backtrack(idx: int) -> None:
            if len(solutions) >= 2:
                return
            if idx == len(_vars):
                solutions.append(dict(assignment))
                return
            entity, attr = _vars[idx]
            for val in attr_values[attr]:
                if not _consistent(entity, attr, val):
                    continue
                assignment[(entity, attr)] = val
                _backtrack(idx + 1)
                del assignment[(entity, attr)]
                if len(solutions) >= 2:
                    return

        _backtrack(0)
        return solutions

    def _solve_functional(
        self, problem: SymbolicProblem, relations: list[Relation], goal: Goal,
    ) -> SymbolicResult | None:
        entities = sorted(problem.entities)
        domains = {k: sorted(v) for k, v in problem.domains.items()}
        if not entities or not domains:
            return None

        constraints = [
            (r.subject, r.attribute, r.object, not r.negated)
            for r in relations if r.attribute
        ]
        if not constraints:
            return None

        solutions = self._solve_csp(entities, domains, constraints)

        steps: list[SymbolicStep] = []
        _sid = 0
        for (ent, attr, val, pos) in constraints:
            _sid += 1
            steps.append(SymbolicStep(
                step_id=_sid,
                premise=["题面约束"],
                conclusion=f"{ent}.{attr}{'=' if pos else '≠'}{val}",
                rule="约束登记",
            ))

        g_attr, g_val = goal.relation, goal.object
        if g_attr is None or g_val is None or not solutions:
            return None

        _answer_candidates = set()
        for sol in solutions:
            for (ent, attr) in sol:
                if attr == g_attr and sol[(ent, attr)] == g_val:
                    _answer_candidates.add(ent)

        if len(_answer_candidates) != 1:
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="csp")

        _answer = _answer_candidates.pop()
        steps.append(SymbolicStep(
            step_id=_sid + 1,
            premise=["约束传播后唯一确定"],
            conclusion=f"{_answer} 的 {g_attr} 为 {g_val}",
            rule="约束传播",
        ))
        return SymbolicResult(answer=_answer, steps=steps, solved=True, confidence=0.8, task_type="csp")

    # ========================================================================
    # 求解算子四：命题逻辑（如果…那么/则、只要…就、除非…否则）
    # ========================================================================

    _IMPLICATION_PATTERNS = [
        (re.compile(r'如果([^。；！？]+?)(?:，)?(?:那么|则|就)([^。；！？，,]+)'), False),
        (re.compile(r'只要([^。；！？]+?)(?:，)?就([^。；！？，,]+)'), False),
        (re.compile(r'除非([^。；！？]+?)(?:，)?否则([^。；！？，,]+)'), True),   # 除非P否则Q → ¬P→Q
    ]

    def _parse_literal(self, text: str) -> tuple[str, int] | None:
        """把短语解析为 (原子, 符号)：+1 真 / -1 假。解析失败返回 None。"""
        t = text.strip().strip('，。；,;！？? ')
        if not t:
            return None
        # 后缀否定/肯定标记
        for _neg, _pos in (("不成立", "成立"), ("为假", "为真"), ("是假", "是真")):
            if t.endswith(_neg):
                return t[: -len(_neg)].strip(), -1
            if t.endswith(_pos):
                return t[: -len(_pos)].strip(), +1
        # 前缀否定：非X
        if t.startswith("非"):
            _rest = t[1:].strip()
            return (_rest or t), -1
        # 裸原子 → 视为真
        return t, +1

    def _extract_implications(self, question: str) -> tuple[list, list[tuple[int, int]]]:
        """提取蕴含式 [(前提文字列表, 结论原子, 结论符号)] 及其文本区间。"""
        impls: list = []
        spans: list[tuple[int, int]] = []
        for _pat, _is_unless in self._IMPLICATION_PATTERNS:
            for m in _pat.finditer(question):
                premise_raw = m.group(1)
                cons_raw = m.group(2)
                _parts = re.split(r'且|并且|而且|和|与', premise_raw)
                premise_lits: list[tuple[str, int]] = []
                for _p in _parts:
                    _lit = self._parse_literal(_p)
                    if _lit is None:
                        premise_lits = []
                        break
                    premise_lits.append(_lit)
                if not premise_lits:
                    continue
                _cons = self._parse_literal(cons_raw)
                if _cons is None:
                    continue
                if _is_unless:
                    # 除非P否则Q → ¬P→Q
                    _p_atom, _p_sign = premise_lits[0]
                    premise_lits = [(_p_atom, -_p_sign)]
                impls.append((premise_lits, _cons[0], _cons[1]))
                spans.append(m.span())
        return impls, spans

    @staticmethod
    def _mask_spans(question: str, spans: list[tuple[int, int]]) -> str:
        _chars = list(question)
        for s, e in spans:
            for i in range(s, min(e, len(_chars))):
                _chars[i] = " "
        return "".join(_chars)

    def _extract_propositional_facts(self, text: str) -> list[tuple[str, int]]:
        facts: list[tuple[str, int]] = []
        for m in re.finditer(r'([^，。；,;！？?]{1,12}?)(不成立|成立|为假|为真|是假|是真)', text):
            atom, val = m.group(1).strip(), m.group(2)
            sign = +1 if val in ("成立", "为真", "是真") else -1
            if atom:
                facts.append((atom, sign))
        return facts

    def _extract_propositional_goal(self, text: str) -> tuple[str | None, tuple[int, int] | None]:
        m = re.search(r'([^，。；,;！？?]{1,12}?)(是否成立|成立吗|为真吗)', text)
        if m:
            _atom = m.group(1).strip()
            if _atom:
                return _atom, m.span()
        return None, None

    def _solve_propositional(self, question: str) -> SymbolicResult | None:
        if not re.search(r'如果|只要|除非', question):
            return None
        impls, spans = self._extract_implications(question)
        if not impls:
            return None
        masked = self._mask_spans(question, spans)
        goal_atom, goal_span = self._extract_propositional_goal(masked)
        if goal_atom is None or goal_span is None:
            return None
        # 目标（问句）也要遮罩，避免「B成立吗」被误当作「B成立」事实
        facts = self._extract_propositional_facts(self._mask_spans(masked, [goal_span]))

        steps: list[SymbolicStep] = []
        _sid = 0
        assignment: dict[str, bool] = {}

        # 事实登记（含事实矛盾检测）
        for atom, sign in facts:
            _sid += 1
            val = (sign == +1)
            if atom in assignment and assignment[atom] != val:
                steps.append(SymbolicStep(step_id=_sid, premise=["事实"], conclusion="事实自相矛盾", rule="矛盾检测"))
                return SymbolicResult(answer=None, steps=steps, solved=False, task_type="propositional")
            assignment[atom] = val
            steps.append(SymbolicStep(
                step_id=_sid,
                premise=["题面事实"],
                conclusion=f"{atom}{'成立' if val else '不成立'}",
                rule="事实登记",
            ))

        # 前向（肯定前件）/ 后向（否定后件）链传播至不动点
        changed = True
        while changed:
            changed = False
            for premise_lits, cons_atom, cons_sign in impls:
                cons_val = (cons_sign == +1)
                # 合取前提全真 → 结论真（modus ponens）
                _all_true = True
                for p_atom, p_sign in premise_lits:
                    if assignment.get(p_atom) != (p_sign == +1):
                        _all_true = False
                        break
                if _all_true:
                    if assignment.get(cons_atom) is None:
                        _sid += 1
                        assignment[cons_atom] = cons_val
                        steps.append(SymbolicStep(
                            step_id=_sid,
                            premise=[f"{p_atom}{'成立' if p_sign == +1 else '不成立'}" for p_atom, p_sign in premise_lits],
                            conclusion=f"{cons_atom}{'成立' if cons_val else '不成立'}",
                            rule="肯定前件",
                        ))
                        changed = True
                    elif assignment[cons_atom] != cons_val:
                        _sid += 1
                        steps.append(SymbolicStep(step_id=_sid, premise=["推导冲突"], conclusion="题面自相矛盾", rule="矛盾检测"))
                        return SymbolicResult(answer=None, steps=steps, solved=False, task_type="propositional")

                # 单前提 + 结论为假 → 前提为假（modus tollens）
                if len(premise_lits) == 1:
                    p_atom, p_sign = premise_lits[0]
                    if assignment.get(cons_atom) == (not cons_val):
                        p_target = (p_sign != +1)
                        if assignment.get(p_atom) is None:
                            _sid += 1
                            assignment[p_atom] = p_target
                            steps.append(SymbolicStep(
                                step_id=_sid,
                                premise=[f"{cons_atom}{'成立' if cons_val else '不成立'}"],
                                conclusion=f"{p_atom}{'成立' if p_target else '不成立'}",
                                rule="否定后件",
                            ))
                            changed = True
                        elif assignment[p_atom] != p_target:
                            _sid += 1
                            steps.append(SymbolicStep(step_id=_sid, premise=["推导冲突"], conclusion="题面自相矛盾", rule="矛盾检测"))
                            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="propositional")

        if goal_atom not in assignment:
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="propositional")

        _goal_val = assignment[goal_atom]
        _answer = f"{goal_atom}{'成立' if _goal_val else '不成立'}"
        _sid += 1
        steps.append(SymbolicStep(step_id=_sid, premise=["推导链完成"], conclusion=_answer, rule="结论判定"))
        return SymbolicResult(answer=_answer, steps=steps, solved=True, confidence=_evidence_conf(0.9, "symbolic", steps), task_type="propositional")

    # ========================================================================
    # 求解算子五：量词约束（至少/恰好/所有/只有）
    # ========================================================================

    _CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def _extract_quantifier_entities(self, question: str) -> list[str]:
        _ent = re.search(r'([甲乙丙丁戊己庚辛壬癸]{2,6})', question)
        if _ent:
            return list(_ent.group(1))
        _ent2 = re.search(r'([A-Z])\s*[、,，]\s*([A-Z])\s*[、,，]?\s*([A-Z])?', question)
        if _ent2:
            return [g for g in _ent2.groups() if g]
        return []

    def _extract_quantifier_count(self, question: str, entities: list[str]) -> tuple[int, int] | None:
        m = re.search(r'恰好([\d一二三四五六七八九十两]+)', question)
        if m:
            n = self._CN_NUM.get(m.group(1))
            if n is None:
                n = int(m.group(1)) if m.group(1).isdigit() else None
            if n is not None:
                return n, n
        m = re.search(r'至少([\d一二三四五六七八九十两]+)', question)
        if m:
            n = self._CN_NUM.get(m.group(1))
            if n is None:
                n = int(m.group(1)) if m.group(1).isdigit() else None
            if n is not None:
                return n, len(entities)
        if re.search(r'所有|每个|全部', question):
            return len(entities), len(entities)
        if re.search(r'只有[甲乙丙丁戊己庚辛壬癸A-Z]{1}(?:会|喜欢|去|住|养)', question):
            return 1, 1
        return None

    def _solve_quantifier(self, question: str) -> SymbolicResult | None:
        if not re.search(r'至少|恰好|所有|每个|全部|只有', question):
            return None
        entities = self._extract_quantifier_entities(question)
        if len(entities) < 2:
            return None

        # 目标谓词：谁 + 动词 + 宾语
        m_goal = re.search(r'谁(会|喜欢|去|住|养)([^\s，。；,;？?]{1,4})', question)
        if not m_goal:
            return None
        verb, obj = m_goal.group(1), m_goal.group(2)
        obj = re.sub(r'(吗|呢|呀|么)$', '', obj)
        if not obj:
            return None

        count = self._extract_quantifier_count(question, entities)
        if count is None:
            return None
        min_c, max_c = count

        # 事实：X会/不会(谓词)
        facts: dict[str, bool] = {}
        for e in entities:
            if re.search(re.escape(e) + verb + re.escape(obj), question):
                facts[e] = True
            elif re.search(re.escape(e) + '不' + verb + re.escape(obj), question):
                facts[e] = False

        # 枚举未知实体的取值，统计满足计数约束的解
        unknown = [e for e in entities if e not in facts]
        known_true = sum(1 for e in entities if facts.get(e) is True)
        solutions: list[dict[str, bool]] = []
        for mask in range(1 << len(unknown)):
            total_true = known_true + (mask).bit_count()
            if not (min_c <= total_true <= max_c):
                continue
            assign = dict(facts)
            for i, e in enumerate(unknown):
                assign[e] = bool(mask & (1 << i))
            solutions.append(assign)
            if len(solutions) > 2:
                break

        steps: list[SymbolicStep] = []
        _sid = 0
        if min_c == max_c:
            _desc = f"{'所有' if min_c == len(entities) else '恰好' + str(min_c) + '人'}{verb}{obj}"
        else:
            _desc = f"至少{min_c}人{verb}{obj}"
        _sid += 1
        steps.append(SymbolicStep(step_id=_sid, premise=["题面约束"], conclusion=_desc, rule="量词约束"))
        for e, v in facts.items():
            _sid += 1
            steps.append(SymbolicStep(
                step_id=_sid,
                premise=["题面事实"],
                conclusion=f"{e}{'' if v else '不'}{verb}{obj}",
                rule="事实登记",
            ))

        if not solutions:
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="quantifier")

        # 唯一性验证：所有解中「满足谓词的实体集合」必须一致
        candidate_sets: set[tuple] = set()
        for assign in solutions:
            true_set = tuple(sorted(e for e in entities if assign.get(e) is True))
            candidate_sets.add(true_set)
        if len(candidate_sets) != 1:
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="quantifier")

        answer_entities = candidate_sets.pop()
        if not answer_entities:
            return SymbolicResult(answer=None, steps=steps, solved=False, task_type="quantifier")

        # 按题面原始实体顺序输出，而非 Unicode 码点顺序
        answer = "、".join(e for e in entities if e in set(answer_entities))
        _sid += 1
        steps.append(SymbolicStep(
            step_id=_sid,
            premise=["量词约束 + 事实唯一确定"],
            conclusion=f"{verb}{obj}的是 {answer}",
            rule="约束传播",
        ))
        return SymbolicResult(answer=answer, steps=steps, solved=True, confidence=0.85, task_type="quantifier")

    # ========================================================================
    # 语义理解辅助：复杂语义识别（诚实降级）
    # ========================================================================

    def _detect_unsupported_semantics(self, question: str) -> str | None:
        """
        ★语义理解辅助：识别当前符号推理无法精确处理的复杂逻辑语义。

        返回语义类型（probability/temporal）或 None。
        识别到即诚实降级（solved=False），由上层交给大模型，绝不硬编答案。
        """
        for _sem_type, _pattern in self._COMPLEX_SEMANTICS.items():
            if re.search(_pattern, question):
                return _sem_type
        return None

    # ========================================================================
    # 能力声明（B2：符号引擎能力边界，诚实枚举，宁缺毋滥）
    # ========================================================================

    def capabilities(self) -> dict[str, Any]:
        """
        ★B2能力声明：明确声明符号引擎「能解什么、不能解什么」，供推理路由
        （QICA 建议 / 检测器纠偏）与验证学习枢纽统计覆盖率时参考。

        核心原则（诚实枚举，宁缺毋滥）：
            - 只列已实现且经自测验证的能力；
            - 每条标注覆盖句式、输入约束、已知边界；
            - 明确标记「不能处理」的语义，绝不虚标覆盖率。

        Returns:
            {"capabilities": [...], "unsupported": [...], "version": str}
            每项能力：{"type", "description", "coverage", "constraints", "known_limits"}
        """
        return {
            "version": "v25.1",
            "capabilities": [
                {
                    "type": "compare",
                    "description": "比较关系推理：极值（最高/最低）、排序、两实体比较",
                    "coverage": (
                        "正向比较「A比B高/矮」；否定比较「A不高于B」「A不比B高」"
                        "（转为非严格 ≤/≥ 参与传递闭包）；符号「A>B」「A<B」「A=B」"
                    ),
                    "constraints": "实体名需为单字/短词（甲乙丙/A/B/C 等）；比较形容词限内置正/反向词表",
                    "known_limits": "并列极值（多个候选）诚实降级 solved=False；题面自相矛盾检测后降级",
                },
                {
                    "type": "arithmetic",
                    "description": "变量赋值 + 算术表达式求值",
                    "coverage": "「A=5」「A是5」「A等于5」；表达式「A+B」「A*B-1」「A乘以B」",
                    "constraints": "变量名限单字母；运算符限 + - * / % 及括号；AST 白名单沙箱（禁 eval 任意代码）",
                    "known_limits": "不支持函数调用、幂运算、变量名多于单字符、非数值表达式",
                },
                {
                    "type": "csp",
                    "description": "条件排除（爱因斯坦谜题简版）",
                    "coverage": (
                        "实体（甲乙丙/A/B/C）+ 属性值域（房子/宠物/饮料/车/食物/颜色/身份）+ "
                        "正/负约束（「甲住红房」「乙不住蓝房」）+ 目标（「谁住蓝房」）"
                    ),
                    "constraints": "实体≤6、属性×实体≤36（回溯搜索规模上限）；值数≥实体数时才启用排列唯一约束",
                    "known_limits": "多解（唯一性不成立）诚实降级；非标准题面句式无法解析则返回 None",
                },
                {
                    "type": "propositional",
                    "description": "命题逻辑：蕴含推理 + 肯定前件/否定后件",
                    "coverage": "「如果P那么Q」「只要P就Q」「除非P否则Q」；事实「A成立/A不成立」；目标「A成立吗」",
                    "constraints": "原子命题需以「成立/不成立/为真/为假/是真/是假」结尾或「非X」前缀；合取用「且/并且/而且/和/与」",
                    "known_limits": "仅支持肯定前件(MP)与否定后件(MT)，不支持复杂真值表/多前提互斥推理；信息不足诚实降级",
                },
                {
                    "type": "quantifier",
                    "description": "量词约束：计数约束 + 枚举验证",
                    "coverage": "「至少N」「恰好N」「所有/每个/全部」「只有X」；目标「谁会/喜欢/去/住/养Y」",
                    "constraints": "实体（甲乙丙/A/B/C）≥2；计数词限中文数字/阿拉伯数字；谓词动词限「会/喜欢/去/住/养」",
                    "known_limits": "未知实体枚举解不唯一（满足谓词的集合不一致）诚实降级；复杂嵌套量词不支持",
                },
            ],
            "unsupported": [
                {
                    "type": "probability",
                    "description": "概率/可能性/百分比/比例题",
                    "handling": "识别后诚实降级 unsupported_probability，交大模型",
                },
                {
                    "type": "temporal",
                    "description": "时序/先后顺序/第几题",
                    "handling": "识别后诚实降级 unsupported_temporal，交大模型",
                },
                {
                    "type": "open_semantics",
                    "description": "开放语义、长文本推理、模糊匹配、常识推理",
                    "handling": "无法形式化则返回空 SymbolicResult，交大模型，绝不硬编答案",
                },
            ],
        }


# ============================================================================
# 自测
# ============================================================================
if __name__ == "__main__":
    reasoner = SymbolicReasoner()
    cases = [
        # 比较/排序
        ("A比B高，B比C高，谁最高？", "compare", "A"),
        ("A比B矮，B比C矮，谁最矮？", "compare", "A"),
        ("A比B高，B比C高，从高到低排序？", "compare", "A > B > C"),
        # 否定比较（阶段5新增）
        ("甲不高于乙，乙比丙矮，谁最高？", "compare", "丙"),
        ("甲不高于乙，乙不高于甲，谁最高？", "compare", None),
        # 算术
        ("A=5, B=3, A+B=?", "arithmetic", "8"),
        ("A=5, B=3, A*B-1=?", "arithmetic", "14"),
        # CSP
        ("甲乙丙三人分别住红蓝绿三种房子。甲住红房，乙不住蓝房，丙不住绿房，谁住蓝房？", "csp", "丙"),
        # 命题逻辑（阶段6）
        ("如果A，那么B。A成立。B成立吗？", "propositional", "B成立"),
        ("如果A，那么B。B不成立。A成立吗？", "propositional", "A不成立"),
        ("如果A，那么B。B成立。A成立吗？", "propositional", None),
        # 量词约束（阶段6）
        ("甲乙丙三人，至少两人会游泳，甲不会游泳，谁会游泳？", "quantifier", "乙、丙"),
        ("甲乙丙三人，恰好一人会游泳，甲会游泳，谁会游泳？", "quantifier", "甲"),
        # 复杂语义诚实降级
        ("这个事件发生的概率是多少？", "unsupported_probability", None),
        # 矛盾检测
        ("A比B高，B比A高，谁最高？", "compare", None),
    ]
    ok = True
    for q, exp_type, exp_ans in cases:
        res = reasoner.reason(q)
        type_ok = res.task_type == exp_type
        ans_ok = res.answer == exp_ans
        status = "OK" if (type_ok and ans_ok) else "FAIL"
        if not (type_ok and ans_ok):
            ok = False
        print(f"[{status}] {q[:26]} => type={res.task_type} solved={res.solved} answer={res.answer}")
    print("\nALL PASS" if ok else "\nSOME FAILED")
