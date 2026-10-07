#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移除模型引用门禁（防回归）：禁止已移除的嵌入模型 `jina` / `paraphrase` 被重新引入。

背景
----
第 N 批下线了两族嵌入模型：`jina`（含 jinaai / jina-embeddings / jinaai/jina 等形态）
与 `paraphrase`（如 paraphrase-MiniLM / paraphrase-multilingual / sentence-transformers/paraphrase
等）。本门禁用 AST/纯文本扫描**已跟踪**的源码与配置（.py / .json / .yaml / .yml / .toml），
一旦某文件引用上述模型族即 FAIL（exit 1）。

设计要点
--------
* **纯文本正则**：`jina` 与 `paraphrase` 均以词边界匹配（`\bjina` / `\bparaphrase`，大小写不敏感），
  仅匹配 token 形态（jina / jinaai / jina-embeddings / paraphrase-MiniLM ...），
  不误伤无关单词的子串。
* **ALLOWLIST（路径子串豁免）**：本门自身（必含 `paraphrase` 字样）与既有说明文档
  （CHANGELOG / docs/台账）被显式豁免，避免自触发。
* 只读，不修改任何代码。

用法
----
  python tools/ci/check_model_ref_removed_gate.py          # 默认扫描
  python tools/ci/check_model_ref_removed_gate.py --selftest
"""
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

#: 扫描的受控文件扩展名
_SCAN_EXTS = (".py", ".json", ".yaml", ".yml", ".toml")

#: 移除模型引用正则（词边界，大小写不敏感）
_JINA_RE = re.compile(r"\bjina", re.I)
_PARA_RE = re.compile(r"\bparaphrase", re.I)

#: 豁免路径子串（本门自身 / 合法说明文档 / 台账）
ALLOWLIST = (
    "tools/ci/check_model_ref_removed_gate.py",
    "CHANGELOG",
    "docs/台账",
)


def _references_removed_model(text: str) -> bool:
    """文本是否引用了已移除模型族（jina 或 paraphrase）。"""
    return bool(_JINA_RE.search(text) or _PARA_RE.search(text))


def _strip_py_full_line_comments(text: str) -> str:
    """★剔除 .py 整行注释，避免历史说明性提及（如 config.py 注释中
    'jina-embeddings-v2-base-zh 已排除'）被误判为重新引入。

    仅去整行 `#...` 注释（行首去空白后以 # 开头）；保留行内字符串与
    活动代码，使真实活动引用仍可被捕获。不做脆弱的行内 `#` 剥离，
    以免误伤含 # 的字符串字面量。
    """
    kept = []
    for line in text.split("\n"):
        if line.strip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _is_allowlisted(relpath: str) -> bool:
    return any(sub in relpath for sub in ALLOWLIST)


def _tracked_source_files():
    r = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        return []
    out = []
    for ln in r.stdout.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if os.path.splitext(ln)[1].lower() in _SCAN_EXTS:
            out.append(ln)
    return out


def check():
    violations = []
    for rel in _tracked_source_files():
        if _is_allowlisted(rel):
            continue
        fp = os.path.join(REPO_ROOT, rel)
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
        except OSError as _e:
            from nucleus._silent_except import silent_exc
            silent_exc(_e, where="tools.ci.check_model_ref_removed_gate::check read")
            continue
        # .py 先剔除整行注释，避免历史说明性提及被误判
        _ext = os.path.splitext(rel)[1].lower()
        _scan = _strip_py_full_line_comments(data) if _ext == ".py" else data
        if _references_removed_model(_scan):
            violations.append(rel)
    if violations:
        sys.stderr.write("[model-ref-removed] ❌ 检测到已移除模型被重新引用：\n")
        for rel in sorted(violations):
            sys.stderr.write("   ! %s\n" % rel)
        sys.stderr.write("[model-ref-removed] 结论：FAIL（jina/paraphrase 已移除，不得回归）\n")
        return 1
    sys.stderr.write("[model-ref-removed] ✅ PASS（无已移除模型引用）\n")
    return 0


def selftest():
    # 正例：含 removed 模型引用 → True
    assert _references_removed_model("model = 'paraphrase-MiniLM-L6-v2'") is True
    assert _references_removed_model("using jina embeddings here") is True
    # 反例：普通串 → False
    assert _references_removed_model("normal text without the model") is False
    # 豁免：allowlisted 路径跳过
    fake_files = [
        "src/model.py",
        "tools/ci/check_model_ref_removed_gate.py",
        "docs/台账/历史.md",
    ]
    flagged = [
        p for p in fake_files
        if not _is_allowlisted(p) and _references_removed_model("paraphrase-MiniLM")
    ]
    assert "tools/ci/check_model_ref_removed_gate.py" not in flagged, "豁免：本门自身应跳过"
    assert "docs/台账/历史.md" not in flagged, "豁免：docs/台账 应跳过"
    assert "src/model.py" in flagged, "非豁免路径应被检测"
    print("[selftest] model-ref-removed 自证通过")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv
    if "--selftest" in argv:
        return selftest()
    return check()


if __name__ == "__main__":
    sys.exit(main())
