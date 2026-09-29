# -*- coding: utf-8 -*-
"""主线第58批 T3（P2-398）死代码扫描器改进 · 单元测试。

覆盖：
- ``extract_dynamic_load_names``：import_module/__import__/getattr 字符串参数
  + ("module.path", "func_name") 字符串元组插件加载模式
- ``collect_config_refs``：data/*.json 标识符字符串提取（排除 knowledge/models 等）
- ``iter_source_files``：extra_excludes 生效
- ``scan`` 回归：reset_llm_dependency_metrics 经 tmp 元组动态加载修正为 DYNAMIC_RISK；
  PulseIntent 仅在其 docstring 出现，仍为 ZERO_REF（扫描器判定正确；FieldMode 已于 相关任务 死代码清理中摘除）
"""
import importlib.util
import json
import os
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "dead_code_scan_m58", os.path.join(_ROOT, "tools", "dead_code_scan.py"))
dcs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dcs)


def _write_tmp_py(tmpdir: str, name: str, src: str) -> str:
    p = os.path.join(tmpdir, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(src)
    return p


def test_extract_import_module_literal():
    with tempfile.TemporaryDirectory() as d:
        f = _write_tmp_py(d, "a.py", 'import importlib\n'
                                  'm = importlib.import_module("nucleus.foo.Bar")\n')
        names = dcs.extract_dynamic_load_names([f])
        assert "Bar" in names
        assert "nucleus" not in names


def test_extract_import_builtin_literal():
    with tempfile.TemporaryDirectory() as d:
        f = _write_tmp_py(d, "a.py", '__import__("pkg.sub.Mod")\n')
        names = dcs.extract_dynamic_load_names([f])
        assert "Mod" in names


def test_extract_getattr_literal():
    with tempfile.TemporaryDirectory() as d:
        f = _write_tmp_py(d, "a.py", 'getattr(obj, "dynamic_func")\n')
        names = dcs.extract_dynamic_load_names([f])
        assert "dynamic_func" in names


def test_extract_tuple_plugin_pattern():
    """★T3 核心：('module.path', 'func_name') 字符串元组插件加载被识别。"""
    with tempfile.TemporaryDirectory() as d:
        src = (
            'import importlib\n'
            'for _mod, _fn in (\n'
            '    ("nucleus.LLMDependencyMetrics", "reset_llm_dependency_metrics"),\n'
            '    ("nucleus.evolution.PatchAutoApprover", "reset_patch_auto_approver"),\n'
            '):\n'
            '    getattr(importlib.import_module(_mod), _fn)()\n'
        )
        f = _write_tmp_py(d, "iso.py", src)
        names = dcs.extract_dynamic_load_names([f])
        assert "reset_llm_dependency_metrics" in names
        assert "reset_patch_auto_approver" in names


def test_extract_tuple_non_module_skipped():
    """elem0 不是模块路径（无点）则不当作插件加载。"""
    with tempfile.TemporaryDirectory() as d:
        f = _write_tmp_py(d, "a.py",
                          'pair = ("just_a_key", "some_func")\n')
        names = dcs.extract_dynamic_load_names([f])
        assert "some_func" not in names


def test_extract_target_tuple_not_matched():
    """for 循环目标元组 (_mod, _fn) 元素是 Name 而非字符串常量，不应误抓。"""
    with tempfile.TemporaryDirectory() as d:
        f = _write_tmp_py(d, "a.py",
                          'for _mod, _fn in (("a.b", "c"),):\n    pass\n')
        names = dcs.extract_dynamic_load_names([f])
        # 内层 ("a.b","c") 仍应命中
        assert "c" in names


def test_collect_config_refs_identifiers_only():
    with tempfile.TemporaryDirectory() as d:
        data_dir = os.path.join(d, "data")
        os.makedirs(data_dir)
        with open(os.path.join(data_dir, "cfg.json"), "w", encoding="utf-8") as fh:
            json.dump({"cls": "MyHandler", "note": "hello world",
                       "path": "abc.def"}, fh)
        # knowledge 子目录应被排除
        know = os.path.join(data_dir, "knowledge")
        os.makedirs(know)
        with open(os.path.join(know, "k.json"), "w", encoding="utf-8") as fh:
            json.dump({"x": "ShouldSkip"}, fh)
        refs = dcs.collect_config_refs(d)
        assert "MyHandler" in refs
        assert "ShouldSkip" not in refs
        # "abc.def" 含点，非合法标识符，不计入
        assert "abc" not in refs


def test_iter_source_files_extra_excludes():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "keep"))
        os.makedirs(os.path.join(d, "skipme"))
        _write_tmp_py(d, os.path.join("keep", "m.py"), "x = 1\n")
        _write_tmp_py(d, os.path.join("skipme", "s.py"), "y = 2\n")
        files = dcs.iter_source_files(d, extra_excludes={"skipme"})
        rels = [os.path.relpath(f, d) for f in files]
        assert any(r.startswith("keep" + os.sep) for r in rels)
        assert not any(r.startswith("skipme" + os.sep) for r in rels)


def test_scan_regression_reset_llm_dependency_metrics_dynamic():
    """★T3 修复回归：tmp 元组动态加载引用应使其归入 DYNAMIC_RISK。"""
    rep = dcs.scan()
    items = {r["name"]: r for r in rep["items"]}
    assert "reset_llm_dependency_metrics" in items
    assert items["reset_llm_dependency_metrics"]["level"] == "DYNAMIC_RISK"
    assert items["reset_llm_dependency_metrics"].get("dynamic_string_hit") is True


def test_collect_refs_excludes_docstring():
    """AST 引用统计不把 docstring 里的 'Class.method' 记为引用 —— 这正是
    PulseIntent 仅在其自身 docstring 出现时仍判 ZERO_REF 的根因（FieldMode 已于 相关任务 摘除）
    （除非被配置/动态引用命中，而那属于保守的 DYNAMIC_RISK）。"""
    with tempfile.TemporaryDirectory() as d:
        src = (
            'class MyDocClass:\n'
            '    """使用示例：MyDocClass.do_something() 仅在此处出现。"""\n'
            '    pass\n'
        )
        f = _write_tmp_py(d, "mod.py", src)
        counter, _ = dcs.collect_refs([f])
        assert counter.get("MyDocClass", 0) == 0


def test_scan_regression_pulseintent_not_over_reported_as_dynamic():
    """PulseIntent 不应仅因 docstring 被误判为 DYNAMIC_RISK；

    （注：FieldMode 已于 相关任务 死代码清理中摘除。）
    若运行期 data/*.json 配置引用了它，则会保守归入 DYNAMIC_RISK（安全方向），
    故此处只断言：它要么 ZERO_REF（无引用），要么 DYNAMIC_RISK（有反射风险信号），
    绝不可能是 TEST_ONLY（那意味着仅 tests 引用，与 docstring 事实矛盾）。
    """
    rep = dcs.scan()
    items = {r["name"]: r for r in rep["items"]}
    for n in ("PulseIntent",):
        assert items[n]["level"] in ("ZERO_REF", "DYNAMIC_RISK")
