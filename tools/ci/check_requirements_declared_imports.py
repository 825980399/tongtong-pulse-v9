# -*- coding: utf-8 -*-
"""第162批 刀4 步4：依赖声明对账门禁。

断言（任务书 §4）：**生产代码**中的第三方 `import`，若**无 ImportError 降级保护**，
则**必须**已在 requirements(.txt|-ci.txt|dev-requirements.txt) 中声明；否则视为
「未声明硬缺口」，阻断提交。

判定口径（与 k4_ast_probe2 一致，AST 精确）：
  · 扫描生产目录（organs/ nucleus/ base/ config.py main.py），排除 tools/ tests/ tmp/ .bak*/ docs/
  · 取顶层包名；若是项目内包 / 标准库 / 已在声明集合 → 跳过
  · 若 import 语句位于 `try:` 体（含祖先 try）且对应 except 捕获 ImportError → 视为有降级保护 → 跳过
  · 其余未声明第三方 import → 记违规

声明集合来源：requirements.txt / dev-requirements.txt 的顶层包名 + 已知「发行名→导入名」别名
（faiss-cpu→faiss, Pillow→PIL, opencv-python→cv2, PyMuPDF→fitz, PyYAML→yaml）。

退出码：0 = 干净；1 = 存在未声明硬缺口（阻断）。
"""
import ast
import io
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 生产目录（相对 REPO_ROOT）
PROD_DIRS = ['organs', 'nucleus', 'base']
PROD_FILES = ['config.py', 'main.py']
EXCLUDE_DIRS = {'tools', 'tests', 'tmp', '.bak_batch160lower', '.bak_batch161lower',
                '.bak_batch161', 'docs', '.git', '.workbuddy', '__pycache__'}

# 发行名 → 导入名 别名
ALIASES = {
    'faiss-cpu': 'faiss',
    'pillow': 'PIL',
    'opencv-python': 'cv2',
    'pymupdf': 'fitz',
    'pyyaml': 'yaml',
    'pyyaml-unsafe': 'yaml',
}

# 已知项目内顶层（动态补充所有仓库根下的包/模块）
PROJECT_TOPS = set()


def _collect_project_tops():
    tops = set()
    for name in os.listdir(REPO_ROOT):
        p = os.path.join(REPO_ROOT, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, '__init__.py')):
            tops.add(name)
        elif os.path.isfile(p) and name.endswith('.py'):
            tops.add(name[:-3])
    tops.update({'nucleus', 'organs', 'base', 'config', 'main', 'tools', 'tests'})
    return tops


def _load_declared():
    """返回 (set of declared import-names, dict of source file -> names)。"""
    declared = set()
    files = ['requirements.txt', 'dev-requirements.txt', 'requirements-ci.txt']
    for fn in files:
        fp = os.path.join(REPO_ROOT, fn)
        if not os.path.isfile(fp):
            continue
        for ln in io.open(fp, encoding='utf-8', newline=None):
            ln = ln.split('#')[0].strip()
            if not ln or ln.startswith('-') or ln.startswith('git+') or ln.startswith('http'):
                continue
            # 可能形如：pkg>=1.0,<2.0 或 pkg==1.0 或 pkg
            name = ln.split('==')[0].split('>=')[0].split('<=')[0].split('~=')[0] \
                     .split('[')[0].split(';')[0].strip()
            if not name:
                continue
            declared.add(name)
            # 别名（发行名→导入名），大小写归一
            alias = ALIASES.get(name.lower())
            if alias:
                declared.add(alias)
            # 归一：连字符转下划线也视为可声明
            declared.add(name.replace('-', '_'))
    return declared


def _is_protected(node, protected_set):
    """node 是否位于捕获 ImportError 的 try 体内。protected_set 由 _collect_protected 预计算。"""
    return id(node) in protected_set


def _collect_protected(tree):
    """收集所有被 try/except ImportError(或其基类) 保护的语句节点 id。"""
    protected = set()

    class V(ast.NodeVisitor):
        def visit_Try(self, t):
            catches_import = False
            for h in t.handlers:
                if h.type is None:
                    catches_import = True
                    break
                name = ''
                if isinstance(h.type, ast.Name):
                    name = h.type.id
                elif isinstance(h.type, ast.Attribute):
                    name = h.type.attr
                if name in ('ImportError', 'Exception', 'BaseException', 'error'):
                    catches_import = True
                    break
            if catches_import:
                for stmt in t.body:
                    for sub in ast.walk(stmt):
                        protected.add(id(sub))
            self.generic_visit(t)

    V().visit(tree)
    return protected


def _top_name(module):
    if module is None:
        return None
    return module.split('.')[0]


def scan_tree(tree, declared, project_tops, protected):
    """对一棵已解析的 AST 返回违规顶层包名列表。"""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_name(alias.name)
                if top and _is_violation(top, declared, project_tops, node, protected):
                    violations.append(top)
        elif isinstance(node, ast.ImportFrom):
            top = _top_name(node.module)
            if top and node.level == 0 and _is_violation(top, declared, project_tops, node, protected):
                violations.append(top)
    return violations


def scan_file(path, declared, project_tops, protected):
    """返回该文件的违规顶层包名列表。"""
    import contextlib
    with contextlib.suppress(OSError, SyntaxError):
        # 读/解析失败（权限/语法）不在此门禁范围（另有编译门禁）；跳过避免误阻
        src = io.open(path, encoding='utf-8', newline=None).read()
        tree = ast.parse(src, filename=path)
        return scan_tree(tree, declared, project_tops, protected)
    return []


def _is_violation(top, declared, project_tops, node, protected):
    if top in project_tops:
        return False
    if top in declared:
        return False
    # 标准库
    if top in sys.stdlib_module_names:
        return False
    # 有 ImportError 降级保护 → 允许未声明（功能可选）
    if _is_protected(node, protected):
        return False
    return True


def selftest():
    # 核心判定自证：未声明第三方 import 必报违规；已声明不报；try/except ImportError
    # 保护的 import 豁免。用 tempfile 写受控 .py，驱动 scan_file / scan_tree。
    import tempfile

    declared = {"allowedpkg"}
    project_tops = {"nucleus", "organs", "base", "config", "main", "tools", "tests"}

    # 正例：未声明第三方包 → 违规
    f1 = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    f1.write("import nonexistentpkg123\n")
    f1.close()
    try:
        vs = scan_file(f1.name, declared, project_tops, set())
        assert vs, "正例：未声明包应报违规"
    finally:
        os.unlink(f1.name)

    # 反例：已声明包 → 不报
    f2 = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    f2.write("import allowedpkg\n")
    f2.close()
    try:
        vs2 = scan_file(f2.name, declared, project_tops, set())
        assert not vs2, "反例：已声明包不应报违规"
    finally:
        os.unlink(f2.name)

    # 豁免例：try/except ImportError 保护的 import → 不报
    src = "try:\n    import fragilepkg\nexcept ImportError:\n    pass\n"
    tree = ast.parse(src)
    prot = _collect_protected(tree)
    vs3 = scan_tree(tree, declared, project_tops, prot)
    assert not vs3, "豁免：有 ImportError 降级保护的 import 不应报违规"
    print("[selftest] declared-imports 自证通过")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv
    if "--selftest" in argv:
        return selftest()
    project_tops = _collect_project_tops()
    declared = _load_declared()

    all_viol = []
    for d in PROD_DIRS:
        base = os.path.join(REPO_ROOT, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS and not x.startswith('.bak')]
            for fn in files:
                if not fn.endswith('.py'):
                    continue
                fp = os.path.join(root, fn)
                try:
                    src = io.open(fp, encoding='utf-8', newline=None).read()
                    tree = ast.parse(src, filename=fp)
                except (OSError, SyntaxError):
                    continue
                prot = _collect_protected(tree)
                vs = scan_file(fp, declared, project_tops, prot)
                for v in vs:
                    all_viol.append((fp, v))

    for fn in PROD_FILES:
        fp = os.path.join(REPO_ROOT, fp)
        if not os.path.isfile(fp):
            continue
        try:
            src = io.open(fp, encoding='utf-8', newline=None).read()
            tree = ast.parse(src, filename=fp)
        except (OSError, SyntaxError):
            continue
        prot = _collect_protected(tree)
        vs = scan_file(fp, declared, project_tops, prot)
        for v in vs:
            all_viol.append((fp, v))

    if all_viol:
        sys.stderr.write('[对账门禁] 发现未声明硬缺口（生产 import 且无降级保护）：\n')
        seen = set()
        for fp, v in sorted(all_viol):
            key = (fp, v)
            if key in seen:
                continue
            seen.add(key)
            sys.stderr.write(f'  - {fp} :: import {v}\n')
        sys.stderr.write(f'[对账门禁] 共 {len(seen)} 处违规；须在 requirements 中声明或加 try/ImportError 保护。\n')
        return 1
    sys.stderr.write('[对账门禁] PASS：生产第三方 import 均有声明或降级保护。\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
