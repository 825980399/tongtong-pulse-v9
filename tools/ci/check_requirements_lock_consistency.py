# -*- coding: utf-8 -*-
"""requirements.txt <-> requirements.lock 一致性校验（第166批 刀4）。

校验：requirements.txt 中**属于 PyPI 可安装子集（即同时出现在 requirements-ci.txt）**
的有效依赖，必须全部能在 requirements.lock 中找到对应的 `==` 锁定条目；缺项即 FAIL。

豁免：不在 requirements-ci.txt 的包视为本地/非 PyPI 包（如 aibot 企业微信 SDK，
pip 上无此包，由部署环境单独提供），不要求锁定——与 requirements-ci.txt 的既有约定一致。

设计：纯标准库、零项目依赖（CI 早期即可独立运行）；无 silent except 块。
可选参数（便于负向测试）：argv[1]=requirements.txt，argv[2]=requirements.lock，
  argv[3]=requirements-ci.txt。

退出码：0=一致；1=不一致 / 文件缺失 / 解析错误。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))
REQ_TXT = os.path.join(PROJECT_ROOT, "requirements.txt")
REQ_LOCK = os.path.join(PROJECT_ROOT, "requirements.lock")
REQ_CI = os.path.join(PROJECT_ROOT, "requirements-ci.txt")

# 版本运算符 / 注释 / 标记分隔符（用于切出包名）
_SPEC_CHARS = set("=<>!~;#")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


def normalize(name: str) -> str:
    """PEP 503 归一：小写，[-_.] 折叠为单个 -。"""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def parse_requirement_names(path: str):
    """从 requirements 类文件抽出有效依赖的归一包名列表（保留重复以反映原始行）。"""
    names = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-") or line.startswith("git+") or "://" in line:
                continue
            cut = len(line)
            for i, ch in enumerate(line):
                if ch in _SPEC_CHARS:
                    cut = i
                    break
            name = line[:cut].strip()
            if not name or not _NAME_RE.match(name):
                continue
            names.append(normalize(name))
    return names


def parse_lock_names(path: str):
    """从 requirements.lock 抽出 `==` 锁定条目的归一包名集合。"""
    names = set()
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if "==" not in line:
                continue
            name = line.split("==", 1)[0].strip()
            if name:
                names.add(normalize(name))
    return names


def main(argv) -> int:
    req_path = argv[1] if len(argv) > 1 else REQ_TXT
    lock_path = argv[2] if len(argv) > 2 else REQ_LOCK
    ci_path = argv[3] if len(argv) > 3 else REQ_CI
    for p in (req_path, lock_path, ci_path):
        if not os.path.isfile(p):
            print(f"[FAIL] 文件缺失: {p}")
            return 1
    req_names = parse_requirement_names(req_path)
    ci_names = set(parse_requirement_names(ci_path))
    lock_names = parse_lock_names(lock_path)
    # PyPI 可安装子集：同时出现在 requirements-ci.txt 的依赖才要求锁定
    expected = [n for n in req_names if n in ci_names]
    skipped = sorted(set(n for n in req_names if n not in ci_names))
    missing = sorted(set(n for n in expected if n not in lock_names))
    if skipped:
        print(f"[INFO] 跳过 {len(skipped)} 个非 PyPI/本地包"
              f"（不在 requirements-ci.txt，无需锁定）：{', '.join(skipped)}")
    if missing:
        print(f"[FAIL] requirements.txt 的 PyPI 依赖中 {len(set(expected))} 个，"
              f"{len(missing)} 个未在 requirements.lock 锁定：")
        for m in missing:
            print(f"  - {m}")
        return 1
    print(f"[PASS] requirements.txt 的 {len(set(expected))} 个 PyPI 依赖全部在 "
          f"requirements.lock（{len(lock_names)} 个 == 锁定）覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
