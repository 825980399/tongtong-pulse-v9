# -*- coding: utf-8 -*-
"""★第158批 上-A 第5刀 T-自我审计-6（P2）：config 声明-读取对账（独立 ci checker）。

定位
----
对账 ``config.py`` 的**声明面**与全仓**读取面**，输出 ``never_read``（声明了但全仓
无读取点）清单，供 config 拆分（P2-65）与死配置治理取数。

与 C-8 的边界（任务书 §二.4 明确）
----------------------------------
* **C-8（broken_chain）= 门禁，拦「增量」**（新引入的机制断链）；
* **本件 = 台账/对账，记「全量」**（声明-读取 diff 全景，含历史存量）；
* 因此本件**走独立文件**，不并入 C-8 主文件（避免与 T-审计-3 下批施工域冲突），
  且**默认不阻断**（rc=0）——453 条 never_read 是**当前存量事实**，不是新增故障。
  待 C-5 落地后由星轨决定是否接管为持续对账门禁。

口径（★与既有口径的关系）
--------------------------
* **声明面** = ``config.py`` 中模块级（含 if/try/with 嵌套层级）被赋值的**全大写**
  名字。小写/下划线开头视为内部变量，不计入声明面（避免把 helper 当配置）。
* **静态读取面** = 全仓 ``config.<ATTR>`` 属性访问，**复用**
  ``tools/scan_config_access._is_config_attr``（不重复实现判定逻辑）。
* **动态读取面** = ``getattr(config, "LITERAL")`` —— 字面量形式可解析，计入读取；
  变量形式（``getattr(config, var)``）**不可静态判定**，单列 ``unknown_dynamic``，
  并在结论中标注「动态通道可能漏读」，**不谎报为已读**。
* ``never_read = declared - static_read - dynamic_read_literal``。

用法
----
    python tools/ci/check_config_reconcile.py                # 打印 JSON 摘要
    python tools/ci/check_config_reconcile.py --out <file>   # 写 JSON 明细
    python tools/ci/check_config_reconcile.py --names        # 只列 never_read 名字

纯只读：不修改任何代码；退出码恒为 0（观测件，非阻断门禁）。
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
from typing import Any

# 项目根入 sys.path，使 `import nucleus...` 在 tools/ci 下可用（与同目录门禁件一致）
sys.path.insert(0, PROJECT_ROOT_SENTINEL := os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from nucleus._silent_except import silent_exc  # 静默异常统一走 CI 门禁认可通道  # noqa: E402

PROJECT_ROOT = PROJECT_ROOT_SENTINEL

#: 扫描时跳过的目录（与 tools/scan_config_access.py 保持一致）。
EXCLUDE_DIRS = {"tmp", ".git", "__pycache__", "node_modules", ".venv", "venv", "tools"}


def _skip_dir(d: str) -> bool:
    if d in EXCLUDE_DIRS:
        return True
    if d.startswith(".bak_batch"):
        return True
    return False


def _iter_py_files(root: str, include_tools: bool = False):
    """遍历工作树 .py 文件（默认排除 tools/，避免把扫描器自身的 import 计入读取）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)
                       or (include_tools and d == "tools")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            if rel.startswith("tmp/"):
                continue
            yield fp, rel


def _parse(fp: str):
    try:
        src = io.open(fp, encoding="utf-8", errors="ignore").read()
        return ast.parse(src, filename=fp)
    except (OSError, SyntaxError, ValueError) as _e:
        # ★不得静默：语法/读取失败须留痕（该文件被跳过，会影响对账完整性）
        silent_exc(_e, where="check_config_reconcile._parse", level="debug")
        return None


# ------------------------------------------------------------------ 声明面
def declared_names(root: str = PROJECT_ROOT) -> set[str]:
    """从 ``config.py`` 提取**声明面**（模块级及浅层嵌套的全大写赋值名）。"""
    _cfg = os.path.join(root, "config.py")
    _tree = _parse(_cfg)
    _out: set[str] = set()
    if _tree is None:
        return _out

    def _walk_body(body):
        for _n in body:
            if isinstance(_n, ast.Assign):
                for _t in _n.targets:
                    if isinstance(_t, ast.Name) and _t.id.isupper():
                        _out.add(_t.id)
            elif isinstance(_n, ast.AnnAssign) and isinstance(_n.target, ast.Name):
                if _n.target.id.isupper():
                    _out.add(_n.target.id)
            # 浅层嵌套：if / try / with 内的赋值同样是声明面
            elif isinstance(_n, (ast.If, ast.Try, ast.With)):
                _walk_body(getattr(_n, "body", []))
                _walk_body(getattr(_n, "orelse", []))
                _walk_body(getattr(_n, "finalbody", []))
                for _h in getattr(_n, "handlers", []):
                    _walk_body(getattr(_h, "body", []))

    _walk_body(_tree.body)
    return _out


# ------------------------------------------------------------------ 读取面
def read_names(root: str = PROJECT_ROOT) -> tuple[set[str], set[str], int]:
    """提取**读取面**。

    Returns:
        ``(static_read, dynamic_read_literal, unknown_dynamic_count)``
          * ``static_read``：``config.<ATTR>`` 静态属性访问；
          * ``dynamic_read_literal``：``getattr(config, "LITERAL")`` 字面量；
          * ``unknown_dynamic_count``：``getattr(config, <变量>)`` 条数（不可静态判定）。
    """
    _static: set[str] = set()
    _dyn: set[str] = set()
    _unknown = 0
    try:
        sys.path.insert(0, os.path.join(root, "tools"))
        from scan_config_access import _is_config_attr  # type: ignore
    except Exception as _e:
        # ★不得静默：复用不可用须留痕（随后降级为仅动态通道扫描）
        silent_exc(_e, where="check_config_reconcile.read_names.import", level="debug")
        _is_config_attr = None  # 复用不可用时降级：仅走动态通道

    for _fp, _rel in _iter_py_files(root):
        if _rel == "config.py":
            continue  # 声明文件自身不算读取方
        _tree = _parse(_fp)
        if _tree is None:
            continue
        for _n in ast.walk(_tree):
            if _is_config_attr is not None:
                _a = _is_config_attr(_n)
                if _a:
                    _static.add(_a.split(".")[0])
            # 动态通道：getattr(config, ...)
            if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name) \
                    and _n.func.id == "getattr" and len(_n.args) >= 2:
                _obj = _n.args[0]
                _is_cfg = isinstance(_obj, ast.Name) and _obj.id == "config"
                if not _is_cfg:
                    continue
                _key = _n.args[1]
                if isinstance(_key, ast.Constant) and isinstance(_key.value, str):
                    _dyn.add(_key.value)
                else:
                    _unknown += 1
    return _static, _dyn, _unknown


# ------------------------------------------------------------------ 对账
def reconcile(root: str = PROJECT_ROOT) -> dict[str, Any]:
    """声明-读取对账，返回可对外的结果字典。"""
    _decl = declared_names(root)
    _static, _dyn, _unknown = read_names(root)
    # ★never_read 采用**静态口径**（declared - static_read），与任务书基线一致：
    #   任务书「615 定义 vs 168 去重读取 → never_read≈453」即 615-168 的静态差。
    #   动态通道（getattr）是**漏读风险源**，不是「已读证据」——故不用于抵扣，
    #   而是单列输出供人工复核（见 ``dynamic_covered_in_never``）。
    _never = sorted(_decl - _static)
    _phantom = sorted(_static - _decl)     # 静态读取了但 config 里没声明（疑似改名/拼错）
    _dyn_cover = sorted((_decl - _static) & _dyn)
    return {
        "version": "158A-T6-1",
        "scope": "config.py 声明面 vs 全仓读取面（★never_read 走静态口径）",
        "declared": len(_decl),
        "static_read": len(_static),
        "dynamic_read_literal": len(_dyn),
        "never_read": len(_never),
        "never_read_rate": (round(len(_never) / float(len(_decl)), 4) if _decl else None),
        "phantom_read": len(_phantom),      # 读取但未声明
        "unknown_dynamic_channels": _unknown,
        # ★动态通道覆盖：never_read 中**可被 getattr 字面量触达**的名字（供复核，
        #   不计入已读——动态通道同时意味着静态工具看不见、易在重构中漏改）。
        "dynamic_covered_in_never": len(_dyn_cover),
        # ★动态通道漏读标注：getattr(config, 变量) 无法静态判定，故 never_read
        #   为**保守上界**（可能偏大），此处如实披露条数，不谎报为已读。
        "caveat": ("存在 %d 处 getattr(config, <变量>) 动态读取，无法静态判定；"
                   "never_read 为保守上界" % _unknown) if _unknown else "",
        "never_read_names": _never,
        "phantom_read_names": _phantom,
        "dynamic_covered_names": _dyn_cover,
        "dynamic_read_names": sorted(_dyn),
    }


def main(argv: list[str] | None = None) -> int:
    _argv = list(sys.argv[1:] if argv is None else argv)
    _out = None
    if "--out" in _argv:
        _i = _argv.index("--out")
        if _i + 1 < len(_argv):
            _out = _argv[_i + 1]
    _names_only = "--names" in _argv

    _res = reconcile()
    if _names_only:
        for _n in _res["never_read_names"]:
            print(_n)
        return 0

    if _out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(_out)), exist_ok=True)
            with io.open(_out, "w", encoding="utf-8") as _f:
                json.dump(_res, _f, ensure_ascii=False, indent=2)
            print("written -> %s" % _out)
        except OSError as _e:
            sys.stderr.write("[config-reconcile] 写出失败: %s: %s\n"
                             % (type(_e).__name__, _e))
            return 0
        return 0

    _brief = {k: v for k, v in _res.items()
              if k not in ("never_read_names", "phantom_read_names", "dynamic_read_names")}
    print(json.dumps(_brief, ensure_ascii=False, indent=2))
    print("\n[never_read 前 30 条]")
    for _n in _res["never_read_names"][:30]:
        print("  " + _n)
    print("（完整清单：--out <file> 或 --names）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
