# -*- coding: utf-8 -*-
"""
VectorEncoder.py —— 向量编码器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 文本到向量的编码服务
机制: 基于VectorEncoder类实现，包含10个核心方法
定位: 语义基础设施层
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

import numpy as np

from nucleus.logger import get_module_logger


_module_logger = get_module_logger("VectorEncoder")

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# ★文本预处理（PHASE17 阶段一 · 门控调优，2026-09-07 路灯）
# ------------------------------------------------------------
# 为什么必须有这一段（实测证据，见 tools/probe_term_collapse.py）：
#   bge-small-zh 的 WordPiece 遇到「未登录的连续 ASCII 串」会整段塌成 [UNK]。
#   官方 bert-base-chinese 是 do_lower_case=True，而 fastembed 导出的
#   tokenizer.json **缺 Normalizer**，推理端没做 lowercase —— 等于喂错别字。
#   实测同一批术语的向量**逐位完全相同**：
#     PulseNodePool / PHASE17 / InfoField / SafeEvolutionExecutor /
#     PulseInnerWorld  →  ['[CLS]', '[UNK]', '[SEP]']  → 5 个向量一模一样
#   后果：专有名词检索 P@1 只有 60%（5 个术语互相无法区分）。
#
# 处理规则（只作用于 ASCII 段，中文不受影响）：
#   1. 缩写-单词边界：HTTPServer → HTTP Server
#   2. 小驼峰边界：   PulseNodePool → Pulse Node Pool
#   3. 字母/数字边界：PHASE17 → PHASE 17
#   4. ASCII 段转小写（对齐官方 do_lower_case，让 "pulse" 进入词表）
#   实测效果：专有名词 P@1 60% → 100%，正例 MRR 0.567 → 0.572（不退化）。
#
# ★改这里必须同步改 PREPROCESS_ID —— 否则旧向量库会被静默复用（语义空间已变）。
# ============================================================
PREPROCESS_ID = "v1"

_ASCII_RUN = re.compile(r"[A-Za-z0-9]+")
_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")   # HTTPServer
_CAMEL_LOWER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")       # PulseNodePool
_DIGIT_BOUND = re.compile(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def preprocess_text(text: str, split_identifiers: bool = True,
                    lowercase_ascii: bool = True) -> str:
    """★编码前的文本归一化。纯函数，无副作用，可单独测试。

    Args:
        text: 原始文本
        split_identifiers: 是否拆 ASCII 标识符（默认 True）
        lowercase_ascii: 是否把 ASCII 段转小写（默认 True）

    Returns:
        归一化后的文本（中文原样保留）
    """
    if not text:
        return text
    if not split_identifiers and not lowercase_ascii:
        return text

    def _fix(m: re.Match[str]) -> str:
        s = m.group(0)
        if split_identifiers:
            s = _CAMEL_ACRONYM.sub(" ", s)
            s = _CAMEL_LOWER.sub(" ", s)
            s = _DIGIT_BOUND.sub(" ", s)
        return s.lower() if lowercase_ascii else s

    return _ASCII_RUN.sub(_fix, text)


def _load_config() -> dict[str, Any]:
    """读取 SEMANTIC_KERNEL_CONFIG，任何异常都回落到「关闭」。"""
    try:
        import config as _cfg
        _raw = getattr(_cfg, "SEMANTIC_KERNEL_CONFIG", {})
        return _raw if isinstance(_raw, dict) else {}
    except Exception:
        return {}


class VectorEncoder:
    """本地中文语义编码器（单例、懒加载、零风险降级）。"""

    _instance: VectorEncoder | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._cfg = _load_config()
        self._model = None
        self._state = "idle"          # idle | loading | ready | disabled
        self._state_reason = ""
        self._load_thread: threading.Thread | None = None
        self._load_attempted = False
        self._encode_count = 0
        self._fail_count = 0
        self._last_error = ""
        self._lock_self = threading.RLock()

        # 开关关闭 → 直接 disabled（行为与现状完全一致）
        if not self._cfg.get("enable_semantic_kernel", False):
            self._state = "disabled"
            self._state_reason = "开关 enable_semantic_kernel=False（灰度未开启）"

    # ---------------- 单例 ----------------
    @classmethod
    def get_instance(cls) -> VectorEncoder:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---------------- 状态查询 ----------------
    def is_available(self) -> bool:
        """模型是否可用。不可用时应回落关键词通道。"""
        if self._state == "ready":
            return True
        if self._state == "disabled":
            return False
        # idle → 触发后台懒加载，本次先返回不可用（不阻塞调用方）
        self._ensure_loading()
        return False

    def status(self) -> dict[str, Any]:
        """★验收用：完整状态快照（阶段一门控要求打印执行路径）。"""
        return {
            "state": self._state,
            "reason": self._state_reason,
            "model": self._cfg.get("model_name", ""),
            "expected_dim": self._cfg.get("expected_dim", 512),
            "cpu_only": self._cfg.get("cpu_only", True),
            "encode_count": self._encode_count,
            "fail_count": self._fail_count,
            "last_error": self._last_error,
            "has_numpy": True,
        }

    # ---------------- 懒加载 ----------------
    def _ensure_loading(self) -> None:
        """首次需要时触发后台加载（不阻塞调用方）。"""
        with self._lock_self:
            if self._load_attempted or self._state in ("loading", "ready", "disabled"):
                return
            self._load_attempted = True
            self._state = "loading"
            self._state_reason = "后台加载中"
        try:
            t = threading.Thread(target=self._load_model, name="VectorEncoderLoad",
                                 daemon=True)
            self._load_thread = t
            t.start()
        except Exception as _e:
            with self._lock_self:
                self._state = "disabled"
                self._state_reason = f"加载线程启动失败: {_e}"

    def _load_model(self) -> None:
        """后台加载模型。三路镜像依次尝试，全部失败则 disabled。"""
        model_name = self._cfg.get("model_name", "BAAI/bge-small-zh-v1.5")
        cache_dir = os.path.join(_PROJECT_ROOT,
                                 self._cfg.get("cache_dir", "data/models"))
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass

        # 尝试顺序：ModelScope > hf-mirror > 直连 HF（星轨 Q6 决策）
        mirrors = self._cfg.get("download_mirrors") or [
            "https://www.modelscope.cn", "https://hf-mirror.com",
            "https://huggingface.co",
        ]
        last_err = ""
        for mirror in mirrors:
            try:
                # fastembed 底层走 huggingface_hub，用 HF_ENDPOINT 切镜像；
                # ModelScope 走的是其兼容端点。
                os.environ["HF_ENDPOINT"] = str(mirror)
                from fastembed import TextEmbedding
                _t0 = time.time()
                self._model = TextEmbedding(
                    model_name=model_name,
                    cache_dir=cache_dir,
                    threads=2 if self._cfg.get("cpu_only", True) else None,
                )
                _cost = time.time() - _t0
                with self._lock_self:
                    self._state = "ready"
                    self._state_reason = f"已加载（镜像 {mirror}，耗时 {_cost:.1f}s）"
                _module_logger.info(
                    f"[语义内核] 模型就绪: {model_name} | 镜像={mirror} | "
                    f"维度={self._cfg.get('expected_dim', 512)} | CPU_ONLY | 耗时={_cost:.1f}s")
                return
            except Exception as _e:
                last_err = f"{type(_e).__name__}: {_e}"
                _module_logger.debug(f"[语义内核] 镜像 {mirror} 加载失败: {last_err}")
                continue

        with self._lock_self:
            self._state = "disabled"
            self._state_reason = f"全部镜像加载失败（{last_err}）"
            self._last_error = last_err
        _module_logger.warning(
            f"[语义内核] 模型不可用，走关键词通道（框架照常运行）: {last_err}")

    # ---------------- 编码 ----------------
    def encode(self, texts: list[str]) -> np.ndarray | None:
        """批量编码。

        Returns:
            np.ndarray (N, dim)，已 L2 归一化；失败返回 None（调用方回落关键词）。
        """
        if not texts:
            return np.zeros((0, self._cfg.get("expected_dim", 512)), dtype=np.float32)
        if not self.is_available():
            return None

        # ★文本预处理（修复 ASCII 标识符塌成 [UNK]，见文件头说明）
        if self._cfg.get("text_preprocess", True):
            texts = [preprocess_text(
                t,
                split_identifiers=bool(self._cfg.get("identifier_split", True)),
                lowercase_ascii=bool(self._cfg.get("ascii_lowercase", True)),
            ) for t in texts]

        try:
            _dim = int(self._cfg.get("expected_dim", 512))
            _bs = int(self._cfg.get("batch_size", 32))
            raw = list(self._model.embed(texts, batch_size=_bs))
            arr = np.asarray(raw, dtype=np.float32)
            if arr.ndim != 2 or arr.shape[0] != len(texts) or arr.shape[1] != _dim:
                self._fail_count += 1
                self._last_error = f"维度异常: 期望 ({len(texts)}, {_dim})，实际 {arr.shape}"
                _module_logger.warning(f"[语义内核] {self._last_error}")
                return None
            # ★质量门禁：L2 归一化 + 校验（红线 6.2 断言点 3）
            norm = np.linalg.norm(arr, axis=1, keepdims=True)
            norm[norm == 0] = 1.0
            arr = arr / norm
            self._encode_count += len(texts)
            return arr
        except Exception as _e:
            self._fail_count += 1
            self._last_error = f"{type(_e).__name__}: {_e}"
            _module_logger.warning(f"[语义内核] 编码失败（回落关键词）: {_e}")
            return None

    def encode_one(self, text: str) -> np.ndarray | None:
        """单条编码，返回 (dim,) 或 None。"""
        if not text:
            return None
        r = self.encode([text])
        return None if r is None or len(r) == 0 else r[0]

    # ---------------- 质量校验（供入库前调用） ----------------
    def verify_vector(self, vec: np.ndarray) -> tuple[bool, str]:
        """★红线断言点 3：向量入库前校验。

        Returns: (是否合格, 原因)
        """
        try:
            if vec is None:
                return False, "向量为空"
            arr = np.asarray(vec, dtype=np.float32).ravel()
            dim = int(self._cfg.get("expected_dim", 512))
            if arr.shape[0] != dim:
                return False, f"维度不符: {arr.shape[0]} != {dim}"
            n = float(np.linalg.norm(arr))
            tol = float(self._cfg.get("norm_tolerance", 0.01))
            if abs(n - 1.0) > tol:
                return False, f"L2 norm={n:.4f}，超出容差 ±{tol}"
            if not np.isfinite(arr).all():
                return False, "含 NaN/Inf"
            return True, "ok"
        except Exception as _e:
            return False, f"校验异常: {type(_e).__name__}: {_e}"

    # ---------------- 相似度（向量检索核心） ----------------
    @staticmethod
    def cosine_matrix(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
        """query(1,dim) 与 matrix(N,dim) 的余弦相似度。

        实现选择（星轨 Q2 决策 1：优先复用 _cosine_cpu_cy，缺失回落纯 numpy）：
            - 默认 numpy：已归一化前提下余弦=点积，一次矩阵乘法完成（全 C 层）。
            - retrieval_backend="cython" 且候选数 ≤ topk_cython_max 时，
              尝试 `_cosine_cpu_cy.batch_cosine_cy`（现成资产），失败静默回落 numpy。

        ★实测（2026-09-07 沙箱，9958×512）—— 为什么默认不走 Cython：
            numpy 矩阵乘 + argpartition = 3.57 ms
            转 list（Cython 函数签名必需入参）= 274 ms（510 万个 Python float 对象）
            → 全量场景 Cython 反而慢 78 倍，故只在候选已缩小后使用。
        """
        try:
            q = np.asarray(query_vec, dtype=np.float32).ravel()
            m = np.asarray(matrix, dtype=np.float32)
            if m.size == 0:
                return np.zeros((0,), dtype=np.float32)

            cfg = _load_config()
            backend = cfg.get("retrieval_backend", "numpy")
            cython_max = int(cfg.get("topk_cython_max", 500))

            if backend == "cython" and m.shape[0] <= cython_max:
                cy_out = VectorEncoder._cosine_cython(q, m)
                if cy_out is not None:
                    return cy_out
                # 未编译 / 不兼容 → 静默回落 numpy（下方继续）

            # 防御：若未归一化则先归一化，保证余弦语义正确
            qn = float(np.linalg.norm(q))
            if qn > 0 and abs(qn - 1.0) > 1e-3:
                q = q / qn
            return (m @ q).astype(np.float32)
        except Exception as _e:
            _module_logger.debug(f"[语义内核] 余弦矩阵计算失败: {_e}")
            return np.zeros((0,), dtype=np.float32)

    @staticmethod
    def _cosine_cython(q: np.ndarray, m: np.ndarray) -> np.ndarray | None:
        """尝试用 `_cosine_cpu_cy.batch_cosine_cy` 计算，不可用返回 None。

        注意：该函数入参是 Python list，需 .tolist() 转换（小候选集才划算）。
        """
        try:
            from nucleus.gpu._cosine_cpu_cy import batch_cosine_cy
            out = batch_cosine_cy(q.tolist(), [row.tolist() for row in m])
            if out is None or len(out) != m.shape[0]:
                return None
            return np.asarray(out, dtype=np.float32)
        except Exception:
            return None

    @classmethod
    def topk(cls, sims: np.ndarray, k: int) -> list[tuple[int, float]]:
        """从相似度数组取 top-k，返回 [(index, score), ...] 降序。

        小候选集（≤ topk_cython_max）时尝试复用 `_topk_retrieve_cy`（现成资产），
        否则用 numpy.argpartition（实测全量场景快 78 倍）。
        """
        try:
            arr = np.asarray(sims, dtype=np.float32).ravel()
            if arr.size == 0 or k <= 0:
                return []
            k = min(k, arr.size)

            cfg = _load_config()
            backend = cfg.get("retrieval_backend", "numpy")
            cython_max = int(cfg.get("topk_cython_max", 500))

            if backend == "cython" and arr.size <= cython_max:
                try:
                    from nucleus.reasoning._topk_retrieve_cy import topk_retrieve_cy
                    # 候选已很小，转换开销可接受
                    r = topk_retrieve_cy(arr.tolist(),
                                         [[float(x)] for x in arr.tolist()], k)
                    if r:
                        return [(int(i), float(s)) for i, s in r]
                except Exception:
                    pass  # 未编译或不兼容 → 回落 numpy

            idx = np.argpartition(-arr, k - 1)[:k]
            idx = idx[np.argsort(-arr[idx])]
            return [(int(i), float(arr[i])) for i in idx]
        except Exception as _e:
            _module_logger.debug(f"[语义内核] topk 失败: {_e}")
            return []

    # ---------------- 相似度采纳门（★门控调优核心） ----------------
    @staticmethod
    def apply_score_gate(sims: np.ndarray, cfg: dict | None = None
                         ) -> np.ndarray:
        """★判定哪些候选算「真的命中」——负例误召回的修复点。

        背景（2026-09-07 实测，见 tools/diag_semantic_scores.py）：
          bge-small-zh 在本语料上的余弦**被严重压缩**：
            语料两两余弦 mean=0.4335 std=0.0800，异类均值 0.4204 vs 同类 0.4605
            （差距仅 0.04）。正例目标分 0.42~0.77，负例误召分 0.50~0.63 ——
            **两个分布几乎完全重叠**，任何一个固定绝对阈值都切不开。
          → 用绝对阈值做门控在数学上不成立，必须改成「相对本次查询的突出程度」。

        四种模式（cfg["score_gate_mode"]）：
          none      全部采纳（改动前的现状，等价零门控）
          absolute  sims >= similarity_threshold
          adaptive  查询内 z-score >= zscore_threshold 且 sims >= absolute_floor
          peak      sims >= peak_ratio × sims.max()
                    ★实测最优：负例误召回 90%→0%，F1 0.345→0.500

        Returns:
            bool 掩码，True = 采纳该候选
        """
        try:
            arr = np.asarray(sims, dtype=np.float64).ravel()
            if arr.size == 0:
                return np.zeros((0,), dtype=bool)
            cfg = cfg if isinstance(cfg, dict) else (_load_config() or {})
            mode = str(cfg.get("score_gate_mode", "peak") or "peak").lower()

            if mode == "none":
                return np.ones(arr.shape, dtype=bool)

            if mode == "absolute":
                return arr >= float(cfg.get("similarity_threshold", 0.35))

            if mode == "adaptive":
                mu = float(arr.mean())
                sd = float(arr.std())
                sd = max(sd, 1e-9)
                z = (arr - mu) / sd
                ok = z >= float(cfg.get("zscore_threshold", 2.5))
                floor = float(cfg.get("absolute_floor", 0.0))
                if floor > 0:
                    ok = ok & (arr >= floor)
                return ok

            # 默认 peak（相对峰值门）
            ratio = float(cfg.get("peak_ratio", 0.95))
            mx = float(arr.max())
            if mx <= 0:
                return np.zeros(arr.shape, dtype=bool)
            return arr >= ratio * mx
        except Exception as _e:
            _module_logger.debug(f"[语义内核] 采纳门计算失败，退回全采纳: {_e}")
            return np.ones(np.asarray(sims).shape, dtype=bool)


# ============================ 便捷入口 ============================
def get_vector_encoder() -> VectorEncoder:
    """获取全局单例。"""
    return VectorEncoder.get_instance()


def is_semantic_available() -> bool:
    """语义内核是否可用（供检索侧判断是否走向量通道）。"""
    try:
        return get_vector_encoder().is_available()
    except Exception:
        return False
