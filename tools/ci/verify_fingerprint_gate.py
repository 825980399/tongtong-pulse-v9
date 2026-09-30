# -*- coding: utf-8 -*-
"""Dxxx-4 指纹化门禁 5 项验收（逻辑级，不改动真实仓库）。

① 改豁免 handler 体 -> 红（防借尸：指纹变化即不再是已知豁免）
② 豁免条目上方插 20 行 -> 绿（改造目的，旧行号表必红，指纹与行号无关）
③ 豁免函数内新增 except:pass -> 红
④ 删除一个被豁免 handler -> 红（白名单腐化须显式 shrink 提交，rot 检查）
⑤ 新增含 except:pass 的未跟踪文件 -> 红（全量集合兜底生效）

运行：python tools/ci/verify_fingerprint_gate.py
退出码：0=5 项全过，1=存在未通过项。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ci_common import extract_handlers, handler_identity  # noqa: E402


def file_added(old_src, new_src, known, rel="sample.py"):
    """复刻门禁 main 的 diff 指纹差集逻辑，返回新增静默 handler 数。"""
    so = set(handler_identity(x) for x in extract_handlers(old_src, rel))
    sn = set(handler_identity(x) for x in extract_handlers(new_src, rel))
    return len(sn - (so | known))


def rot_missing(known, full_set):
    """复刻 rot 检查：已知豁免中在 full_set 缺失的项。"""
    return [k for k in known if k not in full_set]


def untracked_bad(rel, src, known):
    """复刻未跟踪盲区检查：未跟踪文件中不在 known 的静默 handler。"""
    bad = []
    for fp in extract_handlers(src, rel):
        if handler_identity(fp) not in known:
            bad.append(handler_identity(fp))
    return bad


def _build_known(snippets):
    known = set()
    for rel, src in snippets:
        for fp in extract_handlers(src, rel):
            known.add(handler_identity(fp))
    return known


def main():
    failures = []

    # 一个「被豁免」的 handler（body=pass），其指纹进入 known
    EXEMPT = (
        "def foo():\n"
        "    try:\n"
        "        do()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    known = _build_known([("mod.py", EXEMPT)])

    # ① 改体（仍是静默，但体不同 -> 指纹变）-> 红
    changed_body = (
        "def foo():\n"
        "    try:\n"
        "        do()\n"
        "    except Exception:\n"
        "        return None\n"
    )
    if file_added(EXEMPT, changed_body, known) == 0:
        failures.append("① 改体应为红，实际绿")

    # ② 上方插 20 行（行号漂移，指纹不变）-> 绿
    head20 = "\n".join("# line %d" % i for i in range(20)) + "\n"
    if file_added(EXEMPT, head20 + EXEMPT, known) != 0:
        failures.append("② 上方插20行应为绿，实际红")

    # ③ 函数内新增 except:pass -> 红
    added_handler = (
        "def foo():\n"
        "    try:\n"
        "        do()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        do2()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    if file_added(EXEMPT, added_handler, known) == 0:
        failures.append("③ 新增except应为红，实际绿")

    # ④ 删 handler -> 红（rot）
    full_with = set(handler_identity(x) for x in extract_handlers(EXEMPT, "mod.py"))
    if rot_missing(known, full_with):
        failures.append("④ 前置假设错误：完整集合应含已知豁免")
    if not rot_missing(known, set()):
        failures.append("④ 删handler应为红（rot），实际绿")

    # ⑤ 新增未跟踪静默文件 -> 红
    new_file = (
        "def bar():\n"
        "    try:\n"
        "        do()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    if not untracked_bad("new_untracked.py", new_file, known):
        failures.append("⑤ 未跟踪新静默文件应为红，实际绿")

    if failures:
        for f in failures:
            print("FAIL: " + f)
        print("RESULT: %d 项验收未通过" % len(failures))
        sys.exit(1)
    print("RESULT: 5 项验收全部通过（①改体红 ②插行绿 ③新增红 ④删handler红 ⑤未跟踪红）")
    sys.exit(0)


if __name__ == "__main__":
    main()
