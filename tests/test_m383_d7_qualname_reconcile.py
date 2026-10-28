# -*- coding: utf-8 -*-
"""383 刀7：台账 qualname 对账脚本的单测。

覆盖：
  1. 五态各一条（ok / repaired / inlined / removed / file_missing）
  2. dry-run 不改台账一个字节
  3. `--annotate` 只加字段、不删条目、幂等
  4. 判据同源（复用 ci_common，不另写一套 sha/kind 口径）
  5. 真实两本台账的实测结论守卫（187 条，qualname 脱节 = 0）

★第 5 条是**契约测试**：如果将来它红了，说明代码里真的删了/搬了 handler，
  该更新台账（跑 `--annotate`），**不是脚本坏了**。不要为了让它绿而放宽判据。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ci import qualname_reconcile as qr  # noqa: E402

#: 合成源码：四个函数分别演示 ok / repaired / inlined 的宿主 / removed 的空位
_SRC = '''
def keep_me():
    try:
        _x = 1
    except ValueError:
        pass


def repaired_one():
    try:
        _x = 1
    except TypeError as _e:
        silent_exc(_e, "mod.py:repaired_one")


def new_host():
    try:
        _x = 1
    except KeyError:
        return None
'''


def _mk_repo(tmp_path, src=_SRC):
    (tmp_path / "mod.py").write_text(src, encoding="utf-8")
    return str(tmp_path)


def _real_row(tmp_path, qualname, tname, kind):
    """从真实扫描里取一条**精确**台账行（sha16 必须真实才能判 ok）。"""
    keys, _ = qr._scan_file("mod.py", str(tmp_path))
    for (rel, q, t, k, s) in keys:
        if q == qualname and t == tname and k == kind:
            return {"path": rel, "qualname": q, "tname": t, "kind": k,
                    "sha16": s, "lineno": 1}
    raise AssertionError("扫描不到 %s/%s/%s" % (qualname, tname, kind))


def _write_ledger(tmp_path, rows, wrapper=False, name="ledger.json"):
    p = tmp_path / name
    data = ({"schema": "test", "total": len(rows), "rows": rows}
            if wrapper else rows)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------- 五态各一条
def test_m383_d7_ok_when_fingerprint_still_silent(tmp_path):
    """★核心：五元组还在 ⇒ ok，且不进 details（不制造噪声）。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [_real_row(tmp_path, "keep_me", "ValueError", "pass")])
    rep = qr.reconcile(led, root)
    assert rep["stat"] == {"ok": 1}
    assert rep["details"] == []


def test_m383_d7_repaired_when_host_kept_but_report_added(tmp_path):
    """宿主与异常类型没变、但已加 silent_exc ⇒ repaired（可销账），不是 removed。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [{
        "path": "mod.py", "qualname": "repaired_one", "tname": "TypeError",
        "kind": "pass", "sha16": "0000000000000000", "lineno": 1}])
    rep = qr.reconcile(led, root)
    assert rep["stat"] == {"repaired": 1}
    d = rep["details"][0]
    assert d["has_report_now"] is True
    assert d["status"] == "repaired"


def test_m383_d7_inlined_when_host_moved(tmp_path):
    """宿主名没了，但同 (tname, kind) 的 handler 挂在别的函数下 ⇒ inlined。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [{
        "path": "mod.py", "qualname": "ghost_host", "tname": "KeyError",
        "kind": "ret", "sha16": "0000000000000000", "lineno": 1}])
    rep = qr.reconcile(led, root)
    assert rep["stat"] == {"inlined": 1}
    assert rep["details"][0]["now_under"] == "new_host"


def test_m383_d7_removed_when_type_gone(tmp_path):
    """宿主没了、本文件也找不到同 (tname, kind) ⇒ removed。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [{
        "path": "mod.py", "qualname": "ghost_host", "tname": "OSError",
        "kind": "pass", "sha16": "0000000000000000", "lineno": 1}])
    rep = qr.reconcile(led, root)
    assert rep["stat"] == {"removed": 1}
    assert rep["ticketed"]["removed"] == 1


def test_m383_d7_file_missing(tmp_path):
    """文件都不在了 ⇒ file_missing（不与 removed 混为一谈）。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [{
        "path": "no_such_file.py", "qualname": "x", "tname": "Exception",
        "kind": "pass", "sha16": "0000000000000000", "lineno": 1}])
    rep = qr.reconcile(led, root)
    assert rep["stat"] == {"file_missing": 1}


# ------------------------------------------------------------ dry-run 只读
def test_m383_d7_reconcile_does_not_touch_ledger(tmp_path):
    """★默认路径一个字节都不改 —— 这是「只读脚本」的底线。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [
        _real_row(tmp_path, "keep_me", "ValueError", "pass"),
        {"path": "mod.py", "qualname": "ghost_host", "tname": "KeyError",
         "kind": "ret", "sha16": "0000000000000000", "lineno": 1},
    ], wrapper=True)
    before = io.open(led, "rb").read()
    qr.reconcile(led, root)
    qr.reconcile_all(root, [led])
    after = io.open(led, "rb").read()
    assert before == after, "对账不得修改台账"


# --------------------------------------------------- annotate：只加不删 + 幂等
def test_m383_d7_annotate_only_adds_fields(tmp_path):
    """票面要求「不删条目，只加标注」——条目数与原有字段都必须保留。"""
    root = _mk_repo(tmp_path)
    rows = [
        _real_row(tmp_path, "keep_me", "ValueError", "pass"),
        {"path": "mod.py", "qualname": "ghost_host", "tname": "KeyError",
         "kind": "ret", "sha16": "0000000000000000", "lineno": 9, "note": "人工备注"},
    ]
    led = _write_ledger(tmp_path, rows, wrapper=True)
    n_before = len(json.load(io.open(led, encoding="utf-8"))["rows"])

    changed, total = qr.annotate(led, root)
    assert (changed, total) == (2, 2)

    d = json.load(io.open(led, encoding="utf-8"))
    assert len(d["rows"]) == n_before, "annotate 不许删条目"
    assert d["rows"][0]["qualname_status"] == "ok"
    assert d["rows"][1]["qualname_status"] == "inlined"
    assert d["rows"][1]["qualname_now"] == "new_host"
    assert d["rows"][1]["note"] == "人工备注", "原有字段不许被清"


def test_m383_d7_annotate_is_idempotent(tmp_path):
    """重复跑只覆盖状态字段，changed 归零（可安全重入）。"""
    root = _mk_repo(tmp_path)
    led = _write_ledger(tmp_path, [{
        "path": "mod.py", "qualname": "ghost_host", "tname": "KeyError",
        "kind": "ret", "sha16": "0000000000000000", "lineno": 1}])
    qr.annotate(led, root)
    changed2, total2 = qr.annotate(led, root)
    assert (changed2, total2) == (0, 1)


# --------------------------------------------------------- 判据同源（铁律271）
def test_m383_d7_judge_reuses_ci_common():
    """★不许另写一套口径：指纹必须来自 ci_common 的权威实现。"""
    src = io.open(os.path.join(ROOT, "tools/ci/qualname_reconcile.py"),
                  encoding="utf-8").read()
    assert "extract_handlers" in src, "必须调用 ci_common.extract_handlers"
    assert "normalize_body" in src, "sha16 归一化必须走 ci_common.normalize_body"
    assert "_kind_of" in src, "kind 口径必须走 ci_common._kind_of"
    # 反面：不得出现自己写的 kind 判定（pass/ret/assign 三分支重实现）
    assert "ast.Return" not in src.split("def _scan_file")[0], \
        "文件头部不该出现自造的 handler 体判定"


# --------------------------------------------------------- 真实台账实测守卫
def test_m383_d7_real_ledgers_have_zero_qualname_drift():
    """实测结论守卫：两本台账 187 条，qualname 脱节（inlined / removed）= 0。

    红了怎么办（写在断言消息里，省得后人猜）：
      说明代码真的删/搬了 handler ⇒ 跑
      `python tools/ci/qualname_reconcile.py --ledger <本> --annotate`
      更新标注并同步台账；**不要放宽这条判据**。
    """
    rep = qr.reconcile_all()
    assert rep["total"] == 187, "台账条目数变了：%s" % rep["totals"]
    for st in ("inlined", "removed", "file_missing", "parse_error"):
        assert rep["totals"].get(st, 0) == 0, (
            "出现 %s=%d ⇒ 代码与台账脱节，按 docstring 处置：%s"
            % (st, rep["totals"].get(st, 0),
               [d for led in rep["ledgers"].values()
                for d in led["details"] if d["status"] == st][:3]))
