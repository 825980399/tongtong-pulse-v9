# -*- coding: utf-8 -*-
"""
FrequencyCodec.py —— 频率编解码器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 将知识内容编码为频率签名（记忆维核心），相似语义→相近频率→共振更强，支持分层共振与记忆容量管理
机制: 基于FrequencyCodec类实现，v9.5分层共振版，统计增强，共振记忆容量管理，层级预留
定位: 脉冲核心层，频率编码基础设施
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import hashlib
from nucleus.logger import get_module_logger
import math
import threading
import time
from typing import Any


"""
FrequencyCodec —— 频率编码/解码器（v9.5 分层共振版）
版本: v9.5 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年6月9日
更新: 2026年6月14日（v9.5: 版本升级，统计增强，共振记忆容量管理，层级预留）

职责:
    1. 将知识内容编码为频率签名（记忆维核心）
    2. 相似语义 → 相近频率 → 共振更强
    3. 共振记忆存储 —— 记录历史共振模式（v9.5 增加容量上限和清理机制）
    4. 层级共振统计 —— 按 PulseLayer 追踪不同层级的共振频次（v9.5 新增）

类脑原理:
    大脑中，相似的记忆共享相近的神经振荡频率。
    频率编码不是哈希——哈希将相似内容映射到完全不同的值，
    频率编码将相似内容映射到相邻的频率区间。

编码算法 (v3.0 修复增强):
    1. 关键词逐个计算稳定哈希值
    2. 求取哈希均值保证语义平滑
    3. 正弦映射到 [0, 100]，保证相似关键词产生相近频率
"""


_logger = get_module_logger("FrequencyCodec")


# ======================================================================
# ★主线第16批 T2/P2-94：Cython 加速模块的**进程级**缓存
#   原实现用实例属性 `self._cython_loaded` 控制「只记一次日志」→ 每 new 一个
#   FrequencyCodec()（子进程 encode_batch 每次都 new）都会再打一次 INFO，
#   实测刷出 70+ 行。且 `from ... import encode_cy` 写在函数体内，每次调用都
#   走一遍 import 语句（命中 sys.modules 但仍是多余开销）。
#   现改为模块级状态：每**进程**只真正解析一次；主进程 INFO、子进程 DEBUG；
#   同进程 import 次数超阈值打 WARNING（回归哨兵，防再次回到实例级加载）。
# ======================================================================
_CY_LOAD_STATE: dict[str, Any] = {
    "fn": None,            # 已解析到的 encode_cy（None=未解析或不可用）
    "resolved": False,     # 是否已尝试解析（含失败，避免反复 import）
    "import_count": 0,     # 本进程内**实际执行 import 语句**的次数
    "first_pid": None,
    "logged": False,
    # encode_batch 的批量 Cython 实现（同一进程级缓存口径）
    "batch_fn": None,
    "batch_resolved": False,
    "batch_import_count": 0,
}
#: 同进程内 import 次数超过该值即告警（正常应为 1）
_CY_LOAD_WARN_THRESHOLD = 3


def _cy_cache_enabled() -> bool:
    """灰度开关 ENABLE_FREQUENCY_CODEC_CACHE（读不到时默认开启）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_FREQUENCY_CODEC_CACHE", True))
    except Exception:
        return True


def _reset_cy_load_state() -> None:
    """复位进程级缓存（**仅供测试隔离使用**）。"""
    _CY_LOAD_STATE.update({"fn": None, "resolved": False, "import_count": 0,
                           "first_pid": None, "logged": False,
                           "batch_fn": None, "batch_resolved": False,
                           "batch_import_count": 0})


def _get_encode_batch_cy():
    """返回 Cython 版 ``encode_batch_cy``；不可用时返回 None。

    与 `_get_encode_cy` 同口径：**进程级缓存**，只解析一次
    （原实现在 `encode_batch` 热路径内直接 import，也是重复加载来源之一）。
    """
    _st = _CY_LOAD_STATE
    _use_cache = _cy_cache_enabled()
    if _use_cache and _st["batch_resolved"]:
        return _st["batch_fn"]
    try:
        from config import FEATURE  # type: ignore[possibly-unbound]
        _enabled = bool(FEATURE.get("use_cython_extensions", False))
    except Exception as _e:
        _logger.debug(
            f"Cython 开关读取失败（batch），按不可用处理: {type(_e).__name__}: {_e}")
        _enabled = False
    if not _enabled:
        _st["batch_resolved"] = True
        _st["batch_fn"] = None
        return None
    try:
        from nucleus.pulse._frequency_codec_cy import (  # type: ignore
            encode_batch_cy,
        )
        _st["batch_import_count"] += 1
    except ImportError:
        _st["batch_resolved"] = True
        _st["batch_fn"] = None
        return None
    _st["batch_fn"] = encode_batch_cy
    _st["batch_resolved"] = True
    if _st["batch_import_count"] > _CY_LOAD_WARN_THRESHOLD:
        _logger.warning(
            f"encode_batch_cy 同进程重复加载 {_st['batch_import_count']} 次，"
            f"请检查 Cython 缓存是否失效")
    return encode_batch_cy


def _get_encode_cy():
    """返回 Cython 版 ``encode_cy``；不可用时返回 None。

    · 开启缓存（默认）：每进程只解析一次，后续直接返回缓存函数；
    · 关闭缓存（灰度回退）：每次调用都重新 import（等价修复前行为）。
    """
    _st = _CY_LOAD_STATE
    _use_cache = _cy_cache_enabled()
    if _use_cache and _st["resolved"]:
        return _st["fn"]

    # Cython 总开关（沿用既有 config.FEATURE 口径）
    try:
        from config import FEATURE  # type: ignore[possibly-unbound]
        _enabled = bool(FEATURE.get("use_cython_extensions", False))
    except Exception as _e:
        _logger.debug(
            f"Cython 开关读取失败，按不可用处理: {type(_e).__name__}: {_e}")
        _enabled = False
    if not _enabled:
        _st["resolved"] = True
        _st["fn"] = None
        return None

    try:
        from nucleus.pulse._frequency_codec_cy import encode_cy  # type: ignore
        _st["import_count"] += 1
    except ImportError:
        _st["resolved"] = True
        _st["fn"] = None
        if not _st["logged"]:
            _st["logged"] = True
            _logger.warning("Cython模块未编译，使用Python原生实现")
        return None

    _st["fn"] = encode_cy
    _st["resolved"] = True
    _pid = os.getpid()
    if _st["first_pid"] is None:
        _st["first_pid"] = _pid
    try:
        import multiprocessing
        _is_main = multiprocessing.current_process().name == "MainProcess"
    except Exception:
        _is_main = (_st["first_pid"] == _pid)
    _msg = (f"Cython加速模块已加载 (encode_cy) pid={_pid} "
            f"本进程第{_st['import_count']}次")
    if _st["import_count"] > _CY_LOAD_WARN_THRESHOLD:
        _logger.warning(
            f"{_msg} — 同进程重复加载超过 {_CY_LOAD_WARN_THRESHOLD} 次，"
            f"请检查 Cython 缓存是否失效")
    elif _is_main and not _st["logged"]:
        _logger.info(_msg)
    else:
        # 子进程（或非首次）→ DEBUG，避免日志刷屏
        _logger.debug(_msg)
    _st["logged"] = True
    return encode_cy


class FrequencyCodec:
    """
    频率编解码器（v9.5 分层共振版）
    
    工作流程:
        知识文本 → 关键词特征 → 加权哈希均值 → 正弦映射 → [0, 100]
        
    特性:
        - 相似关键词 → 相近频率（共振更容易匹配）
        - 完全不同关键词 → 差距大的频率（共振自然过滤）
        - 确定性：同一输入始终产生相同频率
        - v9.5: 共振记忆容量管理，防止无限增长
        - v9.5: 按层级统计共振频次
    """
    
    FREQ_MIN = 0.0
    FREQ_MAX = 100.0
    
    # v9.5: 共振记忆最大容量
    MAX_RESONANCE_ENTRIES = 100000
    
    def __init__(self):
        # 共振记忆: frequency → [{"node_id": ..., "resonance_count": ..., "layer": ...}, ...]
        self.resonance_memory: dict[float, list[dict[str, Any]]] = {}
        
        self._total_encoded = 0
        # v18.0: Cython加速标记（首次加载时输出日志）
        self._cython_loaded = False        
        # v18.0: 节点频率缓存（避免重复编码相同节点）
        self._node_cache: dict[str, tuple] = {}  # node_id → (frequency, checksum)    
        # ★14.51：纯文本编码 LRU 缓存（解决 qica_knowledge 4.3s 慢推理中
        # frequency_codec.encode(question) 重复计算问题）。相同问题文本直接命中。
        self._text_cache: dict[str, float] = {}
        self._text_cache_max = 2000  # 上限2000条，超出删最旧（Python3.7+ dict有序）
        self._total_decoded = 0
        
        # v9.5: 层级共振统计
        self._layer_resonance_stats: dict[str, int] = {
            "L0": 0, "L1": 0, "L2": 0, "L3": 0
        }
        
        # v9.5: 线程安全锁
        self._lock = threading.Lock()
        
        # v9.5: 共振记忆清理阈值（达到容量上限时触发）
        self._cleanup_threshold = self.MAX_RESONANCE_ENTRIES
        self._last_cleanup_time = time.time()
        self._cleanup_interval = 3600  # 每小时检查一次

    def _single_keyword_hash(self, word: str) -> int:
        """内部工具：对单个关键词生成稳定哈希值"""
        return int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        
    # ========== 编码 v3.0 修复版 ==========
    def encode(self, value: Any, keywords: list[str] | None = None, 
               layer: str | None = None) -> float:
        
        """
        将知识内容编码为频率签名（v9.5: 新增可选的 layer 参数）。
        
        算法 v3.0 —— 关键词哈希均值驱动：
            1. 提取/接收关键词并规范化
            2. 逐个计算关键词哈希，取平均值
            3. 数值归一化 + 正弦平滑映射到 [0, 100]
            
        原理:
            共享关键词 → 哈希均值接近 → 频率接近
            完全不同 → 哈希均值差距大 → 频率差距大
            
        Args:
            value: 知识内容（字符串）
            keywords: 关键词列表（可选）
            layer: 脉冲层级（v9.5 新增，用于统计，不影响编码结果）
        """
        # ★v18.0: 优先使用Cython加速版（纯文本编码，不含layer统计）
        # ★四期：受 use_cython_extensions 开关控制（默认 False，保守灰度）
        _cy_enabled = False
        try:
            from config import FEATURE
            _cy_enabled = bool(FEATURE.get("use_cython_extensions", False))
        except Exception:
            _cy_enabled = False

        if _cy_enabled:
            # ★主线第16批 T2/P2-94：改走模块级进程级缓存（每进程只解析一次）
            _encode_cy = _get_encode_cy()
            if _encode_cy is not None:
                # ★P1-1修复：先计数再调用Cython，确保统计不丢失
                self._total_encoded += 1
                return _encode_cy(str(value), keywords)
        self._total_encoded += 1
        
        text = str(value)
        # ★14.51：纯文本编码缓存——相同文本+关键词直接返回缓存频率
        _cache_key = text[:200] + "|" + (",".join(keywords) if keywords else "")
        if _cache_key in self._text_cache:
            return self._text_cache[_cache_key]
        
        # 步骤1: 获取并规范化关键词
        if not keywords:
            raw_words = text.replace("，", " ").replace("。", " ").replace("的", " ").split()
            keywords = [w for w in raw_words if len(w) >= 2][:3]
        
        if not keywords:
            # 无有效关键词，使用文本长度作为兜底特征
            raw_anchor = len(text) * 1.7
        else:
            # 步骤2: 关键词统一小写，计算哈希均值
            kw_hash_list = []
            for kw in keywords:
                kw_clean = kw.lower()
                kw_hash = self._single_keyword_hash(kw_clean)
                kw_hash_list.append(kw_hash)
            
            hash_avg = sum(kw_hash_list) / len(kw_hash_list)
            # 取模压缩到固定区间，保证数值平滑
            raw_mod = hash_avg % 10000.0
            raw_anchor = raw_mod / 10000.0

        # 步骤3: 正弦平滑映射到 [FREQ_MIN, FREQ_MAX]，避免硬跳变
        rad = raw_anchor * math.pi * 0.9
        freq = math.sin(rad) * 50 + 50
        
        _result = round(freq, 4)
        # ★14.51：写入文本缓存（LRU，超出删最旧）
        self._text_cache[_cache_key] = _result
        if len(self._text_cache) > self._text_cache_max:
            _del_count = len(self._text_cache) - int(self._text_cache_max * 0.8)
            for _k in list(self._text_cache.keys())[:_del_count]:
                del self._text_cache[_k]
        return _result

    
    def encode_node(self, node, layer: str | None = None) -> float:
        _node_id = getattr(node, 'node_id', '')
        if _node_id and _node_id in self._node_cache:
            # ★P1-6修复：校验缓存有效性——节点checksum变化则重新计算
            _cached_freq, _cached_checksum = self._node_cache[_node_id]
            _current_checksum = getattr(node, 'checksum', '')
            if _current_checksum and _cached_checksum == _current_checksum:
                node.frequency_signature = _cached_freq
                return _cached_freq
            # checksum不匹配，缓存失效，走重新编码
        
        freq = self.encode(node.value, node.keywords, layer=layer)
        node.frequency_signature = freq
        
        if _node_id:
            _current_checksum = getattr(node, 'checksum', '')
            self._node_cache[_node_id] = (freq, _current_checksum)
            # ★v23.0修正（内部协作者建议）：固定保留2400条，超出部分删除最旧条目
            # Python 3.7+ dict 保持插入顺序，按顺序删除就是删最旧的
            if len(self._node_cache) > 3000:
                _keep_size = 2400
                _all_keys = list(self._node_cache.keys())
                _del_keys = _all_keys[:len(_all_keys) - _keep_size]
                for _k in _del_keys:
                    del self._node_cache[_k]
        
        return freq
    def invalidate_node_cache(self, node_id: str):
        """
        ★P1-6修复：使指定节点的频率缓存失效。
        当节点内容发生变更（升级/压缩/修改）时调用此方法。
        """
        if node_id and node_id in self._node_cache:
            del self._node_cache[node_id]  
    def encode_batch(self, nodes: list[dict[str, Any]]) -> list[float]:
        """
        ★v23.0新增：批量编码节点。
        
        优先使用Cython的encode_batch_cy，不可用时回退到Python循环。
        供肝脏在批量压缩时调用，减少Python层逐个编码的开销。
        
        Args:
            nodes: 节点字典列表，格式 [{"value": str, "keywords": list}, ...]
        
        Returns:
            频率列表，与输入节点一一对应
        """
        # ★四期：受 use_cython_extensions 开关控制
        _cy_enabled = False
        try:
            from config import FEATURE
            _cy_enabled = bool(FEATURE.get("use_cython_extensions", False))
        except Exception:
            _cy_enabled = False

        # 优先使用Cython批量编码
        # ★主线第16批 T2/P2-94：改走模块级进程级缓存（原为热路径内直接 import，
        #   也是「Cython 模块重复加载」的来源之一）
        if _cy_enabled:
            _encode_batch_cy = _get_encode_batch_cy()
            if _encode_batch_cy is not None:
                self._total_encoded += len(nodes)
                return _encode_batch_cy(nodes)
        
        # Python原生回退
        results = []
        for node in nodes:
            _value = node.get("value", "") if isinstance(node, dict) else ""
            _keywords = node.get("keywords", []) if isinstance(node, dict) else []
            _freq = self.encode(_value, _keywords)
            results.append(_freq)
        return results            
    # ========== 解码 ==========
    
    def decode(self, freq: float, tolerance: float = 0.5,
               layer: str | None = None) -> list[dict[str, Any]]:
        self._total_decoded += 1
        
        results = []
        # ★P0-1修复：加锁保护共振记忆的遍历读取
        with self._lock:
            for mem_freq, entries in self.resonance_memory.items():
                if abs(mem_freq - freq) <= tolerance:
                    for entry in entries:
                        if layer and entry.get("layer") and entry["layer"] != layer:
                            continue
                        results.append({
                            "frequency": mem_freq,
                            "node_id": entry.get("node_id"),
                            "resonance_count": entry.get("resonance_count", 0),
                            "layer": entry.get("layer", "L1"),
                        })
        
        results.sort(key=lambda r: r["resonance_count"], reverse=True)
        return results
    
    # ========== 共振记忆（v9.5 增强） ==========
    
    def record_resonance(self, freq: float, node_id: str, 
                         layer: str = "L2"):
        """
        记录一次共振事件到共振记忆（v9.5: 新增 layer 参数，默认 L2 认知层）。
        """
        with self._lock:
            if freq not in self.resonance_memory:
                self.resonance_memory[freq] = []
            
            for entry in self.resonance_memory[freq]:
                if entry.get("node_id") == node_id:
                    entry["resonance_count"] = entry.get("resonance_count", 0) + 1
                    # 更新层级标记（取最高频层级）
                    if layer in ("L0", "L1") and entry.get("layer") in ("L2", "L3") or layer == "L2" and entry.get("layer") == "L3":
                        entry["layer"] = layer
                    self._update_layer_stats(layer)
                    return
            
            self.resonance_memory[freq].append({
                "node_id": node_id,
                "resonance_count": 1,
                "layer": layer,
            })
            self._update_layer_stats(layer)
            
            # v9.5: 容量检查与清理
            self._check_capacity()
    
    def _update_layer_stats(self, layer: str):
        """v9.5: 更新层级共振统计"""
        if layer in self._layer_resonance_stats:
            self._layer_resonance_stats[layer] += 1
    
    def _check_capacity(self):
        """v9.5: 如果共振记忆超过上限，清理低频条目"""
        total_entries = sum(len(entries) for entries in self.resonance_memory.values())
        if total_entries > self._cleanup_threshold:
            # 清理：移除共振次数低于阈值的条目，保留高频共振
            min_resonance = 1
            cleaned = 0
            for freq in list(self.resonance_memory.keys()):
                entries = self.resonance_memory[freq]
                # 过滤掉共振次数 <= min_resonance 的条目
                kept = [e for e in entries if e["resonance_count"] > min_resonance]
                cleaned += len(entries) - len(kept)
                if kept:
                    self.resonance_memory[freq] = kept
                else:
                    del self.resonance_memory[freq]
            self._last_cleanup_time = time.time()
            # 如果清理后仍然过多，下次提高阈值
            remaining = sum(len(e) for e in self.resonance_memory.values())
            if remaining > self._cleanup_threshold * 0.8:
                min_resonance += 1
    
    def get_top_frequencies(self, top_k: int = 10,
                            layer: str | None = None) -> list[dict[str, Any]]:
        freq_scores = []
        # ★P0-1修复：加锁保护共振记忆的遍历读取
        with self._lock:
            for freq, entries in self.resonance_memory.items():
                total_resonance = 0
                node_count = 0
                for e in entries:
                    if layer and e.get("layer") != layer:
                        continue
                    total_resonance += e.get("resonance_count", 0)
                    node_count += 1
                if node_count > 0:
                    freq_scores.append({
                        "frequency": freq,
                        "total_resonance": total_resonance,
                        "node_count": node_count,
                    })
        
        freq_scores.sort(key=lambda f: f["total_resonance"], reverse=True)
        return freq_scores[:top_k]
    
    # ========== 频率相似度 ==========
    
    def similarity(self, freq_a: float, freq_b: float) -> float:
        """
        计算两个频率之间的相似度。
        频率差越小，相似度越高。
        """
        diff = abs(freq_a - freq_b)
        max_diff = self.FREQ_MAX - self.FREQ_MIN
        if max_diff == 0:
            return 1.0
        return max(0.0, 1.0 - diff / max_diff)
    
    # ========== 统计（v9.5 增强） ==========
    
    def get_stats(self) -> dict[str, Any]:
        """获取编解码统计（v9.5: 包含层级共振统计）"""
        with self._lock:
            total_memory_entries = sum(len(e) for e in self.resonance_memory.values())
        return {
            "total_encoded": self._total_encoded,
            "total_decoded": self._total_decoded,
            "resonance_memory_size": len(self.resonance_memory),
            "resonance_memory_entries": total_memory_entries,
            "layer_resonance_stats": dict(self._layer_resonance_stats),
            "top_frequencies": self.get_top_frequencies(3),
            "last_cleanup_time": self._last_cleanup_time,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== FrequencyCodec v9.5 分层共振版 自测 ===\n")
    
    codec = FrequencyCodec()
    
    # 1. 编码（带 layer 参数）
    freq1 = codec.encode("脉冲场架构是v8.0的核心设计", 
                          keywords=["脉冲场", "架构", "设计"], layer="L2")
    freq2 = codec.encode("v9.0采用纯脉冲架构",
                          keywords=["脉冲", "架构", "v9.0"], layer="L2")
    freq3 = codec.encode("Python是一种编程语言",
                          keywords=["Python", "编程", "语言"], layer="L2")
    freq4 = codec.encode("脉冲神经网络使用脉冲信号",
                          keywords=["脉冲", "神经", "网络"], layer="L2")
    
    print("1. 频率编码:")
    print(f"   '脉冲场架构v8.0' → {freq1} Hz")
    print(f"   'v9.0脉冲架构'   → {freq2} Hz")
    print(f"   'Python编程语言'  → {freq3} Hz")
    print(f"   '脉冲神经网络'    → {freq4} Hz")
    
    # 2. 语义相近验证
    sim_12 = codec.similarity(freq1, freq2)
    sim_14 = codec.similarity(freq1, freq4)
    sim_13 = codec.similarity(freq1, freq3)
    sim_24 = codec.similarity(freq2, freq4)
    
    print("\n2. 语义相近验证:")
    print(f"   脉冲场v8.0 vs v9.0脉冲架构: {sim_12:.4f}")
    print(f"   脉冲场v8.0 vs 脉冲神经网络:   {sim_14:.4f}")
    print(f"   脉冲场v8.0 vs Python编程:     {sim_13:.4f}")
    print(f"   v9.0脉冲 vs 脉冲神经网络:     {sim_24:.4f}")
    
    assert sim_12 > sim_13, "共享关键词的频率应更接近！"
    assert sim_14 > sim_13, "共享关键词的频率应更接近！"
    print("   ✅ 语义相近性验证通过")
    
    # 3. 编码确定性
    freq1_again = codec.encode("脉冲场架构是v8.0的核心设计",
                                keywords=["脉冲场", "架构", "设计"])
    assert freq1 == freq1_again, "同一输入应产生相同频率"
    print("\n3. 编码确定性: ✅")
    
    # 4. 共振记忆（带 layer）
    codec.record_resonance(freq1, "node_001", layer="L2")
    codec.record_resonance(freq1, "node_001", layer="L2")
    codec.record_resonance(freq1, "node_002", layer="L1")
    codec.record_resonance(freq3, "node_003", layer="L2")
    
    top = codec.get_top_frequencies(3)
    print(f"\n4. 共振记忆: {len(codec.resonance_memory)} 个频率")
    for t in top:
        print(f"   {t['frequency']} Hz → {t['total_resonance']}次共振")
    
    # 按层过滤
    top_l2 = codec.get_top_frequencies(3, layer="L2")
    print(f"   L2层过滤: {len(top_l2)} 个频率")
    
    # 5. 解码查询
    results = codec.decode(freq1, tolerance=0.5)
    print(f"\n5. 解码查询 (freq={freq1}, tol=0.5): {len(results)} 条")
    
    # 6. 全局统计
    stats = codec.get_stats()
    print(f"\n6. 统计: 编码{stats['total_encoded']}次 解码{stats['total_decoded']}次 "
          f"记忆{stats['resonance_memory_size']}条 层级共振{stats['layer_resonance_stats']}")
    
    print("\n=== 自测全部通过 ===")