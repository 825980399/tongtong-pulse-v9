# -*- coding: utf-8 -*-
"""
AestheticJudge.py —— 审美评判器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 评估产出的美学质量与协调性
机制: 基于AestheticJudge类实现，包含10个核心方法
定位: 进化评估层
"""

import re
from typing import Any


class AestheticJudge:
    """补丁审美判据评分器。"""

    # 各维度权重（合计 1.0）
    W_SIZE = 0.30
    W_COMPLEXITY = 0.25
    W_READABILITY = 0.20
    W_VERBOSITY = 0.15
    W_NAMING = 0.10

    # 阈值
    SIZE_IDEAL_MIN = 10     # 合理改动最小行数
    SIZE_IDEAL_MAX = 300    # 合理改动最大行数
    DEEP_INDENT = 4         # 深层嵌套判定：缩进级别 >= 4
    LONG_LINE = 120         # 超长行判定：字符数 > 120
    COMMENT_MIN = 0.05      # 注释占比下限
    COMMENT_MAX = 0.40      # 注释占比上限
    IDENT_MIN_LEN = 3       # 合理命名最小长度

    def feedback_guidance(self) -> str:
        """审美偏好指引（供生成侧回流）。

        把框架的审美评分维度与权重翻译成给 LLM 生成器的简短指引，
        使生成的修复代码更符合框架审美偏好（减少 D 级拦截触发）。
        纯文本、不改变评分逻辑、异常由调用方降级为空串。
        """
        return (
            "【框架审美偏好——请按此调整输出代码】\n"
            "1. 改动规模适中（约10-300行，不要过度膨胀，也不做无意义的微改）；\n"
            "2. 控制复杂度：避免深层嵌套（缩进>=4层），把复杂逻辑拆分为清晰的小步骤；\n"
            "3. 可读性优先：逻辑平铺直叙，避免过深的条件嵌套；\n"
            "4. 注释适度（占比5%-40%），在关键处说明意图，不刷屏；\n"
            "5. 命名规范：变量/函数名>=3字符，语义明确，不堆砌缩写。\n"
            "以上维度会由框架自动评分：A/B 级补丁优先自动应用，D 级补丁将转入人工审批。"
        )

    def _check_syntax(self, code: str) -> tuple[bool, str]:
        """语法前置校验：能解析为合法 Python 才允许参与审美评分。

        ★D5修复（P2，2026-09-05，星轨 PHASE6 列为第六批必做第 1 项）：
            原实现只衡量「表面美观」（体积/复杂度/可读性/冗余/命名五个维度），
            **完全不校验语法合法性**。实测一段括号与引号都未闭合的截断代码
            （应用后必然 SyntaxError）竟得到 **B 级 57.7 分**——
            而 A/B 级正是自动应用的放行门槛（见 feedback_guidance 第 58 行）。
            当前未酿成事故，仅因 S1 修复已关闭自动应用；一旦有人重新打开，
            截断补丁就会以 B 级身份被放行，应用即崩。
            故在此补 ast.parse 前置校验，不通过一律判 D 级 0 分。

        ★为什么要尝试多种解析形态：
            补丁的 modified_code 常常是**带缩进的代码片段**（方法体、except 块、
            if 分支等），直接 ast.parse 会因 IndentationError 失败。
            若不做容错，大量「合法片段」会被误判为坏代码而错杀。
            因此依次尝试：① 原样解析 ② dedent 后解析 ③ 包进函数体解析。
            三者皆失败才判定为语法错误。

        Returns:
            (是否通过, 失败原因)；通过时原因为空串。
        """
        import ast as _ast
        import textwrap as _tw

        _dedented = _tw.dedent(code)
        _indented = _tw.indent(_dedented, "    ")
        # 依次尝试各种「上下文包装」，覆盖补丁片段的常见形态：
        #   - 原样          ：完整模块级代码
        #   - dedent        ：整体带缩进的模块级代码
        #   - 函数体        ：方法体片段
        #   - try 前缀      ：except / except..as / else / finally 片段
        #                     （★补丁 #1 实况：以 `except Exception as e:` 开头，
        #                       excpet 不能脱离 try 独立存在，必须补 try 才合法）
        #   - if 前缀       ：elif / else 片段
        #   - class 前缀    ：类体片段
        #   - while 前缀    ：循环体片段
        _candidates = (
            code,
            _dedented,
            "def _patch_probe():\n" + _indented,
            "try:\n    pass\n" + _dedented,
            "try:\n    pass\n" + _indented,
            # elif / else 必须与 if **同级**，不能塞进 if 块内，
            # 故此处 if 之后先补一条 pass 再接原片段
            "if True:\n    pass\n" + _dedented,
            "if True:\n" + _indented,
            "class _PatchProbe:\n" + _indented,
            "while True:\n" + _indented,
        )
        _last_err = ""
        for _candidate in _candidates:
            try:
                _ast.parse(_candidate)
                return True, ""
            except SyntaxError as _e:
                _last_err = f"SyntaxError: {_e.msg}（行 {_e.lineno}）"
            except (ValueError, RecursionError, MemoryError) as _e:
                # 非语法类异常：源码含空字节/嵌套过深/内存不足，同样视为不可用
                _last_err = f"{type(_e).__name__}: {_e}"
        return False, _last_err

    def score(self, code: str | None) -> dict[str, Any]:
        """对一段代码做审美评分。

        Args:
            code: 补丁的 modified_code（新增/修改后的代码片段）

        Returns:
            dict: {total, size, complexity, readability, verbosity, naming,
                   grade, reason, syntax_valid}
                  其中 syntax_valid=False 表示未通过语法校验，
                  此时 total 恒为 0.0、grade 恒为 "D"（不参与审美评分）。
        """
        if not code or not code.strip():
            return {
                "total": 0.0,
                "size": 0.0, "complexity": 0.0, "readability": 0.0,
                "verbosity": 0.0, "naming": 0.0,
                "grade": "D",
                "reason": "无代码内容",
                "syntax_valid": False,
            }

        # ★D5：语法前置校验——不合法直接判 D 级，不再进入审美评分。
        #   宁可拒绝评分，也不能给「应用即崩」的代码打出 B 级。
        _ok, _err = self._check_syntax(code)
        if not _ok:
            return {
                "total": 0.0,
                "size": 0.0, "complexity": 0.0, "readability": 0.0,
                "verbosity": 0.0, "naming": 0.0,
                "grade": "D",
                "reason": f"语法校验未通过，拒绝评分: {_err}",
                "syntax_valid": False,
            }

        lines = code.splitlines()
        _size = self._score_size(len(lines))
        _complexity = self._score_complexity(lines)
        _readability = self._score_readability(lines)
        _verbosity = self._score_verbosity(lines)
        _naming = self._score_naming(code)

        total = (
            _size * self.W_SIZE
            + _complexity * self.W_COMPLEXITY
            + _readability * self.W_READABILITY
            + _verbosity * self.W_VERBOSITY
            + _naming * self.W_NAMING
        )
        total = round(max(0.0, min(100.0, total)), 1)
        grade = self._grade(total)
        return {
            "total": total,
            "size": round(_size, 1),
            "complexity": round(_complexity, 1),
            "readability": round(_readability, 1),
            "verbosity": round(_verbosity, 1),
            "naming": round(_naming, 1),
            "grade": grade,
            "reason": self._reason(total, grade),
            "syntax_valid": True,   # ★D5：已通过语法前置校验
        }

    # ========== 各维度评分（0~100） ==========

    def _score_size(self, n: int) -> float:
        """改动规模：适中得满分，过大/过小线性衰减。"""
        if self.SIZE_IDEAL_MIN <= n <= self.SIZE_IDEAL_MAX:
            return 100.0
        if n == 0:
            return 10.0
        if n < self.SIZE_IDEAL_MIN:
            return 100.0 * (n / self.SIZE_IDEAL_MIN)
        # 过大：超过上限后每 3 倍扣 40 分，最低 10 分
        ratio = n / self.SIZE_IDEAL_MAX
        return max(10.0, 100.0 - (ratio - 1.0) * 40.0)

    def _score_complexity(self, lines: list[str]) -> float:
        """复杂度：深层嵌套（>=4级缩进）占比越低越好。"""
        _total = 0
        _deep = 0
        for _l in lines:
            _stripped = _l.lstrip(" \t")
            if not _stripped or _stripped.startswith("#"):
                continue
            _indent = len(_l) - len(_l.lstrip(" \t"))
            _level = _indent // 4  # 按 4 空格一级估算
            _total += 1
            if _level >= self.DEEP_INDENT:
                _deep += 1
        if _total == 0:
            return 60.0
        _deep_ratio = _deep / _total
        return max(20.0, 100.0 - _deep_ratio * 100.0 * 1.5)

    def _score_readability(self, lines: list[str]) -> float:
        """可读性：注释/说明行占比落在合理区间最佳。"""
        _total = 0
        _comment = 0
        for _l in lines:
            _stripped = _l.strip()
            if not _stripped:
                continue
            _total += 1
            if _stripped.startswith(("#", "\"\"\"")):
                _comment += 1
        if _total == 0:
            return 50.0
        _ratio = _comment / _total
        if self.COMMENT_MIN <= _ratio <= self.COMMENT_MAX:
            return 100.0
        if _ratio > self.COMMENT_MAX:
            # 注释过多（注水）线性衰减
            return max(40.0, 100.0 - (_ratio - self.COMMENT_MAX) * 120.0)
        # 注释过少（无解释）：完全无注释显著扣分（降到 40）
        return max(30.0, 100.0 - (self.COMMENT_MIN - _ratio) / self.COMMENT_MIN * 60.0)

    def _score_verbosity(self, lines: list[str]) -> float:
        """简洁性：超长行（>120字符）占比越低越好。"""
        _total = 0
        _long = 0
        for _l in lines:
            _stripped = _l.strip()
            if not _stripped:
                continue
            _total += 1
            if len(_l) > self.LONG_LINE:
                _long += 1
        if _total == 0:
            return 60.0
        _long_ratio = _long / _total
        return max(30.0, 100.0 - _long_ratio * 100.0 * 2.0)

    def _score_naming(self, code: str) -> float:
        """命名质量：标识符命名长度合理性。"""
        _idens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", code)
        _idens = [_i for _i in _idens
                  if not _i.startswith("__")
                  and _i not in {"self", "cls", "return", "if", "else", "for",
                                 "while", "def", "class", "import", "from",
                                 "try", "except", "finally", "with", "as",
                                 "in", "not", "and", "or", "is", "None",
                                 "True", "False", "lambda", "pass", "raise",
                                 "yield", "assert", "del", "global", "nonlocal",
                                 "break", "continue", "elif", "await", "async"}]
        if not _idens:
            return 60.0
        _short = sum(1 for _i in _idens if len(_i) < self.IDENT_MIN_LEN)
        _ratio = _short / len(_idens)
        return max(30.0, 100.0 - _ratio * 100.0 * 2.0)

    # ========== 等级与结论 ==========

    def _grade(self, total: float) -> str:
        if total >= 85:
            return "S"
        if total >= 70:
            return "A"
        if total >= 55:
            return "B"
        if total >= 40:
            return "C"
        return "D"

    def _reason(self, total: float, grade: str) -> str:
        if total >= 85:
            return "优秀：改动克制、结构清晰、命名规范"
        if total >= 70:
            return "良好：整体优雅，个别维度可优化"
        if total >= 55:
            return "合格：能用但不够优，建议打磨结构"
        if total >= 40:
            return "偏弱：复杂度或可读性欠佳，建议重构"
        return "粗糙：改动过大/过碎或严重缺乏可读性，不建议采纳"


# ========== 模块级单例 ==========
_judge: AestheticJudge | None = None


def get_aesthetic_judge() -> AestheticJudge:
    """获取 AestheticJudge 单例"""
    global _judge
    if _judge is None:
        _judge = AestheticJudge()
    return _judge
