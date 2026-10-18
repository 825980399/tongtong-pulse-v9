# -*- coding: utf-8 -*-
"""B... 配置键空转检测（第288批新建、第290批刀6 扩族）。

扫描 config.py 全部模块级常量定义，检测「定义了但全仓零引用」的死键（空转键）。
★第290批刀6：明确覆盖 *_L2_* / SUBAGENT_* / PHASE18_* 三族（不再只查 ENABLE_*）。

★第300批刀8（去留定案）：与 tools/ci/check_write_only_gates.py 覆盖面比对后定案——
  两者**都查 D1 死键，但维度不同，勿长期并存做门禁**：
    * check_write_only_gates.py  = **CI 增量门禁**（base..target diff，只防新增、
      存量不清，含 D1 显式豁免表，退出码 1 阻断 CI）⇒ **保留为唯一 CI 门禁**。
    * 本件 check_config_unused_keys.py = **离线全量盘点工具**（扫 config.py 全部
      顶层常量，报告存量死键，纯只读不落盘）⇒ **定位为定期审计工具，不进 CI、
      不做阻断**，避免与 gates 并存造成「两份门禁、结论互相打架」。
  结论：CI 门禁以 check_write_only_gates.py 为准；本件仅作离线盘点，勿二者并行。

用法：
  python tools/check_config_unused_keys.py [--json] [--families L2,SUBAGENT,PHASE18,ENABLE]

退出码：0=无死键；1=发现死键；2=参数/扫描错误。
纯只读：不落盘、不修改任何文件。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.py")

# 扫描源码目录（排除 config.py 自身 + 备份/缓存/临时/虚拟环境）
SCAN_DIRS = ("nucleus", "organs", "functions", "base", "utils", "tools", "tests")
EXCLUDE_DIR_NAMES = {"__pycache__", ".git", "node_modules", ".venv", "venv"}

# ★第290批刀6：键族匹配（默认四族）
DEFAULT_FAMILIES = ("L2", "SUBAGENT", "PHASE18", "ENABLE")


def _skip_dir(name: str) -> bool:
    if name in EXCLUDE_DIR_NAMES:
        return True
    if name.startswith(".bak_batch") or name.startswith(".bak_tmp"):
        return True
    if name.startswith(".release-tmp") or name.startswith(".tmp_backup") or name.startswith(".pytest_tmp"):
        return True
    return False


def _iter_py_sources() -> list[tuple[str, str]]:
    """返回 [(相对路径, 源码文本)]，排除 config.py 自身与备份/缓存。"""
    out: list[tuple[str, str]] = []
    for _d in SCAN_DIRS:
        _root = os.path.join(ROOT, _d)
        if not os.path.isdir(_root):
            continue
        for _dp, _dn, _fn in os.walk(_root):
            _dn[:] = [x for x in _dn if not _skip_dir(x)]
            for _f in _fn:
                if not _f.endswith(".py"):
                    continue
                _p = os.path.join(_dp, _f)
                if os.path.abspath(_p) == os.path.abspath(CONFIG_PATH):
                    continue
                try:
                    _src = io.open(_p, "r", encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                out.append((os.path.relpath(_p, ROOT).replace("\\", "/"), _src))
    return out


def enumerate_config_keys(config_src: str) -> dict[str, int]:
    """返回 {KEY: lineno} —— config.py 中所有模块级常量赋值。"""
    keys: dict[str, int] = {}
    try:
        tree = ast.parse(config_src, filename=CONFIG_PATH)
    except SyntaxError:
        return keys
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                keys[t.id] = node.lineno
    return keys


def match_family(key: str, families: tuple[str, ...]) -> bool:
    for fam in families:
        if fam == "L2":
            if "_L2_" in key:
                return True
        elif fam == "SUBAGENT":
            if key.startswith("SUBAGENT_"):
                return True
        elif fam == "PHASE18":
            if key.startswith("PHASE18_"):
                return True
        elif fam == "ENABLE":
            if key.startswith("ENABLE_"):
                return True
    return False


def collect_references(src: str, targets: set[str]) -> set[str]:
    """返回该源码文本中引用的 config 键集合。

    双通道：AST Name（from config import X / 裸 X）+ AST Attribute(config.X)。
    兜底正则仅处理 AST 无法解析的损坏文件。
    """
    used: set[str] = set()
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in targets:
                used.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in targets:
                if isinstance(node.value, ast.Name) and node.value.id == "config":
                    used.add(node.attr)
    except SyntaxError:
        for k in targets:
            if re.search(r"\b" + re.escape(k) + r"\b", src):
                used.add(k)
    return used


def find_unused_keys(config_src: str, sources: list[tuple[str, str]],
                     families: tuple[str, ...]) -> list[tuple[str, int]]:
    all_keys = enumerate_config_keys(config_src)
    targets = {k for k in all_keys if match_family(k, families)}
    used: set[str] = set()
    for _path, _src in sources:
        used |= collect_references(_src, targets)
    return [(k, ln) for k, ln in sorted(all_keys.items(), key=lambda x: x[1])
            if k in targets and k not in used]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="config.py 死键（空转键）检测")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--families", default=",".join(DEFAULT_FAMILIES),
                        help="逗号分隔的键族（默认 L2,SUBAGENT,PHASE18,ENABLE）")
    args = parser.parse_args(argv)

    families = tuple(x.strip() for x in args.families.split(",") if x.strip())
    if not families:
        print("ERROR: --families 不能为空", file=sys.stderr)
        return 2

    if not os.path.isfile(CONFIG_PATH):
        print("ERROR: config.py 不存在: %s" % CONFIG_PATH, file=sys.stderr)
        return 2

    config_src = io.open(CONFIG_PATH, "r", encoding="utf-8", errors="replace").read()
    sources = _iter_py_sources()
    unused = find_unused_keys(config_src, sources, families)
    all_keys = enumerate_config_keys(config_src)
    total_targets = sum(1 for k in all_keys if match_family(k, families))

    result = {
        "config_path": CONFIG_PATH,
        "families": list(families),
        "scanned_files": len(sources),
        "target_keys": total_targets,
        "unused_count": len(unused),
        "unused": [{"key": k, "line": ln} for k, ln in unused],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("配置键空转检测（键族: %s）" % ", ".join(families))
        print("扫描文件: %d | 目标键: %d | 死键: %d" %
              (result["scanned_files"], result["target_keys"], result["unused_count"]))
        for k, ln in unused:
            print("  [死键] %s (config.py:%d)" % (k, ln))

    return 1 if unused else 0


if __name__ == "__main__":
    sys.exit(main())
