# -*- coding: utf-8 -*-
"""Dxxx-4：将旧行号白名单迁移为 handler 指纹表（双轨对照，保留一个批次）。

读取 cw2_t2e_ci_gate_silent_except.py 的 LOCATION_WHITELIST（23 条），
对每条用 AST 在 HEAD 源码中定位静默 handler，生成指纹：
    (path, qualname, nth_in_function, body_sha256[:16])
写出：
    silent_except_fingerprints.json      —— 新豁免指纹集合（门禁加载用，23 条）
    silent_except_legacy_whitelist.json  —— 旧行号表（双轨对照，下批可删）
"""
import ast
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ci_common import extract_handlers, handler_identity, _enclosing_func  # noqa: E402
import cw2_t2e_ci_gate_silent_except as gate  # noqa: E402

ROOT = gate.ROOT
FINGERPRINT_BASELINE = gate.FINGERPRINT_BASELINE
LEGACY_WHITELIST_BASELINE = gate.LEGACY_WHITELIST_BASELINE


def _log(level, msg):
    sys.stderr.write("[migrate_whitelist][%s] %s\n" % (level, msg))


def locate(rel, lineno):
    """在 HEAD 源码中定位 (rel, lineno) 对应的静默 handler 指纹；找不到返回 (None, reason)。"""
    src = gate.git_show("HEAD", rel)
    if src is None:
        return None, "file-missing"
    hs = extract_handlers(src, rel)
    # 1) 精确行号匹配
    for fp in hs:
        if fp.get("lineno") == lineno:
            return fp, "exact"
    # 2) 同函数内最近行号匹配（容忍少量漂移）
    tree = ast.parse(src)
    funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
             for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    qualname = _enclosing_func(funcs, lineno)
    cand = [fp for fp in hs if fp["qualname"] == qualname]
    if cand:
        cand.sort(key=lambda fp: abs(fp["lineno"] - lineno))
        return cand[0], "nearest-in-func"
    return None, "no-handler"


def main():
    legacy = sorted(gate.LOCATION_WHITELIST, key=lambda kv: (kv[0], kv[1]))
    fps = []
    legacy_rows = []
    problems = []
    for rel, lineno in legacy:
        fp, how = locate(rel, lineno)
        legacy_rows.append({"rel": rel, "lineno": lineno})
        if fp is None:
            problems.append((rel, lineno, how))
            _log("warning", "无法定位 %s:%d (%s)" % (rel, lineno, how))
            continue
        row = dict(fp)
        row["legacy_lineno"] = lineno
        row["match"] = how
        fps.append(row)
        _log("info", "OK %s:%d -> %s::%s[#%d]%s (%s)"
              % (rel, lineno, fp["path"], fp["qualname"], fp["nth"], fp["sha16"], how))

    if problems:
        _log("error", "%d 条无法定位，迁移中止（请修正 whitelist 路径/行号后重试）" % len(problems))
        for rel, lineno, how in problems:
            print("  MISS %s:%d %s" % (rel, lineno, how))
        sys.exit(1)

    with io.open(FINGERPRINT_BASELINE, "w", encoding="utf-8") as _f:
        json.dump(fps, _f, ensure_ascii=False, indent=2)
    with io.open(LEGACY_WHITELIST_BASELINE, "w", encoding="utf-8") as _f:
        json.dump(legacy_rows, _f, ensure_ascii=False, indent=2)

    uniq = set(handler_identity(fp) for fp in fps)
    if len(uniq) != len(fps):
        _log("warning", "存在重复指纹：条数=%d 唯一=%d（请人工核对）" % (len(fps), len(uniq)))
    _log("info", "迁移完成：%d 条 -> %s" % (len(fps), FINGERPRINT_BASELINE))
    _log("info", "双轨对照表 -> %s" % LEGACY_WHITELIST_BASELINE)
    print("MIGRATED=%d  UNIQUE=%d" % (len(fps), len(uniq)))


if __name__ == "__main__":
    main()
