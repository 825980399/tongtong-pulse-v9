#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段A A5：PII 终扫三档（S1 包体 / S2 HEAD blob / S3 提交元数据与 remote URL）。

三档各自独立可验（rc 语义见下），输出**只掩码值**（PII 打码），不落真值：

- **S1 包体档**：复用 A3a 的 `verify_package()` 扫写好的发布包字节。
  语义：扫描对象 = 包体（唯一能证明"发布物干净"的口径）。
- **S2 HEAD blob 档**：扫 `git ls-tree -r HEAD` 列出的全部 blob **内容**
  （经 `git cat-file`），覆盖"已入库但不在包体内的文件"。
  语义：历史快照零真值。
- **S3 提交元数据档**：扫 `git log` 的提交消息/作者 + `git remote -v` 的 URL。
  语义：推送出去的历史与远端地址零真值。

rc 语义（可验）：
  0 = 该档零命中（干净）
  1 = 该档有命中（脏，值已掩码）
  2 = 该档执行异常（环境问题，非"脏"）
  单独跑某档：``python tools/ci/check_pii_final_scan.py --tier s1|s2|s3|all``

掩码：命中片段保留类型与行号，**值一律打码**（形如 ``1234********5678`` /
``<EMAIL>``），保证 CI 日志不回吐真值。
"""
import argparse
import contextlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RC_CLEAN = 0
RC_DIRTY = 1
RC_ERROR = 2

#: 提交元数据扫描：只看消息与身份，不看 diff
_META_MAX_CHARS = 4000


#: 内部域白名单（S2/S3 共用，避免同一概念两处定义必然漂移）。
#: `.local` 是本项目统一匿名身份域 `Tongtong Dev <dev@tongtong.local>`，非真实 PII；
#: `github.com`/`gitee.com` 是 git 托管域（SSH 的 `git@` 形态会被邮箱正则命中）。
_INTERNAL_ALLOW_DOMAINS = (
    ".local", ".internal", "github.com", "gitee.com", "tongtong.local",
)


def _mask_value(s: str) -> str:
    """把命中值打码：保留形态线索，绝不回吐真值。"""
    if not s:
        return ""
    s = s.strip()
    if "@" in s:  # 邮箱
        local, _, dom = s.partition("@")
        return f"{local[:2]}***@{dom}"
    if any(ch.isdigit() for ch in s):  # 含数字（手机/日期/生日）
        digits = "".join(ch for ch in s if ch.isdigit())
        keep = digits[:2] if len(digits) >= 2 else ""
        return f"{keep}****(len={len(digits)})"
    return f"***(len={len(s)})"


def _run_git(args, timeout=300):
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(["git"] + args, cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)


# ------------------------------------------------------------------ S1 包体档
def tier_s1(args) -> int:
    """S1：扫发布包体（复用 A3a verify_package）。"""
    pkg = args.package
    if not pkg or not os.path.isfile(pkg):
        print("[S1] 未指定或找不到包体（--package <zip>）；跳过。", file=sys.stderr)
        return RC_ERROR
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ep_s1", os.path.join(ROOT, "tools", "export_public.py"))
        ep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ep)
        hits = ep.verify_package(pkg, pkg.endswith(".zip"))
    except Exception as _e:
        print(f"[S1] 执行异常: {type(_e).__name__}", file=sys.stderr)
        return RC_ERROR
    if not hits:
        print("[S1] ✅ 包体 PII 终扫通过：零命中。")
        return RC_CLEAN
    print(f"[S1] ❌ 包体 PII 终扫命中 {len(hits)} 处（值已掩码）：", file=sys.stderr)
    for arc, name, ln, snip in hits[:50]:
        print(f"  {arc}:{ln}  [{name}]  {_mask_value(snip)}", file=sys.stderr)
    return RC_DIRTY


# ------------------------------------------------------------------ S2 HEAD blob
def _changed_files_in_push(args) -> set:
    """本批将推送的变更所触及的文件（相对仓库根，POSIX 风格）。

    取 `<remote_head>..HEAD` 的 diff；若该基点不存在，依次退回
    `HEAD~1..HEAD`、`HEAD~3..HEAD`。全部查不到时返回空集——此时全部按
    历史存量处理（宁可宽松不误阻断），并在报告中标明「未能判定变更范围」。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    for rng in (f"{args.remote_head}..HEAD", "HEAD~1..HEAD", "HEAD~3..HEAD"):
        try:
            r = subprocess.run(["git", "diff", "--name-only", rng],
                               cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               env=env, timeout=120)
        except Exception:
            continue
        if r.returncode == 0 and r.stdout.strip():
            return {ln.strip().replace("\\", "/")
                    for ln in r.stdout.split("\n") if ln.strip()}
    return set()


def tier_s2(args) -> int:
    """S2：扫 HEAD blob 内容，按「本次变更 / 历史存量」分档（路灯裁定选项2）。"""
    try:
        ep = _load_ep()
    except Exception as _e:
        print(f"[S2] 加载扫描器异常: {type(_e).__name__}", file=sys.stderr)
        return RC_ERROR
    try:
        r = _run_git(["ls-tree", "-r", "--name-only", "HEAD"])
        if r.returncode != 0:
            print("[S2] git ls-tree 失败。", file=sys.stderr)
            return RC_ERROR
        names = [n for n in r.stdout.split("\n") if n.strip()]
    except Exception as _e:
        print(f"[S2] git 调用异常: {type(_e).__name__}", file=sys.stderr)
        return RC_ERROR

    changed = _changed_files_in_push(args)
    _scope = ("变更范围已判定" if changed else
              "**未能判定变更范围**（无 upstream 对比基点），本次全部按历史存量处理")
    print(f"[S2] {_scope}；本次变更文件 {len(changed)} 个。")

    # ★二维分档（路灯 2026-10-05 18:34 裁定选项2 之延伸）：
    #   阻断判据 = 「本次变更」×「会进发布包」。
    #   内部协作目录（docs/微光、docs/路灯与星轨对话、docs/分析报告…）已被
    #   export_public.EXCLUDE_DIRS 排除出发布包（S1 零命中已证），其 PII 属
    #   内部运行资料 —— 命中只报告，不阻断，避免阻断他方在途文件（协作红线第1条）
    #   且这些文件不在我方处置范围。
    new_pkg_hits = 0   # 本次变更 × 进包 ⇒ 阻断
    new_int_hits = 0   # 本次变更 × 不进包 ⇒ 仅报告
    old_hits = 0       # 历史存量 ⇒ 仅报告
    for name in names:
        try:
            blob = _run_git(["cat-file", "-p", f"HEAD:{name}"], timeout=60)
        except Exception:
            continue
        if blob.returncode != 0:
            continue
        is_changed = name in changed
        # 该文件是否会进入发布包（复用导出器同一判据，保证与 S1 同口径）
        # 判不了就按「进包」从严（fail-closed）
        in_pkg = True
        with contextlib.suppress(Exception):
            in_pkg = not ep.should_skip(name)
        for ln, line in enumerate(blob.stdout.split("\n"), 1):
            for pname, pat in ep.all_pii_patterns():
                m = pat.search(line)
                if not m:
                    continue
                if any(d in m.group(0) for d in ep.ALLOW_DOMAINS):
                    continue
                if any(d in m.group(0) for d in _INTERNAL_ALLOW_DOMAINS):
                    continue  # 内部匿名域（dev@tongtong.local）非真实 PII
                if is_changed and in_pkg:
                    new_pkg_hits += 1
                    print(f"  [本次变更·进包] {name}:{ln}  [{pname}]  "
                          f"{_mask_value(m.group(0))}", file=sys.stderr)
                elif is_changed:
                    new_int_hits += 1
                    if new_int_hits <= 20:
                        print(f"  [本次变更·不进包·仅报告] {name}:{ln}  [{pname}]  "
                              f"{_mask_value(m.group(0))}", file=sys.stderr)
                else:
                    old_hits += 1
                    if old_hits <= 20:
                        print(f"  [历史存量·仅报告] {name}:{ln}  [{pname}]  "
                              f"{_mask_value(m.group(0))}", file=sys.stderr)

    if new_pkg_hits:
        print(f"[S2] ❌ 本次变更且**进发布包**的文件命中 {new_pkg_hits} 处"
              f"（值已掩码）—— 阻断。", file=sys.stderr)
        print(f"[S2]    另有：本次变更·不进包 {new_int_hits} 处、"
              f"历史存量 {old_hits} 处（均仅报告）。", file=sys.stderr)
        return RC_DIRTY
    if new_int_hits or old_hits:
        if args.strict_history:
            _tot = new_int_hits + old_hits
            print(f"[S2] ❌ --strict-history：非阻断档命中 {_tot} 处 —— 阻断。",
                  file=sys.stderr)
            return RC_DIRTY
        print("[S2] ✅ **进发布包的文件零命中**（阻断档通过）。")
        if new_int_hits:
            print(f"[S2] ⚠ 本次变更·不进包 {new_int_hits} 处（内部协作文档，"
                  f"已被 EXCLUDE_DIRS 排除）—— 仅报告。")
        if old_hits:
            print(f"[S2] ⚠ 历史存量 {old_hits} 处 —— 仅报告；"
                  f"清理需重写 git 历史（filter-repo/BFG，不可逆），已另立票。")
        return RC_CLEAN
    print(f"[S2] ✅ HEAD blob PII 终扫通过：{len(names)} 文件零命中。")
    return RC_CLEAN


# ------------------------------------------------------------------ S3 元数据/remote
# ★口径必须与 S1/S2（export_public）一致，否则 S3 单独一档比全项目严 ⇒ 误报满天飞：
#   · URL 路径里的仓库 ID（.../<ACCOUNT_ID>/...）会被当手机号
#   · SSH 协议的 git@github.com 会被当邮箱
#   · .local/.internal 等内部域邮箱应按保留域处理
_META_PATS = [
    ("疑似手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("疑似身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("疑似邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]

#: 内部/保留域（S3 口径 = export_public.ALLOW_DOMAINS + 共享内部域块）
_META_ALLOW_DOMAINS = _INTERNAL_ALLOW_DOMAINS

#: URL 内的仓库 ID 段（/<ACCOUNT_ID>/）——数字段非手机号
_URL_PATH_ID = re.compile(r"/\d{6,}/")

#: 整行像 commit SHA（40/64 位十六进制）时，行内数字段不是手机号
_SHA_LINE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.I)


def _meta_line_hits(line):
    """按与 S1/S2 同口径判定单行，产出 (name, 命中片段)。"""
    for name, pat in _META_PATS:
        for m in pat.finditer(line):
            frag = m.group(0)
            if any(d in frag for d in _META_ALLOW_DOMAINS):
                continue
            if name == "疑似手机号" and _URL_PATH_ID.search(line):
                continue
            if name == "疑似手机号" and _SHA_LINE.match(line.strip()):
                continue  # 整行是 commit SHA，其内数字段非手机号
            yield name, frag


def tier_s3(args) -> int:
    """S3：扫提交元数据（消息/作者）与 remote URL。"""
    total = 0
    try:
        r = _run_git(["log", f"--max-count={args.max_commits}", "--pretty=%H%n%an%n%ae%n%s%n%b"])
        if r.returncode != 0:
            print("[S3] git log 失败。", file=sys.stderr)
            return RC_ERROR
        meta = r.stdout[:_META_MAX_CHARS * 40]
    except Exception as _e:
        print(f"[S3] git log 异常: {type(_e).__name__}", file=sys.stderr)
        return RC_ERROR

    for i, line in enumerate(meta.split("\n"), 1):
        for name, frag in _meta_line_hits(line):
            total += 1
            if total <= 50:
                print(f"  [commit-meta] L{i} [{name}]  {_mask_value(frag)}",
                      file=sys.stderr)

    rem = ""
    with contextlib.suppress(Exception):
        rr = _run_git(["remote", "-v"])
        rem = rr.stdout
    for line in rem.split("\n"):
        if not line.strip():
            continue
        for name, frag in _meta_line_hits(line):
            total += 1
            print(f"  [remote-url] [{name}]  {_mask_value(frag)}", file=sys.stderr)

    if total == 0:
        print("[S3] ✅ 提交元数据与 remote URL PII 终扫通过：零命中。")
        return RC_CLEAN
    print(f"[S3] ❌ 提交元数据/remote PII 终扫命中 {total} 处（值已掩码）。",
          file=sys.stderr)
    return RC_DIRTY


_EP = None


def _load_ep():
    global _EP
    if _EP is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ep_s2", os.path.join(ROOT, "tools", "export_public.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _EP = m
    return _EP


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PII 终扫三档（S1 包体 / S2 HEAD blob / S3 元数据）")
    ap.add_argument("--tier", default="all", choices=["s1", "s2", "s3", "all"])
    ap.add_argument("--package", default="", help="S1：发布包路径（.zip 或目录）")
    ap.add_argument("--max-commits", type=int, default=200, help="S3：回看提交数")
    ap.add_argument("--remote-head", default="origin/HEAD",
                    help="S2：变更范围比对基点（默认 origin/HEAD）")
    ap.add_argument("--strict-history", action="store_true",
                    help="S2：历史存量命中也计入 rc=1（清史完成后启用）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args(argv)

    res = {}
    if args.tier in ("s1", "all"):
        res["s1"] = tier_s1(args)
    if args.tier in ("s2", "all"):
        res["s2"] = tier_s2(args)
    if args.tier in ("s3", "all"):
        res["s3"] = tier_s3(args)

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    # 任一档脏(1) ⇒ 总 rc=1；全为执行异常(2) ⇒ rc=2；否则 0
    codes = list(res.values())
    if RC_DIRTY in codes:
        return RC_DIRTY
    if codes and all(c == RC_ERROR for c in codes):
        return RC_ERROR
    return RC_CLEAN


if __name__ == "__main__":
    sys.exit(main())
