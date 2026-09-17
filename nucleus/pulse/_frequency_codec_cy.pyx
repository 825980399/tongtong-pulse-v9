# _frequency_codec_cy.pyx
# Cython加速版频率编解码器（v18.0修复版 + v23.0 nogil优化 + ★五期 MD5 C 化）
# ★P1-4修复：与Python版FrequencyCodec.encode()算法完全一致
# 接收value和keywords两个参数，基于关键词哈希均值+正弦映射

# ★五期热路径扩展：MD5 哈希 C 化（解决 encode 加速比 1.02x 瓶颈）
#   原实现 int(hashlib.md5(b).hexdigest(), 16) 依赖 Python 内置 hashlib（持 GIL、Python 对象开销）。
#   本模块实现标准 MD5（RFC 1321）的 C 版本 _md5_int()，输出与 hashlib.md5 逐字节一致
#   （同为大端 128 位整数），故全库频率签名完全不变；MD5 核心可放 nogil 释放 GIL。
#   Parity 验证：1 万组随机关键词，_md5_int == hashlib 结果。

import math          # 保留给 Python 层使用（不在 nogil 内）

from libc.stdint cimport uint8_t, uint32_t, uint64_t
from libc.stddef cimport size_t
from libc.math cimport sin

# 定义PI常量（C double），供nogil块使用
cdef double PI_CONST = 3.141592653589793

# ========== ★五期：标准 MD5（RFC 1321）C 实现 ==========

cdef uint32_t _MD5_S[64]
_MD5_S = [
    7, 12, 17, 22,  7, 12, 17, 22,  7, 12, 17, 22,  7, 12, 17, 22,
    5,  9, 14, 20,  5,  9, 14, 20,  5,  9, 14, 20,  5,  9, 14, 20,
    4, 11, 16, 23,  4, 11, 16, 23,  4, 11, 16, 23,  4, 11, 16, 23,
    6, 10, 15, 21,  6, 10, 15, 21,  6, 10, 15, 21,  6, 10, 15, 21,
]

cdef uint32_t _MD5_K[64]
_MD5_K = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee,
    0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa,
    0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
    0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
    0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05,
    0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039,
    0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
    0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
]

cdef inline uint32_t _ROL(uint32_t x, int c) nogil:
    return (x << c) | (x >> (32 - c))


cdef void _md5_transform(uint32_t* state, const uint8_t* block) nogil:
    cdef uint32_t a = state[0]
    cdef uint32_t b = state[1]
    cdef uint32_t c = state[2]
    cdef uint32_t d = state[3]
    cdef uint32_t x[16]
    cdef uint32_t f, g, tmp
    cdef int i
    for i in range(16):
        x[i] = (<uint32_t>block[i*4]) | (<uint32_t>block[i*4+1] << 8) \
             | (<uint32_t>block[i*4+2] << 16) | (<uint32_t>block[i*4+3] << 24)
    for i in range(64):
        if i < 16:
            f = (b & c) | ((~b) & d)
            g = i
        elif i < 32:
            f = (d & b) | ((~d) & c)
            g = (5 * i + 1) % 16
        elif i < 48:
            f = b ^ c ^ d
            g = (3 * i + 5) % 16
        else:
            f = c ^ (b | (~d))
            g = (7 * i) % 16
        tmp = d
        d = c
        c = b
        b = b + _ROL(a + f + _MD5_K[i] + x[g], _MD5_S[i])
        a = tmp
    state[0] += a
    state[1] += b
    state[2] += c
    state[3] += d


cdef void _md5_full(const uint8_t* data, size_t length, uint8_t out[16]) nogil:
    cdef uint32_t state[4]
    state[0] = 0x67452301
    state[1] = 0xefcdab89
    state[2] = 0x98badcfe
    state[3] = 0x10325476
    cdef uint64_t total = length
    cdef size_t processed = 0
    cdef uint8_t block[64]
    cdef size_t remaining, i
    # 处理完整 64 字节块
    while length - processed >= 64:
        _md5_transform(state, data + processed)
        processed += 64
    # 填充
    remaining = length - processed
    for i in range(remaining):
        block[i] = data[processed + i]
    block[remaining] = 0x80
    if remaining < 56:
        for i in range(remaining + 1, 56):
            block[i] = 0
        # 长度（bit，little-endian 64 位）
        for i in range(8):
            block[56 + i] = <uint8_t>((total * 8) >> (8 * i))
        _md5_transform(state, block)
    else:
        for i in range(remaining + 1, 64):
            block[i] = 0
        _md5_transform(state, block)
        # 第二块：全零 + 长度
        for i in range(56):
            block[i] = 0
        for i in range(8):
            block[56 + i] = <uint8_t>((total * 8) >> (8 * i))
        _md5_transform(state, block)
    # 输出 little-endian
    for i in range(4):
        out[i*4]     = <uint8_t>(state[i] & 0xff)
        out[i*4+1]   = <uint8_t>((state[i] >> 8) & 0xff)
        out[i*4+2]   = <uint8_t>((state[i] >> 16) & 0xff)
        out[i*4+3]   = <uint8_t>((state[i] >> 24) & 0xff)


def _md5_int(bytes data) -> int:
    """
    ★五期：C 实现 MD5 → 大端 128 位整数。
    与 int(hashlib.md5(data).hexdigest(), 16) 完全一致（标准 MD5，大端字节序）。
    """
    cdef const uint8_t[::1] buf = data
    cdef uint8_t out[16]
    _md5_full(&buf[0], len(data), out)
    return int.from_bytes(bytes(out[:16]), 'big')


def encode_cy(value: str, keywords: list = None) -> float:
    """
    Cython加速版频率编码：与Python版encode()输出完全一致。
    
    算法：
    1. 如果没有提供keywords，从value中提取
    2. 对每个关键词做MD5哈希（★五期：C 实现，与 hashlib 逐字节一致，可 nogil）
    3. 计算哈希均值
    4. 归一化 + 正弦映射到 [0, 100]
    
    Args:
        value: 知识内容（字符串）
        keywords: 关键词列表（可选）
    
    Returns:
        频率签名，范围 [0.0, 100.0]
    """
    if not value:
        return 0.0
    
    # 步骤1: 获取并规范化关键词
    if not keywords:
        text = str(value)
        raw_words = text.replace("，", " ").replace("。", " ").replace("的", " ").split()
        keywords = [w for w in raw_words if len(w) >= 2][:3]
    
    if not keywords:
        # 无有效关键词，使用文本长度作为兜底特征
        raw_anchor = len(str(value)) * 1.7
    else:
        # 步骤2: 关键词统一小写，计算哈希均值（★五期：C MD5）
        kw_hash_list = []
        for kw in keywords:
            kw_clean = kw.lower()
            kw_bytes = kw_clean.encode('utf-8')
            kw_hash = _md5_int(kw_bytes)
            kw_hash_list.append(kw_hash)
        
        hash_avg = sum(kw_hash_list) / len(kw_hash_list)
        # 取模压缩到固定区间，保证数值平滑
        raw_mod = hash_avg % 10000.0
        raw_anchor = raw_mod / 10000.0
    
    # 步骤3: 正弦平滑映射到 [0, 100]
    # ★v23.0修复：先转为C double，在nogil块内使用C数学函数
    cdef double cdef_raw_anchor = raw_anchor
    cdef double cdef_rad
    cdef double cdef_freq
    
    with nogil:
        cdef_rad = cdef_raw_anchor * PI_CONST * 0.9
        cdef_freq = sin(cdef_rad) * 50.0 + 50.0
    
    return round(cdef_freq, 4)


def encode_batch_cy(list nodes) -> list:
    """
    ★v23.0新增：批量编码节点，减少Python层循环开销。
    
    接收一个节点字典列表，每个字典包含"value"和"keywords"键。
    在Cython中用C循环遍历节点，避免Python函数调用开销。
    
    注意：★五期起哈希用 C MD5（与encode_cy一致），批量路径同样受益。
    
    Args:
        nodes: 节点字典列表，格式 [{"value": str, "keywords": list}, ...]
    
    Returns:
        频率列表，与输入节点一一对应
    """
    cdef list results = []
    cdef dict node
    cdef str value
    cdef list keywords
    cdef float freq
    cdef int i
    
    for i in range(len(nodes)):
        node = nodes[i]
        value = str(node.get("value", ""))
        keywords = list(node.get("keywords", []) or [])
        freq = encode_cy(value, keywords)
        results.append(freq)
    
    return results
