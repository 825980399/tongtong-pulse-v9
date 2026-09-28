# -*- coding: utf-8 -*-
"""
第58批 T5 门控单测：统一 Event 常量 + 注册机制。

验证：
1. 20 个事件常量值与原硬编码字符串完全一致（替换不改变运行时行为）。
2. 注册机制 register_event / is_registered_event / all_event_values 工作正常。
3. 全库范围内，这 20 个事件名字符串不再以裸字符串形式出现于
   _emit / emit / publish / dispatch / trigger 等调用首参（替换已完成、无遗漏）。
"""
import os
import ast
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.const import (  # noqa: E402
    Event,
    register_event,
    is_registered_event,
    all_event_values,
)

# 常量名 -> 原硬编码事件名（值）
CONST_TO_VALUE = {
    "EXPRESS_URGE": "express.urge",
    "HORMONES_DETECT": "hormones.detect",
    "NARRATIVE_RECORD": "narrative.record",
    "CONTROLLER_OPEN_URL": "controller.open_url",
    "LEGS_LEARN_NOW": "legs.learn_now",
    "INNER_WORLD_CACHE_CLEAR": "inner_world.cache_clear",
    "SEMANTIC_CLASSIFY": "semantic.classify",
    "REFLECTION_INSIGHT": "reflection.insight",
    "HEART_BEAT": "heart.beat",
    "CORTEX_DIALOG_START": "cortex.dialog.start",
    "ORGAN_HANDBOOK_UPDATED": "organ_handbook_updated",
    "TOOL_CREATED": "tool.created",
    "MOTOR_EXECUTE": "motor.execute",
    "LUNGS_SELECT_MODEL": "lungs.select_model",
    "INTUITION_REINFORCE": "intuition.reinforce",
    "CONTROLLER_SEARCH_STAGE_COMPLETED": "controller.search_stage_completed",
    "GROWTH_NEED_DETECTED": "growth.need_detected",
    "LIFE_STATE_CHANGED": "life_state.changed",
    "DREAM_DEDUCTION": "dream.deduction",
    "STRESS_RECOVER": "stress.recover",
    "CARE_INITIATIVE": "care.initiative",
    "CHAT_MESSAGE": "chat.message",
    "CURIOSITY_TICK": "curiosity.tick",
    "DIGEST_KNOWLEDGE": "digest.knowledge",
    "ENVIRONMENT_MUTATED": "environment.mutated",
    "GLOBAL_LEARNER_DRIFT_DETECTED": "global_learner.drift_detected",
    "MOTIVATION_URGE": "motivation.urge",
    "CONSTRAINT_SELF_MODIFY_FORBIDDEN": "constraint.self_modify_forbidden",
    "CONSTRAINT_AGGRESSIVE_RESTRUCTURE_FORBIDDEN": "constraint.aggressive_restructure_forbidden",
    "UNKNOWN_EVENT": "unknown.event",
    "STRESS_ACTIVATE": "stress.activate",
}

EXCLUDE_DIRS = {
    ".git", ".bak_batch58", "backups", ".release-tmp", "batch_backups",
    "__pycache__", "node_modules", "data", "docs", "tmp", "code_backups",
    ".bak_batch54", ".bak_batch55", ".bak_batch56", ".bak_batch57",
    ".bak_batch53", ".bak_batch52", ".bak_batch51", ".bak_batch59",
}
EMIT_FUNCS = {"_emit", "emit", "dispatch", "publish", "fire", "trigger", "broadcast", "send_event"}


class TestEventConstValues(unittest.TestCase):
    """常量值与原字符串完全一致，且值唯一。"""

    def test_const_values_match_originals(self):
        for name, value in CONST_TO_VALUE.items():
            self.assertTrue(hasattr(Event, name), "Event 缺少常量 %s" % name)
            self.assertEqual(getattr(Event, name), value,
                             "Event.%s 值应为 %r，实为 %r" % (name, value, getattr(Event, name)))

    def test_const_values_unique(self):
        values = list(CONST_TO_VALUE.values())
        self.assertEqual(len(set(values)), len(values), "事件值存在重复，违反唯一性")

    def test_const_count(self):
        # 本批集中管理 20 个高频事件名
        self.assertEqual(len(CONST_TO_VALUE), 31)


class TestEventRegistry(unittest.TestCase):
    """注册机制：登记 / 查询 / 去重。（各用例使用独立常量名，避免共享状态串扰）"""

    def test_is_registered_existing(self):
        self.assertTrue(is_registered_event("express.urge"))
        self.assertTrue(is_registered_event("stress.recover"))
        self.assertFalse(is_registered_event("__not_a_real_event__"))

    def test_all_event_values(self):
        vals = all_event_values()
        self.assertIsInstance(vals, frozenset)
        self.assertIn("express.urge", vals)
        self.assertIn("heart.beat", vals)

    def test_register_event_new(self):
        name, value = "_M58_REG_NEW", "_m58.reg.new"
        register_event(name, value)  # 同名同值幂等，重复运行不报错
        self.assertTrue(is_registered_event(value))
        self.assertTrue(hasattr(Event, name))
        self.assertEqual(getattr(Event, name), value)

    def test_register_event_idempotent_same_value(self):
        name, value = "_M58_REG_IDEM", "_m58.reg.idem"
        register_event(name, value)
        register_event(name, value)  # 同名同值再次登记应为幂等
        self.assertEqual(getattr(Event, name), value)

    def test_register_event_conflict_raises(self):
        name, value = "_M58_REG_CONF", "_m58.reg.conf"
        register_event(name, value)
        with self.assertRaises(ValueError):
            register_event(name, "different.value")


class TestNoRawEventLiterals(unittest.TestCase):
    """全库扫描：20 个事件名不得再以裸字符串形式出现在 emit 调用首参。"""

    def _scan_raw_literals(self):
        hits = []
        for dirpath, dirnames, filenames in os.walk(_PROJECT_ROOT):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, _PROJECT_ROOT)
                if rel.replace("\\", "/") == "nucleus/const.py":
                    continue  # 常量定义处允许出现原值
                if rel.replace("\\", "/") == "tests/test_event_const_m58.py":
                    continue  # 本测试文件内以字典形式引用，非 emit 调用
                try:
                    src = open(full, "r", encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                try:
                    tree = ast.parse(src, filename=rel)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        f = node.func
                        callee = f.attr if isinstance(f, ast.Attribute) else (
                            f.id if isinstance(f, ast.Name) else None)
                        if callee in EMIT_FUNCS and node.args:
                            a0 = node.args[0]
                            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                                if a0.value in CONST_TO_VALUE.values():
                                    hits.append((rel, node.lineno, a0.value))
        return hits

    def test_no_raw_emit_args(self):
        hits = self._scan_raw_literals()
        self.assertEqual(
            hits, [],
            "以下位置仍存在裸字符串事件名（应已替换为 Event 常量）：%s" % hits,
        )


if __name__ == "__main__":
    unittest.main()
