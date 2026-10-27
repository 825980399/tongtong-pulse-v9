# -*- coding: utf-8 -*-
"""★375批刀2：K2 进程池架构票立项 —— 「不新建池」要有 git 副本、且与实践一致。

为什么这刀必须做：原票只存在于 `tmp/p342_root/`（`tmp/` 被 gitignore），换机/换窗口即
不可见；342 那条判据在别的机器上**永远 skip**，等于判据不存在（铁律159 同型）。
本刀把票落 git 副本 + 加立项结论，并把判据改成优先读 git 副本。

本刀**不改任何运行时代码** —— 结论是「立项但不施工」，判据要能把这条结论钉住：
生产面 `ProcessPoolExecutor` 实例化处数不得因本批变化。
"""
import ast
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TICKET = os.path.join(ROOT, "docs", "路灯与星轨对话", "架构",
                      "K2进程池架构票_20261027.md")
TMP_TICKET = os.path.join(ROOT, "tmp", "p342_root", "k2_arch_ticket.md")

# 375 刀2 立项时的生产面实例化处数（AST 数 Call，不数字面串——注释里的说明不算池）
BASELINE_INSTANTIATIONS = {
    "nucleus/HybridParallelScheduler.py": 1,
    "nucleus/reasoning/ReasoningWorkerPool.py": 4,
}


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _count_pools(rel):
    """只数真的被调用/构造的 ProcessPoolExecutor，不算注释与文档串。"""
    _tree = ast.parse(_read(rel))
    _n = 0
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Call):
            _fn = _node.func
            _name = getattr(_fn, "id", None) or getattr(_fn, "attr", None)
            if _name == "ProcessPoolExecutor":
                _n += 1
    return _n


# ------------------------------- ① git 副本存在且内容完整


_TICKET_REL = "docs/路灯与星轨对话/架构/K2进程池架构票_20261027.md"


def _conclusion_block():
    """只取「立项结论」段 —— 结论必须在这一段里，不许靠别处零散子串蒙混过关。

    反例（375 刀2 反向验证 V1 实测）：原断言在全文找「不新建进程池」，
    而标题行「（结论：不新建进程池）」也含该串 ⇒ 把结论段反转成「可以建池」
    后仍然全绿，等于判据没有判别力。
    """
    _t = _read(_TICKET_REL)
    _i = _t.find("## 一、375 刀2 立项结论")
    _j = _t.find("## 二、原票正文")
    assert _i >= 0, "缺「立项结论」段"
    assert _j > _i, "「立项结论」段右边界缺失"
    return _t[_i:_j]


def test_01_ticket_committed_copy_exists():
    assert os.path.isfile(TICKET), f"架构票 git 副本缺失：{TICKET}"
    _b = _conclusion_block()
    assert "不新建进程池" in _b, "结论段必须明确「不新建进程池」"
    assert "不进入施工排期" in _b, "结论段必须明确不排期"
    for _bad in ("可以建池", "批准建池", "同意建池"):
        assert _bad not in _b, "结论段出现反向表述：" + _bad
    _t = _read(_TICKET_REL)
    for _k in ("T-K2进程池票-1", "375 刀2", "翻案"):
        assert _k in _t, "git 副本缺关键内容 " + _k


def test_02_original_body_sections_intact():
    """原票五节标题全在（git 副本是照搬+前缀，不是重写）。"""
    _t = _read("docs/路灯与星轨对话/架构/K2进程池架构票_20261027.md")
    for _sec in ("## 一、为什么线程池到不了两位数",
                 "## 二、可行性评估",
                 "## 三、影响面",
                 "## 四、分叉建议",
                 "## 五、与 K3 的衔接"):
        assert _sec in _t, "原票章节丢失：" + _sec


def test_03_key_numbers_carried_over():
    """结论引用的四个实测数字必须在档（23.9 / 20.21 / 7.95 / 19.47）。

    断言只认数字本体，不绑单位写法 —— 原票写「19.47 s」（带空格），
    立项段写「23.9s」（无空格），用带单位字面量会误红。
    """
    _t = _read("docs/路灯与星轨对话/架构/K2进程池架构票_20261027.md")
    for _n in ("23.9", "20.21", "7.95", "19.47"):
        assert _n in _t, "缺实测数字 " + _n


def test_04_body_matches_tmp_when_present():
    """tmp 原稿在时，正文必须逐字一致（防「git 副本被悄悄改写」）。"""
    if not os.path.isfile(TMP_TICKET):
        # 不 skip：改用结构性替代断言，保证本用例在任何机器上都真的判一次
        _t = _read("docs/路灯与星轨对话/架构/K2进程池架构票_20261027.md")
        assert "一字未改" in _t and "## 二、原票正文" in _t
        return
    with open(TMP_TICKET, encoding="utf-8") as f:
        _orig = f.read().replace("\r\n", "\n")
    _t = _read("docs/路灯与星轨对话/架构/K2进程池架构票_20261027.md")
    assert _orig in _t, "git 副本与 tmp 原稿不再逐字一致（原票正文被改写了）"


# ------------------------------- ② 「不新建池」与实践一致


def test_05_no_new_process_pool_instantiated():
    """立项结论 = 不新建池；生产面实例化处数必须与本刀基线一致。"""
    for _rel, _want in BASELINE_INSTANTIATIONS.items():
        _got = _count_pools(_rel)
        assert _got == _want, (f"{_rel} 的 ProcessPoolExecutor 实例化 "
                               f"{_got} != 基线 {_want}")


def test_06_submit_cpu_bound_route_untouched():
    """刀2 不动路由：`submit_cpu_bound` 仍只有「委托 Hybrid」一处实现。"""
    _t = _read("nucleus/parallel_scheduler.py")
    assert _t.count("def submit_cpu_bound(") == 1, "submit_cpu_bound 定义重复或缺失"
    assert "_hs.submit(func, *args, task_type=_HybridTaskType.CPU_BOUND" in _t


# ------------------------------- ③ 342 判据已改成可达


def test_07_342_gate_prefers_git_copy():
    """342 那条判据不许再「只在 tmp 缺时 skip」——必须优先读 git 副本。

    ★用 AST 取**函数体字面量**而不是源码子串：docstring 里也写着同一个路径，
    源码子串断言会拿注释当代码（375 刀2 反向验证 V3 实测踩到 —— 删掉 git 副本
    候选后判据仍绿）。
    """
    _tree = ast.parse(_read("tests/test_m342_batch.py"))
    _fn = next((n for n in ast.walk(_tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "test_342_d3_arch_ticket_written"), None)
    assert _fn is not None, "342 的架构票判据函数不见了"
    # 按**节点**剔除 docstring：`ast.get_docstring` 会 cleandoc，与原 Constant
    # 值不相等，靠字符串比较过滤不掉（本用例 v2 实测踩到）。
    _body_nodes = list(_fn.body)
    if (_body_nodes and isinstance(_body_nodes[0], ast.Expr)
            and isinstance(getattr(_body_nodes[0], "value", None), ast.Constant)):
        _body_nodes = _body_nodes[1:]
    _code_lits = [n.value
                  for _st in _body_nodes
                  for n in ast.walk(_st)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert any("K2进程池架构票_20261027.md" in x for x in _code_lits), (
        "函数体里没有 git 副本路径（退回只看 tmp/ 时必红）")
    assert any("p342_root" in x for x in _code_lits), (
        "应保留 tmp 兜底候选（git 副本缺失时仍可判）")
    _has_skip = any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "skip"
                    for n in ast.walk(_fn))
    assert not _has_skip, "判据仍在 skip，等于永久不判（铁律159）"
