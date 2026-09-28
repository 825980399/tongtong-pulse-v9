# -*- coding: utf-8 -*-
"""cw3 门禁 B 原型（第2版）：ctx 读写时序「陈旧读 = 0」CI 门禁（覆盖拆分层级）。

缺陷类（148批 D148-12 实证）：某名字 N 属 InferenceContext.__slots__，
  调用者 F（编排器 或 任一 _ir_ 方法）在第 k 行调用 self._ir_C(ctx)，
  _ir_C 内读取 ctx.N（Load），且：
    ① F 在 k 之前对局部名 N 有赋值（可能已改值），
    ② F 在 k 之前没有 `ctx.N = N` 同步写，
    ③ C 自身在读取前没有对 ctx.N 赋值（否则 C 自己就是写入者）
  ⇒ C 读到旧值 = 陈旧读。
本版相对第1版的修正：第1版只扫编排器 ⇒ 刀5–刀7 出现 `_ir_→_ir_` 两级调用后必然漏检（已实测证伪）。

受控豁免：调用行 / 调用行上一行 / 被调方法体内任一行的行尾注释含 `# iw-allow-stale: <非空理由>`。

用法：python cw3_ctx_stale_read_gate.py [--root .] [--rev HEAD|WORKTREE] [--file PATH] [--json OUT]
退出码：0=PASS / 2=FAIL(有陈旧读) / 3=解析错误
"""
import argparse, ast, collections, io, json, os, re, subprocess, sys, datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
TARGET = 'organs/brain/PulseInnerWorld.py'
MAIN = '_on_inference_request'
EXEMPT = re.compile(r'#\s*iw-allow-stale\s*[:：]\s*(\S.*)')
CTX_NAMES = ('ctx', '_ctx')


def load(a):
    if a.file:
        return io.open(a.file, encoding='utf-8', errors='replace').read()
    if a.rev.upper() == 'WORKTREE':
        return io.open(os.path.join(a.root, TARGET), encoding='utf-8', errors='replace').read()
    r = subprocess.run(['git', 'show', '%s:%s' % (a.rev, TARGET)], cwd=a.root, capture_output=True)
    return None if r.returncode else r.stdout.decode('utf-8', errors='replace')


def slots_of(tree):
    ic = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'InferenceContext']
    if not ic:
        return []
    for st in ic[0].body:
        ids = [getattr(getattr(st, 'target', None), 'id', '')] + [getattr(t, 'id', '') for t in getattr(st, 'targets', [])]
        if '__slots__' in ids:
            return [ast.unparse(e).strip("'\"") for e in st.value.elts]
    return []


def attr_ctx_writes(fnobj, SLOTS):
    """方法内 `ctx.N = ...` 的行（含 mirror 解包 ctx.N = x 与任意右值）。"""
    out = collections.defaultdict(list)
    for n in ast.walk(fnobj):
        tg = []
        if isinstance(n, ast.Assign):
            tg = n.targets
        elif isinstance(n, ast.AugAssign):
            tg = [n.target]
        for tt in tg:
            if isinstance(tt, ast.Attribute) and isinstance(tt.value, ast.Name) \
                    and tt.value.id in CTX_NAMES and tt.attr in SLOTS:
                out[tt.attr].append(n.lineno)
    return out


def attr_ctx_reads(fnobj, SLOTS):
    out = collections.defaultdict(list)
    for n in ast.walk(fnobj):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                and n.value.id in CTX_NAMES and n.attr in SLOTS and isinstance(n.ctx, ast.Load):
            out[n.attr].append(n.lineno)
    return out


def local_writes(fnobj, SLOTS):
    out = collections.defaultdict(list)
    for n in ast.walk(fnobj):
        if isinstance(n, ast.Assign):
            # 先排除「元组解包自 ctx 属性的镜像写」：`a, b = ctx.a, ctx.b` 属语义等价的取回，不是改值
            mirror = isinstance(n.value, (ast.Tuple, ast.List)) and all(
                isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id in CTX_NAMES
                for e in n.value.elts)
            multi = len(n.targets) > 1 or isinstance(n.targets[0], (ast.Tuple, ast.List))
            for tt in n.targets:
                elts = tt.elts if isinstance(tt, (ast.Tuple, ast.List)) else [tt]
                for e in elts:
                    if isinstance(e, ast.Name) and e.id in SLOTS and not (mirror and multi):
                        out[e.id].append(n.lineno)
        elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id in SLOTS:
            out[n.target.id].append(n.lineno)
    return out


def calls_of(fnobj, IR):
    out = []
    for n in ast.walk(fnobj):
        f = n.func if isinstance(n, ast.Call) else None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id == 'self' and f.attr in IR:
            out.append((f.attr, n.lineno))
    return sorted(out, key=lambda x: x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.getcwd())
    ap.add_argument('--rev', default='WORKTREE')
    ap.add_argument('--file', default=None)
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    src = load(a)
    if src is None:
        print('CW3-B 读取失败')
        return 3
    lines = src.split('\n')
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        print('CW3-B 解析错误: %s' % ex)
        return 3
    cls = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'PulseInnerWorld']
    if not cls:
        print('CW3-B 未找到 PulseInnerWorld')
        return 3
    ms = {f.name: f for f in cls[0].body if isinstance(f, ast.FunctionDef)}
    IR = sorted(k for k in ms if k.startswith('_ir_'))
    SLOTS = set(slots_of(tree))
    if MAIN not in ms or not SLOTS:
        print('CW3-B 缺编排器或 InferenceContext.__slots__')
        return 3

    risks, exempted = [], []
    total_calls = 0
    for fname in [MAIN] + IR:
        F = ms[fname]
        lw, sw = local_writes(F, SLOTS), attr_ctx_writes(F, SLOTS)
        for callee, k in calls_of(F, set(IR)):
            total_calls += 1
            cr = attr_ctx_reads(ms[callee], SLOTS)
            cw = attr_ctx_writes(ms[callee], SLOTS)
            for N in sorted(cr):
                first_read = min(cr[N])
                if sw.get(N) and min(sw[N]) < k:
                    continue                      # 调用前已同步
                if cw.get(N) and min(cw[N]) <= first_read:
                    continue                      # 被调方自己先写后读
                if not lw.get(N):
                    continue
                if min(lw[N]) >= k:
                    continue                      # 局部写在调用之后 ⇒ 非本缺陷类
                hit = None
                for probe in (k, k - 1):
                    if 0 < probe <= len(lines) and EXEMPT.search(lines[probe - 1]):
                        hit = (probe, EXEMPT.search(lines[probe - 1]).group(1))
                if not hit:
                    for i in range(ms[callee].lineno - 1, min(ms[callee].end_lineno, len(lines))):
                        m2 = EXEMPT.search(lines[i])
                        if m2:
                            hit = (i + 1, m2.group(1))
                            break
                rec = {'caller': fname, 'call_line': k, 'callee': callee, 'name': N,
                       'caller_local_writes': sorted(lw[N])[:6], 'callee_first_read': first_read}
                (exempted if hit else risks).append(dict(rec, **({'exempt_at': hit} if hit else {})))

    print('CW3-B ctx 陈旧读门禁 v2 @%s  源=%s  slots=%d  _ir_=%d  检查调用点=%d'
          % (datetime.datetime.now().strftime('%H:%M:%S'), (a.file or a.rev).split(os.sep)[-1],
             len(SLOTS), len(IR), total_calls))
    for r in risks[:10]:
        print('  [RISK] %s@%d → %s 读 ctx.%s：%s 在调用前只写了局部 %s（未同步进 ctx）'
              % (r['caller'], r['call_line'], r['callee'], r['name'], r['caller_local_writes'], r['name']))
    for r in exempted[:6]:
        print('  [EXEMPT] %s@%s ctx.%s 理由=%s' % (r['caller'], r['call_line'], r['name'], str(r['exempt_at'][1])[:60]))
    if a.json:
        io.open(a.json, 'w', encoding='utf-8').write(json.dumps(
            {'observed_at': datetime.datetime.now().isoformat(timespec='seconds'),
             'source': a.file or a.rev, 'slots': sorted(SLOTS), 'ir': IR,
             'checked_calls': total_calls, 'risks': risks, 'exempted': exempted}, ensure_ascii=False, indent=1))
    if not risks:
        print('  [OK] 陈旧读风险 0 项（豁免 %d 项）' % len(exempted))
        return 0
    print('  [FAIL] 陈旧读风险 %d 项 ⇒ 在调用者侧补 `ctx.%s = %s` 同步，或经星轨裁决加 # iw-allow-stale: <理由>'
          % (len(risks), risks[0]['name'], risks[0]['name']))
    return 2


if __name__ == '__main__':
    sys.exit(main())
