#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_write_only_gates.py —— "只写不读"增量 CI 门禁（第106批 T-106b）。

复用第104批 CI 四道关口模式（与 cw2_t2e_ci_gate_silent_except.py 同构）：
对 base..target 的 diff，新增的「只写不读」缺陷（config 死键 / 标记 / 字段）必须为 0。

检测维度（增量，仅防新增、不清理存量）：
  D1 死配置键        config.py 新增顶层 UPPER 键，全仓无读取点 → 报红
  D2 只写不读属性/字段  self._X = / d["k"] = 等写点落在本 diff 新增行，且全仓无读取点 → 报红

设计要点：
  - 复用烛微审计原型 wo4_detect_writeonly.py 的 AST 分析内核（D1/D2），此处收编为可用门禁。
  - 增量判定：仅当缺陷的「写点」落在本次 diff 的**新增行**时才报红；存量死键/标记不清理。
  - D1 死键判据：config.py 顶层 UPPER 键 → 全仓读取方计数（AST + token + 字符串三通道 +
    config.get_xxx() 间接读取通道 + 别名常量传播），零读取方才判死。
  - D2 写点判据：属性 self._X= / 字典 d["k"]= 写点 vs ast.Load 读点（全仓合并，保守），
    无字符串/扩展名/别名/密集动态访问等间接读取信号才判「已实锤」。
  - 置信度：「已实锤」直接判失败（CI 红）；「疑似/需人工」仅作告警（不阻塞），避免误伤。

用法（CI 第五道关口）：
  python -X utf8 check_write_only_gates.py --base HEAD~1 --target worktree
  python -X utf8 check_write_only_gates.py --base origin/master --target worktree --verbose
  python -X utf8 check_write_only_gates.py --selftest        # 正控：尺子会响且不乱响
退出码：0=通过；1=存在新增只写不读缺陷（CI 失败）。
"""
from __future__ import annotations

import argparse
import ast
import difflib
import io
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from nucleus._silent_except import silent_exc

class GitBaselineUnavailable(Exception):
    """★T-112e#1：git 增量基线不可用（git diff 失败）时抛出，门禁据此 rc=2 中止（禁假绿）。"""

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_REL = "config.py"
CONFIG_PY = os.path.join(ROOT, CONFIG_REL)

EXCLUDE_TOP = {"tmp", "backups", "logs", "data", "models"}
EXCLUDE_ANY = {"__pycache__", "build", "dist", ".git", "node_modules", ".venv", "venv"}
BAK_RE = re.compile(r"\.bak", re.I)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

POSCtl_ATTR = ["_beat_count", "_silence_threshold", "_organ_last_active",
               "_check_interval", "_last_beat_time", "_beat_intervals",
               "_total_emitted", "_layer_stats", "_counter_lock",
               "_mark_deferred_issues"]
POSCtl_KEY = ["silence_details", "issues", "risk_level", "runtime_verified",
              "event_type", "silent_organs", "source_organ", "payload",
              "priority", "issue_count"]
# ★T-112e#3：D1 死配置键显式豁免裁决表（接线/删除/显式豁免三选一 → 此处为显式豁免）。
#   键 -> (裁决编号, 理由)；命中即不报。存量键经门禁全量盘点逐条裁决，未来新增死键仍会被增量门禁捕获。
D1_EXEMPT_KEYS: dict = {
    "ADAPTIVE_LOAD_CHECK_INTERVAL": ("T112e-D1-001", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "CAUSAL_INFERENCE_CONFIG": ("T112e-D1-002", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "COLD_INDEX_BACKOFF_BASE": ("T112e-D1-003", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "COLD_NODE_LOAD_ON_DEMAND": ("T112e-D1-004", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "ENABLE_CONTRADICTION_WRITE_PRECHECK": ("T112e-D1-005", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "ENABLE_LIVER_SEMANTIC_SUMMARY": ("T112e-D1-006", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "ENABLE_TEST_LOG_ISOLATION": ("T112e-D1-007", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "EXPERIENCE_CLEANUP_BATCH": ("T112e-D1-008", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "EXPERIENCE_SUMMARY_VERSION": ("T112e-D1-009", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "HOT_NODE_LEVELS": ("T112e-D1-010", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "PARQUET_VERIFY_COUNT_DRIFT_PCT": ("T112e-D1-011", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "PARQUET_VERIFY_VALUE_NONEMPTY_TOLERANCE": ("T112e-D1-012", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "SOURCE_URL_STRUCTURED_ENABLED": ("T112e-D1-013", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "SYSTEM_PORT": ("T112e-D1-014", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "WEB_TIME_EXTRACTION_CONFIG": ("T112e-D1-015", "已人工核验为被动/历史兼容字段，间接引用静态不可见（T-112e 显式豁免）"),
    "HEBBIAN": ("T112e-D1-016", "仅经 getter 返回但零调用方，确认为被动字段（T-112e 显式豁免）"),
    "KNOWLEDGE_TREE": ("T112e-D1-017", "仅经 getter 返回但零调用方，确认为被动字段（T-112e 显式豁免）"),
    "PURGE": ("T112e-D1-018", "仅经 getter 返回但零调用方，确认为被动字段（T-112e 显式豁免）"),
    "COLD_BATCH_RECALL_ENABLED": ("T112e-D1-019", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_COMPACT_ROWCOUNT_VERIFY": ("T112e-D1-020", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_COMPACT_SKIP_ERROR_ENABLED": ("T112e-D1-021", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_DISABLE_STARTUP_COMPACT": ("T112e-D1-022", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_SIDECAR_INDEX_ENABLED": ("T112e-D1-023", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_STARTUP_COMPACT_WAIT_SECONDS": ("T112e-D1-024", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_STORAGE_SCHEMA_M81_COMPLETE": ("T112e-D1-025", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_WRITE_BATCH_SIZE": ("T112e-D1-026", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "COLD_WRITE_FLUSH_INTERVAL": ("T112e-D1-027", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "DISTRIBUTED_CONSISTENCY": ("T112e-D1-028", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "DISTRIBUTED_MODE": ("T112e-D1-029", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "DISTRIBUTED_NODE_ID": ("T112e-D1-030", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "DISTRIBUTED_REPLICA_COUNT": ("T112e-D1-031", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "DISTRIBUTED_SHARD_COUNT": ("T112e-D1-032", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_CODE_REVIEW_INTEGRATION": ("T112e-D1-033", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_DIGESTION_QUALITY_CLOSED_LOOP": ("T112e-D1-034", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_EVOLUTION_HEALTH_INTEGRATION": ("T112e-D1-035", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_LLM_TRACE_ERROR_CAPTURE": ("T112e-D1-036", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_LLM_TRACE_PROMPT_VERSION": ("T112e-D1-037", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_LOG_ANALYZER_INTEGRATION": ("T112e-D1-038", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_SEARCH_QUALITY_CLOSED_LOOP": ("T112e-D1-039", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "ENABLE_STRICT_WRITE_GUARD": ("T112e-D1-040", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "EVOLUTION_PROMPT_VERSION": ("T112e-D1-041", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "EXPERIENCE_RETRIEVER_FILTER_POLLUTED": ("T112e-D1-042", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "EXPERIENCE_RETRIEVER_SCAN_LIMIT": ("T112e-D1-043", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "EXPERIENCE_RETRIEVER_TOP_K": ("T112e-D1-044", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "FAISS_BATCH_SIZE": ("T112e-D1-045", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "FAISS_INDEX_PATH": ("T112e-D1-046", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "FAISS_INDEX_TYPE": ("T112e-D1-047", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "FAISS_USE_GPU": ("T112e-D1-048", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "INFLUXDB_BATCH_SIZE": ("T112e-D1-049", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "INFLUXDB_FLUSH_INTERVAL": ("T112e-D1-050", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "KAL_CACHE_SIZE": ("T112e-D1-051", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "KAL_STORAGE_BACKEND": ("T112e-D1-052", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "LLM_TRACE_DEFAULT_PROMPT_VERSION": ("T112e-D1-053", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "LLM_TRACE_ERROR_MAX_LEN": ("T112e-D1-054", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "MODEL_SELF_UPDATER_KEEP_VERSIONS": ("T112e-D1-055", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "NEO4J_BATCH_SIZE": ("T112e-D1-056", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "NEO4J_CONNECTION_POOL_SIZE": ("T112e-D1-057", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "PATCH_AUTO_APPROVE_PRUNE_PENDING": ("T112e-D1-058", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "QICA_CHANNEL_WEIGHTS_V2": ("T112e-D1-059", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "QICA_USE_TUNED_WEIGHTS": ("T112e-D1-060", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "QUEUE_DEPTH_THRESHOLD_CRITICAL": ("T112e-D1-061", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "QUEUE_DEPTH_THRESHOLD_HIGH": ("T112e-D1-062", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "QUEUE_DEPTH_THRESHOLD_MEDIUM": ("T112e-D1-063", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "REPORT_BUS_DATA_QUALITY_WARN": ("T112e-D1-064", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "REPORT_BUS_HEALTH_SCORE_WARN": ("T112e-D1-065", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "REPORT_BUS_PATCH_FIX_RATE_WARN": ("T112e-D1-066", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "REPORT_BUS_POLLUTION_WARN": ("T112e-D1-067", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "SEMANTIC_CACHE_CAPACITY": ("T112e-D1-068", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "SEMANTIC_CACHE_DIR": ("T112e-D1-069", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "SEMANTIC_CACHE_MAX_TEXT": ("T112e-D1-070", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "SEMANTIC_CACHE_TTL_DAYS": ("T112e-D1-071", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "VECTOR_DIMENSION": ("T112e-D1-072", "仓内字符串/常量提及的间接引用，静态不可见（T-112e 显式豁免）"),
    "BACKGROUND_REPLY_TRUNCATE_CHARS": ("T112e-D1-073", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "CORRUPTED_QUARANTINE_DAYS": ("T112e-D1-074", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "DIALOG_HISTORY_PERSIST": ("T112e-D1-075", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "FAISS_SEARCH_CACHE_SIZE": ("T112e-D1-076", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "FAISS_TRAIN_THRESHOLD": ("T112e-D1-077", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "LIVER_CONFIG": ("T112e-D1-078", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "STOMACH_JSON_FAILURE_WARNING_THRESHOLD": ("T112e-D1-079", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "STOMACH_JSON_FALLBACK_EXTRACT": ("T112e-D1-080", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "STOMACH_JSON_RECORD_FAILURES": ("T112e-D1-081", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "STOMACH_USE_JSON5": ("T112e-D1-082", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
    "THINKING_DISCIPLINE_CONFIG": ("T112e-D1-083", "仓内文本提及的间接引用（如 config.get/别名常量），静态不可见（T-112e 显式豁免）"),
}

# ============================================================ 文件清单
def list_py_files():
    """审计口径 = git 跟踪的 .py。优先 git CLI → 目录遍历（git 不可用时）。"""
    rels, origin = None, None
    try:
        out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        if out.returncode == 0 and out.stdout.strip():
            rels = [p for p in out.stdout.split(chr(0)) if p]
            origin = "git-ls-files"
    except Exception:
        rels = None
    if rels is None:
        files = []
        for root, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_ANY and not BAK_RE.search(d)]
            for n in names:
                if n.endswith(".py") and not BAK_RE.search(n):
                    files.append(os.path.join(root, n))
        return sorted(files), "walk(no-git)"
    files = []
    for p in rels:
        parts = p.split("/")
        if BAK_RE.search(p):
            continue
        if any(x in EXCLUDE_ANY for x in parts):
            continue
        if parts[0] in EXCLUDE_TOP:
            continue
        fp = os.path.join(ROOT, *parts)
        if os.path.isfile(fp):
            files.append(fp)
    return sorted(files), origin


# ============================================================ 索引
class Index:
    def __init__(self, srcs=None):
        self.srcs = srcs or {}
        self.files = []
        self.origin = "unknown"
        self.attr_store = defaultdict(list)
        self.attr_load = defaultdict(list)
        self.attr_str = defaultdict(list)
        self.key_store = defaultdict(list)
        self.key_load = defaultdict(list)
        self.key_dictlit = defaultdict(list)
        self.key_update = defaultdict(list)
        self.str_lit = defaultdict(list)
        self.aliases = defaultdict(set)
        self.dyn_risk = Counter()
        self.ext_mentions = {}
        self.tokens = defaultdict(Counter)
        self.name_load = defaultdict(list)
        self.enums = {}
        self.enum_defs = {}
        self.funcs = []

    def rel(self, p):
        try:
            return os.path.relpath(p, ROOT).replace("\\", "/")
        except Exception:
            return str(p)

    def is_test(self, p):
        r = self.rel(p)
        return r.startswith("tests/") or "/tests/" in r or os.path.basename(r).startswith("test_")

    @staticmethod
    def const(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def qual(self, node):
        if isinstance(node, ast.Attribute):
            base = self.qual(node.value)
            return (base + "." + node.attr) if base else node.attr
        if isinstance(node, ast.Name):
            return node.id
        return None

    def src(self, p):
        if p not in self.srcs:
            try:
                with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
                    self.srcs[p] = fh.read()
            except Exception:
                self.srcs[p] = ""
        return self.srcs[p]

    def line_of(self, p, n):
        try:
            return self.src(p).splitlines()[n - 1].strip()
        except Exception as e:
            silent_exc(e, where="tools.ci.check_write_only_gates::line_of L241")
            return ""

    def build(self, files):
        self.files = list(files)
        for f in self.files:
            t = self._parse(f)
            if not t:
                continue
            self._tokenize(f)
            self._index_file(f, t)

    def _parse(self, f):
        try:
            return ast.parse(self.src(f), filename=f)
        except (SyntaxError, ValueError) as e:
            silent_exc(e, where="tools.ci.check_write_only_gates::_parse L256")
            return None

    def _tokenize(self, f):
        r = self.rel(f)
        c = self.tokens
        for i, line in enumerate(self.src(f).splitlines(), 1):
            for tok in TOKEN_RE.findall(line):
                c[tok][r] += 1

    def _event_literal(self, node):
        c = self.const(node)
        if c is not None:
            return c
        q = self.qual(node)
        if q:
            return self.enums.get(q)
        return None

    def _index_file(self, f, tree):
        r = self.rel(f)
        w_store_slice = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                if isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                    tgts = [n.target]
                else:
                    tgts = n.targets
                for tg in tgts:
                    if isinstance(tg, ast.Subscript) and isinstance(tg.slice, ast.Constant):
                        w_store_slice.add(id(tg.slice))
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute):
                (self.attr_store if isinstance(n.ctx, (ast.Store, ast.Del))
                 else self.attr_load)[n.attr].append((f, n.lineno))
            elif isinstance(n, ast.Subscript):
                k = self.const(n.slice)
                if k is not None:
                    (self.key_store if isinstance(n.ctx, (ast.Store, ast.Del))
                     else self.key_load)[k].append((f, n.lineno))
            elif isinstance(n, ast.Call):
                fnq = self.qual(n.func) or (getattr(n.func, "attr", "") or "")
                fa = fnq.rsplit(".", 1)[-1] if fnq else ""
                if fa in ("get", "pop", "setdefault", "remove", "fetch", "getlist") and n.args:
                    k = self.const(n.args[0])
                    if k is not None:
                        self.key_load[k].append((f, n.lineno))
                if fa == "update" and n.args and isinstance(n.args[0], ast.Dict):
                    for kk in n.args[0].keys:
                        c = self.const(kk)
                        if c is not None:
                            self.key_update[c].append((f, n.lineno))
                if fa in ("getattr", "hasattr", "delattr", "setattr") and len(n.args) >= 2:
                    k = self.const(n.args[1])
                    if k is not None:
                        self.attr_str[k].append((f, n.lineno))
                    else:
                        self.dyn_risk[r] += 1
                if fa == "vars":
                    self.dyn_risk[r] += 1
            elif isinstance(n, ast.Dict):
                for kk, vv in zip(n.keys, n.values):
                    c = self.const(kk)
                    if c is None:
                        continue
                    if id(kk) in w_store_slice:
                        continue
                    self.key_dictlit[c].append((f, n.lineno))
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                self.name_load[n.id].append((f, n.lineno))
            elif isinstance(n, ast.Name) and n.id == "__dict__":
                self.dyn_risk[r] += 1
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                tg = n.target if isinstance(n, ast.AnnAssign) else (
                    n.targets[0] if len(n.targets) == 1 else None)
                v = self.const(n.value)
                if isinstance(tg, ast.Name) and v is not None and len(v) <= 60:
                    self.aliases[tg.id].add(v)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in w_store_slice:
                v = n.value
                if len(v) <= 80:
                    self.str_lit[v].append((f, n.lineno))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs.append({"file": r, "abs": f, "name": n.name,
                                   "line": n.lineno, "node": n})

    def alias_reads(self, literal):
        hits = []
        for var, lits in self.aliases.items():
            if literal in lits:
                loads = self.name_load.get(var, [])
                if loads:
                    hits.append(f"{var}@{self.rel(loads[0][0])}:{loads[0][1]}x{len(loads)}")
        return hits[:6]


# ============================================================ D1 死配置键
def d1_dead_config(ix: Index) -> dict:
    src = ix.src(CONFIG_PY)
    keys, seen = [], set()
    for m in re.finditer(r"^(?P<n>[A-Z][A-Z0-9_]{2,})\s*(?::[^=\n]+)?=", src, re.M):
        n = m.group("n")
        if n in seen:
            continue
        seen.add(n)
        keys.append((src[:m.start()].count("\n") + 1, n))
    getter_map = defaultdict(set)
    try:
        ct = ast.parse(src)
    except SyntaxError:
        ct = None
    if ct is not None:
        for fnode in [x for x in ast.walk(ct)
                      if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for rr in [y for y in ast.walk(fnode) if isinstance(y, ast.Return) and y.value]:
                for sub in ast.walk(rr.value):
                    if isinstance(sub, ast.Name) and sub.id in seen:
                        getter_map[sub.id].add(fnode.name)
    items = []
    for ln, name in keys:
        if name in D1_EXEMPT_KEYS:
            continue  # ★T-112e#3：已裁决豁免，不报红
        def split(occs):
            prod = [o for o in occs if ix.rel(o[0]) != CONFIG_REL and not ix.is_test(o[0])]
            test = [o for o in occs if ix.rel(o[0]) != CONFIG_REL and ix.is_test(o[0])]
            return prod, test
        p = t = 0
        read_sites = []
        for bucket in ("attr_load", "attr_str", "name_load", "key_load"):
            pr, te = split(getattr(ix, bucket).get(name, []))
            p += len(pr)
            t += len(te)
            read_sites += [f"{ix.rel(f)}:{l}" for f, l in pr[:3]]
        tok = ix.tokens.get(name, Counter())
        tok_noncfg = {k: v for k, v in tok.items() if k != CONFIG_REL}
        strhits = [(f, l) for f, l in ix.str_lit.get(name, []) if ix.rel(f) != CONFIG_REL]
        alias = [a for a in ix.alias_reads(name)]
        n_tok = sum(tok_noncfg.values())
        getters = sorted(getter_map.get(name, set()))
        getter_ext = []
        for g in getters:
            for occ in (ix.attr_load.get(g, []) + ix.name_load.get(g, [])
                        + ix.attr_str.get(g, [])):
                if ix.rel(occ[0]) != CONFIG_REL:
                    getter_ext.append(f"{ix.rel(occ[0])}:{occ[1]}")
        if p:
            status = "read"
        elif t:
            status = "test-only"
        elif getter_ext:
            status = "read-via-getter"
        elif strhits or alias:
            status = "string-only"
        elif n_tok:
            status = "text-only"
        elif getters:
            status = "dead(getter零调用)"
        else:
            status = "dead"
        items.append({"key": name, "line": ln, "reads_prod": p, "reads_test": t,
                      "status": status, "sample_reads": read_sites, "alias_reads": alias,
                      "config_getters": getters, "getter_ext_sites": getter_ext[:4]})
    return {"total_keys": len(keys),
            "counts": dict(Counter(i["status"] for i in items)),
            "items": items}


# ============================================================ D2 只写不读
def _ext_index(ix: Index) -> dict:
    out = {}
    roots = [os.path.join(ROOT, "nucleus"), os.path.join(ROOT, "organs"),
             os.path.join(ROOT, "base"), os.path.join(ROOT, "utils")]
    texts = []
    for rt in roots:
        for root, dirs, names in os.walk(rt):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_ANY and not BAK_RE.search(d)]
            for n in names:
                if n.endswith((".pyx", ".pxd", ".pyi")):
                    try:
                        texts.append(io.open(os.path.join(root, n), encoding="utf-8",
                                             errors="replace").read())
                    except Exception as e:
                        silent_exc(e, where="tools.ci.check_write_only_gates::_ext_index L438")
    blob = "\n".join(texts)
    if blob:
        for m in re.finditer(r"_[A-Za-z][A-Za-z0-9_]{2,}", blob):
            out.setdefault(m.group(0), ["compiled-ext"])
    return out


def _confidence(rec, alias):
    if alias or rec.get("ext_mentions"):
        return "需人工(动态/别名)"
    if rec.get("string_mentions") or rec.get("token_files", 0) > len(rec.get("write_files", [])):
        return "疑似(仓内有文本提及)"
    if rec.get("dyn_risk_writesite", 0) >= 3:
        return "疑似(写点文件动态访问密集)"
    return "已实锤"


def d2_writeonly(ix: Index) -> dict:
    ext_names = _ext_index(ix)
    attrs = []
    for name, stores in ix.attr_store.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        if not name.startswith("_"):
            continue
        if ix.attr_load.get(name) or ix.attr_str.get(name):
            continue
        prod = [s for s in stores if not ix.is_test(s[0])]
        if not prod:
            continue
        wfiles = sorted({ix.rel(f) for f, _ in stores})
        alias = ix.alias_reads(name)
        rec = {"name": name, "writes": len(stores), "writes_prod": len(prod),
               "write_files": wfiles[:8],
               "sites": [f"{ix.rel(f)}:{l}" for f, l in prod[:8]],
               "string_mentions": len([o for o in ix.str_lit.get(name, [])]),
               "token_files": len(ix.tokens.get(name, {})),
               "alias_reads": alias, "ext_mentions": ext_names.get(name, []),
               "dyn_risk_writesite": sum(ix.dyn_risk.get(w, 0) for w in wfiles),
               "prod_reads": 0, "test_reads": 0}
        rec["confidence"] = _confidence(rec, alias)
        attrs.append(rec)
    keys = []
    for name, stores in ix.key_store.items():
        if len(name) < 3:
            continue
        if ix.key_load.get(name) or ix.key_dictlit.get(name) or ix.key_update.get(name):
            continue
        prod = [s for s in stores if not ix.is_test(s[0])]
        if not prod:
            continue
        wfiles = sorted({ix.rel(f) for f, _ in stores})
        alias = ix.alias_reads(name)
        rec = {"key": name, "writes": len(stores), "writes_prod": len(prod),
               "write_files": wfiles[:8],
               "sites": [f"{ix.rel(f)}:{l}" for f, l in prod[:8]],
               "string_mentions": len(ix.str_lit.get(name, [])),
               "token_files": len(ix.tokens.get(name, {})),
               "alias_reads": alias, "ext_mentions": ext_names.get(name, []),
               "dyn_risk_writesite": sum(ix.dyn_risk.get(w, 0) for w in wfiles)}
        rec["confidence"] = _confidence(rec, alias)
        keys.append(rec)
    return {"attrs": sorted(attrs, key=lambda x: -x["writes_prod"]),
            "dict_keys": sorted(keys, key=lambda x: -x["writes_prod"]),
            "attrs_hard": [a for a in attrs if a["confidence"] == "已实锤"],
            "keys_hard": [k for k in keys if k["confidence"] == "已实锤"]}


# ============================================================ D3 消费者存在性
def _consumer_claims(fn_node) -> tuple:
    """判定消费者函数是否会「认领消费」。

    Returns:
        (True, reason)  —— 有认领路径（合法，不报）;
        (False, reason) —— 无认领路径（死消费者，报红）;
        (None, reason)  —— 无法静态判定（需人工，不报红）。
    """
    _has_claim_side = False
    _returns = []  # 每个 return 的判定: True(truthy)/False(falsy)/None(unknown)
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Attribute) and n.attr == "consumed_by":
            _has_claim_side = True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "mark_consumed":
            _has_claim_side = True
        if isinstance(n, ast.Return):
            if n.value is None:
                _returns.append(False)
            elif isinstance(n.value, ast.Constant):
                _v = n.value.value
                _returns.append(bool(_v) and _v not in (0, 0.0, "", [], {}, None))
            else:
                _returns.append(None)  # 变量/调用/表达式 -> 可能 truthy（保守不报）
    if _has_claim_side:
        return True, "side-effect claim (consumed_by/mark_consumed)"
    if any(r is True for r in _returns):
        return True, "returns truthy-constant path"
    if any(r is None for r in _returns):
        return None, "returns dynamic value (可能 truthy)"
    if _returns:
        return False, "only falsy/None returns, no claim side-effect"
    return False, "no return and no claim side-effect"


def d3_dead_consumer(ix: Index) -> dict:
    """D3 消费者存在性：订阅了报告但消费者函数从不认领（死消费者）。

    判定：``bus.subscribe(type, consumer)`` 的 consumer 若在同文件函数定义中
    既无 ``consumed_by``/``mark_consumed`` 写点，又所有 return 均为明确 falsy → 死。
    """
    items = []
    for f in ix.files:
        # ★第111批 T-111a：仅扫描 ReportBus 包（消费者唯一登记处
        #   nucleus/reporting/consumers.py），排除事件总线 subscribe 与测试 fixture，
        #   避免把事件总线订阅误判为「死消费者」。
        if not ix.rel(f).startswith("nucleus/reporting/"):
            continue
        try:
            tree = ast.parse(ix.src(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "subscribe"):
                continue
            consumer = None
            if len(node.args) >= 2:
                consumer = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == "consumer":
                        consumer = kw.value
            if consumer is None:
                continue
            cname = None
            if isinstance(consumer, ast.Name):
                cname = consumer.id
            elif isinstance(consumer, ast.Attribute):
                cname = consumer.attr
            if not cname:
                items.append({"file": ix.rel(f), "line": node.lineno,
                              "consumer": "<unresolved>", "status": "needs-human",
                              "reason": "consumer 非可解析函数名(lambda/partial等)"})
                continue
            fn_node = None
            for func in ix.funcs:
                if func["abs"] == f and func["name"] == cname:
                    fn_node = func["node"]
                    break
            if fn_node is None:
                items.append({"file": ix.rel(f), "line": node.lineno,
                              "consumer": cname, "status": "needs-human",
                              "reason": "找不到同文件消费者函数定义(可能跨模块)"})
                continue
            claimed, reason = _consumer_claims(fn_node)
            if claimed is False:
                items.append({"file": ix.rel(f), "line": node.lineno,
                              "consumer": cname, "status": "dead", "reason": reason})
            elif claimed is None:
                # 无法静态判定（消费者返回动态值，可能 truthy）→ 仅作告警，不阻塞
                items.append({"file": ix.rel(f), "line": node.lineno,
                              "consumer": cname, "status": "needs-human",
                              "reason": reason})
    return {"items": items,
            "dead": [x for x in items if x["status"] == "dead"],
            "needs_human": [x for x in items if x["status"] == "needs-human"]}


# ============================================================ git / diff 工具
def git_show(ref, relpath):
    r = subprocess.run(["git", "show", f"{ref}:{relpath}"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def git_read_worktree(relpath):
    p = os.path.join(ROOT, relpath)
    if not os.path.exists(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace").read()


def changed_files(base):
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    # ★T-112e#1：git 失败不再静默 0 变更（假绿）→ 显式报错，门禁中止 rc=2
    if r.returncode != 0:
        raise GitBaselineUnavailable(
            f"增量基线不可用（git diff rc={r.returncode}）：无法判定新增缺陷，门禁中止(rc=2)")
    lst = [x for x in r.stdout.splitlines() if x.strip()]
    out = []
    for x in lst:
        if x.startswith('"') and x.endswith('"'):
            try:
                x = x[1:-1].encode().decode("unicode_escape").encode("latin1").decode("utf-8")
            except Exception as e:
                silent_exc(e, where="tools.ci.check_write_only_gates::changed_files L635")
        out.append(x)
    return out


def added_lines(old_text, new_text):
    """返回 new_text 中相对 old_text 新增的行号集合（1-based）。"""
    ol = old_text.splitlines()
    nl = new_text.splitlines()
    added = set()
    sm = difflib.SequenceMatcher(None, ol, nl, autojunk=False)
    for _tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if _tag in ("insert", "replace"):
            for j in range(j1, j2):
                added.add(j + 1)
    return added


def config_top_keys(text):
    """config.py 顶层 UPPER 键集合（行首，形如 KEY = ... / KEY: type = ...）。"""
    return {m.group("n") for m in re.finditer(
        r"^(?P<n>[A-Z][A-Z0-9_]{2,})\s*(?::[^=\n]+)?=", text, re.M)}


# ============================================================ 增量门禁核心
def new_write_only_gates(base, target, verbose=False, full=False):
    """对比 base 与 target，返回本次 diff 新增的只写不读缺陷清单。

    返回结构：{"d1": [...], "d2_attrs": [...], "d2_keys": [...], "files": int}
    """
    # 目标树全量索引（用于跨文件读取方计数）—— 始终构建（full 模式也依赖）
    tgt_files, origin = list_py_files()
    if full:
        files = list(tgt_files)
    else:
        try:
            files = changed_files(base)
        except GitBaselineUnavailable:
            raise
    tix = Index()
    tix.build(tgt_files)

    d1 = d1_dead_config(tix)
    d2 = d2_writeonly(tix)
    d3_all = d3_dead_consumer(tix)["dead"]

    findings = {"d1": [], "d2_attrs": [], "d2_keys": [], "d3": [], "files": len(files)}

    for rel in files:
        if full:
            old = None
            new = git_read_worktree(rel) if target == "worktree" else git_show(target, rel)
        else:
            old = git_show(base, rel)
            new = git_read_worktree(rel) if target == "worktree" else git_show(target, rel)
        if new is None:
            continue
        if old is None:
            old = ""
        added = None if full else added_lines(old, new)

        # ---- D1：config.py 新增死键（写点落在新增行）----
        if rel == CONFIG_REL:
            # ★T-112e#2：全量模式下 old 即当前文件，所有键都算「存量」，若据此跳过则全盘不报；
            #   故全量模式置空 old_keys，真正盘点全部存量写点（仅豁免表 D1_EXEMPT_KEYS 才跳过）。
            old_keys = set() if full else (config_top_keys(old) if isinstance(old, str) else set())
            for it in d1["items"]:
                if it["key"] in old_keys:
                    continue  # 存量键，不清理
                if added is not None and it["line"] not in added:
                    continue  # 写点非新增行（误匹配，跳过）
                if it["status"] in ("read", "test-only", "read-via-getter"):
                    continue  # 已有读取方，合法
                findings["d1"].append(it)

        # ---- D2：属性/字段写点落在新增行 ----
        for rec in d2["attrs"]:
            for site in rec["sites"]:
                sfile, _, sline = site.partition(":")
                if sfile == rel and (added is None or int(sline) in added):
                    findings["d2_attrs"].append(rec)
                    break
        for rec in d2["dict_keys"]:
            for site in rec["sites"]:
                sfile, _, sline = site.partition(":")
                if sfile == rel and (added is None or int(sline) in added):
                    findings["d2_keys"].append(rec)
                    break

        # ---- D3：消费者存在性（订阅但从不认领）----
        for it in d3_all:
            if it["file"] == rel and (added is None or it["line"] in added):
                findings["d3"].append(it)

    # 去重（同一 attr/key 可能因多处写点重复计入）
    def _uniq(lst, key):
        seen = set()
        out = []
        for r in lst:
            k = key(r)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out
    findings["d2_attrs"] = _uniq(findings["d2_attrs"], lambda r: r["name"])
    findings["d2_keys"] = _uniq(findings["d2_keys"], lambda r: r["key"])
    return findings


# ============================================================ 正控 / 自测
FIXTURE_WRITE_ONLY = '''
class Foo:
    def __init__(self):
        self._dead_flag = True
        self._dead_map = {}
        self._dead_map["never_read_key"] = 1
        self._live_counter = 0

    def tick(self):
        self._live_counter += 1
        return self._live_counter
'''
FIXTURE_READ_OK = '''
class Foo:
    def __init__(self):
        self._dead_flag = True
        self._dead_map = {}
        self._dead_map["never_read_key"] = 1
        self._live_counter = 0

    def tick(self):
        if self._dead_flag:
            return self._dead_map.get("never_read_key", 0) + self._live_counter
        return self._live_counter
'''


def _fixture_index(src):
    ix = Index(srcs={"/fixture/F.py": src})
    ix.files = ["/fixture/F.py"]
    t = ast.parse(src)
    ix._tokenize("/fixture/F.py")
    ix._index_file("/fixture/F.py", t)
    return ix


FIXTURE_CONSUMER_DEAD = '''
bus.subscribe("health", dead_consumer)
def dead_consumer(env):
    # 订阅但永远不认领（只写不读型死消费者）
    return False
'''
FIXTURE_CONSUMER_OK = '''
bus.subscribe("health", ok_consumer)
def ok_consumer(env):
    return True
'''


def selftest() -> int:
    print("== check_write_only_gates selftest：尺子必须响（已知只写不读）且必须不响（已知有读取） ==")
    a = _fixture_index(FIXTURE_WRITE_ONLY)
    b = _fixture_index(FIXTURE_READ_OK)
    wa = {x["name"] for x in d2_writeonly(a)["attrs"]}
    wb = {x["name"] for x in d2_writeonly(b)["attrs"]}
    ka = {x["key"] for x in d2_writeonly(a)["dict_keys"]}
    kb = {x["key"] for x in d2_writeonly(b)["dict_keys"]}
    checks = [
        ("fixtureA attr _dead_flag -> FLAGGED", "_dead_flag" in wa),
        ("fixtureA dict key never_read_key -> FLAGGED", "never_read_key" in ka),
        ("fixtureB attr _dead_flag -> NOT flagged (ruler silent on read)", "_dead_flag" not in wb),
        ("fixtureB dict key never_read_key -> NOT flagged", "never_read_key" not in kb),
        ("live attr _live_counter -> never flagged",
         "_live_counter" not in wa and "_live_counter" not in wb),
    ]
    # 增量门禁正控：在 config 中新增一个无读取点的键，应被 new_write_only_gates 捕获
    old_cfg = "EXISTING_KEY = 1\n"
    new_cfg = "EXISTING_KEY = 1\nNEW_DEAD_KEY = 2\n"
    # 用一个只在 _fixture 里出现的独立索引做 D1（简化：直接调用 d1 在合成 config 上）
    sim = Index(srcs={CONFIG_PY: new_cfg})
    sim.files = [CONFIG_PY]
    sim._index_file(CONFIG_PY, ast.parse(new_cfg))
    sim_d1 = d1_dead_config(sim)
    new_keys = config_top_keys(new_cfg) - config_top_keys(old_cfg)
    added = added_lines(old_cfg, new_cfg)
    caught = [it for it in sim_d1["items"]
              if it["key"] in new_keys and it["line"] in added
              and it["status"] not in ("read", "test-only", "read-via-getter")]
    checks.append(("incremental: NEW_DEAD_KEY added & write-only -> FLAGGED", bool(caught)))
    # 反向：存量键 EXISTING_KEY 即便无读也不应被当作"新增"
    checks.append(("incremental: EXISTING_KEY not flagged as new",
                   not any(it["key"] == "EXISTING_KEY" for it in caught)))

    # ---- D3 消费者存在性正控（fixture 置于 nucleus/reporting/ 以命中作用域）----
    _d3p = "nucleus/reporting/_d3_fixture.py"
    fx_dead = Index(srcs={_d3p: FIXTURE_CONSUMER_DEAD})
    fx_dead.files = [_d3p]
    _t = ast.parse(FIXTURE_CONSUMER_DEAD)
    fx_dead._tokenize(_d3p); fx_dead._index_file(_d3p, _t)
    fx_ok = Index(srcs={_d3p: FIXTURE_CONSUMER_OK})
    fx_ok.files = [_d3p]
    _t = ast.parse(FIXTURE_CONSUMER_OK)
    fx_ok._tokenize(_d3p); fx_ok._index_file(_d3p, _t)
    d3_dead_a = {x["consumer"] for x in d3_dead_consumer(fx_dead)["dead"]}
    d3_dead_b = {x["consumer"] for x in d3_dead_consumer(fx_ok)["dead"]}
    checks.append(("D3: dead_consumer (return False) -> FLAGGED",
                   "dead_consumer" in d3_dead_a))
    checks.append(("D3: ok_consumer (return True) -> NOT flagged",
                   "ok_consumer" not in d3_dead_b))
    # D3 增量正控：新增一个 return False 的死消费者订阅，应被 added 行捕获
    old_sub = "register()\n"
    new_sub = ("register()\nbus.subscribe('health', never_claims)\n"
               "def never_claims(env):\n    return False\n")
    six = Index(srcs={_d3p: new_sub})
    six.files = [_d3p]
    _t = ast.parse(new_sub)
    six._tokenize(_d3p); six._index_file(_d3p, _t)
    sim_d3 = d3_dead_consumer(six)["dead"]
    added_sub = added_lines(old_sub, new_sub)
    caught_d3 = [it for it in sim_d3 if it["line"] in added_sub]
    checks.append(("incremental D3: new dead subscribe -> FLAGGED", bool(caught_d3)))

    ok = True
    for name, got in checks:
        print(f"  [{'PASS' if got else 'FAIL'}] {name}")
        ok &= got
    print(f"== selftest {'ALL PASS' if ok else 'FAILED'} ==")
    return 0 if ok else 1


# ============================================================ main（CI 门禁）
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--target", default="worktree", choices=["worktree", "HEAD"])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="全量模式：忽略增量基线，盘点全部存量写点（git 不可用时兜底）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    try:
        findings = new_write_only_gates(args.base, args.target, args.verbose, full=args.full)
    except GitBaselineUnavailable as e:
        print("=" * 74)
        print(f"  [BASELINE UNAVAILABLE] {e}")
        print("  门禁中止：增量基线不可用，无法判定是否新增缺陷（禁止假绿）。")
        print("  如需全量盘点存量，请加 --full 兜底（仍会逐条裁决，不静默通过）。")
        print("=" * 74)
        return 2
    print("=" * 74)
    print("CI 门禁（第106批 T-106b）：新增「只写不读」缺陷必须为 0")
    print(f"  base={args.base}  target={args.target}  变更 .py 文件数={findings['files']}")
    print("=" * 74)

    total = 0
    if findings["d1"]:
        print(f"  [FAIL] config.py 新增死配置键（无读取点）: {len(findings['d1'])} 个")
        for it in findings["d1"]:
            total += 1
            print(f"    - {it['key']}  config.py:{it['line']}  status={it['status']}  "
                  f"reads={it['reads_prod']}  sample={it['sample_reads'][:2]}")
    if findings["d2_attrs"]:
        print(f"  [FAIL] 新增只写不读属性: {len(findings['d2_attrs'])} 个")
        for rec in findings["d2_attrs"]:
            total += 1 if rec["confidence"] == "已实锤" else 0
            mark = "RED" if rec["confidence"] == "已实锤" else "WARN"
            print(f"    - [{mark}] {rec['name']}  w_prod={rec['writes_prod']}  "
                  f"conf={rec['confidence']}  {rec['sites'][0]}")
    if findings["d2_keys"]:
        print(f"  [FAIL] 新增只写不读字段(字典键): {len(findings['d2_keys'])} 个")
        for rec in findings["d2_keys"]:
            total += 1 if rec["confidence"] == "已实锤" else 0
            mark = "RED" if rec["confidence"] == "已实锤" else "WARN"
            print(f"    - [{mark}] {rec['key']}  w_prod={rec['writes_prod']}  "
                  f"conf={rec['confidence']}  {rec['sites'][0]}")

    if findings["d3"]:
        print(f"  [FAIL] 新增「只注册不消费」死消费者(订阅但消费者从不认领): "
              f"{len(findings['d3'])} 个")
        for it in findings["d3"]:
            total += 1  # D3 dead 即实锤
            print(f"    - [RED] {it['consumer']}  {it['file']}:{it['line']}  "
                  f"reason={it['reason']}")

    print("-" * 74)
    hard = (len(findings["d1"])
            + sum(1 for r in findings["d2_attrs"] if r["confidence"] == "已实锤")
            + sum(1 for r in findings["d2_keys"] if r["confidence"] == "已实锤")
            + len(findings["d3"]))
    if hard == 0:
        print("  结论: PASS —— 本次变更未新增「只写不读」缺陷（存量死键不清理，仅防新增）")
        return 0
    print(f"  结论: FAIL —— 新增「已实锤」只写不读缺陷 {hard} 处，涉及 "
          f"{findings['files']} 个变更文件")
    print("  整改要求: 为新增配置键/标记/字段补一个读取点（或在同 PR 内移除无用的写入）；")
    print("              属设计意图的被动字段，请在代码评审中说明后由维护者加白名单豁免。")
    return 1


if __name__ == "__main__":
    sys.setrecursionlimit(20000)
    raise SystemExit(main())
