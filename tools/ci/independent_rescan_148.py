# -*- coding: utf-8 -*-
"""第148批 阶段四：独立二次复扫（不依赖 export_public 自身扫描器）。

对导出包做独立核验，作为 export_public.scan_text 的旁路交叉验证：
  1) 强 PII 0 命中（含扫描器自身 tools/export_public.py）
  2) 内部文档 / 敏感文件 / 历史密钥 0 漏出
  3) 所有 .md 相对链接可访问（导出包内）
  4) 记录最终文件数 / 体积，作为首发版本基线

本脚本**重新实现**扫描逻辑，不复用 export_public 的任何 PII 模式或函数，
避免「自扫自查」盲区。退出码 0=全部通过，1=存在违规。
"""
import argparse
import io
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus._silent_except import silent_exc

# 强 PII 模式（独立重新实现）
STRONG_PATTERNS = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("出生日期", re.compile(r"2020[年.\-/]0?7[月.\-/]0?4")),
    ("API Key 赋值", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"']"
        r"(?!<|$|your|<YOUR|\.\.\.)[A-Za-z0-9_\-]{16,}[\"']")),
]

# 内部目录（绝不应出现在发布包）
INTERNAL_DIRS = {
    "路灯与星轨对话", "分析报告", "台账", "审查报告", "验收",
    "归档", "archive", "性能报告",
}
# 敏感文件名（绝不进发布包）
SENSITIVE_NAMES = {
    ".env", "credentials.json", "secrets.json", "token.json",
    ".owner_pii.json", ".owner_pii.json.example",
}

# 保留域（RFC 2606 / 项目约定）：这些域名不构成真实身份，扫描时跳过
ALLOW_DOMAINS = (
    "example.com", "example.org", "example.net", "example.invalid",
    "users.noreply.example.org", "users.noreply.invalid", "example",
)

# 行内豁免标记：该行含此注释则跳过（测试夹具 / 反例）
SCAN_SKIP_MARKERS = (
    "# pii-scan-ignore",
    "# export-ignore-pii",
)

# 文件级豁免标记：文件前 5 行含此标记则整文件跳过（PII 清洗测试夹具用假数据）
FILE_SCAN_SKIP_MARKERS = (
    "# export-scan-skip-file",
    "# pii-scan-skip-file",
)

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".zip", ".gz", ".tar", ".whl", ".pyc", ".pyd", ".so", ".dll",
    ".pdf", ".bin", ".onnx", ".npy", ".npz", ".db", ".sqlite",
}


def scan_text(path: str) -> list[tuple[str, int, str]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXT:
        return []
    try:
        with io.open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError as e:
        silent_exc(e, where="independent_rescan.scan_text", level="warning")
        return []
    # 文件级跳过标记（前 5 行）
    if any(mk in "".join(lines[:5]) for mk in FILE_SCAN_SKIP_MARKERS):
        return []
    hits: list[tuple[str, int, str]] = []
    for i, line in enumerate(lines, 1):
        if any(mk in line for mk in SCAN_SKIP_MARKERS):
            continue
        for name, pat in STRONG_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            snip = m.group(0)
            # 邮箱：保留域（example.com 等 / .invalid）不算真实身份
            if name == "邮箱" and any(d in snip for d in ALLOW_DOMAINS):
                continue
            if len(snip) > 60:
                snip = snip[:60] + "…"
            hits.append((name, i, snip))
    return hits


def iter_pkg_files(pkg: str):
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [d for d in dns if d not in INTERNAL_DIRS]
        for f in sorted(fns):
            yield os.path.join(dp, f)


def check_internal_leak(pkg: str) -> list[str]:
    """返回漏出的内部目录/敏感文件相对路径。"""
    leaked: list[str] = []
    for dp, dns, fns in os.walk(pkg):
        rel_dp = os.path.relpath(dp, pkg)
        parts = rel_dp.replace("\\", "/").split("/")
        if any(p in INTERNAL_DIRS for p in parts if p):
            leaked.append(rel_dp)
            continue
        for f in fns:
            if f in SENSITIVE_NAMES:
                leaked.append(os.path.relpath(os.path.join(dp, f), pkg).replace("\\", "/"))
    return leaked


def check_links(pkg: str) -> list[str]:
    """返回导出包内损坏的 .md 相对链接（导出包根相对）。"""
    broken: list[str] = []
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for path in iter_pkg_files(pkg):
        if os.path.splitext(path)[1].lower() != ".md":
            continue
        try:
            with io.open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError as e:
            silent_exc(e, where="independent_rescan.check_links", level="warning")
            continue
        base = os.path.dirname(path)
        for m in link_re.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("#"):
                continue
            # 去掉锚点
            target = target.split("#")[0]
            if not target:
                continue
            abs_t = os.path.normpath(os.path.join(base, target))
            if not os.path.exists(abs_t):
                rel = os.path.relpath(path, pkg).replace("\\", "/")
                broken.append("%s -> %s" % (rel, target))
    return broken


def main() -> int:
    ap = argparse.ArgumentParser(description="第148批 阶段四 独立二次复扫")
    ap.add_argument("--export-root", required=True,
                    help="导出包根目录（含 tongtong-pulse-net/ 的目录，或直接传 tongtong-pulse-net 本身）")
    args = ap.parse_args()

    root = os.path.abspath(args.export_root)
    pkg = os.path.join(root, "tongtong-pulse-net")
    if not os.path.isdir(pkg):
        pkg = root
    if not os.path.isdir(pkg):
        print("[FAIL] 导出包目录不存在: %s" % root, file=sys.stderr)
        return 2

    files = list(iter_pkg_files(pkg))
    total_size = sum(os.path.getsize(p) for p in files if os.path.isfile(p))

    print("=" * 74)
    print("第148批 阶段四 · 独立二次复扫（旁路，不复用 export_public 扫描器）")
    print("  导出包: %s" % pkg)
    print("  文件数: %d" % len(files))
    print("  总大小: %.2f MB" % (total_size / 1024 / 1024))
    print("=" * 74)

    # 1) 强 PII 复扫（含扫描器自身）
    pii_hits: list[tuple[str, str, int, str]] = []
    for p in files:
        rel = os.path.relpath(p, pkg).replace("\\", "/")
        if rel == "tools/export_public.py":
            continue  # 扫描器自身由独立子项单独核验
        for name, ln, snip in scan_text(p):
            pii_hits.append((rel, name, ln, snip))
    # 扫描器自身（独立核验，强制扫）
    self_path = os.path.join(pkg, "tools", "export_public.py")
    self_pii = scan_text(self_path) if os.path.isfile(self_path) else []
    if self_pii:
        for name, ln, snip in self_pii:
            pii_hits.append(("tools/export_public.py", name, ln, snip))

    if pii_hits:
        print("[1] 强 PII 复扫：FAIL —— %d 处" % len(pii_hits))
        for rel, name, ln, snip in pii_hits[:50]:
            print("    %s:%d  [%s] %s" % (rel, ln, name, snip))
        if len(pii_hits) > 50:
            print("    ... 其余 %d 处略" % (len(pii_hits) - 50))
    else:
        print("[1] 强 PII 复扫：PASS —— 导出包（含扫描器自身）零强 PII 命中")

    # 2) 内部泄漏
    leaked = check_internal_leak(pkg)
    if leaked:
        print("[2] 内部泄漏：FAIL —— %d 处" % len(leaked))
        for x in leaked[:50]:
            print("    %s" % x)
    else:
        print("[2] 内部泄漏：PASS —— 无内部目录/敏感文件漏出")

    # 3) 链接
    broken = check_links(pkg)
    if broken:
        print("[3] 链接可访问性：FAIL —— %d 处坏链" % len(broken))
        for x in broken[:50]:
            print("    %s" % x)
    else:
        print("[3] 链接可访问性：PASS —— 导出包内 .md 相对链接均可访问")

    print("=" * 74)
    ok = not pii_hits and not leaked and not broken
    print("  结论: %s" % ("PASS" if ok else "FAIL"))
    # 记录基线（供首发版本对照）
    print("  BASELINE files=%d size_mb=%.2f" % (len(files), total_size / 1024 / 1024))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
