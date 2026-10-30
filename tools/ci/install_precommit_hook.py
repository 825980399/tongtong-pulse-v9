#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第162批刀13/14/15 · 门禁 hook 安装源（入库，供星轨复核 + 重装 + 一致性校验）。

本脚本把 tools/ci/hooks/ 下的 hook 模板安装到 .git/hooks/：
  - pre-commit   （含刀13 GATE_PATH_SPLIT / 刀14 GATE_PARALLEL / 404刀3 七层自检）
  - commit-msg   （含刀15 重锚分类 classify_reanchor 调用）

用法：
    # 安装/同步（默认行为，幂等）
    "D:/Program Files/Python312/python.exe" tools/ci/install_precommit_hook.py

    # 只校验「模板 ≡ 实装」，不写任何文件
    "D:/Program Files/Python312/python.exe" tools/ci/install_precommit_hook.py --check

    # 校验并打印差异（unified diff，定位"模板落后"具体落后在哪一段）
    "D:/Program Files/Python312/python.exe" tools/ci/install_precommit_hook.py --check --diff

    # 只处理指定 hook
    "D:/Program Files/Python312/python.exe" tools/ci/install_precommit_hook.py --check pre-commit

退出码：
    0  一致 / 安装成功
    1  存在不一致（--check 模式）/ 安装失败
    2  环境错误（hooks 目录不存在、模板缺失）

★为什么要有 --check（405 刀5 的由来，别把这段删了）：
    404 刀3 往**本机** .git/hooks/pre-commit 加了 30 行「七层自检」，却没同步回
    tools/ci/hooks/pre-commit。旧版本脚本只会单向覆盖，且没人会无故重跑它，
    于是这一步**五天没进版本库**（模板 9939 B vs 实装 11434 B），期间那段代码里
    还写着「本 hook 未纳入版本管理」这句错话自我麻醉。
    ⇒ 单向安装不足以发现"实装比模板新"，必须有一个只读比对入口。
      --check 就是它：不一致即非零退出，能挂进任何例行检查。

说明：
  - .git/hooks/ 下的 hook 本体不入库（git 默认忽略），故以本脚本 + hooks/ 模板为
    「安装源」；模板本身在版本库里（见 c4adee56 / 7ad8d6b4 / 1071e384 三笔历史）。
  - 每次修改 tools/ci/hooks/* 模板后，重跑本脚本即可同步本地 hook；
  - 改完模板**务必**跑一次 --check 确认「模板 ≡ 实装」，否则就是你只改了一边。
  - 回退开关为环境变量：GATE_PATH_SPLIT=0 / GATE_PARALLEL=0 / GATE_COLLECT_LEVEL=full。
"""
import difflib
import os
import shutil
import stat
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(SCRIPT_DIR, "hooks")

#: 安装目标目录：main() 里按仓库根定位后赋值（worktree 下与 <root>/.git/hooks 不同）。
#: 保留旧的 HOOKS_DIR 名字，既有调用方（若有）无需改动。
HOOKS_DIR = ""

HOOKS = ("pre-commit", "commit-msg")


def _log(msg):
    """CI 件的最小留痕口（函数名 `_log` 在 cw2 门禁的 LOG_FUNCS 白名单内）。"""
    print(msg, file=sys.stderr)


def _root():
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()


def _hooks_dir(root):
    """定位 hooks 目录；worktree 下 `.git` 是文件，回退 `git rev-parse --git-path hooks`。"""
    d = os.path.join(root, ".git", "hooks")
    if os.path.isdir(d):
        return d
    try:
        p = subprocess.check_output(
            ["git", "rev-parse", "--git-path", "hooks"], cwd=root, text=True
        ).strip()
    except Exception as e:
        _log("[install] git rev-parse --git-path hooks 失败：%s" % (e,))
        return d
    if not p:
        return d
    if not os.path.isabs(p):
        p = os.path.join(root, p)
    return p if os.path.isdir(p) else d


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _shape(data):
    """(行数, 字节数) —— 用于人读的不一致摘要。"""
    return data.count(b"\n"), len(data)


def _diff_text(a, b, name):
    """两段文本的 unified diff 行列表（按行比对，忽略行尾差异带来的噪声）。"""
    la = a.decode("utf-8", "replace").splitlines(keepends=True)
    lb = b.decode("utf-8", "replace").splitlines(keepends=True)
    return list(difflib.unified_diff(
        la, lb, fromfile="模板 tools/ci/hooks/%s" % name,
        tofile="实装 .git/hooks/%s" % name, n=2))


def _compare(name, hooks_dir):
    """比对单个 hook。返回 (state, detail_lines, diff_lines)。

    state ∈ {"same", "missing", "stale", "no_template"}
    """
    src = os.path.join(TEMPLATE_DIR, name)
    dst = os.path.join(hooks_dir, name)
    if not os.path.isfile(src):
        return "no_template", ["模板缺失：%s" % src], []
    if not os.path.isfile(dst):
        return "missing", ["实装缺失：%s（跑本脚本不带参数即可安装）" % dst], []
    a = _read_bytes(src)
    b = _read_bytes(dst)
    if a == b:
        lines, size = _shape(a)
        return "same", ["%d 行 / %d B" % (lines, size)], []
    la, sa = _shape(a)
    lb, sb = _shape(b)
    detail = [
        "模板 %d 行/%d B  vs  实装 %d 行/%d B  （差 %+d 行 / %+d B）"
        % (la, sa, lb, sb, lb - la, sb - sa)
    ]
    if b"\r\n" in a:
        detail.append("⚠ 模板含 CRLF（hook 模板应保持纯 LF）")
    return "stale", detail, _diff_text(a, b, name)


def _install(name):
    src = os.path.join(TEMPLATE_DIR, name)
    dst = os.path.join(HOOKS_DIR, name)
    if not os.path.isfile(src):
        _log("[install] 模板缺失：%s" % src)
        return False
    before = _read_bytes(dst) if os.path.isfile(dst) else None
    after = _read_bytes(src)
    if before == after:
        print("[install] %s 已是最新（模板 ≡ 实装），跳过写入" % name)
        return True
    shutil.copyfile(src, dst)
    # 确保可执行（POSIX 权限位；Windows 下 git 以 sh 调用，权限不影响）
    try:
        st = os.stat(dst)
        os.chmod(dst, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        _log("[install] chmod 跳过（%s）" % (e,))
    print("[install] %s -> %s%s" % (name, dst, "（已更新）" if before else "（新装）"))
    return True


def _parse_args(argv):
    """极简参数解析：--check / --diff / 位置参数为要处理的 hook 名。"""
    check = False
    show_diff = False
    names = []
    for a in argv:
        if a == "--check":
            check = True
        elif a == "--diff":
            show_diff = True
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif a.startswith("-"):
            _log("[install] 未知选项：%s（-h 看用法）" % a)
            sys.exit(2)
        else:
            names.append(a)
    unknown = [n for n in names if n not in HOOKS]
    if unknown:
        _log("[install] 未知 hook：%s（可选：%s）" % (", ".join(unknown), ", ".join(HOOKS)))
        sys.exit(2)
    if show_diff and not check:
        _log("[install] --diff 需与 --check 一起用（安装模式不打印差异）")
        sys.exit(2)
    return check, show_diff, (names or list(HOOKS))


def _do_check(names, show_diff):
    bad = 0
    for h in names:
        state, detail, diff = _compare(h, HOOKS_DIR)
        label = {"same": "SAME ", "stale": "STALE", "missing": "MISS ",
                 "no_template": "NOTPL"}[state]
        for i, d in enumerate(detail):
            prefix = "[check] %-11s %s  " % (h, label) if i == 0 else "[check] %-11s %s  " % ("", "     ")
            print(prefix + d)
        if state != "same":
            bad += 1
            if show_diff and diff:
                for ln in diff:
                    print("        " + ln.rstrip("\n"))
    if bad:
        print("[check] 结论: FAIL —— %d/%d 个 hook 模板与实装不一致。"
              % (bad, len(names)))
        print("[check] 修复：跑本脚本不带参数（python tools/ci/install_precommit_hook.py）同步实装；"
              "若实装才是对的，请先把实装内容搬回 tools/ci/hooks/ 模板再重装。")
        return 1
    print("[check] 结论: PASS —— %d/%d 个 hook 模板 ≡ 实装。" % (len(names), len(names)))
    return 0


def _do_install(names):
    ok = True
    for h in names:
        ok = _install(h) and ok
    if ok:
        print("[install] 完成。重装验证：git commit 任一改动触发 hook；")
        print("         文档专用提交应见 'GATE_PATH_SPLIT：仅文档路径，跳过代码/collect 门禁'；")
        print("         含 .py 提交应见 'GATE_PARALLEL：5 静态门并发执行'；")
        print("         一致性复核：python tools/ci/install_precommit_hook.py --check")
    return 0 if ok else 1


def main(argv=None):
    global HOOKS_DIR
    check, show_diff, names = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        root = _root()
    except Exception as e:
        _log("[install] 不在 git 仓库内（git rev-parse 失败）：%s" % (e,))
        return 2
    HOOKS_DIR = _hooks_dir(root)
    if not os.path.isdir(HOOKS_DIR):
        _log("[install] hooks 目录不存在：%s" % HOOKS_DIR)
        return 2
    return _do_check(names, show_diff) if check else _do_install(names)


if __name__ == "__main__":
    sys.exit(main())
