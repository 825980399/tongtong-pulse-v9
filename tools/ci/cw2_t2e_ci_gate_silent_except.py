# -*- coding: utf-8 -*-
"""任务二 d：防回潮 CI 门禁 + pre-commit hook（真实可执行，非伪码）。

规则（D148-4 指纹化改造后）：
  1. 对 base..HEAD（或工作树）的 diff，新增静默 except handler 数必须 = 0
     （只降不升 / 指纹豁免集合外新增 = 0）。匹配改为「handler 指纹」，
     行号漂移不再导致误报。
  2. 变更 .py 文件不得含 b"\\r\\r\\n"（CRCRLF 事故正主）。
  3. 变更 .py 文件必须可解析（parse_err=0）。
  4. 全仓「已知豁免指纹集合」幂等比对兜底：任一已知豁免 handler 在仓库中消失
     （被删/被改名）→ 视为「白名单腐化」，必须显式 shrink 提交（rot 检查）。
  5. 未跟踪新 .py 文件也纳入全量扫描，消除「未 tracked 盲区」：其静默 handler
     若不在已知豁免集合 → 阻断。

实现：AST 比较 base 版本与目标版本中「静默 handler」集合（按指纹 identity 取差集）。
唯一键：(path, qualname, nth_in_function, body_sha256[:16])，行号仅作备注。
退出码：0=通过，1=存在违规（CI fail / pre-commit 阻断），2=指纹基线缺失（配置错误）。

用法：
  python -X utf8 cw2_t2e_ci_gate_silent_except.py [--base HEAD] [--target worktree|HEAD]
                                            [--emit-baseline PATH] [--verbose]
"""
import argparse
import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ci_common import extract_handlers, handler_identity  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

FINGERPRINT_BASELINE = os.path.join(os.path.dirname(__file__), "silent_except_fingerprints.json")
LEGACY_WHITELIST_BASELINE = os.path.join(os.path.dirname(__file__), "silent_except_legacy_whitelist.json")


# ---- 第132批 T-132d：静默except豁免白名单（8 处合理自举兜底） ----
# 这些位置是框架有意的静默兜底（PM 的 ImportError 日志模块自举、RWP 的 shutdown 期落盘/回收保护），
# 不应计入"待改造"清单，也不应在未来被重新引入时触发违规。
# ★D148-4：本行号白名单已迁移为「handler 指纹」(silent_except_fingerprints.json)，
#   此处仅作一个批次的**双轨对照**保留，下一批可删除。键 = (relpath, 源文件行号)。
LOCATION_WHITELIST = {
    ("nucleus/reasoning/PatchManager.py", 3364),   # except ImportError: pass（日志模块不可用兜底）
    ("nucleus/reasoning/PatchManager.py", 3493),   # except ImportError: pass
    # ★第146批：T146-7 在 _load_json(:3610) 处加 4 行 utf-8-sig 注释 → 其后 handler 漂移 +4，原 3641 → 3645
    ("nucleus/reasoning/PatchManager.py", 3645),   # except ImportError: pass
    ("nucleus/reasoning/PatchManager.py", 3729),   # except ImportError: pass（★146批：原 3725，漂移 +4）
    ("nucleus/reasoning/PatchManager.py", 3786),   # except ImportError: pass（★146批：原 3782，漂移 +4）
    ("nucleus/reasoning/ReasoningWorkerPool.py", 707),   # shutdown 取消在途任务：except Exception: pass
    ("nucleus/reasoning/ReasoningWorkerPool.py", 747),   # shutdown join 子进程：except Exception: pass
    ("nucleus/reasoning/ReasoningWorkerPool.py", 795),   # shutdown 落盘保护(_sd)：except Exception: pass
    # ---- 第137批 T-137：PulseInnerWorld 首刀拆分 —— 支撑簇/尾块 42 方法搬到
    #      organs/brain/pulse_inner_world_support.py，守卫三连搬到 nucleus/iw_text_guard.py。
    #      以下 7 处静默 handler 系**平移**（PulseInnerWorld.py 同位置已删，全库净增=0），
    #      非新增回潮。键 = (relpath, 新文件行号)。
    ("nucleus/iw_text_guard.py", 36),                        # _search_topic_guard_enabled：config 读不到时默认开启
    ("organs/brain/pulse_inner_world_support.py", 84),       # _execute_qica_method：配置读取兜底
    ("organs/brain/pulse_inner_world_support.py", 540),      # _get_stress_reasoning_modulation：返回默认 stress
    ("organs/brain/pulse_inner_world_support.py", 702),      # _get_emotion_modulation：返回 default
    # ★第146批：T146-3 在 support.py:708 处加 5 行「出口渲染」注释 → 其后 handler 漂移 +5，原 2001 → 2006
    ("organs/brain/pulse_inner_world_support.py", 2006),     # _generate_life_stage_summary：初始化 _total
    ("organs/brain/pulse_inner_world_support.py", 2013),     # _generate_life_stage_summary：返回空串（★146批：原 2008，漂移 +5）
    # ---- 第139批 T-139b：PulseInnerWorld 第二刀拆分 —— 知识检索簇 24 方法搬到
    #      organs/brain/pulse_inner_world_knowledge.py（纯平移，PulseInnerWorld.py
    #      同位置已删；实测全库静默 handler 53 → 49+4 = 53，净增=0）。
    #      键 = (relpath, 新文件行号)。
    ("organs/brain/pulse_inner_world_knowledge.py", 216),    # _knowledge_retrieve：检索前提兜底 _question_for_infer
    ("organs/brain/pulse_inner_world_knowledge.py", 792),    # _evaluate_fusion_quality：融合阈值兜底常量
    ("organs/brain/pulse_inner_world_knowledge.py", 863),    # _fuse_multiple_nodes：融合配置读取兜底 _cfg_fuse=None
    ("organs/brain/pulse_inner_world_knowledge.py", 2300),   # _orchestrate_reason：候选列表兜底 _candidates=[]
    # ---- 第140批 T-140b：历史一次性脚本归档到 tools/archive/（纯 git mv，非新增回潮）----
    #      以下 3 处静默 handler 系**平移**（旧路径 tools/xxx.py 同内容已删，
    #      实测 HEAD 旧路径与新路径 handler 数完全一致、净增=0）。键 = (新路径, 行号)。
    ("tools/archive/cleanup_alias_placeholder_nodes.py", 87),   # _framework_looks_running：psutil 探测 except OSError: pass
    ("tools/archive/cleanup_alias_placeholder_nodes.py", 100),  # _framework_looks_running：探测兜底 except Exception: pass
    ("tools/archive/patch_template_helper.py", 246),            # <module>：shutil.rmtree 失败 → shutil_rm = False
    # ---- 第143批 T-143a：PII 清洗 —— _identity_rules 兜底 handler 的**捕获体**
    #      内嵌身份语句含真名，改造后 body 指纹字符串变化（handler 本身未增未删，
    #      该文件静默 handler 总数 49→49 不变）。键 = (relpath, 行号)。
    ("organs/brain/PulseInnerWorld.py", 358),                   # _load_inner_world_config：_identity_rules 默认兜底（★147批：__slots__ 再+2，原 349→352→354）
    ("organs/brain/PulseInnerWorld.py", 565),                   # _ir_build_context：guidance 兜底（★147批刀2：平移自 _on_inference_request，净增0）
}


def _log(level, msg):
    sys.stderr.write("[ci_gate_silent_except][%s] %s\n" % (level, msg))


def git_show(ref, relpath):
    r = subprocess.run(["git", "show", "%s:%s" % (ref, relpath)], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def git_read_worktree(relpath):
    p = os.path.join(ROOT, relpath)
    if not os.path.exists(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace").read()


def changed_files(base):
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    lst = [x for x in r.stdout.splitlines() if x.strip()]
    out = []
    for x in lst:
        if x.startswith('"') and x.endswith('"'):
            try:
                x = x[1:-1].encode().decode("unicode_escape").encode("latin1").decode("utf-8")
            except Exception:
                _log("warning", "中文/特殊路径反解失败，保留原串")
        out.append(x)
    return out


def git_ls_files_py():
    r = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    return [x for x in r.stdout.splitlines() if x.strip()]


def git_untracked_py():
    r = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", "*.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return [x for x in r.stdout.splitlines() if x.strip()]


# ---- 第126批 T-126c 新增：CRCRLF 行尾污染检测 ----
def check_crcrlf(files):
    bad = []
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with io.open(p, "rb") as _f:
            _data = _f.read()
        if b"\r\r\n" in _data:
            bad.append(rel)
    return bad


def load_known_fingerprints():
    if not os.path.exists(FINGERPRINT_BASELINE):
        _log("error", "缺失指纹基线 %s，请先运行 tools/ci/migrate_whitelist_to_fingerprints.py" % FINGERPRINT_BASELINE)
        return None
    with io.open(FINGERPRINT_BASELINE, encoding="utf-8") as _f:
        data = json.load(_f)
    return set(handler_identity(x) for x in data)


def full_repo_scan(KNOWN):
    """全仓静默 handler 指纹集合（含未跟踪 .py），用于 rot + 盲区兜底。

    返回 (full_set, untracked_bad)：
      full_set: 仓库当前所有静默 handler 的 identity 集合（tracked）。
      untracked_bad: 未跟踪 .py 中「不在已知豁免集合」的静默 handler [(rel, identity)]。
    """
    full = set()
    for rel in git_ls_files_py():
        src = git_read_worktree(rel)
        if not src:
            continue
        try:
            hs = extract_handlers(src, rel)
        except SyntaxError:
            continue
        full |= set(handler_identity(x) for x in hs)
    untracked_bad = []
    for rel in git_untracked_py():
        src = git_read_worktree(rel)
        if not src:
            continue
        try:
            hs = extract_handlers(src, rel)
        except SyntaxError:
            continue
        for fp in hs:
            if handler_identity(fp) not in KNOWN:
                untracked_bad.append((rel, handler_identity(fp)))
    return full, untracked_bad


# ---- 第126批 T-126c 新增：生成全仓静默except指纹基线（独立复扫用） ----
def emit_baseline(path):
    base = {}
    for rel in git_ls_files_py():
        src = git_read_worktree(rel) or ""
        try:
            hs = extract_handlers(src, rel)
        except SyntaxError:
            continue
        if hs:
            base[rel] = hs
    with io.open(path, "w", encoding="utf-8") as _f:
        json.dump(base, _f, ensure_ascii=False, indent=2)
    print("BASELINE_EMITTED -> %s  files=%d" % (path, len(git_ls_files_py())))


def _fmt_identity(ident):
    path, qualname, nth, sha16 = ident
    return "%s::%s[#%d]%s" % (path, qualname, nth, sha16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--target", default="worktree", choices=["worktree", "HEAD"])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--emit-baseline", default=None)
    args = ap.parse_args()

    KNOWN = load_known_fingerprints()
    if KNOWN is None:
        sys.exit(2)

    if args.emit_baseline:
        emit_baseline(args.emit_baseline)
        return 0

    files = changed_files(args.base)
    print("=" * 74)
    print("CI / pre-commit 门禁：防静默except回潮（指纹化）+ rot + 未跟踪盲区 + CRCRLF")
    print("  base=%s  target=%s  变更 .py 文件数=%d  已知豁免=%d"
          % (args.base, args.target, len(files), len(KNOWN)))
    print("=" * 74)

    total_added = 0
    parse_errs = 0
    added_details = []
    for rel in files:
        old = git_show(args.base, rel)
        new = git_read_worktree(rel) if args.target == "worktree" else git_show("HEAD", rel)
        if new is None:
            continue
        if old is None:
            old = ""
        try:
            so = set(handler_identity(x) for x in extract_handlers(old, rel))
        except SyntaxError:
            parse_errs += 1
            continue
        try:
            sn = set(handler_identity(x) for x in extract_handlers(new, rel))
        except SyntaxError:
            parse_errs += 1
            continue
        genuine = sn - (so | KNOWN)
        if genuine:
            total_added += len(genuine)
            added_details.append((rel, genuine))

    # 全仓兜底：rot（已知豁免消失）+ 未跟踪盲区
    full_set, untracked_bad = full_repo_scan(KNOWN)
    rot = [k for k in KNOWN if k not in full_set]

    crcrlf_bad = check_crcrlf(files)

    print("-" * 74)
    ok = True
    if total_added == 0:
        print("  [1] 静默except（diff 指纹差集）：PASS —— 本次变更未新增（只降不升 / 豁免外新增=0）")
    else:
        ok = False
        print("  [1] 静默except（diff 指纹差集）：FAIL —— 新增 %d 处" % total_added)
        if args.verbose:
            for rel, gens in added_details:
                for g in gens:
                    print("        %s  %s" % (rel, _fmt_identity(g)))
    if parse_errs == 0:
        print("  [2] parse_err：PASS —— 变更 .py 均可解析")
    else:
        ok = False
        print("  [2] parse_err：FAIL —— %d 个文件解析失败" % parse_errs)
    if not rot:
        print("  [3] 白名单 rot：PASS —— 已知豁免 %d 条全部仍存在于仓库" % len(KNOWN))
    else:
        ok = False
        print("  [3] 白名单 rot：FAIL —— %d 条已知豁免在仓库中消失（须显式 shrink 提交）：" % len(rot))
        if args.verbose:
            for k in rot:
                print("        %s" % _fmt_identity(k))
    if not untracked_bad:
        print("  [4] 未跟踪盲区：PASS —— 无未跟踪 .py 含非豁免静默 handler")
    else:
        ok = False
        print("  [4] 未跟踪盲区：FAIL —— %d 处未跟踪 .py 含静默 handler：" % len(untracked_bad))
        if args.verbose:
            for rel, ident in untracked_bad:
                print("        %s  %s" % (rel, _fmt_identity(ident)))
    if not crcrlf_bad:
        print("  [5] CRCRLF(双CR行尾)：PASS —— 无 CRCRLF 行尾污染")
    else:
        ok = False
        print("  [5] CRCRLF(双CR行尾)：FAIL —— %d 文件含双CR：%s" % (len(crcrlf_bad), crcrlf_bad))
    print("=" * 74)
    print("  结论: PASS" if ok else "  结论: FAIL —— 提交被阻断，请整改后重试")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
