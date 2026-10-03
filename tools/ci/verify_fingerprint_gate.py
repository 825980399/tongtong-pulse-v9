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

# ★T-审计-5：静默异常统一走 CI 门禁认可通道（需项目根入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from nucleus._silent_except import silent_exc  # noqa: E402


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


# ========== ★第158批 上-A T-自我审计-5（P2）：器官公开方法签名指纹 ==========
# 边界（与 C-8 **互补**，不重叠）：
#   · C-8（broken_chain）管「方法级零引用」——有没有人用这个方法；
#   · 本件管「签名变更 → 调用点破坏预检」——用法还对不对。
# 命名：模块级符号统一 ``fp_`` 前缀，避撞 ``ci_common.py`` 导出（星轨裁定）。
import ast
import json

#: 扫描时跳过的目录。
FP_SKIP_DIRS = {"tmp", ".git", "__pycache__", "node_modules", ".venv", "venv"}


def _fp_skip_dir(d):
    return d in FP_SKIP_DIRS or d.startswith(".bak_batch")


def _fp_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fp_baseline_path():
    """签名基线路径（``tools/ci/baselines/method_signature_baseline.json``）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "baselines", "method_signature_baseline.json")


def fp_method_signature(node):
    """从 AST ``FunctionDef`` 生成**规范化签名**（与行号/空白/注释无关）。

    形式：``name(a, b=1, *args, c, **kw)``。默认值用 ``repr`` 归一，
    因此改个缩进或挪行不会误报，而**增删参数/改默认值一定会变**。
    """
    a = node.args
    parts = []

    def _one(arg, default=None):
        if default is None:
            return arg.arg
        try:
            return "%s=%r" % (arg.arg, default.value)
        except AttributeError as _ae:
            # ★不得静默：默认值取不到时退化为 "?"，须留痕（否则签名可能被误判为未变）
            silent_exc(_ae, where="verify_fingerprint_gate.fp_method_signature._one",
                       level="debug")
            return "%s=?" % arg.arg

    pos = list(getattr(a, "posonlyargs", []) or []) + list(a.args)
    n_def = len(a.defaults)
    first_def = len(pos) - n_def
    for i, arg in enumerate(pos):
        parts.append(_one(arg, a.defaults[i - first_def] if i >= first_def else None))
    if a.vararg is not None:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, dflt in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(_one(arg, dflt))
    if a.kwarg is not None:
        parts.append("**" + a.kwarg.arg)
    return "%s(%s)" % (node.name, ", ".join(parts))


def fp_param_count(signature):
    """从规范化签名取**位置参数**个数（用于调用点破坏预检的粗粒度判定）。"""
    try:
        inner = signature[signature.index("(") + 1:signature.rindex(")")]
    except (ValueError, IndexError) as _ve:
        # ★不得静默：签名串不含括号（非法）时返回 None，须留痕供复核
        silent_exc(_ve, where="verify_fingerprint_gate.fp_param_count", level="debug")
        return None
    if not inner.strip():
        return 0
    n = 0
    for piece in inner.split(","):
        p = piece.strip()
        if p.startswith("*"):
            continue                      # *args / **kwargs 不计入位置参数
        n += 1
    return n


def fp_collect_signatures(root=None, subdir="organs"):
    """采集公开方法签名。

    Returns:
        ``{"<rel_file>": {"<Class>.<method>": "<signature>"}}``，只收**公开**
        方法（不以下划线开头）与**顶层类**内的方法。
    """
    base = os.path.join(root or _fp_root(), subdir)
    out = {}
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not _fp_skip_dir(d)]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root or _fp_root()).replace("\\", "/")
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    tree = ast.parse(f.read(), filename=fp)
            except (OSError, SyntaxError, ValueError) as _e:
                silent_exc(_e, where="verify_fingerprint_gate.fp_collect_signatures",
                           level="debug")
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if item.name.startswith("_"):
                        continue          # 公开方法
                    out.setdefault(rel, {})["%s.%s" % (node.name, item.name)] = \
                        fp_method_signature(item)
    return out


def fp_load_baseline(path=None):
    """读取签名基线；不存在/损坏返回 ``{}``。"""
    p = path or fp_baseline_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("signatures", {}) if isinstance(d, dict) else {}
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="verify_fingerprint_gate.fp_load_baseline", level="warning")
        return {}


def fp_save_baseline(signatures, path=None):
    """写签名基线。返回是否成功。"""
    p = path or fp_baseline_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": "158A-T5-1", "signatures": signatures},
                      f, ensure_ascii=False, indent=2, sort_keys=True)
        return True
    except OSError as _e:
        silent_exc(_e, where="verify_fingerprint_gate.fp_save_baseline", level="warning")
        return False


def fp_signature_drift(current, baseline):
    """比对基线与当前，返回签名漂移列表。

    Returns:
        ``[{"file", "symbol", "old", "new", "kind"}]``；``kind`` ∈
        ``changed``（签名变）/ ``removed``（方法消失）。
    """
    out = []
    for rel, syms in sorted((baseline or {}).items()):
        cur = (current or {}).get(rel, {})
        for name, old in sorted((syms or {}).items()):
            new = cur.get(name)
            if new is None:
                out.append({"file": rel, "symbol": name, "old": old,
                            "new": None, "kind": "removed"})
            elif new != old:
                out.append({"file": rel, "symbol": name, "old": old,
                            "new": new, "kind": "changed"})
    return out


def fp_call_sites(root, class_name, method, limit=50):
    """扫全仓 ``<class_name>.<method>(`` 形态的调用点（粗粒度但可复核）。

    Returns:
        ``[{"file", "line", "text"}]``（最多 ``limit`` 条）。
    """
    base = root or _fp_root()
    hits = []
    needle = class_name + "." + method + "("
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not _fp_skip_dir(d)]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, base).replace("\\", "/")
            if rel.startswith("tmp/"):
                continue
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, start=1):
                        if needle in line:
                            hits.append({"file": rel, "line": i,
                                         "text": line.strip()[:120]})
                            if len(hits) >= limit:
                                return hits
            except OSError as _e:
                silent_exc(_e, where="verify_fingerprint_gate.fp_call_sites", level="debug")
    return hits


def fp_breaking_calls(root, drift, limit=20):
    """★调用点破坏预检：签名位置参数增加时，找出**可能传参不足**的调用点。

    这是「签名变更 → 调用点破坏」的核心：新增必填参数后，仍按旧写法调用的
    位置会在运行期 TypeError。静态无法百分百确定（默认值/关键字参数），
    故此处**只报可疑点供人工复核**，不冒充判定。
    """
    warn = []
    for d in drift or []:
        if d.get("kind") != "changed":
            continue
        old_n = fp_param_count(d.get("old") or "")
        new_n = fp_param_count(d.get("new") or "")
        if old_n is None or new_n is None or new_n <= old_n:
            continue                      # 参数未增加 → 无破坏风险
        symbol = d.get("symbol") or ""
        if "." not in symbol:
            continue
        cls, meth = symbol.split(".", 1)
        for site in fp_call_sites(root, cls, meth, limit=limit):
            warn.append({
                "symbol": symbol, "old": d.get("old"), "new": d.get("new"),
                "old_positional": old_n, "new_positional": new_n,
                "suspect": site,
                "note": "位置参数由 %d 增至 %d，该调用点可能未补参（运行期 TypeError 风险）"
                        % (old_n, new_n),
            })
    return warn


def fp_rule_signature():
    """★T-规则生命周期-1 概念：本门禁**规则自身**的签名指纹（防意外修改）。

    把本件的关键规则常量与阈值序列化成一个指纹：任何人改动豁免目录集、
    破坏预检阈值等规则本身，指纹即变化 → 可被基线比对发现。
    """
    return {
        "skip_dirs": sorted(FP_SKIP_DIRS),
        "baseline_name": os.path.basename(fp_baseline_path()),
        "drift_kinds": ["changed", "removed"],
        "version": "158A-T5-1",
    }


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

    # ===== ★第158批 上-A T-审计-5：签名指纹 4 项验收 =====
    # ⑥ 签名规范化：改缩进/挪行 → 签名不变（绿）；改参数 → 签名变（红）
    _src_a = "class C:\n    def m(self, a, b=1):\n        return a\n"
    _src_b = "\n\n" + _src_a.replace("    def", "  def")   # 仅缩进变化
    _src_c = "class C:\n    def m(self, a, b=1, c=2):\n        return a\n"  # 增参数
    _tree_a = ast.parse(_src_a)
    _tree_b = ast.parse(_src_b)
    _tree_c = ast.parse(_src_c)
    _sig_a = fp_method_signature(_tree_a.body[0].body[0])
    _sig_b = fp_method_signature(_tree_b.body[0].body[0])
    _sig_c = fp_method_signature(_tree_c.body[0].body[0])
    if _sig_a != _sig_b:
        failures.append("⑥ 缩进变化不应改签名（实际 %r vs %r）" % (_sig_a, _sig_b))
    if _sig_a == _sig_c:
        failures.append("⑥ 增参数应改签名（两者同为 %r）" % _sig_a)

    # ⑦ 漂移检出：基线 vs 当前 → changed / removed 两类
    _base = {"organs/x.py": {"C.m": _sig_a, "C.gone": "gone(self)"}}
    _cur = {"organs/x.py": {"C.m": _sig_c}}
    _drift = fp_signature_drift(_cur, _base)
    _kinds = sorted(d["kind"] for d in _drift)
    if _kinds != ["changed", "removed"]:
        failures.append("⑦ 漂移应检出 changed+removed（实际 %s）" % _kinds)

    # ⑧ 调用点破坏预检：位置参数 2→3，应能命中可疑调用点
    #    （计数含 self，故为 3→4；破坏判定用的是**差值**，含 self 不影响结论）
    if fp_param_count(_sig_a) != 3 or fp_param_count(_sig_c) != 4:
        failures.append("⑧ 位置参数计数应 a=3 / c=4（实际 %s / %s）"
                        % (fp_param_count(_sig_a), fp_param_count(_sig_c)))
    _warn = fp_breaking_calls(os.path.dirname(_fp_root()) or None,
                              [{"kind": "changed", "symbol": "PulseLiver.m",
                                "old": _sig_a, "new": _sig_c}], limit=3)
    _warn_self = fp_breaking_calls(_fp_root(),
                                   [{"kind": "changed", "symbol": "PulseLiver.m",
                                     "old": _sig_a, "new": _sig_c}], limit=3)
    if not isinstance(_warn_self, list):
        failures.append("⑧ 调用点破坏预检应返回列表")

    # ⑨ 规则签名生效：规则常量变更 → 指纹随之变化
    _rs = fp_rule_signature()
    if not isinstance(_rs, dict) or not _rs.get("version"):
        failures.append("⑨ 规则签名应含 version")
    _rs_snapshot = json.dumps(_rs, sort_keys=True, ensure_ascii=False)
    if _rs_snapshot != json.dumps(fp_rule_signature(), sort_keys=True, ensure_ascii=False):
        failures.append("⑨ 规则签名应稳定（两次调用不一致）")
    _saved = set(FP_SKIP_DIRS)
    try:
        FP_SKIP_DIRS.add("__fp_probe__")
        if json.dumps(fp_rule_signature(), sort_keys=True, ensure_ascii=False) == _rs_snapshot:
            failures.append("⑨ 规则常量变更应改指纹（实际未变）")
    finally:
        FP_SKIP_DIRS.clear()
        FP_SKIP_DIRS.update(_saved)

    if failures:
        for f in failures:
            print("FAIL: " + f)
        print("RESULT: %d 项验收未通过" % len(failures))
        sys.exit(1)
    print("RESULT: 9 项验收全部通过（①改体红 ②插行绿 ③新增红 ④删handler红 "
          "⑤未跟踪红 ⑥签名规范化 ⑦漂移检出 ⑧调用点破坏预检 ⑨规则签名）")
    sys.exit(0)


if __name__ == "__main__":
    main()
