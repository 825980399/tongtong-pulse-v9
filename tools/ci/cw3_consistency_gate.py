# -*- coding: utf-8 -*-
"""cw3 门禁 A 原型：推理核心文件「语义守恒」CI 门禁（文件作用域口径）。

设计目标（149批 T149-1-5 已证：拆分只改结构不改事件语义，故可硬门禁化）：
  - 锁定 try/except/emit/控制流 计数 与 emit priority 直方，防"顺手改逻辑"夹带在大重构提交里；
  - 与基准 JSON 比较，差异非 0 即红；
  - 允许受控变更：基准文件必须与代码同提交被修改，且带 delta_reason（误判豁免流程）。

用法：
  python cw3_consistency_gate.py --target worktree [--base HEAD] [--baseline FILE] [--update]
退出码：0=PASS / 2=FAIL(守恒量漂移) / 3=用法或解析错误
"""
import argparse, ast, collections, io, json, os, subprocess, sys, datetime, hashlib

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

WATCH = ['organs/brain/PulseInnerWorld.py']          # 监控文件（可扩展）
METRICS = ['try', 'except', 'except_pass', 'if', 'for', 'while', 'return', 'raise', 'await',
           'emit_calls', 'methods']


def read_blob(rev, path, root):
    if rev in ('WORKTREE', 'worktree'):
        return io.open(os.path.join(root, path), encoding='utf-8', errors='replace').read()
    r = subprocess.run(['git', 'show', '%s:%s' % (rev, path)], cwd=root, capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode('utf-8', errors='replace')


def metrics_of(src):
    tree = ast.parse(src)
    d = collections.Counter()
    pri = collections.Counter()
    ev = collections.Counter()
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Try):
            d['try'] += 1
        elif isinstance(nd, ast.ExceptHandler):
            d['except'] += 1
            body = [x for x in nd.body
                    if not (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant)
                            and isinstance(x.value.value, str))]
            if body and all(isinstance(x, ast.Pass) for x in body):
                d['except_pass'] += 1
        if isinstance(nd, ast.If):
            d['if'] += 1
        if isinstance(nd, ast.For):
            d['for'] += 1
        if isinstance(nd, ast.While):
            d['while'] += 1
        if isinstance(nd, ast.Return):
            d['return'] += 1
        if isinstance(nd, ast.Raise):
            d['raise'] += 1
        if isinstance(nd, ast.Await):
            d['await'] += 1
        if isinstance(nd, ast.FunctionDef):
            d['methods'] += 1
        if isinstance(nd, ast.Call):
            nm = getattr(nd.func, 'attr', None) or getattr(nd.func, 'id', None)
            if nm and 'emit' in nm:
                d['emit_calls'] += 1
                for kw in nd.keywords:
                    if kw.arg in ('priority',) and isinstance(kw.value, ast.Constant):
                        pri[str(kw.value.value)] += 1
                    if kw.arg == 'event' and isinstance(kw.value, ast.Constant):
                        ev[str(kw.value.value)] += 1
                if nd.args and isinstance(nd.args[0], ast.Constant) and isinstance(nd.args[0].value, str):
                    ev[nd.args[0].value] += 1
                for a in nd.args[1:]:
                    if isinstance(a, ast.Constant) and isinstance(a.value, int):
                        pri['pos-%d' % a.value] += 1
    d['lines'] = len(src.splitlines())
    d['ast_sha256_12'] = hashlib.sha256(ast.dump(tree, annotate_fields=False).encode('utf-8')).hexdigest()[:12]
    return d, pri, ev


def collect(root, rev):
    out = {}
    for p in WATCH:
        src = read_blob(rev, p, root)
        if src is None:
            return None
        d, pri, ev = metrics_of(src)
        out[p] = {'metrics': {k: d[k] for k in METRICS + ['lines', 'ast_sha256_12']},
                  'priority_hist': dict(sorted(pri.items())), 'event_hist': dict(sorted(ev.items()))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.getcwd())
    ap.add_argument('--target', default='worktree')
    ap.add_argument('--base', default='HEAD')
    ap.add_argument('--baseline', default=None)
    ap.add_argument('--update', action='store_true', help='生成/刷新基准文件（仅在显式批准时）')
    a = ap.parse_args()
    bl = a.baseline or os.path.join(a.root, 'tools', 'ci', 'baselines', 'iw_consistency_baseline.json')

    cur = collect(a.root, a.target)
    if cur is None:
        print('CW3-A 用法错误：监控文件不可读')
        return 3
    if a.update or not os.path.exists(bl):
        _old = {}
        if os.path.exists(bl):
            try:
                _old = json.load(io.open(bl, encoding='utf-8-sig'))
            except Exception:
                _old = {}
        payload = {'schema': 'cw3-consistency/1', 'anchor_rev': a.base,
                   'anchor_sha': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=a.root,
                                                capture_output=True, text=True).stdout.strip()[:40],
                   'observed_at': datetime.datetime.now().isoformat(timespec='seconds'),
                   'watch': WATCH, 'files': cur,
                   'delta_reason': _old.get('delta_reason', ''),
                   'expected_delta': _old.get('expected_delta', {})}
        os.makedirs(os.path.dirname(bl), exist_ok=True) if a.update else None
        if a.update:
            io.open(bl, 'w', encoding='utf-8', newline='\n').write(json.dumps(payload, ensure_ascii=False, indent=1))
            print('CW3-A 基准已写入 %s' % bl)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    base = json.load(io.open(bl, encoding='utf-8-sig'))
    delta_reason = str(base.get('delta_reason', '')).strip()
    expected_delta = base.get('expected_delta') or {}
    fails = []
    for p in WATCH:
        b = base['files'].get(p)
        if b is None:
            fails.append('%s 无基准（须先 --update）' % p)
            continue
        for k in METRICS:                       # 硬门禁：11 个语义计数（lines / ast_sha 仅展示，不参与判定）
            bv, cv = b['metrics'].get(k, 0), cur[p]['metrics'].get(k, 0)
            if bv == cv:
                continue
            _delta = cv - bv
            # 精确白名单：仅当基准带 delta_reason（受控变更授权）且声明了本指标的预期增量时才放行
            _exp = expected_delta.get(k) if (isinstance(expected_delta, dict) and delta_reason) else None
            if _exp is not None and _delta == _exp:
                continue
            fails.append('%s: 指标 %s 基准 %s → 当前 %s%s' % (
                p, k, bv, cv, ' (预期增量 %s)' % _exp if _exp is not None else ''))
        for key, lbl in (('priority_hist', 'emit priority 直方'), ('event_hist', '事件名直方')):
            if b.get(key) != cur[p].get(key):
                bk, ck = b.get(key, {}), cur[p].get(key, {})
                diff = {x: (bk.get(x, 0), ck.get(x, 0)) for x in set(bk) | set(ck) if bk.get(x, 0) != ck.get(x, 0)}
                fails.append('%s: %s 漂移 %s' % (p, lbl, diff))
    print('CW3-A 语义守恒门禁 @%s  target=%s  基准锚=%s(%s)'
          % (datetime.datetime.now().strftime('%H:%M:%S'), a.target, base.get('anchor_rev'), base.get('anchor_sha', '')[:7]))
    if not fails:
        print('  [OK] 硬门禁指标全部一致（参考：行数 %s / AST 指纹 %s）'
              % (cur[WATCH[0]]['metrics']['lines'], cur[WATCH[0]]['metrics']['ast_sha256_12']))
        return 0
    if not delta_reason:
        print('  [FAIL] %d 项漂移且无 delta_reason（须与代码同提交更新 %s 并填 delta_reason）'
              % (len(fails), os.path.relpath(bl, a.root)))
        for f in fails[:12]:
            print('     - ' + f)
        return 2
    print('  [FAIL] %d 项非预期漂移（expected_delta 未覆盖即视为夹带，须回退或补白名单）：' % len(fails))
    for f in fails[:12]:
        print('     - ' + f)
    print('  处方：① 若为误伤 ⇒ 在 %s 的 expected_delta 补声明该增量；② 若为真漂移 ⇒ 回退该改动。'
          % os.path.relpath(bl, a.root))
    return 2


if __name__ == '__main__':
    sys.exit(main())
