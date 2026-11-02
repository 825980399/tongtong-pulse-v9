# -*- coding: utf-8 -*-
"""424 刀1 门控单测：核心目录静默 except 清零（assign/ret/cont 三类形态）。

口径：
  - 用**门禁同款判据** `tools/ci/ci_common.extract_handlers` 反查，不自造判据；
  - 「已改」= 该 handler 里出现 `[424刀1]` 且不再被判为静默 handler；
  - 行为断言：注入错误后**返回值与改前一致**（任务书硬要求：不改返回逻辑），
    同时 `silent_exc` 被调用一次且级别为 DEBUG。

★每个用例独立路径（tmp_path），不碰生产数据。
"""
from __future__ import annotations

import ast
import logging
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "ci"))

from ci_common import extract_handlers

from nucleus._silent_except import silent_exc

TAG = "[424刀1]"
DIRS = ("nucleus/evolution", "nucleus/mnemosyne")
WHERE_RE = re.compile(r"""where=["']([A-Za-z0-9_.]+)""" + re.escape(TAG))


def _read(path):
    """统一读文件（utf-8-sig 剥 BOM；errors=replace 防个别字节炸掉整条用例）。"""
    with open(path, encoding="utf-8-sig", errors="replace") as _f:
        return _f.read()


def _py_files(dirs):
    out = []
    for d in dirs:
        for dp, _dn, fns in os.walk(os.path.join(ROOT, d)):
            if "__pycache__" in dp:
                continue
            for fn in fns:
                if fn.endswith(".py"):
                    out.append(os.path.join(dp, fn))
    return sorted(out)


def _sites():
    """扫出所有带 [424刀1] 的 handler：[(rel, lineno, qualname_of_call, where)]。"""
    found = []
    for p in _py_files(DIRS):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        src = _read(p)
        if TAG not in src:
            continue
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if not isinstance(n, ast.ExceptHandler):
                continue
            body_src = "\n".join(ast.unparse(x) for x in n.body)
            m = WHERE_RE.search(body_src)
            if not m:
                continue
            found.append((rel, n.lineno, m.group(1)))
    return found


def _silent_map():
    """{(rel, qualname): count} —— 门禁口径下的静默 handler。"""
    out = {}
    for p in _py_files(DIRS):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        src = _read(p)
        for h in extract_handlers(src, rel):
            k = (rel, h["qualname"])
            out[k] = out.get(k, 0) + 1
    return out


# ---------------------------------------------------------------- 1 站点清单
def test_01_site_manifest_is_24_and_each_removes_one_handler():
    """24 处（具名清单口径：只降不升）+ 每个文件「改掉的静默数」恰好等于站点数。

    ★为什么不用「(文件, 函数名) 不再静默」当判据：同一个函数里可能有多个 handler
      （如 `TestGenerator._run_roundtrip_test` 有 2 个，本批只改「失败当通过」那个），
      按函数名判会把「留着的那个」误当成本批没生效。
      改用 per-file 计数守恒：HEAD 静默数 − 现在静默数 == 该文件站点数。
    """
    sites = _sites()
    assert len(sites) == 24, f"站点数应为 24，实际 {len(sites)}：{sites}"

    now = {}
    for p in _py_files(DIRS):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        src = _read(p)
        now[rel] = len(extract_handlers(src, rel))

    per_file = {}
    for rel, _ln, _w in sites:
        per_file[rel] = per_file.get(rel, 0) + 1

    import subprocess
    for rel, n in sorted(per_file.items()):
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, check=False,
                           capture_output=True, text=True, encoding="utf-8-sig")
        head_src = r.stdout
        head_n = len(extract_handlers(head_src, rel))
        # ★428 显式关闸：原判据（HEAD − 现在 == 站点数）依赖「本批改动**尚未提交**」
        #   这个一次性状态；424 批一经提交，HEAD 就含改动 ⇒ 差值恒 0 ⇒ 永久红。
        #   实测 428 批跑出 EvolutionLoop.py「HEAD 5 − 现在 5 = 0，但站点数 1」。
        #   故：已在 HEAD 里的文件改走**与提交状态无关**的直接判据 —— 该文件里带
        #   [424刀1] 留痕的 handler，现在必须已不被判为静默。原断言保留给未提交情形。
        if head_n - now[rel] == 0 and n > 0:
            _silent_ln = {h["lineno"] for h in extract_handlers(_read(os.path.join(ROOT, rel)), rel)}
            _tagged = [ln for (r_, ln, _w) in sites if r_ == rel]
            _still = [ln for ln in _tagged if ln in _silent_ln]
            assert not _still, (
                f"{rel}: 该文件已随提交进入 HEAD，改用直接判据 —— "
                f"带 [424刀1] 留痕的 handler 仍有 {len(_still)} 处被判静默：{_still}")
            continue
        assert head_n - now[rel] == n, (
            f"{rel}: HEAD 静默 {head_n} − 现在 {now[rel]} = {head_n - now[rel]}，"
            f"但站点数 {n}（静默数没按预期下降 ⇒ 有一处 silent_exc 没生效）")


# ---------------------------------------------------------------- 2 总量只降不升
def test_02_core_dirs_silent_total_not_grow():
    """两目录静默 handler 总量 ≤ 59。用 `<=` 不钉死，允许继续降。

    演进账（每次变动都必须在此**具名**，否则「只降不升」就成了空话）：
      改前 81 → 424 批 24 处 ⇒ 57；
      ★428 批 +3（三处**探测型** handler，异常本身就是返回值，加日志等于刷屏）：
        · `LLMEvolutionEngine._m428_self_contained_prefix` 的 pass 形态
        · 同函数 continue 形态（逐行回退候选前缀）
        · `perf_delta_probe._m428_parses_standalone`（判定能否独立成模块）
        三者已在 G02 门禁 `silent_except_fingerprints.json` 显式豁免（113 → 116 条）；
      ★428 批 −1（`LLMEvolutionEngine` 解析失败留痕 DEBUG→WARNING，该 handler 不再判静默）
      ⇒ 57 + 3 − 1 = 59。
    """
    total = sum(_silent_map().values())
    assert total <= 59, f"静默 handler 反涨到 {total}（应为 ≤59）"


# ---------------------------------------------------------------- 3 行为不变 + 有留痕
def test_03_load_history_keeps_return_and_logs(tmp_path, monkeypatch):
    """patch_quality_evaluator.load_history：文件不存在 ⇒ 仍返回 []，但多一条 DEBUG 留痕。"""
    from nucleus.evolution import patch_quality_evaluator as pqe

    calls = []
    monkeypatch.setattr(pqe, "silent_exc", lambda e, **kw: calls.append((e, kw)))

    got = pqe.load_history(str(tmp_path / "nope.json"))
    assert got == [], "返回值逻辑不许变：原来返回 [] 还是 []"
    assert len(calls) == 1, "必须留痕一次"
    exc, kw = calls[0]
    assert isinstance(exc, OSError)
    assert kw["where"].startswith("patch_quality_evaluator.load_history" + TAG)


def test_04_get_stats_keeps_shape_and_logs(tmp_path, monkeypatch):
    """verification_learning_hub.get_stats：内部状态损坏 ⇒ 仍返回全 0 结构，但留痕。"""
    from nucleus.mnemosyne import verification_learning_hub as vlh

    calls = []
    monkeypatch.setattr(vlh, "silent_exc", lambda e, **kw: calls.append((e, kw)))
    hub = vlh.VerificationLearningHub(file_path=str(tmp_path / "vl.json"))
    hub._entries = None

    got = hub.get_stats()
    assert got["total"] == 0 and got["remediation_count"] == 0
    assert got["top_failure_reasons"] == []
    assert len(calls) == 1
    assert isinstance(calls[0][0], TypeError)
    assert calls[0][1]["where"].startswith("verification_learning_hub.get_stats" + TAG)


def test_05_ledger_bad_line_skipped_but_reported(tmp_path, monkeypatch):
    """criteria_window.write_ledger_conclusions：账本坏行 ⇒ 跳过且仍能追加，但留痕。"""
    from nucleus.evolution import criteria_window as cw

    calls = []
    monkeypatch.setattr(cw, "silent_exc", lambda e, **kw: calls.append((e, kw)))
    led = tmp_path / "ledger.jsonl"
    led.write_text('{ this is not json \n', encoding="utf-8")

    out = cw.write_ledger_conclusions(path=str(led), now=1.0)
    assert out.get("ok") is True, "返回值逻辑不许变：坏行应被跳过，结论仍写入"
    assert len(calls) == 1
    assert calls[0][1]["where"].startswith("criteria_window.write_ledger_conclusions" + TAG)


# ---------------------------------------------------------------- 5 级别必须是 DEBUG
def test_06_log_level_is_debug_not_warning(caplog):
    """走真 silent_exc：级别必须是 DEBUG，且格式含 `[静默异常可见化]` 与类型/信息。

    ★任务书：「不要把正常运行的日志搞出一堆 warning——只记 DEBUG 级别」。
    """
    from nucleus._silent_except import silent_exc

    with caplog.at_level(logging.DEBUG, logger="pulse.silent_except"):
        silent_exc(ValueError("探测用错误"), where="unittest.m424_k1" + TAG)

    recs = [r for r in caplog.records if "静默异常可见化" in r.getMessage()]
    assert len(recs) == 1, f"应捕获 1 条，实际 {len(recs)}"
    r = recs[0]
    assert r.levelno == logging.DEBUG, f"级别必须是 DEBUG，实际 {r.levelname}"
    msg = r.getMessage()
    assert msg.startswith("[静默异常可见化] unittest.m424_k1" + TAG)
    assert "ValueError: 探测用错误" in msg


# ---------------------------------------------------------------- 6 白名单已显式 shrink
def test_07_fingerprint_baseline_shrunk_and_consistent():
    """基线演进：116→113 的三条已移除；★428 批 →116（+3 探测型豁免，见 test_02）。

    剩余条目仍必须能在仓库里找到（不腐化）。
    """
    import json as _json

    from ci_common import handler_identity

    with open(os.path.join(ROOT, "tools", "ci", "silent_except_fingerprints.json"),
              encoding="utf-8") as _bf:
        base = _json.load(_bf)
    assert len(base) == 116, f"基线应为 116 条，实际 {len(base)}"
    # ★428：新增的 3 条必须就是那两处**探测型**函数（不得拿别的静默来凑数）
    _new_428 = {
        ("nucleus/evolution/LLMEvolutionEngine.py", "_m428_self_contained_prefix"),
        ("nucleus/evolution/perf_delta_probe.py", "_m428_parses_standalone"),
    }
    _have = {(e["path"], e["qualname"]) for e in base}
    assert _new_428 <= _have, f"428 批探测型豁免未登记：{_new_428 - _have}"
    gone = {
        ("nucleus/evolution/criteria_window.py", "write_ledger_conclusions"),
        ("nucleus/evolution/zero_break_judge.py", "write_calibration_ledger"),
        ("nucleus/mnemosyne/PulseSnapshot.py", "cleanup_temp_snapshot_residue"),
    }
    for e in base:
        assert (e["path"], e["qualname"]) not in gone, f"已修的 {e['qualname']} 仍在白名单里"

    known = {handler_identity(x) for x in base}
    present = set()
    for rel in _all_repo_py():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        src = _read(p)
        try:
            present.update(handler_identity(h) for h in extract_handlers(src, rel))
        except SyntaxError as _e:
            # ★本文件也被静默 except 门禁扫（tests/ 同受管），这里必须留痕
            silent_exc(_e, where="tests.test_m424_k1._all_repo_py[424刀1]")
            continue
    rot = known - present
    assert not rot, f"白名单腐化 {len(rot)} 条：{list(rot)[:3]}"


def _all_repo_py():
    import subprocess
    r = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, check=False,
                       capture_output=True, text=True, encoding="utf-8")
    return [x for x in r.stdout.splitlines() if x.strip()]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
