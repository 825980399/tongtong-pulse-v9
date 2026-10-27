# -*- coding: utf-8 -*-
"""第162批 刀4 对账门禁单测。

验证 tools/ci/check_requirements_declared_imports.py 的判定口径：
  · 未声明且无 ImportError 保护的第三方 import → 违规
  · 已声明 / 标准库 / 项目内包 / 有 try-ImportError 保护 → 干净
"""
import ast
import os
import sys

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_CI = os.path.join(TOOLS, 'tools', 'ci')
sys.path.insert(0, TOOLS_CI)
import check_requirements_declared_imports as G

DECLARED = G._load_declared()
PROJ_TOPS = G._collect_project_tops()


def _scan_src(src):
    tree = ast.parse(src)
    prot = G._collect_protected(tree)
    # 使用同一棵已解析 tree，避免节点 id 不匹配
    return G.scan_tree(tree, DECLARED, PROJ_TOPS, prot)


def test_undeclared_unprotected_is_violation():
    # 一个完全不存在、未声明、无 try 保护的第三方 import
    vs = _scan_src("import some_totally_unknown_pkg_xyz\n")
    assert 'some_totally_unknown_pkg_xyz' in vs


def test_declared_pkg_is_clean():
    # requests 已声明 → 干净
    vs = _scan_src("import requests\n")
    assert 'requests' not in vs


def test_stdlib_is_clean():
    vs = _scan_src("import json\nimport os\n")
    assert not vs


def test_project_internal_is_clean():
    vs = _scan_src("from nucleus.logger import get_module_logger\n")
    assert not vs


def test_try_import_error_protected_is_clean():
    src = (
        "try:\n"
        "    import some_optional_undeclared_pkg\n"
        "except ImportError:\n"
        "    pass\n"
    )
    vs = _scan_src(src)
    assert 'some_optional_undeclared_pkg' not in vs


def test_try_non_import_error_not_protected():
    # try 只捕获 ValueError，不保护 ImportError → 仍违规
    src = (
        "try:\n"
        "    import another_undeclared_pkg_abc\n"
        "except ValueError:\n"
        "    pass\n"
    )
    vs = _scan_src(src)
    assert 'another_undeclared_pkg_abc' in vs


def test_alias_pillow_pil_resolved():
    # Pillow 声明 → PIL 视为已声明
    vs = _scan_src("from PIL import Image\n")
    assert 'PIL' not in vs


def test_alias_pymupdf_fitz_resolved():
    vs = _scan_src("import fitz\n")
    assert 'fitz' not in vs


def test_repo_tree_clean():
    # 全仓库生产树不应有未声明硬缺口（集成）
    rc = G.main()
    assert rc == 0, "生产第三方 import 存在未声明硬缺口，见 stderr 输出"

# ==========================================================================
# ★373刀6 附带回归：门禁自身的三处机制缺陷（修好后必须一直修着）
# ==========================================================================
def test_scan_file_protection_uses_own_tree(tmp_path):
    """scan_file 内部二次解析时，protected 必须按**它自己那棵树**重算。

    旧实现把 protected 做成入参（调用方传另一棵树的节点 id）：两份 AST 节点
    对象不同、id 永不命中 ⇒「有 ImportError 降级保护」的豁免系统性失效。
    """
    _f = tmp_path / "m.py"
    _f.write_text(
        "try:\n"
        "    import some_optional_undeclared_pkg_qqq\n"
        "except ImportError:\n"
        "    some_optional_undeclared_pkg_qqq = None\n",
        encoding="utf-8")
    vs = G.scan_file(str(_f), DECLARED, PROJ_TOPS)
    assert "some_optional_undeclared_pkg_qqq" not in vs, \
        f"有 ImportError 保护的 import 仍被判违规（豁免走不到）: {vs}"


def test_main_actually_scans_prod_files(tmp_path, monkeypatch):
    """config.py / main.py 必须**真被扫到**（旧代码 `fp = join(ROOT, fp)` 自引用，
    实际扫的是上轮 walk 泄漏的变量 ⇒ 两个入口文件从来没被扫过）。"""
    (tmp_path / "main.py").write_text("import undeclared_pkg_in_main_zzz\n",
                                      encoding="utf-8")
    (tmp_path / "config.py").write_text("import undeclared_pkg_in_config_yyy\n",
                                        encoding="utf-8")
    monkeypatch.setattr(G, "REPO_ROOT", str(tmp_path))
    assert G.main([]) == 1, "入口文件里的未声明硬缺口没被检出（PROD_FILES 未真扫）"


def test_namespace_package_dir_is_project_top(tmp_path, monkeypatch):
    """无 __init__.py 的仓库内代码目录（命名空间包）算项目内顶层。

    somatics/ 就是这种：main.py 的 `from somatics.sensor import Sensor`
    不应被算成「未声明第三方 import」。
    """
    (tmp_path / "somatics").mkdir()
    (tmp_path / "somatics" / "sensor.py").write_text(
        "class Sensor:\n    pass\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from somatics.sensor import Sensor\n", encoding="utf-8")
    monkeypatch.setattr(G, "REPO_ROOT", str(tmp_path))
    assert G.main([]) == 0, "命名空间包目录被误判成第三方包"
