"""quality_audit —— 验收测量脚本化（第五批 任务3，P1-3）

统一、独立、可复现的验收测量工具。解决「带结果上限的 grep」「只看组件不测链路」
导致的验收口径失真问题。

包含检查项：
  --except-check  裸 except（except Exception: / except:）的准确逐文件计数
  --header-check  命中「v10 PulseNet」头部（首行 docstring 口径）的文件数
  --exp-pollution 经验库污染率 / 最高频重复次数 / 唯一内容数
  --bak-count     源码树中 .bak 备份文件数量
  --ruff-check    ruff 检查：F 类错误数 + 总问题数
  --all           运行全部检查，输出 JSON

通用参数：
  --threshold NAME=VALUE   设置某项通过阈值（可重复）
  --output PATH            将 JSON 结果写入文件
  --project-root PATH     指定被扫描的项目根（默认 tools/ 的上一级）

红线（与全项目一致）：
  - 本脚本只读取、统计、输出 JSON，**绝不修改任何生产数据**；
  - 不引入裸 except Exception:（只记录异常类型）。
"""
from __future__ import annotations
from nucleus.data.exclude_dirs import QUALITY_AUDIT_EXCLUDED  # ★第55批 T4（统一排除清单）

import argparse
import json
import os
import re
import subprocess
import sys
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# 扫描时跳过的目录（测试隔离脚本、依赖、版本控制、工作区元数据）


def _m55_unified_excludes() -> bool:
    """★第55批 T4 灰度开关：关掉可回退到第55批前的各自定义清单。"""
    try:
        import config as _m55_cfg

        return bool(getattr(_m55_cfg, "ENABLE_EXCLUDE_DIRS_UNIFIED", True))
    except Exception as e:
        silent_exc(e, where="tools.quality_audit::_m55_unified_excludes L47")
        return True

_M55_LEGACY__SKIP_DIRS = {".git", "__pycache__", ".workbuddy", "tmp", "node_modules",
              ".venv", "venv", "site-packages"}

#: ★第55批 T4：统一清单（基础项来自 exclude_dirs，新增目录只需改一处）
_SKIP_DIRS: frozenset[str] = (
    QUALITY_AUDIT_EXCLUDED if _m55_unified_excludes() else _M55_LEGACY__SKIP_DIRS
)

# 裸 except 两种形态：except: 与 except Exception:（无 as 绑定）
_RE_BARE_EXCEPT = re.compile(r"^\s*except\s*(Exception)?\s*:")

# v10 头部口径：模块 docstring 含 "v10 pulsenet"（不区分大小写）
_RE_V10_HEADER = re.compile(r"v10\s+pulsenet", re.IGNORECASE)

# 经验库去重聚类阈值（与 tools/dedup_experience_pool.py 保持一致）
_MIN_SUMMARY_LEN = 8


# ============ 各检查项（纯函数，可单测、不碰生产数据） ============

def _iter_py_files(roots: list[str]):
    """遍历 roots 下所有 .py 文件，跳过 _SKIP_DIRS。生成 (abspath, relpath)。"""
    for _root in roots:
        for _dir, _dirs, _files in os.walk(_root):
            _dirs[:] = [d for d in _dirs if d not in _SKIP_DIRS]
            for _f in _files:
                if _f.endswith(".py"):
                    _p = os.path.join(_dir, _f)
                    yield _p, os.path.relpath(_p, _root)


def scan_bare_except(roots: list[str]) -> tuple[int, list[str]]:
    """逐文件扫描裸 except（except: / except Exception:），返回 (数量, 明细)。"""
    _count = 0
    _details: list[str] = []
    for _p, _rel in _iter_py_files(roots):
        try:
            with open(_p, encoding="utf-8", errors="replace") as _f:
                for _i, _line in enumerate(_f, 1):
                    if _RE_BARE_EXCEPT.match(_line):
                        _count += 1
                        _snip = _line.strip()[:80]
                        _details.append(f"{_rel}:{_i}: {_snip}")
        except Exception as _e:  # 单文件读取失败不应中断整体扫描
            _details.append(f"{_rel}: 读取失败({type(_e).__name__})")
    return _count, _details


def count_v10_header(roots: list[str]) -> tuple[int, list[str]]:
    """统计模块 docstring 含 "v10 PulseNet" 的文件数，返回 (数量, 明细)。

    用 ast 取模块 docstring（可能是多行），避免「首行仅写 \"\"\" 而 v10 在次行」
    导致漏计。
    """
    import ast
    _count = 0
    _details: list[str] = []
    for _p, _rel in _iter_py_files(roots):
        try:
            with open(_p, encoding="utf-8", errors="replace") as _f:
                _src = _f.read()
            _tree = ast.parse(_src)
            _doc = ast.get_docstring(_tree) or ""
            if _RE_V10_HEADER.search(_doc):
                _count += 1
                _details.append(_rel)
        except SyntaxError:
            _details.append(f"{_rel}: 语法错误跳过")
        except Exception as _e:
            _details.append(f"{_rel}: 读取失败({type(_e).__name__})")
    return _count, _details


def compute_exp_pollution(pool_path: str) -> dict:
    """统计经验库污染情况。返回结构化字典（不修改文件）。"""
    if not os.path.exists(pool_path):
        return {"exists": False, "path": pool_path, "total": 0,
                "unique_content": 0, "top_freq": 0, "duplicate_count": 0,
                "pollution_rate": 0.0}
    _d = safe_read_json(pool_path, default={})
    _items = _d.get("experiences", []) if isinstance(_d, dict) else _d

    _count: dict[str, int] = {}
    for _it in _items:
        if not isinstance(_it, dict):
            continue
        _s = (_it.get("summary") or "").strip()
        if len(_s) < _MIN_SUMMARY_LEN:
            continue
        _count[_s] = _count.get(_s, 0) + 1

    _total = len(_items)
    _unique = len(_count)
    _nonempty_total = sum(_count.values())
    _duplicate = max(0, _nonempty_total - _unique)
    _top_freq = max(_count.values()) if _count else 0
    _rate = (_duplicate / _total) if _total else 0.0
    return {
        "exists": True,
        "path": pool_path,
        "total": _total,
        "unique_content": _unique,
        "top_freq": _top_freq,
        "duplicate_count": _duplicate,
        "pollution_rate": round(_rate, 4),
    }


def count_bak(roots: list[str]) -> tuple[int, list[str]]:
    """统计源码树中 .bak* 备份文件数量，返回 (数量, 明细)。"""
    _count = 0
    _details: list[str] = []
    for _root in roots:
        for _dir, _dirs, _files in os.walk(_root):
            _dirs[:] = [d for d in _dirs if d not in _SKIP_DIRS]
            for _f in _files:
                if ".bak" in _f:
                    _count += 1
                    _details.append(os.path.relpath(os.path.join(_dir, _f), _root))
    return _count, _details


def _find_ruff() -> str | None:
    """定位 ruff 可执行文件。"""
    import shutil
    _which = shutil.which("ruff")
    if _which:
        return _which
    for _cand in (
        r"D:\Program Files\Python312\Scripts\ruff",
        "/d/Program Files/Python312/Scripts/ruff",
        os.path.join(_PROJ, "node_modules", ".bin", "ruff"),
    ):
        if os.path.exists(_cand):
            return _cand
    return None


def run_ruff_check(ruff_bin: str, roots: list[str]) -> dict:
    """运行 ruff，返回 F 类错误数与总问题数。不修改任何文件。"""
    _cmd_base = [ruff_bin, "check", "--output-format=concise"]
    try:
        _all = subprocess.run(_cmd_base + roots, capture_output=True, text=True,
                              timeout=120, encoding="utf-8", errors="replace")
        _f = subprocess.run([*_cmd_base, "--select", "F", *roots],
                            capture_output=True, text=True, timeout=120,
                            encoding="utf-8", errors="replace")
    except Exception as _e:
        return {"available": False, "error": f"{type(_e).__name__}: {_e}",
                "f_errors": -1, "total": -1, "details": []}

    _total = _count_rule_lines(_all.stdout)
    _f_err = _count_rule_lines(_f.stdout)
    _details = [_l for _l in (_f.stdout or "").splitlines() if _l.strip()]
    return {"available": True, "f_errors": _f_err, "total": _total, "details": _details}


def _count_rule_lines(text: str) -> int:
    """ruff --output-format=concise 每行形如 path:line:col: CODE 描述。"""
    _n = 0
    for _l in (text or "").splitlines():
        _l = _l.strip()
        if not _l:
            continue
        # 末段形如 "F401 ..." 或行内含规则码 [A-Z][0-9]{1,3}
        if re.search(r"\b[ABCDEFGH][0-9]{1,3}\b", _l):
            _n += 1
    return _n


# ============ 评估与编排 ============

def evaluate(measured, threshold, lower_is_better: bool) -> bool:
    """按方向判定是否通过阈值。"""
    if measured is None:
        return False
    return measured <= threshold if lower_is_better else measured >= threshold


# 检查项元信息：方向（lower_is_better）与默认阈值
_CHECK_META = {
    "except":      {"label": "裸 except 数量", "lower": True,  "default": 0},
    "header":      {"label": "v10 头部文件数", "lower": False, "default": 0},
    "exp_pollution": {"label": "经验库污染率", "lower": True,  "default": 1.0},
    "bak":         {"label": ".bak 备份数",   "lower": True,  "default": 0},
    "ruff":        {"label": "ruff F 类错误",  "lower": True,  "default": 0},
}


def build_report(enabled: set[str], thresholds: dict, project_root: str) -> dict:
    """运行 enabled 中各检查，组装统一 JSON 报告。"""
    _roots = [project_root]
    _pool = os.path.join(project_root, "data", "experience", "experience_pool.json")
    _report: dict = {"project_root": project_root, "checks": {}}

    if "except" in enabled:
        _c, _d = scan_bare_except(_roots)
        _t = thresholds.get("except", _CHECK_META["except"]["default"])
        _report["checks"]["except"] = {
            "name": "except", "label": _CHECK_META["except"]["label"],
            "measured": _c, "threshold": _t,
            "lower_is_better": True,
            "passed": evaluate(_c, _t, True), "details": _d[:50],
        }

    if "header" in enabled:
        _c, _d = count_v10_header(_roots)
        _t = thresholds.get("header", _CHECK_META["header"]["default"])
        _report["checks"]["header"] = {
            "name": "header", "label": _CHECK_META["header"]["label"],
            "measured": _c, "threshold": _t,
            "lower_is_better": False,
            "passed": evaluate(_c, _t, False), "details": _d[:50],
        }

    if "exp_pollution" in enabled:
        _r = compute_exp_pollution(_pool)
        _measured = _r.get("pollution_rate", 1.0)
        _t = thresholds.get("exp_pollution", _CHECK_META["exp_pollution"]["default"])
        _report["checks"]["exp_pollution"] = {
            "name": "exp_pollution", "label": _CHECK_META["exp_pollution"]["label"],
            "measured": _measured, "threshold": _t,
            "lower_is_better": True,
            "passed": evaluate(_measured, _t, True),
            "details": {
                "total": _r.get("total"), "unique_content": _r.get("unique_content"),
                "top_freq": _r.get("top_freq"),
                "duplicate_count": _r.get("duplicate_count"),
            },
        }

    if "bak" in enabled:
        _c, _d = count_bak(_roots)
        _t = thresholds.get("bak", _CHECK_META["bak"]["default"])
        _report["checks"]["bak"] = {
            "name": "bak", "label": _CHECK_META["bak"]["label"],
            "measured": _c, "threshold": _t,
            "lower_is_better": True,
            "passed": evaluate(_c, _t, True), "details": _d[:50],
        }

    if "ruff" in enabled:
        _rb = _find_ruff()
        if not _rb:
            _report["checks"]["ruff"] = {
                "name": "ruff", "label": _CHECK_META["ruff"]["label"],
                "measured": None, "threshold": thresholds.get(
                    "ruff", _CHECK_META["ruff"]["default"]),
                "lower_is_better": True, "passed": False,
                "details": ["未找到 ruff 可执行文件"],
            }
        else:
            _r = run_ruff_check(_rb, _roots)
            _t = thresholds.get("ruff", _CHECK_META["ruff"]["default"])
            _report["checks"]["ruff"] = {
                "name": "ruff", "label": _CHECK_META["ruff"]["label"],
                "measured": _r.get("f_errors"), "threshold": _t,
                "lower_is_better": True,
                "passed": evaluate(_r.get("f_errors"), _t, True),
                "details": _r.get("details", [])[:50],
            }

    _passed = all(_c.get("passed", False) for _c in _report["checks"].values())
    _report["all_passed"] = _passed
    return _report


def main() -> int:
    _p = argparse.ArgumentParser(description="验收测量脚本（第五批任务3）")
    _p.add_argument("--except-check", action="store_true", help="裸 except 计数")
    _p.add_argument("--header-check", action="store_true", help="v10 头部文件数")
    _p.add_argument("--exp-pollution", action="store_true", help="经验库污染率")
    _p.add_argument("--bak-count", action="store_true", help=".bak 备份数")
    _p.add_argument("--ruff-check", action="store_true", help="ruff F 类错误数")
    _p.add_argument("--all", action="store_true", help="运行全部检查")
    _p.add_argument("--threshold", action="append", default=[],
                    metavar="NAME=VALUE",
                    help="通过阈值，如 --threshold except=0（可重复）")
    _p.add_argument("--output", default=None, help="JSON 结果输出文件路径")
    _p.add_argument("--project-root", default=_PROJ, help="被扫描的项目根")
    _args = _p.parse_args()

    _enabled = {"except", "header", "exp_pollution", "bak", "ruff"} \
        if _args.all or not any([
            _args.except_check, _args.header_check, _args.exp_pollution,
            _args.bak_count, _args.ruff_check]) \
        else set()
    if _args.except_check:
        _enabled.add("except")
    if _args.header_check:
        _enabled.add("header")
    if _args.exp_pollution:
        _enabled.add("exp_pollution")
    if _args.bak_count:
        _enabled.add("bak")
    if _args.ruff_check:
        _enabled.add("ruff")

    _thresholds: dict = {}
    for _kv in _args.threshold:
        if "=" in _kv:
            _k, _v = _kv.split("=", 1)
            try:
                _thresholds[_k.strip()] = float(_v)
            except ValueError:
                print(f"[警告] 阈值无法解析（跳过）: {_kv}", file=sys.stderr)

    _report = build_report(_enabled, _thresholds, _args.project_root)
    _out = json.dumps(_report, ensure_ascii=False, indent=2)
    if _args.output:
        with open(_args.output, "w", encoding="utf-8") as _f:
            _f.write(_out)
        print(f"[quality_audit] 结果已写入: {_args.output}")
    else:
        print(_out)

    return 0 if _report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
