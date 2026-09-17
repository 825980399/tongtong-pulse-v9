# _topk_retrieve_cy.pyx
# ★v27新增：批量余弦相似度 + top-k 排序一体化 Cython 加速（向量检索热路径）
# 对应 fast_ops.fast_vector_search：一次调用完成「余弦批量计算 + 降序 top-k 选取」，
# 在 C 层完成排序（qsort），避免 Python 层的整表排序开销。
# 仅纯数值 + libc.math / libc.stdlib，与 Python 版输出一致（top_k 个 (索引, 分数) 降序）。

from libc.math cimport sqrt
from libc.stdlib cimport malloc, free, qsort

# 空/退化向量的极小范数（与 fast_ops 语义一致：避免除零）
DEF EPSILON = 1e-9

cdef struct ScoreIdx:
    int idx
    double score

cdef int cmp_desc(const void *a, const void *b) noexcept nogil:
    """降序比较（分数大者在前）"""
    cdef double sa = (<ScoreIdx*>a).score
    cdef double sb = (<ScoreIdx*>b).score
    if sa > sb:
        return -1
    if sa < sb:
        return 1
    return 0


def topk_retrieve_cy(list query_vec, list candidate_vecs, int top_k) -> list:
    """
    ★v27：批量余弦相似度 + top-k 选取（一体化）。

    query_vec: 查询向量（数值列表）
    candidate_vecs: 候选向量列表
    top_k: 返回的 top 数量（<=0 → 返回空；>n → 取 n）

    Returns: [(index, score), ...] 按分数降序，最多 top_k 个。
    """
    cdef int n = len(candidate_vecs)
    cdef int dim = len(query_vec)
    cdef int i, j
    cdef double qn = 0.0, dot = 0.0, cn = 0.0, v = 0.0, denom = 0.0
    cdef list q
    cdef ScoreIdx* arr = NULL
    cdef list result = []
    cdef int k = 0
    cdef object c

    if n <= 0 or dim <= 0 or top_k <= 0:
        return []
    k = top_k if top_k < n else n

    # 查询向量范数（与 Python 版一致：norm=0 → EPSILON，避免除零）
    q = [float(x) for x in query_vec]
    for j in range(dim):
        qn += q[j] * q[j]
    if qn > 0.0:
        qn = sqrt(qn)
    else:
        qn = EPSILON

    arr = <ScoreIdx*> malloc(n * sizeof(ScoreIdx))
    if arr == NULL:
        # 内存分配失败 → 回退空（调用方会降级 Python 版）
        return []
    try:
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
            arr[i].idx = i
            arr[i].score = (dot / denom) if denom != 0.0 else 0.0

        qsort(arr, n, sizeof(ScoreIdx), cmp_desc)

        for i in range(k):
            result.append((arr[i].idx, arr[i].score))
    finally:
        free(arr)

    return result
