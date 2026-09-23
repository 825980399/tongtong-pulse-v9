#!/usr/bin/env python
"""probe_term_collapse —— 探针：专有名词查询向量是否坍缩为同一个向量。

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config as CFG
from tools.verify_semantic_kernel import CORPUS, TERM_QUERIES

CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
from nucleus.semantic.VectorEncoder import get_vector_encoder  # noqa: E402

enc = get_vector_encoder()
t0 = time.time()
while not enc.is_available() and time.time() - t0 < 180:
    time.sleep(1)
if not enc.is_available():
    print("编码器不可用:", enc.status())
    sys.exit(2)

qs = [q for _t, q in TERM_QUERIES]
V = enc.encode(qs)
print("term query vectors shape:", V.shape)

print("\n[1] 逐个编码 vs 批量编码 是否一致")
V1 = np.stack([enc.encode_one(q) for q in qs])
diff = np.abs(V - V1).max()
print(f"    max|batch - single| = {diff:.6f}")

print("\n[2] 查询向量两两余弦（下三角）")
S = V @ V.T
hdr = "        " + "".join(f"{t:<8}" for t, _q in TERM_QUERIES)
print(hdr)
for i, (ti, qi) in enumerate(TERM_QUERIES):
    row = "".join(f"{S[i,j]:<8.4f}" for j in range(len(qs)))
    print(f"  {ti:<6}{row}")

print("\n[3] 与自身目标语料的分数（高亮低分=疑似坍缩）")
cmap = {c[0]: c[2] for c in CORPUS}
C = enc.encode([cmap[t] for t, _q in TERM_QUERIES])
self_sim = np.sum(V * C, axis=1)
for (t, q), s in zip(TERM_QUERIES, self_sim):
    print(f"  {t:<5}{q:<26}self_sim={s:.6f}")

print("\n[4] 是否完全相同的向量（余弦 == 1.0 的组）")
seen = {}
for i, (t, q) in enumerate(TERM_QUERIES):
    key = None
    for k in seen:
        if abs(S[k, i] - 1.0) < 1e-5:
            key = k
            break
    if key is None:
        seen[i] = [t]
    else:
        seen[key].append(t)
for k, grp in seen.items():
    if len(grp) > 1:
        print(f"  ★完全相同: {grp}")

print("\n[5] 向量前 8 维数值（看是否真的一样）")
np.set_printoptions(precision=5, suppress=True, linewidth=200)
for (t, q), v in zip(TERM_QUERIES, V):
    print(f"  {t:<5}{q:<26}{v[:8]}")
