# -*- coding: utf-8 -*-
"""414 批刀1 判据单测：O3 三条根因改造 + 两档补档。

覆盖矩阵
--------
A 组（改动1）私有方法进候选池
  A1 三个跳过条件（魔术方法/私有开关/入口名）从**生产 AST** 提取，不是文本扫描
  A2 `_m414_private_pool_on()` 真的接在跳过条件里
  A3 开关函数读 config 且缺省 True
  A4 谓词行为：开关 ON 私有名进池 / 开关 OFF 私有名被跳 / 魔术方法恒跳
  A5 两处 issue 都带 `is_private`

B 组（改动2）public_api 重写
  B1 `__all__` 命中 ⇒ True
  B2 main.py 反射表命中 ⇒ True
  B3 都不命中 ⇒ **False**（旧判据会给 True —— 这就是区分力）
  B4 私有名 ⇒ False
  B5 反射表是 AST 动态读的（往 main.py 加一项，判据立刻跟上）

C 组（改动3）测试引用
  C1 tests/ 引用过 ⇒ test_ref True
  C2 judge_entry 拒 `test_only`
  C3 没被测试引用 ⇒ False

D 组（补档）值传递 / getattr 派发
  D1 `Thread(target=self.x)` ⇒ value_ref True
  D2 `getattr(o, "x")` ⇒ getattr_ref True
  D3/D4 judge_entry 各自拒

E 组 回归不破坏 412 契约
  E1 假索引 public_api=True 仍拒（412 test_25 语义）
  E2 version 前缀仍是 "412."
  E3 basis 含 412 钉住的两个关键词
"""

import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.evolution.o3_delete_dryrun as dd  # noqa: E402
import nucleus.self_inspector as si  # noqa: E402
from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer  # noqa: E402


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read() \
        .replace("\r\n", "\n")


# ================================================================ A 改动1

def _cand_loop():
    """从**生产源码**里取出「候选生成」那个内层 For。

    ★注意层级：`_methods.setdefault` 在外层 `for _f in _py_files` 的**内层**
      `for _m in _re.finditer(...)` 里。只按 unparse 含关键字筛会先命中外层
      （外层 body 是 try/assign/内层for，没有 if-continue）⇒ 必须要求
      「body 里直接有 if…continue」才能命中内层。
    """
    tree = ast.parse(_src("nucleus/self_inspector.py"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_detect_dead_code":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.For):
                continue
            if "_methods.setdefault" not in ast.unparse(sub):
                continue
            if any(isinstance(st, ast.If) and len(st.body) == 1
                   and isinstance(st.body[0], ast.Continue) for st in sub.body):
                return sub
    return None


def _skip_tests():
    """该 For 里形如 `if <test>: continue` 的跳过条件（AST 表达式节点）。

    ★不用文本扫描：源码注释里为了说明历史会写到旧写法，文本扫描会自伤
      （413 刀2 栽过一次）。AST 只看**可执行代码**。
    """
    loop = _cand_loop()
    assert loop is not None, "找不到候选生成的 For 循环"
    out = []
    for st in loop.body:
        if isinstance(st, ast.If) and len(st.body) == 1 \
                and isinstance(st.body[0], ast.Continue):
            out.append(st.test)
    return out


def _build_pred():
    """把生产的跳过条件编译成一个谓词 `_p(name) -> bool`。"""
    tests = [ast.unparse(x) for x in _skip_tests()]
    body = "".join("    if %s:\n        return False\n" % x for x in tests)
    src = "def _p(_name):\n%s    return True\n" % body
    ns = {"_m414_private_pool_on": lambda: si._m414_private_pool_on()}
    exec(compile(src, "<m414_pred>", "exec"), ns)   # noqa: S102 - 来源是本仓源码 AST
    return ns["_p"]


def test_a1_skip_conditions_shape():
    """三个跳过条件：魔术方法守卫 + 私有开关 + 入口名白名单。"""
    ts = [ast.unparse(x) for x in _skip_tests()]
    assert len(ts) == 3, ts
    assert any("startswith('__')" in x and "endswith('__')" in x for x in ts), ts
    assert any("_m414_private_pool_on()" in x for x in ts), ts
    assert any("'teardown'" in x for x in ts), ts
    # ★核心：裸的「所有下划线开头一律跳过」必须已经不在
    assert not any(x.replace('"', "'").strip() == "_name.startswith('_')" for x in ts), ts


def test_a2_switch_guard_present():
    tree = ast.parse(_src("nucleus/self_inspector.py"))
    assert any(isinstance(n, ast.FunctionDef) and n.name == "_m414_private_pool_on"
               for n in tree.body), "缺 _m414_private_pool_on 定义"


def test_a3_switch_reads_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M414_O3_PRIVATE_IN_POOL", False, raising=False)
    assert si._m414_private_pool_on() is False
    monkeypatch.setattr(config, "ENABLE_M414_O3_PRIVATE_IN_POOL", True, raising=False)
    assert si._m414_private_pool_on() is True


def test_a4_predicate_behaviour(monkeypatch):
    monkeypatch.setattr(si, "_m414_private_pool_on", lambda: True)
    p = _build_pred()
    assert p("_priv_fn") is True          # 私有进池
    assert p("pub_fn") is True
    assert p("__init__") is False         # 魔术方法恒跳
    assert p("__repr__") is False
    assert p("main") is False             # 入口名仍跳
    monkeypatch.setattr(si, "_m414_private_pool_on", lambda: False)
    p2 = _build_pred()
    assert p2("_priv_fn") is False        # 开关关 ⇒ 退回 412 行为
    assert p2("pub_fn") is True


def test_a5_issue_carries_is_private():
    tree = ast.parse(_src("nucleus/self_inspector.py"))
    _n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k in node.keys:
            if isinstance(k, ast.Constant) and k.value == "is_private":
                _n += 1
    assert _n >= 2, "review 与 dead 两处 issue 都要带 is_private，实测 %d" % _n


# ================================================================ 最小项目

MOD_A = '''# -*- coding: utf-8 -*-
__all__ = ["in_all_fn"]

import threading


class A:
    def pub_orphan(self):
        return 1

    def in_all_fn(self):
        return 2

    def fn_reflect(self):
        return 3

    def _priv_orphan(self):
        return 4

    def test_called_fn(self):
        return 5

    def thread_target(self):
        return 6

    def dyn_fn(self):
        return 7

    def __init__(self):
        self._t = threading.Thread(target=self.thread_target)
        getattr(self, "dyn_fn")()
'''

TEST_X = '''# -*- coding: utf-8 -*-


def test_one():
    obj = None
    return obj.test_called_fn
'''


def _mkproj(root, reflect_lines=None):
    os.makedirs(os.path.join(root, "nucleus"), exist_ok=True)
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    io.open(os.path.join(root, "config.py"), "w", encoding="utf-8",
            newline="\n").write("X = 1\n")
    _refl = reflect_lines if reflect_lines is not None else \
        ["    ('nucleus.mod_a', 'fn_reflect'),"]
    io.open(os.path.join(root, "main.py"), "w", encoding="utf-8", newline="\n").write(
        "REFLECT = [\n%s\n]\nfor _m, _f in REFLECT:\n    pass\n" % "\n".join(_refl))
    io.open(os.path.join(root, "nucleus", "__init__.py"), "w", encoding="utf-8",
            newline="\n").write("")
    io.open(os.path.join(root, "nucleus", "mod_a.py"), "w", encoding="utf-8",
            newline="\n").write(MOD_A)
    io.open(os.path.join(root, "tests", "test_x.py"), "w", encoding="utf-8",
            newline="\n").write(TEST_X)
    return root


def _iso(root):
    g = CallGraphAnalyzer(project_root=root,
                          scan_dirs=["nucleus", "main.py", "config.py"])
    return {r["name"]: r for r in g.get_isolated_functions()}


@pytest.fixture()
def proj(tmp_path):
    return _mkproj(str(tmp_path / "proj"))


# ================================================================ B 改动2

def test_b1_dunder_all_means_public(proj):
    r = _iso(proj)
    assert "in_all_fn" in r, sorted(r)
    assert r["in_all_fn"]["public_api"] is True


def test_b2_reflect_table_means_public(proj):
    r = _iso(proj)
    assert r["fn_reflect"]["public_api"] is True


def test_b3_plain_public_name_is_not_public_api(proj):
    """★这就是 413 根因②：旧判据 `not startswith('_')` 会给 True。"""
    r = _iso(proj)
    assert r["pub_orphan"]["public_api"] is False


def test_b4_private_name_not_public(proj):
    r = _iso(proj)
    assert r["_priv_orphan"]["public_api"] is False


def test_b5_reflect_table_is_read_dynamically(tmp_path):
    """往 main.py 反射表加一项 ⇒ 判据立刻跟上（不是硬编码 21 个名字）。"""
    root = _mkproj(str(tmp_path / "p2"),
                   reflect_lines=["    ('nucleus.mod_a', 'fn_reflect'),",
                                  "    ('nucleus.mod_a', 'pub_orphan'),"])
    r = _iso(root)
    assert r["pub_orphan"]["public_api"] is True


# ================================================================ C 改动3

def test_c1_test_ref_detected(proj):
    r = _iso(proj)
    assert r["test_called_fn"]["test_ref"] is True


def test_c2_judge_rejects_test_only(proj):
    r = _iso(proj)
    _i = dd.isolated_index(_FakeGraph(list(r.values())))
    _j = dd.judge_entry({"file": "nucleus/mod_a.py", "method": "test_called_fn",
                         "issue_type": "dead_code", "status": "open"}, _i)
    assert _j["eligible"] is False
    assert any("test_only" in x for x in _j["reasons"]), _j["reasons"]


def test_c3_no_test_ref_when_unreferenced(proj):
    r = _iso(proj)
    assert r["pub_orphan"]["test_ref"] is False


# ================================================================ D 补档

def test_d1_value_ref_detected(proj):
    """`Thread(target=self.thread_target)` —— 没有 call 边但确实在用。

    ★这里直接测判据**集合**，不经过调用图解析：图可能自己就把某些形态
      解析成边了（那样方法有入度、根本不会进孤儿清单，测试也就测不到判据）。
      判据本身是否成立，要在集合层面证明。
    """
    g = CallGraphAnalyzer(project_root=proj,
                          scan_dirs=["nucleus", "main.py", "config.py"])
    assert "thread_target" in g._m414_value_ref_names()


def test_d2_getattr_ref_detected(proj):
    g = CallGraphAnalyzer(project_root=proj,
                          scan_dirs=["nucleus", "main.py", "config.py"])
    assert "dyn_fn" in g._m414_getattr_ref_names()


def test_d3_judge_rejects_value_ref():
    _i = dd.isolated_index(_FakeGraph([_iso_row("thread_target", value_ref=True)]))
    _j = dd.judge_entry({"file": "nucleus/mod_a.py", "method": "thread_target",
                         "issue_type": "dead_code", "status": "open"}, _i)
    assert _j["eligible"] is False
    assert any("value_ref" in x for x in _j["reasons"]), _j["reasons"]


def test_d4_judge_rejects_getattr_ref():
    _i = dd.isolated_index(_FakeGraph([_iso_row("dyn_fn", getattr_ref=True)]))
    _j = dd.judge_entry({"file": "nucleus/mod_a.py", "method": "dyn_fn",
                         "issue_type": "dead_code", "status": "open"}, _i)
    assert _j["eligible"] is False
    assert any("getattr_ref" in x for x in _j["reasons"]), _j["reasons"]


def _iso_row(name, file="nucleus/mod_a.py", **kw):
    """构造一条孤立方法行（默认三档全 False，全干净 ⇒ 应判 eligible）。"""
    _r = {"id": "%s::%s" % (file, name), "name": name, "file": file, "line": 1,
          "type": "method", "public_api": False, "entry_like": False,
          "test_ref": False, "value_ref": False, "getattr_ref": False}
    _r.update(kw)
    return _r


def test_d5_clean_row_is_eligible():
    """反向对照：三档全 False 的干净行必须能判 eligible。

    ★没有这一条，上面 D3/D4 的「拒」可能只是被判死流程本身挡掉了
      （比如 method_not_isolated 也能让 eligible=False）—— 那就是假绿。
    """
    _i = dd.isolated_index(_FakeGraph([_iso_row("clean_fn")]))
    _j = dd.judge_entry({"file": "nucleus/mod_a.py", "method": "clean_fn",
                         "issue_type": "dead_code", "status": "open"}, _i)
    assert _j["eligible"] is True, _j["reasons"]


class _FakeGraph:
    """只实现 isolated_index 需要的最小接口。"""

    def __init__(self, iso):
        self._iso = list(iso)

    def get_isolated_functions(self):
        return list(self._iso)

    def _ensure(self):
        return {"nodes": [{"file": x["file"]} for x in self._iso]}


# ================================================================ E 回归

def test_e1_public_api_switch_still_rejects():
    """412 test_25 的语义必须仍然成立（假索引 public_api=True 仍拒）。"""
    _iso1 = [{"id": "a::pub", "name": "pub", "file": "nucleus/x.py", "line": 1,
              "type": "method", "public_api": True, "entry_like": False,
              "test_ref": False, "value_ref": False, "getattr_ref": False}]
    _i = dd.isolated_index(_FakeGraph(_iso1))
    _j = dd.judge_entry({"file": "nucleus/x.py", "method": "pub",
                         "issue_type": "dead_code", "status": "open"}, _i)
    assert _j["eligible"] is False
    assert any("public_api" in x for x in _j["reasons"]), _j["reasons"]


def test_e2_version_keeps_412_prefix():
    assert dd.version().startswith("412."), dd.version()


def test_e3_basis_keeps_keywords():
    """412 钉住的两个关键词不能丢；414 新判据也要写进 basis 供审计。"""
    _r = dd.report(entries=[], graph=_FakeGraph([]))
    assert "O3判死族" in _r["basis"] and "非核心文件" in _r["basis"], _r["basis"]
    assert "test_only" in _r["basis"] and "value_ref" in _r["basis"], _r["basis"]
