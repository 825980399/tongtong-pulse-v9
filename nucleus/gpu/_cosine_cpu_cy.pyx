# _cosine_cpu_cy.pyx
# ★v25.1新增：批量余弦相似度 Cython 加速版（CPU 降级路径热路径）
# 对应 GPUCore._cosine_cpu（纯数值数组循环，输出完全一致）
# 仅纯数值 + libc.math，无 Python 对象操作（nogil 块内只 C 类型）

from libc.math cimport sqrt

# 空列表/退化向量时的极小范数（与 GPUCore._cosine_cpu 一致）
DEF EPSILON = 1e-9


def batch_cosine_cy(list query_vec, list candidate_vecs) -> list:
    """
    ★v25.1：批量余弦相似度（CPU 降级路径加速）。
    与 GPUCore._cosine_cpu 输出一致：query 与每个 candidate 的余弦 [-1,1]。

    query_vec: 查询向量（数值列表）
    candidate_vecs: 候选向量列表
    """
    cdef int n = len(candidate_vecs)
    cdef int dim = len(query_vec)
    cdef int i, j
    cdef double qn = 0.0, dot = 0.0, cn = 0.0, v = 0.0
    cdef list q = [float(x) for x in query_vec]
    cdef list results = []
    cdef object c
    cdef double denom = 0.0

    if n == 0 or dim == 0:
        return []

    # 查询向量范数（与 Python 版一致：norm=0 → 1e-9）
    for j in range(dim):
        qn += q[j] * q[j]
    if qn > 0.0:
        qn = sqrt(qn)
    else:
        qn = EPSILON

    for i in range(n):
        c = candidate_vecs[i]
        dot = 0.0
        cn = 0.0
        for j in range(dim):
            v = float(c[j])
            dot += q[j] * v
            cn += v * v
        if cn > 0.0:
            cn = sqrt(cn)
        else:
            cn = EPSILON
        denom = qn * cn
        if denom == 0.0:
            denom = EPSILON
        results.append(dot / denom)

    return results
