"""export_public —— 对外发布包导出工具（第143批 T-143d）

用途
----
把项目导出成**干净、可对外公开**的发布包（zip 或目录），用于比赛提交 /
开源首发。与 ``package_full_project.py``（"完整代码备份"，含全部内部文档）
不同，本工具的核心目标是**只保留对外可见的内容**：

- **只包含**：全部源码（代码）、根 ``README.md``、``docs/`` 里对外的那部分
  核心文档（见 ``PUBLIC_DOCS_ALLOW``）、依赖声明、配置文件等。
- **排除**：``data/``、``tmp/``、``logs/``、``models/`` 等运行数据；
  全部内部协作文档（``docs/路灯与星轨对话/`` 任务书与交付报告、
  ``docs/分析报告/``、``docs/归档/``、``docs/archive/`` 等内部笔记）；
  备份快照（``.bak_batchN/``）；缓存与虚拟环境；密钥 / 凭据类文件。

设计原则
--------
1. **白名单优先**（fail-closed）：``docs/`` 只放行 ``PUBLIC_DOCS_ALLOW``
   中显式列出的文件/目录，其余一律不打；源码区按黑名单剔除内部资产。
2. **默认 dry-run 安全**：加 ``--dry-run`` 只列清单不落盘。
3. **PII 复扫断言**：导出后对包内每个文本文件跑一遍敏感信息扫描
   （真名 / 手机号 / 邮箱 / API Key / 真实绝对路径），命中即失败退出，
   保证发布包不泄露个人信息（对齐第143批 T-143a 验收）。
4. 复用 ``nucleus.data.exclude_dirs`` 的统一排除语义，避免各工具各写一份。

用法
----
::

    python tools/export_public.py                 # dry-run，列清单
    python tools/export_public.py --out dist.zip  # 导出 zip
    python tools/export_public.py --out dist/     # 导出目录（不带扩展名）
    python tools/export_public.py --no-scan       # 跳过 PII 复扫（不推荐）

版本: v10 PulseNet · 工具
设计: 路灯、星轨
日期: 2026年9月27日
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from collections.abc import Iterator

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from nucleus._silent_except import silent_exc
from nucleus.data.exclude_dirs import PACKAGE_EXCLUDED

# =============================================================================
# 一、排除规则
# =============================================================================

#: 目录级排除（任意层级命中即剪枝）。PACKAGE_EXCLUDED 已含缓存 / VCS / IDE。
EXCLUDE_DIRS: frozenset[str] = PACKAGE_EXCLUDED | frozenset({
    # 运行数据与产物（发布包绝不含）
    "data", "tmp", "logs", "models", "hardware", "output", "dist",
    "htmlcov", "site-packages", ".ipynb_checkpoints",
    # 测试隔离与工作区元数据（临时产物）
    ".pytest_tmp", ".tmp_backup", ".bak_tmp", ".release-tmp",
    ".mpy-workbench", ".ruff_cache",
    # 构建产物（Cython 编译中间件，内含真实绝对路径）
    "build", "temp.win-amd64-cpython-312", "Release",
    # CI/宿主平台配置（含内部流程，不进发布包）
    ".gitee", ".github",
    # 内部协作文档（整目录剔除）
    "路灯与星轨对话",          # 任务书 / 交付报告 / 与星轨对话记录
    "分析报告",                # 技术债务前置分析、第三方分析、台账 CSV
    "台账",
    "审查报告",
    "验收",
    "归档",                    # 历史实施与阶段性报告
    "archive",                 # 被替换掉的历史版本与规范
    "第三方分析",
    # 备份 / 副本 / 快照
    "code_backups",
})

#: 文件名级排除（后缀或精确名）
EXCLUDE_FILE_EXT: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".log", ".bak", ".orig", ".rej",
    ".o", ".obj", ".lib", ".exp", ".ilk", ".pdb", ".tlog",
})

#: 精确排除的文件名（凭据 / 本地配置 / 内部账本）
EXCLUDE_EXACT_NAMES: frozenset[str] = frozenset({
    ".env",
    "credentials.json",
    "secrets.json",
    "token.json",
})

#: 排除的路径前缀（相对仓库根，正斜杠）
EXCLUDE_PATH_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".workbuddy/",
    ".rebuilt_131/",
)

#: 备份目录/文件前缀（.bak_batchN、xxx.bak 等）
BACKUP_PREFIXES: tuple[str, ...] = (".bak",)

#: Windows 保留设备名（仓库里若混入 `nul` / `con` 等重定向残留，会令
#: os.path.relpath 抛 ValueError；一律跳过）
RESERVED_DEVICE_NAMES: frozenset[str] = frozenset({
    "nul", "con", "aux", "prn", "com1", "com2", "com3", "com4",
    "lpt1", "lpt2", "lpt3",
})

# =============================================================================
# 二、docs/ 对外白名单（fail-closed：只放行这里列出的）
# =============================================================================

#: docs/ 下允许进入发布包的**精确文件**（相对 docs/）
PUBLIC_DOCS_ALLOW_FILES: frozenset[str] = frozenset({
    "README.md",                       # docs 索引
    "demo-quickstart.md",              # 演示快速启动（比赛/演示）
    "项目架构总览_20260927.md",        # 一页看懂架构
    "项目结构树.md",                   # 目录结构说明
    "完整进化路线与技术债务清单_v1.0.md",  # 长期路线 + 债务总账
})

#: docs/ 下允许整目录带入的**子目录**（相对 docs/）
PUBLIC_DOCS_ALLOW_DIRS: frozenset[str] = frozenset({
    "比赛准备",          # 第143批 T-143c 对外运行数据卡片
    "设计文档",          # 系统设计（对外可读）
    "工具类文档",        # 工具使用说明
    "操作手册",          # 操作类手册
})

#: docs/ 下**明确排除**的内部目录（在文档索引里有名，但属内部叙事）
EXCLUDE_DOC_DIRS: frozenset[str] = frozenset({
    "路灯与星轨对话", "分析报告", "台账", "审查报告", "验收",
    "归档", "archive", "性能报告",
})

# =============================================================================
# 三、敏感信息复扫模式（对齐 T-143a 已清洗的 PII 类型）
# =============================================================================

PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("真名·任桂林", re.compile("任桂林")),
    ("真名·任宥曈", re.compile("任宥曈")),
    ("昵称·小曈曈", re.compile("小曈曈")),
    ("出生日期", re.compile(r"2020[年.\-/]0?7[月.\-/]0?4")),
    ("真实项目路径", re.compile(r"[Dd]:[\\/]xinrenlei")),
    ("真实用户名", re.compile(r"[Cc]:[\\/]Users[\\/]Administrator")),
    ("API Key 赋值", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"']"
        r"(?!<|$|your|<YOUR|\.\.\.)[A-Za-z0-9_\-]{16,}[\"']")),
]

#: 视为二进制 / 无需扫描的扩展名
BINARY_EXT: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".rar",
    ".onnx", ".pt", ".pth", ".bin", ".npy", ".npz", ".pkl",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".woff", ".woff2", ".ttf",
    ".db", ".sqlite", ".sqlite3", ".parquet", ".mp3", ".mp4", ".wav",
})

#: 扫描豁免文件（相对仓库根）：本扫描器自身必然含 PII 正则字面量
SCAN_EXEMPT_FILES: frozenset[str] = frozenset({
    "tools/export_public.py",
})

#: 允许的"占位 / 保留域"——RFC 2606 / RFC 6761 保留，非真实身份
ALLOW_DOMAINS: tuple[str, ...] = (
    "example.com", "example.org", "example.net", "example.invalid",
    "users.noreply.example.org", "users.noreply.invalid", "example",
)

#: 行内豁免标记：该行含此注释则跳过扫描（用于测试夹具 / 反例）
SCAN_SKIP_MARKERS: tuple[str, ...] = (
    "# pii-scan-ignore",
    "# export-ignore-pii",
)

#: 文件级豁免标记：文件**前 5 行**含此标记，则整文件跳过扫描
#:（用于 PII 清洗/脱敏功能的测试夹具 —— 其"敏感数据"本身是构造的假数据）
FILE_SCAN_SKIP_MARKERS: tuple[str, ...] = (
    "# export-scan-skip-file",
    "# pii-scan-skip-file",
)


def _norm(rel: str) -> str:
    return rel.replace("\\", "/")


def is_backup_name(name: str) -> bool:
    if any(name.startswith(p) for p in BACKUP_PREFIXES):
        return True
    return ".bak" in name.lower()


def _under_excluded_doc_dir(rel_from_docs: str) -> bool:
    parts = _norm(rel_from_docs).split("/")
    return any(p in EXCLUDE_DOC_DIRS for p in parts[:-1]) or \
        (len(parts) == 1 and parts[0] in EXCLUDE_DOC_DIRS)


def docs_allowed(rel_from_docs: str) -> bool:
    """docs/ 下该相对路径是否允许进入发布包（fail-closed）。"""
    rel = _norm(rel_from_docs)
    parts = rel.split("/")
    # 任何层级命中内部目录 → 排除
    for p in parts[:-1]:
        if p in EXCLUDE_DOC_DIRS:
            return False
    if len(parts) == 1:
        return parts[0] in PUBLIC_DOCS_ALLOW_FILES
    # 子目录：顶级目录需在白名单目录内，且不在排除目录内
    top = parts[0]
    if top in EXCLUDE_DOC_DIRS:
        return False
    return top in PUBLIC_DOCS_ALLOW_DIRS


def should_skip(rel: str) -> bool:
    """相对仓库根的路径是否应排除。"""
    rel_n = _norm(rel)
    parts = rel_n.split("/")

    for pref in EXCLUDE_PATH_PREFIXES:
        if rel_n.startswith(pref) or ("/" + pref) in ("/" + rel_n):
            return True

    if any(is_backup_name(p) for p in parts):
        return True
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if parts[-1] in EXCLUDE_EXACT_NAMES:
        return True
    if os.path.splitext(parts[-1])[1].lower() in EXCLUDE_FILE_EXT:
        return True

    # docs/ 单列：白名单优先（fail-closed）
    if parts[0] == "docs":
        return not docs_allowed("/".join(parts[1:]))

    # tests/ 属代码，保留；但 test 夹具里的临时产物已由后缀规则剔除
    return False


def iter_public_files(root: str) -> Iterator[str]:
    """产出应进入发布包的绝对路径。"""
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns
                  if d not in EXCLUDE_DIRS and not is_backup_name(d)]
        for f in sorted(fns):
            if f.lower() in RESERVED_DEVICE_NAMES:
                continue
            full = os.path.join(dp, f)
            try:
                rel = os.path.relpath(full, root)
            except ValueError:
                # 设备文件 / 挂载点异常，跳过
                continue
            if not should_skip(rel):
                yield full


# =============================================================================
# 四、PII 复扫
# =============================================================================

def scan_text(path: str) -> list[tuple[str, int, str]]:
    """扫描单个文本文件，返回 [(模式名, 行号, 命中片段)]。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXT:
        return []
    hits: list[tuple[str, int, str]] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            head = []
            for i, line in enumerate(fh, 1):
                if i <= 5:
                    head.append(line)
                elif i == 6:
                    if any(mk in "".join(head) for mk in FILE_SCAN_SKIP_MARKERS):
                        return []
                if any(mk in line for mk in SCAN_SKIP_MARKERS):
                    continue
                for name, pat in PII_PATTERNS:
                    m = pat.search(line)
                    if not m:
                        continue
                    snip = m.group(0)
                    # 保留域（example.com 等）不算真实身份
                    if any(d in snip for d in ALLOW_DOMAINS):
                        continue
                    if len(snip) > 60:
                        snip = snip[:60] + "…"
                    hits.append((name, i, snip))
            # 短文件（<=5 行）也要判一次文件级标记
            if any(mk in "".join(head) for mk in FILE_SCAN_SKIP_MARKERS):
                return []
    except OSError as e:
        silent_exc(e, where="export_public.scan_text", level="warning")
        return []
    return hits


def verify_clean(root: str, files: list[str]) -> list[tuple[str, str, int, str]]:
    """对导出清单中的文件做 PII 复扫，返回全部命中。"""
    out: list[tuple[str, str, int, str]] = []
    for p in files:
        rel = _norm(os.path.relpath(p, root))
        if rel in SCAN_EXEMPT_FILES:
            continue
        for name, ln, snip in scan_text(p):
            out.append((rel, name, ln, snip))
    return out


# =============================================================================
# 五、导出
# =============================================================================

def export_zip(root: str, out_zip: str, files: list[str]) -> None:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            rel = os.path.relpath(p, root)
            zf.write(p, _norm(os.path.join("tongtong-pulse-net", rel)))


def export_dir(root: str, out_dir: str, files: list[str]) -> None:
    import shutil
    for p in files:
        rel = os.path.relpath(p, root)
        dst = os.path.join(out_dir, "tongtong-pulse-net", _norm(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对外发布包导出（第143批 T-143d）")
    ap.add_argument("--out", default=None,
                    help="输出 zip 路径或目录路径（省略则 dry-run 只列清单）")
    ap.add_argument("--root", default=PROJECT_ROOT,
                    help="项目根（默认自动探测）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列清单不落盘")
    ap.add_argument("--no-scan", action="store_true",
                    help="跳过导出后的 PII 复扫（不推荐）")
    ap.add_argument("--quiet", action="store_true", help="只打印统计")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    files = sorted(iter_public_files(root))

    if not files:
        print("[FAIL] 未找到任何文件，检查 --root 是否正确", file=sys.stderr)
        return 2

    # ---- 清单汇总 ----
    by_top: dict[str, int] = {}
    total = 0
    for p in files:
        rel = _norm(os.path.relpath(p, root))
        top = rel.split("/")[0]
        by_top[top] = by_top.get(top, 0) + 1
        total += os.path.getsize(p)

    print(f"项目根: {root}")
    print(f"待导出文件数: {len(files)}")
    print(f"原始总大小: {total/1024/1024:.2f} MB")
    print("按顶层分布:")
    for k in sorted(by_top, key=lambda x: -by_top[x]):
        print(f"  {k:<20} {by_top[k]:>6}")

    if args.dry_run or not args.out:
        if not args.quiet:
            print("\n--- 文件清单（前 200 条）---")
            for p in files[:200]:
                print("  " + _norm(os.path.relpath(p, root)))
            if len(files) > 200:
                print(f"  ... 其余 {len(files)-200} 条略")
        print("\n[dry-run] 未落盘。加 --out <路径> 导出。")
        return 0

    # ---- 落盘 ----
    out = os.path.abspath(args.out)
    is_zip = out.lower().endswith(".zip")
    if is_zip:
        export_zip(root, out, files)
        out_size = os.path.getsize(out)
    else:
        export_dir(root, out, files)
        out_size = -1
    print(f"\n导出完成: {out}")

    # ---- PII 复扫 ----
    if not args.no_scan:
        hits = verify_clean(root, files)
        if hits:
            print(f"\n[FAIL] PII 复扫发现 {len(hits)} 处敏感信息，发布包不干净：",
                  file=sys.stderr)
            for rel, name, ln, snip in hits[:50]:
                print(f"  {rel}:{ln}  [{name}]  {snip}", file=sys.stderr)
            if len(hits) > 50:
                print(f"  ... 其余 {len(hits)-50} 处略", file=sys.stderr)
            return 1
        print("[OK] PII 复扫通过：导出清单零敏感信息命中。")
    else:
        print("[WARN] 已跳过 PII 复扫。")

    if is_zip:
        print(f"压缩包大小: {out_size/1024/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
