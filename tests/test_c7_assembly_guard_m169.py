# -*- coding: utf-8 -*-
"""169批停窗段 C-7 门控单测：装配回路通电（Sensor 幂等 + loader 守卫 + 差集自检）。

结构守卫为主（装配属启动期路径，轻量夹具不可端到端跑），另含行为等价性验证：
  - Sensor 守卫式复用：两分支均 `if not hasattr(self, "sensor")`，
    且 legacy 段（停用路径）内**不得**出现裸 `self.sensor = Sensor()`；
  - loader 二次守卫：`OrganLoader` 两分支均幂等构造；
  - 差集校验增强：sensor 实例数纳入可观测输出，且 ≠1 时告警；
  - 门禁契约：legacy 段 `_create_organ` 数仍为 37（不得扩张/收缩）。
"""
import ast
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MAIN = os.path.join(_ROOT, "main.py")


def _src():
    with io.open(MAIN, "r", encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _func_body(name):
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(_src(), node) or ""
            return seg
    raise AssertionError("未找到函数 {}".format(name))


class TestSensorGuard:
    def test_both_branches_guarded(self):
        for fn in ("_init_organs_declarative", "_init_organs_legacy"):
            seg = _func_body(fn)
            assert 'if not hasattr(self, "sensor"):' in seg, \
                "{} 缺 Sensor 守卫".format(fn)

    def test_no_bare_sensor_assign_in_legacy(self):
        """★legacy 段（停用路径）的 Sensor 赋值必须在 hasattr 守卫内。"""
        seg = _func_body("_init_organs_legacy")
        idx = seg.find("self.sensor = Sensor()")
        assert idx > 0, "legacy 段未找到 Sensor 赋值"
        guard = seg.rfind('if not hasattr(self, "sensor"):', 0, idx)
        assert 0 <= guard < idx, "legacy 段 Sensor 赋值不在守卫内"

    def test_all_sensor_assigns_guarded(self):
        """全文件：每个 `self.sensor = Sensor()` 都被 hasattr 守卫包裹。"""
        tree = ast.parse(_src())
        # 收集所有 If 守卫体覆盖的行号
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = ast.dump(node.test)
                if "hasattr" in test and "sensor" in test:
                    for sub in ast.walk(node):
                        _ln = getattr(sub, "lineno", None)
                        if _ln is not None:
                            guarded.add(_ln)
        assigns = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                v = node.value
                if (isinstance(v, ast.Call)
                        and isinstance(v.func, ast.Name)
                        and v.func.id == "Sensor"):
                    tgt = node.targets[0]
                    if (isinstance(tgt, ast.Attribute)
                            and tgt.attr == "sensor"):
                        assigns.append(node.lineno)
        assert assigns, "未找到任何 self.sensor = Sensor() 赋值"
        naked = [ln for ln in assigns if ln not in guarded]
        assert not naked, "未守卫的 Sensor 赋值行: {}".format(naked)


class TestLoaderGuard:
    def test_both_branches_loader_guarded(self):
        for fn in ("_init_organs_declarative", "_init_organs_legacy"):
            seg = _func_body(fn)
            assert 'if not hasattr(self, "organ_loader"):' in seg, \
                "{} 缺 organ_loader 守卫".format(fn)

    def test_loader_still_loaded(self):
        """守卫后仍必须真正 load（不能被守卫吞掉装配）。"""
        for fn in ("_init_organs_declarative", "_init_organs_legacy"):
            seg = _func_body(fn)
            assert "load_organs()" in seg, "{} 未调用 load_organs".format(fn)


class TestDiffCheckEnhancement:
    def test_sensor_instance_count_observable(self):
        seg = _func_body("_init_organs_with_feature")
        assert "sensor_instances" in seg, "差集校验未纳入 sensor 实例数"
        assert "sensor 实例数" in seg, "缺 sensor 实例数日志"

    def test_warns_when_not_one(self):
        seg = _func_body("_init_organs_with_feature")
        assert "if _sensor_n != 1:" in seg, "缺 !=1 告警分支"


class TestLegacyGateContract:
    def test_legacy_create_organ_count_still_37(self):
        """门禁契约：legacy 段 _create_organ 数不得扩张/收缩（基线 37）。"""
        seg = _func_body("_init_organs_legacy")
        n = seg.count("self._create_organ(")
        assert n == 37, "legacy 段 _create_organ=%d（基线 37）" % n

    def test_declarative_quasi_organ_count_1(self):
        seg = _func_body("_init_organs_declarative")
        n = seg.count("self._create_organ(")
        assert n == 1, "声明式段 _create_organ=%d（预期 1）" % n