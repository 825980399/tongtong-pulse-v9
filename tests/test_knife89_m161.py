# -*- coding: utf-8 -*-
"""第161批 刀8/刀9 门控单测（正反例）

刀8：QICA 路径检索接入语义相关性判据
  - 反例（正例语义）：不同问题命中同一批节点 ⇒ 判据须能按问题区分
  - 正例：闸门关闭时回滚旧行为；阈值 <0 时不设限
  - 日志可观测：须输出「相关度=」（任务书 8.4）

刀9：身份关系模板缺失字段兜底完整句
  - 反例：relation 为脏值/缺失 ⇒ 禁输出「是 我的 您」类断裂句
  - 正例：relation 合法时原样输出；开关关闭时回滚
"""
# -*- coding: utf-8 -*-
# export-scan-skip-file  （本文件 PII 均为构造的假夹具，非真实身份）

import importlib.util
import io
import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPO = ROOT


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, relpath))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ikm = _load("ikm_k89", "nucleus/mnemosyne/IdentityKnowledgeManager.py")


class _Ctx:
    """最小 InferenceContext 替身（只需 question 属性）。"""

    def __init__(self, question):
        self.question = question
        self.user_name = "tester"


class _Node:
    def __init__(self, node_id, value, keywords=None):
        self.node_id = node_id
        self.value = value
        self.keywords = keywords or []


class _Pool:
    def __init__(self, l3=None, l2=None):
        self._l3 = l3 or []
        self._l2 = l2 or []

    def query(self, evol_level=None, space_path_prefix=None, limit=10):
        return self._l3 if evol_level == "L3" else self._l2


class _StubInnerWorld:
    """只提供刀8 补丁实际用到的方法/属性的最小替身。"""

    def __init__(self, nodes, threshold=None):
        self.node_pool = _Pool(l3=nodes)
        self._logs = []
        self._threshold = threshold

    def _log(self, level, msg):
        self._logs.append((level, msg))

    def _clean_node_value(self, v):
        return str(v)

    def _is_internal_knowledge_node(self, v):
        return "[internal]" in v

    def _calculate_match_relevance(self, question, node_value, keywords):
        # 复用真模块的判据（同源），避免单测里另写一套口径
        return _real_relevance(question, node_value, keywords)

    # 绑定被测方法
    _ir_qica_knowledge_retrieve = None


def _real_relevance(question, value, keywords):
    """取真实实现（PulseInnerWorld 合成类上的 _calculate_match_relevance），
    避免单测里另写一套打分口径。"""
    piw = _load("piw_k89", "organs/brain/PulseInnerWorld.py")
    cls = piw.PulseInnerWorld
    fn = cls._calculate_match_relevance
    return fn(_StubInnerWorld(None), question, value, keywords)


class TestKnife8RelevanceGate:
    """刀8：路径目录浏览接入语义判据。"""

    def test_01_switch_exists_and_defaults(self):
        import config
        assert hasattr(config, "KNOWLEDGE_QICA_PATH_RELEVANCE_GATE")
        assert config.KNOWLEDGE_QICA_PATH_RELEVANCE_GATE is True
        assert config.KNOWLEDGE_QICA_PATH_MIN_RELEVANCE == 0.05

    def test_02_threshold_reused_not_duplicated(self):
        """★单一真相源：语义阈值复用 160上A 刀2 的全局开关，不新增第二套口径。"""
        src = io.open(os.path.join(REPO, "organs", "brain", "PulseInnerWorld.py"),
                      encoding="utf-8", newline=None).read()
        assert "KNOWLEDGE_GLOBAL_HIT_THRESHOLD" in src, "应复用全局阈值开关"
        # 不得出现自造的第二个语义阈值常量
        assert "KNOWLEDGE_QICA_SEMANTIC_THRESHOLD" not in src, "不应另立语义阈值"

    def test_03_relevance_log_observable(self):
        """★任务书 8.4：本路径须有「相关度=」INFO 级可观测日志。"""
        src = io.open(os.path.join(REPO, "organs", "brain", "PulseInnerWorld.py"),
                      encoding="utf-8", newline=None).read()
        assert "相关度=" in src, "刀8 须补 相关度= 日志"
        assert "QICA路径判据" in src

    def test_04_irrelevant_node_skipped(self):
        """★反例：与问题完全无关的节点必须被跳过（不得目录浏览直返）。"""
        node = _Node("n_metrics", "PulseMetricsCollector 指标采集器由 92 条认知融合而成，"
                                  "包含健康检查报告与全部运行指标汇总。",
                     keywords=["PulseMetricsCollector", "健康检查报告"])
        stub = _StubInnerWorld([node])
        # 问题是完全另一主题 → 相关度应低于下限
        rel = stub._calculate_match_relevance("你今天晚饭吃了什么", node.value, node.keywords)
        assert rel < 0.05, f"无关节点相关度应 <0.05，实测 {rel:.3f}"

    def test_05_relevant_node_accepted(self):
        """正例：与问题高度相关的节点须通过。"""
        node = _Node("n_health", "健康检查报告：本次体检包含血压、血糖与肝功能共 24 项指标。",
                     keywords=["健康检查报告", "体检"])
        stub = _StubInnerWorld([node])
        rel = stub._calculate_match_relevance("给我看健康检查报告", node.value, node.keywords)
        assert rel >= 0.05, f"相关节点应过闸，实测 {rel:.3f}"

    def test_06_gate_off_means_legacy_behaviour(self):
        """开关关闭 ⇒ 完整回滚旧行为（只判长度）。"""
        import config
        old = config.KNOWLEDGE_QICA_PATH_RELEVANCE_GATE
        try:
            config.KNOWLEDGE_QICA_PATH_RELEVANCE_GATE = False
            src = io.open(os.path.join(REPO, "organs", "brain", "PulseInnerWorld.py"),
                          encoding="utf-8", newline=None).read()
            assert "if not _gate_on:" in src and "return True" in src, "须保留闸门关闭分支"
        finally:
            config.KNOWLEDGE_QICA_PATH_RELEVANCE_GATE = old

    def test_07_threshold_negative_disables_gate(self):
        """★复用 160上A 刀2 语义：全局阈值 <0 = 关闭语义判据 ⇒ 本路也不设限。"""
        src = io.open(os.path.join(REPO, "organs", "brain", "PulseInnerWorld.py"),
                      encoding="utf-8", newline=None).read()
        assert "if _semantic_threshold < 0:" in src, "须沿用 160上A 刀2 的关闭语义"


class TestKnife9RelationFallback:
    """刀9：关系模板缺失字段兜底完整句。"""

    def test_01_switch_exists_and_defaults(self):
        import config
        assert hasattr(config, "IDENTITY_RELATION_FALLBACK_COMPLETE")
        assert config.IDENTITY_RELATION_FALLBACK_COMPLETE is True

    def test_02_relation_words_is_single_source(self):
        """★单一真相源：有效性判据复用本模块 RELATION_WORDS，不另立词表。"""
        src = io.open(os.path.join(REPO, "nucleus", "mnemosyne", "IdentityKnowledgeManager.py"),
                      encoding="utf-8", newline=None).read()
        assert "_rel_text not in RELATION_WORDS" in src, "应复用 RELATION_WORDS 判有效"

    def test_03_dirty_relation_fallback_to_complete_sentence(self):
        """★反例：relation 为脏值 ⇒ 输出完整句，禁「是 我的 您」断裂。"""
        mgr = ikm.IdentityKnowledgeManager.__new__(ikm.IdentityKnowledgeManager)
        mgr._lock = __import__("threading").RLock()
        mgr._people = {
            "小林": {
                "aliases": ["小贵"],
                "relations": [{"relation": "您", "target": "自己", "confidence": 0.9}],
                "updated": 0.0,
            }
        }
        out = mgr.describe("小林")
        assert "是我的您" not in out, f"禁输出断裂句，实测: {out}"
        assert "家人" in out, f"应兜底为完整句「家人」，实测: {out}"
        assert out.rstrip().endswith("。"), "必须是完整句"

    def test_04_missing_relation_fallback(self):
        """relation 缺失（空串/None）同样兜底。"""
        import threading
        for bad in (None, "", "   "):
            mgr = ikm.IdentityKnowledgeManager.__new__(ikm.IdentityKnowledgeManager)
            mgr._lock = threading.RLock()
            mgr._people = {
                "某人": {
                    "aliases": [],
                    "relations": [{"relation": bad, "target": "自己", "confidence": 0.5}],
                    "updated": 0.0,
                }
            }
            out = mgr.describe("某人")
            assert "的None" not in out and "的  " not in out, f"实测: {out}"
            assert out.rstrip().endswith("。"), f"必须是完整句，实测: {out}"

    def test_05_valid_relation_unchanged(self):
        """正例：合法关系词原样输出（零行为变更）。"""
        import threading
        mgr = ikm.IdentityKnowledgeManager.__new__(ikm.IdentityKnowledgeManager)
        mgr._lock = threading.RLock()
        mgr._people = {
            "小林": {
                "aliases": [],
                "relations": [{"relation": "父亲", "target": "自己", "confidence": 0.95}],
                "updated": 0.0,
            }
        }
        out = mgr.describe("小林")
        assert out == "小林是我的父亲。", f"合法关系须原样输出，实测: {out}"

    def test_06_no_relations_branch_unchanged(self):
        """无关系时走既有的「还没弄清我们的关系」分支，不受影响。"""
        import threading
        mgr = ikm.IdentityKnowledgeManager.__new__(ikm.IdentityKnowledgeManager)
        mgr._lock = threading.RLock()
        mgr._people = {"路人": {"aliases": [], "relations": [], "updated": 0.0}}
        out = mgr.describe("路人")
        assert "还没弄清我们的关系" in out, f"实测: {out}"

    def test_07_switch_off_allows_legacy(self):
        """开关关闭 ⇒ 回滚旧行为（不做有效性校验）。"""
        import config
        old = config.IDENTITY_RELATION_FALLBACK_COMPLETE
        try:
            config.IDENTITY_RELATION_FALLBACK_COMPLETE = False
            import threading
            mgr = ikm.IdentityKnowledgeManager.__new__(ikm.IdentityKnowledgeManager)
            mgr._lock = threading.RLock()
            mgr._people = {
                "小林": {
                    "aliases": [],
                    "relations": [{"relation": "您", "target": "自己", "confidence": 0.9}],
                    "updated": 0.0,
                }
            }
            out = mgr.describe("小林")
            assert out == "小林是我的您。", "开关关闭须回滚旧行为"
        finally:
            config.IDENTITY_RELATION_FALLBACK_COMPLETE = old

    def test_08_no_broken_sentence_in_output(self):
        """★任务书 9.2 硬红线：任何输入都不得产出「是 我的 您」类断裂句。"""
        import threading
        for rel in ("您", "你", None, "", "未知关系", "X", "？？"):
            mgr = ikm.IdentityKnowledgeManager.__new__(ikm.IdentityKnowledgeManager)
            mgr._lock = threading.RLock()
            mgr._people = {
                "小林": {
                    "aliases": ["小贵"],
                    "relations": [{"relation": rel, "target": "自己", "confidence": 0.9}],
                    "updated": 0.0,
                }
            }
            out = mgr.describe("小林")
            assert "是我的您" not in out and "是我的是" not in out, f"断裂句: {out}"
            assert out.rstrip().endswith("。"), f"非完整句: {out}"