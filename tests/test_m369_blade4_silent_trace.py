# -*- coding: utf-8 -*-
"""369 刀4（指纹 68 条 debug 渐进留痕观察 · B6 丙方案）定向单测。

覆盖任务书刀4 要求的 4 例：
  ① 68 条留痕覆盖对照可复现（已豁免 / 已留痕 / 本刀新增 分列）；
  ② 每条留痕的异常类型/消息/计数字段齐备（where + level）；
  ③ 既有控制流语义不被留痕改动（handler body 末条仍为 continue/break）；
  ④ `tools/ci/ci_common.py` 与指纹文件零 diff（不改判据、不销账）。

★本刀**只留痕、不切判据、不改规则/阈值、不销 363 已核查 6 条指纹**。
★口径 100% 复用既有扫描器 extract_handlers_v2（不自造定位逻辑）。
"""
import ast
import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tools"))

import scan_silent_except_dual as dual  # noqa: E402

LEDGER = os.path.join(_ROOT, "tools", "ci", "baselines",
                      "m365_silent_except_ledger.json")


def _ident(r):
    return (r["path"], r["qualname"], int(r["nth"] or 0), r["tname"], r["kind"])


@pytest.fixture(scope="module")
def ledger():
    with open(LEDGER, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def scan(ledger):
    """对台账涉及的每个文件重扫（复用既有判据）。"""
    cur = {}
    for path in sorted({r["path"] for r in ledger["rows"]}):
        fp = os.path.join(_ROOT, path)
        if not os.path.exists(fp):
            continue
        with open(fp, "rb") as f:
            src = f.read().decode("utf-8", "replace")
        for h in dual.extract_handlers_v2(src, path=path.replace("\\", "/")):
            cur[_ident(h)] = h
    return cur


# ------------------------------------------------------------------ ①

def test_ledger_coverage_three_classes_reproducible(ledger, scan):
    """① 68 条三类分列可复现：已豁免 11 / 已留痕 0 / 待留痕 0 / 已毕业 57。

    ★399刀1：留痕面从 56 条降到 **0** —— 不是判据失效，是那 56 条**在这一批全部毕业**
    （399 重跑差集实测：68 条里 58 条已离开"新判据差集"，其中 55 条补了 silent_exc）。
    分类口径不变（互斥且覆盖全集），`retired` 从 1 涨到 58。
    ★真正守的**不是** `rest == 56` 这个数，而是下面两条：
      ① `todo == 0`（留痕面里没有未留痕的）；
      ② 四类之和 == 全集（守恒式）。
    钉 `rest == 56` 会把"修好了"判成失败（铁律 268）。
    """
    rows = ledger["rows"]
    assert len(rows) == 68, "台账全集应为 68（★全集只增不删），实际 %d" % len(rows)

    exempt = [r for r in rows if r.get("manual_review") or r.get("exemption")]
    # ★371A5：`retired_by` 条目已「毕业」（被后续施工修好、不再静默），退出留痕面；
    #   历史全量 68 条不动，只让计数如实减少。
    rest = [r for r in rows
            if not (r.get("manual_review") or r.get("exemption") or r.get("retired_by"))]
    logged = [r for r in rest if _ident(r) not in scan]
    todo = [r for r in rest if _ident(r) in scan]

    assert len(exempt) == 11, "已豁免（6 manual_review + 5 control_flow）应为 11，实际 %d" % len(exempt)
    assert len(todo) == 0, "仍有 %d 条未留痕：%s" % (
        len(todo), sorted({t["path"] for t in todo}))
    # 四类互斥且覆盖全集（exempt + logged + todo + retired）
    _retired = [r for r in rows if r.get("retired_by")
                and not (r.get("manual_review") or r.get("exemption"))]
    assert len(exempt) + len(logged) + len(todo) + len(_retired) == len(rows)
    # ★399刀1 新增：本批退役的行必须写明为什么（不许"莫名消失"）
    _mine = [r for r in rows if str(r.get("retired_by") or "").startswith("399")]
    assert _mine, "399 批一条都没退役？那这批要修的东西去哪了"
    assert all(r.get("retired_reason") for r in _mine), \
        "399 退役的行必须带 retired_reason（否则归因是空的）"


def test_exempt_entries_not_double_traced(ledger):
    """① 附：已在册的 11 条不得被本刀重复留痕（manual_review 6 + control_flow 5）。"""
    mr = [r for r in ledger["rows"] if r.get("manual_review")]
    cf = [r for r in ledger["rows"] if r.get("exemption")]
    assert len(mr) == 6, "manual_review 应为 6"
    assert len(cf) == 5, "control_flow exemption 应为 5"
    # control_flow 五条须是 queue.Empty 型（任务书点名）
    for r in cf:
        assert r["exemption"]["kind"] == "control_flow"
        assert "queue.Empty" in r["exemption"]["reason"]


# ------------------------------------------------------------------ ②

def _handlers_by_qualname(path):
    """按 (qualname) 归集该文件的所有 except handler（AST 级，不依赖 nth）。

    ★为什么不用 nth 定位：补留痕后 `_has_report` 会跳过已留痕 handler ⇒
    同函数内 nth 会**重编号**（实测 ParamABTestEngine：0/1/2 → 0），
    用台账 nth 反查会一条都匹配不上（本刀首版单测就是这么假通过的）。
    ⇒ 改为按 (qualname, tname, kind) 归集，再在其中找留痕到位的一条。
    """
    with open(os.path.join(_ROOT, path), "rb") as f:
        src = f.read().decode("utf-8", "replace")
    tree = ast.parse(src)
    funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
             for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    out = {}
    for h in sorted([n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)],
                    key=lambda n: n.lineno):
        qn = dual._enclosing_func(funcs, h.lineno)
        out.setdefault(qn, []).append(h)
    return out, tree


def _silent_exc_calls(h):
    return [n for n in ast.walk(h) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == "silent_exc")
        or (isinstance(n.func, ast.Attribute) and n.func.attr == "silent_exc"))]


def _kind_of_body(h):
    """★不能用扫描器的 `_kind_of`：它按 `body[0]` 判形态，而留痕后 body[0]
    已变成 `silent_exc(...)` ⇒ 会判成 None，导致台账 kind 一条都对不上
    （本刀首版单测就是这样**空跑假通过**的）。
    正确口径：在 body 中找第一条 Continue/Break —— 那才是真正的控制流形态。
    """
    for st in h.body:
        if isinstance(st, ast.Continue):
            return "cont"
        if isinstance(st, ast.Break):
            return "break"
    return None


def test_trace_calls_have_where_and_level(ledger):
    """② 台账 57 条每条都要能在其 (qualname, tname, kind) 下找到留痕，且字段齐备。

    ★本例同时守护「不是假通过」：找不到对应 handler 会直接失败（不是静默跳过）。
    """
    # ★399刀1：留痕面已归零（56 条全毕业）⇒ 改验**已毕业且 reason=fixed** 的那些行：
    #   它们才是"确实补了 silent_exc"的一群。判据意图不变（留痕必须带 where/level），
    #   只是证据从"在册"搬到了"结案"。
    rows = [r for r in ledger["rows"] if r.get("retired_reason") == "fixed"]
    checked = 0
    for r in rows:
        path = r["path"]
        by_qn, _ = _handlers_by_qualname(path)
        cands = by_qn.get(r["qualname"], [])
        assert cands, "%s 中找不到 qualname=%s 的任何 handler（台账与代码结构已脱节）" % (
            path, r["qualname"])
        matched = None
        for h in cands:
            if (ast.unparse(h.type) if h.type else "bare") != r["tname"]:
                continue
            if _kind_of_body(h) != r["kind"]:
                continue
            if not _silent_exc_calls(h):
                continue
            matched = h
            break
        assert matched is not None, \
            "%s::%s tname=%s kind=%s 未找到已补留痕的 handler" % (
                path, r["qualname"], r["tname"], r["kind"])
        for c in _silent_exc_calls(matched):
            kws = {k.arg for k in c.keywords}
            assert "where" in kws, "%s::%s 留痕缺 where" % (path, r["qualname"])
            assert "level" in kws, "%s::%s 留痕缺 level" % (path, r["qualname"])
            w = [k.value.value for k in c.keywords
                 if k.arg == "where" and isinstance(k.value, ast.Constant)]
            assert w and len(str(w[0])) >= 8, \
                "%s::%s where 过短，不足以定位" % (path, r["qualname"])
            lv = [k.value.value for k in c.keywords
                  if k.arg == "level" and isinstance(k.value, ast.Constant)]
            assert lv and lv[0] == "debug", \
                "%s::%s 留痕级别应为 debug（观测级，非告警级）" % (path, r["qualname"])
        checked += 1
    assert checked == len(rows) > 0, "应核查并命中 %d 条，实际 %d" % (len(rows), checked)


# ------------------------------------------------------------------ ③

def test_control_flow_semantics_preserved(ledger):
    """③ 留痕不得改动控制流：handler body 末条仍须是原来的 continue/break。"""
    # ★399刀1：留痕面已归零（56 条全毕业）⇒ 改验**已毕业且 reason=fixed** 的那些行：
    #   它们才是"确实补了 silent_exc"的一群。判据意图不变（留痕必须带 where/level），
    #   只是证据从"在册"搬到了"结案"。
    rows = [r for r in ledger["rows"] if r.get("retired_reason") == "fixed"]
    checked = 0
    for r in rows:
        path = r["path"]
        by_qn, _ = _handlers_by_qualname(path)
        cands = by_qn.get(r["qualname"], [])
        assert cands, "%s 中找不到 qualname=%s" % (path, r["qualname"])
        matched = None
        for h in cands:
            if (ast.unparse(h.type) if h.type else "bare") != r["tname"]:
                continue
            if _kind_of_body(h) != r["kind"]:
                continue
            if not _silent_exc_calls(h):
                continue
            matched = h
            break
        assert matched is not None, "%s::%s 未找到已补留痕的 handler" % (path, r["qualname"])
        last = matched.body[-1]
        want = ast.Break if r["kind"] == "break" else ast.Continue
        assert isinstance(last, want), \
            "%s::%s 控制流末条被改动：期望 %s，实际 %s" % (
                path, r["qualname"], r["kind"], type(last).__name__)
        checked += 1
    assert checked == len(rows) > 0, "应核查 %d 条，实际 %d（防止空跑假通过）" % (len(rows), checked)


# ------------------------------------------------------------------ ④

def test_judge_and_fingerprint_files_untouched():
    """④ 判据改动必须受控：改判据/指纹文件 ⇒ 必须同时升 JUDGE_VERSION。

    ★371A5 口径变更：原断言「一律不得改」是 369 刀4 的**刀内**纪律（只留痕不切判据）。
    该纪律不能永久锁死判据——A5 主体切换是另一张票（T-ci门禁三形态扩展-1）的裁决。
    改为「改则必须留版本痕」：偷偷改判据仍会红，合法升级可放行。
    """
    env = dict(os.environ)
    for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(_k, None)
    targets = ["tools/ci/ci_common.py",
               "tools/ci/silent_except_fingerprints.json",
               "tools/ci/silent_except_legacy_whitelist.json"]
    r = subprocess.run(["git", "status", "--porcelain", "--"] + targets,
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    _dirty = (r.stdout or "").strip()
    if not _dirty:
        return          # 判据/指纹未被改动 ⇒ 无需版本痕
    from ci_common import JUDGE_VERSION, FROZEN_JUDGE_VERSION
    assert JUDGE_VERSION != FROZEN_JUDGE_VERSION, (
        "判据/指纹文件有改动但 JUDGE_VERSION 仍停在冻结基线 %r ⇒ 疑似偷偷改判据。\n"
        "改动：%s\n若属受控升级（如 A5 主体切换），请同步升 ci_common.JUDGE_VERSION。"
        % (FROZEN_JUDGE_VERSION, _dirty))


def test_ledger_baseline_untouched():
    """④ 补：m365 台账改动必须受控（与 ④ 同口径：改则必须升 JUDGE_VERSION）。

    ★371A5 口径变更：原为「一律不得改」的刀内纪律；A5 第1笔把 `main.py::_probe_loop`
    从 break 修成 continue 后，该条在台账里已「毕业」，必须显式标记 retired_by 才能
    如实体现在留痕面计数上（57→56）。合法变更因此需要一个受控放行口。
    """
    env = dict(os.environ)
    for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(_k, None)
    r = subprocess.run(["git", "status", "--porcelain", "--",
                        "tools/ci/baselines/m365_silent_except_ledger.json"],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    _dirty = (r.stdout or "").strip()
    if not _dirty:
        return          # 台账未被改动 ⇒ 无需版本痕
    from ci_common import JUDGE_VERSION, FROZEN_JUDGE_VERSION
    assert JUDGE_VERSION != FROZEN_JUDGE_VERSION, (
        "台账基线被改动但 JUDGE_VERSION 仍停在冻结基线 %r ⇒ 疑似偷偷改台账读数。\n"
        "改动：%s\n若属受控升级（如 A5 主体切换/条目毕业），请同步升 ci_common.JUDGE_VERSION。"
        % (FROZEN_JUDGE_VERSION, _dirty))
