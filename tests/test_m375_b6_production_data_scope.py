# -*- coding: utf-8 -*-
"""★375批刀6：`production_data` 标记的实测口径 —— 判据自检。

## 为什么刀6 不是「改标记」而是「先造判据」

任务书原文：「标记与真写面完全不重合（17 带标记无一真写、10 真写无一标记）；改实测口径」。
实测复核（`tools/ci/check_production_data_marks.py`）把这两组数都**推翻**了：

  * 标记不是 17 处而是 **15 处**，且**全部挂在 class 上**（不是 test 函数）；
  * 「10 真写」经判据甄别后 **0 条真写生产 data/** —— 那 10 条分别是
    `tmp_path / "data" / ...`（写副本）、`tempfile.mkdtemp()` 下的 data、
    `json.dumps({"data": {...}})`（payload 键）、`_in_backup("data/...")`（纯字符串入参）。
    也就是说，「10 真写」是**旧口径的假阳性集合**，不是事实。

所以本刀先做**判据**（可复跑、可门禁，能区分副本与生产），错配清单如实报出、交裁决。
本件锁判据的判别力：四个坑各一条反证用例，缺一个就会让判据退回「字符串里有 data 就算」。

## 判据的四个坑（都是本轮实测踩出来的，见 check_production_data_marks.py 注释）

  坑1 把 `tmp_path / "data" / "call_graph"` 当生产写（基准分析缺失）
  坑2 把 `nucleus/data/exclude_dirs.py` 这个**代码目录**当生产数据区（首段判据缺失）
  坑3 把类属性常量 `POOL = join(_ROOT,"data",...)` 漏掉（常量收集只到模块级/函数级）
  坑4 把 `json.dumps` / `_in_backup("data/x")` 当写（写类白名单过宽）
"""
import ast
import importlib.util
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CI_FILE = os.path.join(ROOT, "tools", "ci", "check_production_data_marks.py")


def _load():
    _spec = importlib.util.spec_from_file_location("_pd_marks_375", CI_FILE)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


PD = _load()


def _scan(src):
    """分析一段源码，返回 {qual: row}。"""
    return {r["qual"]: r for r in PD.analyze_source(src)}


# ============================== ① 坑1：tmp 副本不是生产
def test_01_tmp_path_data_is_not_production():
    """`tmp_path / "data" / "x"` 写的是**副本**，不是生产 data/。"""
    _r = _scan(
        'import os\n'
        'def test_a(tmp_path):\n'
        '    _p = os.path.join(str(tmp_path), "data", "patches", "pending.json")\n'
        '    with open(_p, "w") as f:\n'
        '        f.write("x")\n')
    assert _r["test_a"]["kind"] == "none", "tmp_path 下的 data/ 被误判成生产写"


def test_02_tempfile_dir_is_not_production():
    _r = _scan(
        'import os, tempfile\n'
        'def test_b():\n'
        '    _d = tempfile.mkdtemp()\n'
        '    _p = os.path.join(_d, "data", "x.json")\n'
        '    os.makedirs(_p)\n')
    assert _r["test_b"]["kind"] == "none"


def test_03_str_wrapped_tmp_is_still_tmp():
    """`str(tmp_path)` 只是类型转换，基准**必须**仍判成 TMP。

    ★只看 kind 红不了（实测）：穿透缺失时基准变 UNKNOWN ⇒ 同样不计入生产面 ⇒
    kind 仍是 none。所以要直判判据的内部读数 `bases`：
    判成 TMP 才算「知道这是副本」，判成 UNKNOWN 只是「不知道」——两件事。
    """
    _r = _scan(
        'import os\n'
        'def test_c(tmp_path):\n'
        '    _p = os.path.join(str(tmp_path), "data", "reports", "r.json")\n'
        '    open(_p, "w").write("x")\n')
    assert _r["test_c"]["kind"] == "none"
    assert _r["test_c"]["bases"] == ["TMP"], f"str(tmp_path) 未被穿透：{_r['test_c']['bases']}"


# ============================== ② 坑2：代码目录 nucleus/data 不是数据区
def test_04_code_subdir_data_is_not_production():
    """`nucleus/data/exclude_dirs.py` 是**代码**，不是生产数据区。"""
    _r = _scan(
        'import os\n'
        'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
        'def test_d():\n'
        '    p = os.path.join(ROOT, "nucleus", "data", "exclude_dirs.py")\n'
        '    open(p, encoding="utf-8").read()\n')
    assert _r["test_d"]["kind"] == "none", "nucleus/data/ 被误判成生产数据区"


# ============================== ③ 生产面三种形态必须都判到
def test_05_project_root_data_is_production():
    _r = _scan(
        'import os\n'
        'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
        'def test_e():\n'
        '    p = os.path.join(ROOT, "data", "patches", "patch_history.json")\n'
        '    open(p, encoding="utf-8").read()\n')
    assert _r["test_e"]["kind"] == "content_read"


def test_06_relative_data_literal_is_production():
    _r = _scan(
        'def test_f():\n'
        '    with open("data/patches/pending_patches.json", "w") as f:\n'
        '        f.write("x")\n')
    assert _r["test_f"]["kind"] == "write"


def test_07_class_attr_const_is_production():
    """坑3：类属性常量 `POOL = join(_ROOT,"data",...)` 必须被识别。

    v4 判据只收「模块级 + 函数内」赋值，于是 `TestProductionState` 整类被判
    「完全不碰生产数据」—— 实测代价是 15 处标记全被判「过度标记」。
    """
    _r = _scan(
        'import os\n'
        '_ROOT = os.path.dirname(os.path.abspath(__file__))\n'
        'class TestX:\n'
        '    POOL = os.path.join(_ROOT, "data", "experience", "experience_pool.json")\n'
        '    def test_g(self):\n'
        '        os.path.isfile(self.POOL)\n')
    assert _r["TestX"]["kind"] == "existence_only", "类属性常量未被收集"


# ============================== ④ 坑4：白名单过宽 / 守卫不是真写
def test_08_json_dumps_is_not_write():
    """`json.dumps({"data": {...}})` 是内存序列化，payload 键不构成路径。

    ★直测 `write_targets` 而不是端到端 kind（实测）：端到端只能看到
    「Dict 目标的基准是 UNKNOWN ⇒ 不算 PROD」，所以 dumps 被误列进写类白名单
    这件事**在 kind 上看不出来**（照样 none）。必须断言它压根不产生写目标。
    """
    _call = ast.parse('json.dumps({"data": {"node_id": "x"}})').body[0].value
    assert PD.write_targets(_call) == [], "dumps 被当成了写调用"


def test_09_string_arg_judge_is_not_write():
    """`_in_backup("data/code_backups/x.py")` 是纯字符串入参断言，零 IO。"""
    _r = _scan(
        'def _in_backup(p):\n'
        '    return "code_backups" in p\n'
        'def test_i():\n'
        '    assert _in_backup("data/code_backups/old/main.py") is True\n')
    assert _r["test_i"]["kind"] == "none"


def test_10_guard_write_is_tagged_via_guard():
    """调守卫的写口 = 测守卫，**不等于**测试真写（测试类环境守卫一律拒写）。"""
    _r = _scan(
        'import os\n'
        'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
        'class WG:\n'
        '    @staticmethod\n'
        '    def guard_write(p, component=""):\n'
        '        return False\n'
        'def test_j():\n'
        '    p = os.path.join(ROOT, "data", "experience", "pool.json")\n'
        '    WG.guard_write(p, component="UT")\n')
    assert _r["test_j"]["kind"] == "via_guard", "守卫写口被算成真写"


# ============================== ⑤ UNKNOWN 不计入（宁可漏报不误报）
def test_11_unknown_base_is_not_counted():
    _r = _scan(
        'import os\n'
        'def get_dir():\n'
        '    return "/x"\n'
        'def test_k():\n'
        '    p = os.path.join(get_dir(), "data", "a.json")\n'
        '    open(p, "w").write("x")\n')
    assert _r["test_k"]["kind"] == "none", "基准不可判被当成了生产（会误报）"


# ============================== ⑥ 标记判定必须精确
def test_12_mark_detection_is_exact():
    """`usefixtures("guard_production_data")` 不是标记（v6 多算过 1 处）。"""
    _r = _scan(
        'import pytest\n'
        '@pytest.mark.production_data\n'
        'class TestMarked:\n'
        '    def test_a(self):\n'
        '        assert True\n'
        '@pytest.mark.usefixtures("guard_production_data")\n'
        'class TestNotMarked:\n'
        '    def test_b(self):\n'
        '        assert True\n')
    assert _r["TestMarked"]["marked"] is True
    assert _r["TestNotMarked"]["marked"] is False, "usefixtures 被误判成标记"


def test_13_read_mode_is_not_write():
    _r = _scan(
        'import os\n'
        'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
        'def test_l():\n'
        '    p = os.path.join(ROOT, "data", "a.json")\n'
        '    open(p, encoding="utf-8").read()\n')
    assert _r["test_l"]["kind"] == "content_read", "只读被算成写"


# ============================== ⑦ 本件只读（除 --json 显式输出）
def test_14_ci_module_only_writes_on_explicit_json():
    with open(CI_FILE, encoding="utf-8") as _f:
        _src = _f.read()
    _tree = ast.parse(_src)
    _w_open, _dump = [], []
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.Call):
            _f = getattr(_n.func, "id", None) or getattr(_n.func, "attr", None) or ""
            if _f == "open":
                _m = ""
                if len(_n.args) >= 2 and isinstance(_n.args[1], ast.Constant):
                    _m = str(_n.args[1].value)
                if any(c in _m for c in "wax+"):
                    _w_open.append(_n.lineno)
            if _f == "dump":
                _dump.append(_n.lineno)
    assert len(_w_open) == 1, f"写口应只有 --json 一处：{_w_open}"
    assert len(_dump) == 1, f"json.dump 应只有一处：{_dump}"
    # 该写口必须在 `if _a.json:` 分支内
    _lines = _src.split("\n")
    _guard = [i for i, ln in enumerate(_lines, 1) if "if _a.json:" in ln]
    assert _guard, "找不到 --json 分支"
    assert _w_open[0] > _guard[0], "写口不在 --json 分支之后"


# ============================== ⑧ 全仓实测自洽
@pytest.fixture(scope="module")
def repo_scan():
    """全仓扫描只做一次（6732 个测试的 AST 分析约 10s，别每条用例都跑）。"""
    return PD.analyze_tree(os.path.join(ROOT, "tests"))


def test_15_repo_scan_counts_add_up(repo_scan):
    """各分支之和 == 总量（铁律 A5：漏一类会让「合计」骗人）。"""
    _r = repo_scan
    _c = _r["counts"]
    assert _c["marked"] == _c["marked_and_touch"] + _c["over_marked"]
    assert _c["touch_prod"] == _c["marked_and_touch"] + _c["missing_mark"]
    assert len(_r["over_marked"]) == _c["over_marked"]
    assert len(_r["missing_mark"]) == _c["missing_mark"]
    assert _c["marked"] > 0, "扫描面失效（一个标记都没找到）"
    assert _r["scope"], "口径必须随结果输出（铁律219）"


def test_16_kind_vocabulary_is_closed(repo_scan):
    """分类词表是闭集：实测出的 kind 必须都在已定义词表里。

    ★不写成「词表里每一类都必须出现」—— 那是把测试绑在**当代语料**上：
    `write` 这一类当前恰好为 0 条（唯一的真写意图都走守卫），语料一变就会红（铁律 G5 同型）。
    """
    _all = {x["kind"] for x in (repo_scan["over_marked"] + repo_scan["missing_mark"]
                                + repo_scan["marked_ok"])}
    assert _all <= {"write", "via_guard", "content_read", "existence_only", "none"}, _all
