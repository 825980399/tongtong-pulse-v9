# -*- coding: utf-8 -*-
"""
VectorStore.py —— 向量存储

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 向量数据库存储与检索
机制: 基于VectorStore类实现，包含10个核心方法
定位: 语义存储层
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from typing import Any

import numpy as np

from nucleus.logger import get_module_logger
from nucleus.semantic.VectorEncoder import PREPROCESS_ID, get_vector_encoder
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc

_module_logger = logging.getLogger(__name__)
_logger = get_module_logger("VectorStore")

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ★v2（2026-09-07）：向量语义空间因「文本预处理」改变（ASCII 标识符拆分 +
#   lowercase，见 VectorEncoder.preprocess_text）。v1 的旧向量必须重编码，
#   否则专有名词向量全是 [UNK] 塌缩值，检索结果静默错误。
META_VERSION = 2

# ★第十批 任务3（2026-09-09）：出厂黄金模型/维度。
#   这是「是否允许向生产向量库落盘」的判据，**硬编码、不从运行时会变的
#   config 读取**。根因：verify_phase17_1_5.py 等测试会运行时改写全局
#   SEMANTIC_KERNEL_CONFIG["model_name"]/["expected_dim"] 为 test/bge-small-zh / 8；
#   若某 VectorStore 单例此前已用这些被污染的 config 实例化并 flush，会把测试
#   模型/维度写进生产 data/knowledge/vectors.npz（23:50 污染事件）。
#   ⚠️ 未来真实更换嵌入模型时，必须同步更新此处，否则生产库会被判定为「不匹配」
#   并被备份清空（见 _backup_polluted）。
_GOLDEN_MODEL = "BAAI/bge-small-zh-v1.5"
_GOLDEN_DIM = 512


def _load_config() -> dict[str, Any]:
    try:
        import config as _cfg
        _raw = getattr(_cfg, "SEMANTIC_KERNEL_CONFIG", {})
        return _raw if isinstance(_raw, dict) else {}
    except Exception:
        return {}


def _text_hash(text: str) -> str:
    """文本指纹：内容变了才需要重新编码（避免重复编码浪费）。"""
    try:
        return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:16]
    except Exception as e:
        silent_exc(e, where="nucleus.semantic.VectorStore::_text_hash L68")
        return ""


class VectorStore:
    """向量存储 + 检索供给者（单例）。"""

    _instance: VectorStore | None = None
    _cls_lock = threading.Lock()

    def __init__(self):
        self._cfg = _load_config()
        self._lock = threading.RLock()

        self._dim = int(self._cfg.get("expected_dim", 512))
        self._model = str(self._cfg.get("model_name", ""))
        self._vec_path = os.path.join(_PROJECT_ROOT,
                                      self._cfg.get("vector_file",
                                                    "data/knowledge/vectors.npz"))
        self._meta_path = os.path.join(
            _PROJECT_ROOT,
            self._cfg.get("vector_meta_file",
                          "data/knowledge/vectors_meta.json"))

        self._matrix = np.zeros((0, self._dim), dtype=np.float32)
        self._ids: list[str] = []                 # 行号 → node_id
        self._row_of: dict[str, int] = {}         # node_id → 行号
        self._hash_of: dict[str, str] = {}        # node_id → 文本指纹
        self._updated: dict[str, float] = {}      # node_id → 时间戳

        self._dirty = 0                           # 未落盘条数
        self._last_flush = time.time()
        self._loaded = False
        self._load_error = ""
        self._writes = 0
        self._encoder = None
        self._flush_error_logged = False   # 落盘失败日志节流标记

        # ★主线第3批 任务3（P1-23）：quality_flag 检索消费（打通假闭环第5例）。
        #   默认挂载经验库 provider；为 None 时检索行为与原版完全一致。
        self._quality_flag_provider = _build_default_quality_flag_provider()

        atexit.register(self.shutdown)

    # ---------------- 路径与污染判定 ----------------
    def _is_production_path(self) -> bool:
        """★第十批 任务3：判据——本实例是否指向生产向量库。

        仅当 _vec_path 恰好等于 `项目根/data/knowledge/vectors.npz` 时才算生产库。
        任何临时测试库（verify_* 的 mkdtemp、test_isolation 的 ISO_DIR 等）都不在此列，
        不受生产护栏限制，可正常写入测试数据。
        """
        _prod = os.path.join(_PROJECT_ROOT, "data", "knowledge", "vectors.npz")
        return os.path.abspath(self._vec_path) == os.path.abspath(_prod)

    def _is_config_golden(self) -> bool:
        """★第十批 任务3：本实例配置是否与出厂黄金模型/维度一致。"""
        return self._model == _GOLDEN_MODEL and self._dim == _GOLDEN_DIM

    # ---------------- 单例 ----------------
    @classmethod
    def get_instance(cls) -> VectorStore:
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---------------- 生命周期 ----------------
    def _ensure_encoder(self):
        if self._encoder is None:
            self._encoder = get_vector_encoder()
        return self._encoder

    def _ensure_loaded(self) -> bool:
        """懒加载。返回是否已可用（未开关/文件不存在都算可用，只是空库）。"""
        if self._loaded:
            return True
        with self._lock:
            if self._loaded:
                return True
            try:
                self._load()
            except Exception as _e:
                self._load_error = f"{type(_e).__name__}: {_e}"
                _logger.warning(f"[向量库] 加载失败，以空库启动: {_e}")
            self._loaded = True
        return True

    @staticmethod
    def _file_fingerprint(path: str) -> str:
        """★第九批 5.3（星轨要求）：库文件的时间/体积指纹。

        背景：库内 meta 与当前配置不符时会拒绝加载，但此前只 WARNING 一句
        「模型变更」，看不出这份旧库**是什么时候、被谁**写出来的——排查
        「向量库污染」类问题时缺关键证据。此处补 mtime + size，
        便于判断该库是刚被某个测试覆盖写入，还是长期存在的存量库。
        """
        try:
            _st = os.stat(path)
            _mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_st.st_mtime))
            return f"mtime={_mtime}, size={_st.st_size}B, age={int(time.time() - _st.st_mtime)}s"
        except Exception as e:
            silent_exc(e, where="nucleus.semantic.VectorStore::_file_fingerprint L170")
            return "mtime=未知"

    def _backup_polluted(self) -> None:
        """★第十批 任务3：把不匹配的「污染文件」备份到 vectors_backup_{ts}/ 并移除原文件，
        使下次加载从空库开始（自愈），而不是「拒绝加载导致语义内核完全不可用」。

        幂等：仅当文件存在时执行；备份后原文件被移走，下次 _load 看到文件不存在直接空库。
        仅针对生产路径调用（见 _maybe_backup_on_mismatch）——临时测试库不备份，
        以免破坏 verify 脚本「换回模型仍能加载」的断言。
        """
        if not (os.path.exists(self._meta_path) or os.path.exists(self._vec_path)):
            return
        _ts = time.strftime("%Y%m%d_%H%M%S")
        _base = os.path.dirname(self._vec_path) or "."
        _backup_dir = os.path.join(_base, f"vectors_backup_{_ts}")
        try:
            os.makedirs(_backup_dir, exist_ok=True)
            for _p in (self._meta_path, self._vec_path):
                if os.path.exists(_p):
                    try:
                        shutil.move(_p, os.path.join(_backup_dir, os.path.basename(_p)))
                    except Exception as _exc:
                        _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            _logger.warning(
                f"[向量库] ★检测到生产库不匹配，已备份污染文件到 {_backup_dir} 并清空，"
                f"从空库启动（如需恢复旧向量请重跑 1.4 全量编码）")
        except Exception as _e:
            _logger.warning(f"[向量库] 备份污染文件失败: {_e}")

    def _maybe_backup_on_mismatch(self, meta: dict) -> None:
        """★第十批 任务3：生产库 + meta 的 model/dim/version 与出厂黄金值不符
        （即该库确属被污染或不兼容）→ 备份污染文件并清空。

        判据用「文件内容是否被污染」，而非「实例配置是否为黄金」——因为正常生产
        实例是黄金配置、却读到被污染(测试串入)的 meta 时，同样需要备份清空。
        临时测试库（_is_production_path()=False）不备份，保留原文件供测试断言。
        """
        if not self._is_production_path():
            return
        _m = str(meta.get("model", ""))
        _d = int(meta.get("dim", 0) or 0)
        _v = int(meta.get("version", META_VERSION) or META_VERSION)
        if _m != _GOLDEN_MODEL or _d != _GOLDEN_DIM or _v != META_VERSION:
            self._backup_polluted()

    def self_check_at_startup(self) -> bool:
        """★第十批 任务3：框架启动时的向量库自检。

        检查生产库 meta 的 model/dim 是否与出厂黄金值一致。不一致说明此前可能
        被测试污染，自动备份并清空（从空库启动，语义内核仍可运行）。
        只检查不修改运行时的 model/dim；返回是否健康。
        """
        if not self._is_production_path():
            return True  # 临时/隔离实例无需自检
        if not os.path.exists(self._meta_path):
            _logger.info("[向量库] 自检：无持久化文件，以空库启动（待 1.4 全量编码）")
            return True
        try:
            meta = safe_read_json(self._meta_path, default={})
        except (ValueError, OSError) as _e:
            _logger.warning(f"[向量库] 自检：meta 读取失败 {_e}")
            return False
        _m = meta.get("model")
        _d = int(meta.get("dim", 0))
        if _m != _GOLDEN_MODEL or _d != _GOLDEN_DIM:
            _logger.error(
                f"[向量库] ★自检告警：生产库 model={_m!r} dim={_d} 与黄金值"
                f"({_GOLDEN_MODEL!r}, {_GOLDEN_DIM})不符，疑似此前被测试污染。"
                f"将备份并清空，从空库启动（语义内核不受影响）。")
            self._backup_polluted()
            return False
        _logger.info(f"[向量库] 自检通过：model={_m} dim={_d} 匹配，共 "
                     f"{meta.get('count', '?')} 条")
        return True

    def _load(self) -> None:
        if not os.path.exists(self._meta_path) or not os.path.exists(self._vec_path):
            _logger.info("[向量库] 无持久化文件，以空库启动（等待 1.4 全量编码）")
            return
        meta = safe_read_json(self._meta_path, default={})
        if int(meta.get("version", 0)) != META_VERSION:
            _logger.warning(f"[向量库] meta 版本不符({meta.get('version')})，"
                            f"忽略旧文件，以空库启动 | {self._file_fingerprint(self._meta_path)}")
            self._maybe_backup_on_mismatch(meta)
            return
        # ★模型指纹校验：换模型后旧向量语义空间不一致，必须拒绝加载
        if str(meta.get("model", "")) and str(meta.get("model")) != self._model:
            _logger.warning(
                f"[向量库] 模型变更：库内={meta.get('model')} 当前={self._model}，"
                f"拒绝加载旧向量（需重跑 1.4 全量编码）| "
                f"meta:{self._file_fingerprint(self._meta_path)}; "
                f"vec:{self._file_fingerprint(self._vec_path)}")
            self._maybe_backup_on_mismatch(meta)
            return
        # ★预处理指纹校验：改了文本预处理 = 换了语义空间，旧向量同样不能复用
        _cur_pre = str(PREPROCESS_ID) if self._cfg.get(
            "text_preprocess", True) else "raw"
        _old_pre = str(meta.get("preprocess", "raw"))
        if _old_pre != _cur_pre:
            _logger.warning(
                f"[向量库] 文本预处理变更：库内={_old_pre} 当前={_cur_pre}，"
                f"拒绝加载旧向量（需重跑 1.4 全量编码）| "
                f"{self._file_fingerprint(self._meta_path)}")
            self._maybe_backup_on_mismatch(meta)
            return
        dim = int(meta.get("dim", self._dim))
        if dim != self._dim:
            _logger.warning(f"[向量库] 维度不符：库内={dim} 当前={self._dim}，拒绝加载 | "
                            f"meta:{self._file_fingerprint(self._meta_path)}; "
                            f"vec:{self._file_fingerprint(self._vec_path)}")
            self._maybe_backup_on_mismatch(meta)
            return

        # ★第107批 T-107a（D171）：修复 npz 句柄泄漏——np.load 返回 NpzFile 持有
        #   文件句柄，若不 close 则在进程生命周期内一直占用（重载时累积泄漏）。
        #   改用 with 上下文确保句柄关闭，并 .copy() 把矩阵拷入内存（避免视图悬空）。
        with np.load(self._vec_path) as data:
            key = "matrix" if "matrix" in data.files else data.files[0]
            mat = np.asarray(data[key], dtype=np.float32).copy()
        ids = list(meta.get("node_ids") or [])
        if mat.shape[0] != len(ids) or mat.ndim != 2 or mat.shape[1] != self._dim:
            _logger.warning(f"[向量库] 数据不一致：矩阵 {mat.shape} vs "
                            f"{len(ids)} 个 id，拒绝加载")
            self._maybe_backup_on_mismatch(meta)
            return

        self._matrix = mat
        self._ids = ids
        self._row_of = {nid: i for i, nid in enumerate(ids)}
        self._hash_of = dict(meta.get("text_hash") or {})
        self._updated = dict(meta.get("updated") or {})
        _logger.info(f"[向量库] 已加载 {len(ids)} 条向量（dim={self._dim}）")

    def shutdown(self) -> None:
        """★退出前强制落盘（atexit 自动注册）。"""
        try:
            # ★主线第18批 T6/P2-105：隔离目录已被测试清理时，
            #   不得在 atexit 阶段重建目录并写盘 —— 否则 tmp/ 永远留残留。
            #   判据：目标位于项目 tmp/ 之下 **且** 其目录已不存在。
            #   （生产 data/ 路径不受影响：目录一直存在，判据不成立。）
            _p = os.path.abspath(str(getattr(self, "_vec_path", "") or ""))
            _proj = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            _tmp_root = os.path.abspath(os.path.join(_proj, "tmp"))
            if _p and _p.startswith(_tmp_root + os.sep) \
                    and not os.path.isdir(os.path.dirname(_p)):
                _logger.debug(
                    "[向量库] 隔离目录已清理，跳过 atexit 落盘重建: %s", _p)
                return
            if self._dirty > 0:
                self.flush(force=True)
        except Exception as _e:
            _logger.debug(f"[向量库] 退出 flush 失败: {_e}")

    # ---------------- 写入 ----------------
    def has_vector(self, node_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            return str(node_id) in self._row_of

    def needs_encode(self, node_id: str, text: str) -> bool:
        """是否（重新）编码：无向量 或 文本已变更。"""
        self._ensure_loaded()
        with self._lock:
            nid = str(node_id)
            if nid not in self._row_of:
                return True
            return self._hash_of.get(nid) != _text_hash(text)

    def put(self, node_id: str, text: str, vec: np.ndarray | None) -> tuple[bool, str]:
        """写入/更新单条向量。

        Returns: (是否成功, 原因)
        """
        self._ensure_loaded()
        nid = str(node_id or "")
        if not nid:
            return False, "node_id 为空"
        enc = self._ensure_encoder()
        ok, why = enc.verify_vector(vec)
        if not ok:
            return False, why                     # ★红线 6.2 断言点 3

        arr = np.asarray(vec, dtype=np.float32).ravel()
        with self._lock:
            if nid in self._row_of:
                self._matrix[self._row_of[nid]] = arr
            else:
                if self._matrix.shape[0] == 0:
                    self._matrix = arr.reshape(1, -1)
                else:
                    self._matrix = np.vstack([self._matrix, arr.reshape(1, -1)])
                self._row_of[nid] = len(self._ids)
                self._ids.append(nid)
            self._hash_of[nid] = _text_hash(text)
            self._updated[nid] = time.time()
            self._dirty += 1
        self._maybe_flush()
        return True, "ok"

    def put_batch(self, items: list[tuple[str, str, np.ndarray]]) -> tuple[int, int]:
        """批量写入 [(node_id, text, vec), ...]，返回 (成功数, 失败数)。"""
        ok_n = fail_n = 0
        for nid, text, vec in items:
            good, _why = self.put(nid, text, vec)
            if good:
                ok_n += 1
            else:
                fail_n += 1
        return ok_n, fail_n

    def remove(self, node_id: str) -> bool:
        """删除向量（★L1 冷存淘汰联动移除，星轨 Q1 决策）。

        实现：置零 + 从索引摘除，物理压缩在 flush 时做（避免频繁搬 20MB）。
        """
        self._ensure_loaded()
        with self._lock:
            nid = str(node_id)
            row = self._row_of.pop(nid, None)
            if row is None:
                return False
            self._hash_of.pop(nid, None)
            self._updated.pop(nid, None)
            if 0 <= row < self._matrix.shape[0]:
                self._matrix[row] = 0.0
            self._dirty += 1
        self._maybe_flush()
        return True

    # ---------------- 落盘 ----------------
    def _maybe_flush(self) -> None:
        cnt = int(self._cfg.get("vector_flush_count", 100))
        sec = float(self._cfg.get("vector_flush_interval_sec", 300))
        if self._dirty >= cnt or (time.time() - self._last_flush) >= sec:
            self.flush()

    def reap_orphans(self, valid_ids) -> int:
        """★第102批 T-102b：反向回收孤儿向量。

        删除「向量库里有、但节点已不存在」的条目，返回移除条数。
        （对应债务 D161：remove 生产 0 调用 + reconcile 只单向补码）
        """
        self._ensure_loaded()
        _valid = set(str(x) for x in (valid_ids or ()) if x)
        if not _valid:
            return 0
        with self._lock:
            _dead = [nid for nid in self._ids if str(nid) not in _valid]
            if not _dead:
                return 0
            for nid in _dead:
                self._row_of.pop(nid, None)
                self._hash_of.pop(nid, None)
                self._updated.pop(nid, None)
            self._dirty += len(_dead)
        try:
            self.flush(force=True)
        except Exception as _exc:
            _module_logger.debug(
                f"[向量库] [第102批 T-102b] 孤儿回收后落盘失败(已忽略): "
                f"{type(_exc).__name__}: {_exc}")
        return len(_dead)

    def flush(self, force: bool = False) -> bool:
        """原子落盘。force=True 时忽略 dirty 计数强制写。"""
        with self._lock:
            if not force and self._dirty == 0:
                return True
            # ★第十批 任务3：生产库护栏。若本实例「指向生产向量库」但配置
            #   (model/dim) 与出厂黄金值不符，判定为疑似测试串入，**拒绝落盘**，
            #   避免把 test/bge-small-zh / dim=8 写进 data/knowledge/vectors.npz。
            #   临时测试库（_is_production_path()=False）不受此限，正常写入。
            if self._is_production_path() and not self._is_config_golden():
                if not self._flush_error_logged:
                    self._flush_error_logged = True
                    _logger.error(
                        f"[向量库] ★拒绝写入生产库：实例配置(model={self._model!r}, "
                        f"dim={self._dim}) 与出厂黄金值({_GOLDEN_MODEL!r}, {_GOLDEN_DIM})不符，"
                        f"疑似测试串入。已丢弃本次落盘（内存数据保留，不污染生产库）。")
                return False
            try:
                os.makedirs(os.path.dirname(self._vec_path), exist_ok=True)
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            # 物理压缩：剔除已删除（全零行 + 不在索引中）的行
            keep = [i for i, nid in enumerate(self._ids) if nid in self._row_of]
            if len(keep) != len(self._ids):
                if keep:
                    self._matrix = self._matrix[keep]
                else:
                    self._matrix = np.zeros((0, self._dim), dtype=np.float32)
                self._ids = [self._ids[i] for i in keep]
                self._row_of = {nid: i for i, nid in enumerate(self._ids)}

            # ★踩坑记录：np.savez 对**不以 .npz 结尾**的路径会自动追加 .npz，
            #   若写成 "xxx.npz.tmp"，实际落盘的是 "xxx.npz.tmp.npz"，
            #   os.replace 找 "xxx.npz.tmp" 会报 No such file or directory
            #   —— 原子写 100% 失败。故把 .tmp 放在**后缀之前**。
            tmp_vec = self._vec_path + ".tmp.npz"
            tmp_meta = self._meta_path + ".tmp"
            # ★主线第15批 T6/P2-95：落盘重试 + 备用路径
            #   实测 WinError 5（拒绝访问）15 次：os.replace 是 Windows 上最易被
            #   杀毒/索引器/并发进程短暂占用的操作，原实现**一次失败即放弃整批写入**。
            _retry_on, _max_retries = self._flush_retry_config()
            _attempts = (1 + _max_retries) if _retry_on else 1
            meta = {
                "version": META_VERSION,
                "model": self._model,
                # ★预处理指纹：换预处理 = 换语义空间，旧库必须重建
                "preprocess": (str(PREPROCESS_ID)
                               if self._cfg.get("text_preprocess", True)
                               else "raw"),
                "dim": self._dim,
                "count": len(self._ids),
                "node_ids": self._ids,
                "text_hash": self._hash_of,
                "updated": self._updated,
                "saved_at": time.time(),
            }
            # 落盘前「目标可写性」预检（占用/只读时提前告警，便于定位文件锁）
            self._precheck_flush_target(self._vec_path)
            _last_err: Exception | None = None
            for _attempt in range(1, _attempts + 1):
                try:
                    # ★原子写：先写 tmp，再 os.replace（进程被杀不留半截文件）
                    np.savez(tmp_vec, matrix=self._matrix)
                    with open(tmp_meta, "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False)
                    os.replace(tmp_vec, self._vec_path)
                    os.replace(tmp_meta, self._meta_path)
                    self._dirty = 0
                    self._last_flush = time.time()
                    self._writes += 1
                    if _attempt > 1:
                        _logger.info(
                            f"[向量库] 落盘重试成功（第{_attempt}次，共{_attempts}次尝试）")
                    return True
                except Exception as _e:
                    _last_err = _e
                    if _attempt < _attempts:
                        # 指数退避：0.1s → 0.2s → 0.4s …（上限 1s）
                        time.sleep(min(1.0, 0.1 * (2 ** (_attempt - 1))))
                        continue

            # ---- 全部重试失败 → 转存备用目录（不丢数据）----
            _reason = self._describe_flush_error(_last_err)
            _backup_path = self._flush_to_backup(tmp_vec, tmp_meta)
            # ★日志节流：9958 条批量写入时若持续失败，不能刷 9958 行 WARNING
            if not self._flush_error_logged:
                self._flush_error_logged = True
                _logger.warning(
                    f"[向量库] 落盘失败（内存数据未丢）: 原因={_reason}, "
                    f"错误={_last_err}, 尝试={_attempts}次, 备用路径={_backup_path or '无'}")
            else:
                _logger.debug(
                    f"[向量库] 落盘失败: 原因={_reason}, 错误={_last_err}")
            for p in (tmp_vec, tmp_meta):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception as _exc:
                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            return False

    # ==================================================================
    # ★主线第15批 T6/P2-95：落盘重试/预检/备用路径/原因分类
    # ==================================================================
    @staticmethod
    def _flush_retry_config() -> tuple[bool, int]:
        """读取落盘重试配置：(开关, 最大重试次数)。"""
        enabled, retries = True, 3
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "ENABLE_VECTOR_STORE_RETRY", True))
            retries = int(getattr(_cfg, "VECTOR_STORE_MAX_RETRIES", 3) or 3)
        except Exception as _e:
            _logger.debug(
                f"[向量库] 落盘重试配置读取失败，使用默认值(True,3): "
                f"{type(_e).__name__}: {_e}")
        return enabled, max(0, retries)

    @staticmethod
    def _describe_flush_error(err: Exception | None) -> str:
        """把落盘异常归类为可读原因：权限 / 文件被占用 / 磁盘空间 / 其他。"""
        if err is None:
            return "未知"
        _name = type(err).__name__
        _text = str(err)
        _low = _text.lower()
        if isinstance(err, PermissionError) or "winerror 5" in _low or "拒绝访问" in _text:
            return "权限拒绝/文件被占用（WinError 5）"
        if isinstance(err, FileNotFoundError) or "no such file" in _low:
            return "目标路径不存在"
        if isinstance(err, OSError) and ("space" in _low or "空间" in _text):
            return "磁盘空间不足"
        return f"{_name}"

    def _precheck_flush_target(self, target: str) -> bool:
        """落盘前预检目标可写性（不可写只记日志，不阻断重试与备用路径）。"""
        try:
            _dir = os.path.dirname(target) or "."
            if not os.path.isdir(_dir):
                _logger.debug(f"[向量库] 落盘预检：目录不存在 {_dir}")
                return False
            if os.path.exists(target) and not os.access(target, os.W_OK):
                _logger.debug(f"[向量库] 落盘预检：目标只读或被占用 {target}")
                return False
            return True
        except Exception as _e:
            _logger.debug(f"[向量库] 落盘预检异常（已忽略）: {type(_e).__name__}: {_e}")
            return False

    def _flush_backup_dir(self) -> str:
        """备用落盘目录。

        · 目标位于项目 `data/` 之下（生产）→ `<项目根>/data/vector_store_backup/`
          （与任务书约定一致）。
        · 目标在 `data/` 之外（如测试隔离目录 `tmp/test_data/`）→ **就地**在目标旁
          建 `vector_store_backup/`，避免测试/临时路径的落盘失败把备份写进生产 data/。
        """
        try:
            _p = os.path.abspath(self._vec_path)
            _base = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            _data_dir = os.path.join(_base, "data")
            if _p.startswith(os.path.abspath(_data_dir) + os.sep):
                return os.path.join(_data_dir, "vector_store_backup")
            return os.path.join(os.path.dirname(_p), "vector_store_backup")
        except Exception as _e:
            _logger.debug(
                f"[向量库] 备用目录推导失败: {type(_e).__name__}: {_e}")
            return ""

    def _flush_to_backup(self, tmp_vec: str, tmp_meta: str) -> str:
        """把 tmp 文件转存到备用目录（不丢数据）。

        Returns: 备用目录路径；转存失败返回空串。
        """
        try:
            _bak = self._flush_backup_dir()
            os.makedirs(_bak, exist_ok=True)
            if os.path.exists(tmp_vec):
                os.replace(tmp_vec, os.path.join(_bak, os.path.basename(self._vec_path)))
            if os.path.exists(tmp_meta):
                os.replace(tmp_meta, os.path.join(_bak, os.path.basename(self._meta_path)))
            _logger.warning(
                f"[向量库] 已转存备用目录（主路径落盘失败，数据未丢）: {_bak}")
            return _bak
        except Exception as _e:
            _logger.warning(
                f"[向量库] 转存备用目录失败: {type(_e).__name__}: {_e}")
            return ""

    # ---------------- 检索（ResonanceEngine provider 接口）----------------
    def search_by_text(self, text: str, top_k: int = 50,
                       apply_gate: bool | None = None
                       ) -> list[tuple[str, float]]:
        """★provider 接口：文本 → [(node_id, 余弦相似度), ...] 降序。

        语义不可用时返回 []（调用方回落关键词通道）。

        ★采纳门（2026-09-07 门控调优）：
            `apply_gate=None`（默认）时读配置 `enable_score_gate`。
            开启后，只返回「相对本次查询显著突出」的候选 —— 这是修复
            「负例误召回 90%」的落地点：危险查询不再被塞进一堆只因
            余弦压缩而显得接近的常规知识条目。
            （详见 VectorEncoder.apply_score_gate 文档字符串里的实测数据）
        """
        if not text:
            return []
        self._ensure_loaded()
        enc = self._ensure_encoder()
        if not enc.is_available():
            return []
        qvec = enc.encode_one(text)
        if qvec is None:
            return []
        if apply_gate is None:
            apply_gate = bool(self._cfg.get("enable_score_gate", True))
        return self.search_by_vector(qvec, top_k, apply_gate=apply_gate)

    def search_by_vector(self, qvec: np.ndarray, top_k: int = 50,
                         apply_gate: bool = False
                         ) -> list[tuple[str, float]]:
        """向量 → [(node_id, 相似度), ...] 降序。

        ★`apply_gate` 默认 False —— 保持既有契约（旧验收脚本按 top_k 截断
          断言条数）。生产路径走 `search_by_text`，由配置决定是否开启门控。
        """
        self._ensure_loaded()
        with self._lock:
            mat = self._matrix
            ids = list(self._ids)
        if mat.shape[0] == 0:
            return []
        try:
            from nucleus.semantic.VectorEncoder import VectorEncoder
            sims = VectorEncoder.cosine_matrix(qvec, mat)
            if apply_gate:
                keep = VectorEncoder.apply_score_gate(sims, self._cfg)
                if not keep.any():
                    return []          # 无候选过门 → 调用方回落关键词通道
                # 未过门的置 -inf，topk 取完后过滤掉
                sims = np.where(keep, sims, -np.inf).astype(np.float32)
            hits = VectorEncoder.topk(sims, min(top_k, len(ids)))
            _raw = [(ids[i], float(s)) for i, s in hits
                    if 0 <= i < len(ids) and np.isfinite(s)]
            # ★主线第3批 任务3（P1-23）：quality_flag 检索消费（假闭环第5例）
            if self._quality_flag_provider is None:
                return _raw
            _prov = self._quality_flag_provider
            # 放宽：先取更多候选，过滤污染节点后再裁到 top_k
            _cand = VectorEncoder.topk(sims, min(top_k * 3, len(ids)))
            _out = []
            for i, s in _cand:
                if not (0 <= i < len(ids) and np.isfinite(s)):
                    continue
                _w = _quality_weight(_prov(ids[i]))
                if _w <= 0:
                    continue  # polluted(0.0) -> 直接过滤
                _out.append((ids[i], float(s) * _w))
            _out.sort(key=lambda x: x[1], reverse=True)
            return _out[:top_k]
        except Exception as _e:
            _logger.debug(f"[向量库] 检索失败: {_e}")
            return []

    # ---------------- quality_flag 检索消费（主线第3批 任务3）----------------
    def set_quality_flag_provider(self, provider) -> None:
        """注入 quality_flag 查询函数 node_id -> flag(str|None)。None 关闭消费（恢复原行为）。"""
        self._quality_flag_provider = provider

    # ---------------- 状态 / 验收 ----------------
    def status(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            # ★count 报**有效向量数**（已摘除索引的删除项不计入）。
            #   _ids 里可能还留着待压缩的空槽，单独用 slots 暴露，避免运维误读。
            return {
                "count": len(self._row_of),
                "slots": len(self._ids),
                "pending_compact": len(self._ids) - len(self._row_of),
                "dim": self._dim,
                "model": self._model,
                "dirty": self._dirty,
                "writes": self._writes,
                "vec_file": self._vec_path,
                "meta_file": self._meta_path,
                "vec_file_exists": os.path.exists(self._vec_path),
                "load_error": self._load_error,
                "matrix_bytes": int(self._matrix.nbytes),
            }

    def all_ids(self) -> list[str]:
        """当前**有效**向量的 node_id 列表（已删除的不含）。"""
        self._ensure_loaded()
        with self._lock:
            return [nid for nid in self._ids if nid in self._row_of]


# ============================ quality_flag 检索消费（主线第3批 任务3）============================
# 权重：polluted 直接过滤(0.0)；suspect 降权；placeholder_alias 大幅降权；其他视为正常。
QUALITY_FLAG_WEIGHTS = {
    "polluted": 0.0,
    "suspect": 0.5,
    "placeholder_alias": 0.3,
    "clean": 1.0,
    "ok": 1.0,
    "none": 1.0,
    "": 1.0,
}


def _quality_weight(flag) -> float:
    """quality_flag -> 检索权重。None/未知 -> 1.0（不影响正常节点）。"""
    if flag is None:
        return 1.0
    return float(QUALITY_FLAG_WEIGHTS.get(str(flag).lower(), 1.0))


def _build_default_quality_flag_provider():
    """默认 provider：惰性读取经验库（reasoning_experience / experience_pool）的 quality_flag。
    文件缺失/损坏 -> 空表安全降级。知识快照(placeholder_alias)由
    build_snapshot_quality_flag_provider 或内存节点池显式注册，避免 318MB 快照常驻。
    """
    _cache: dict = {}
    _loaded = {"done": False}

    def _load():
        _maps: dict = {}
        for _rel in ("data/context/reasoning_experience.json",
                     "data/experience/experience_pool.json"):
            _p = os.path.join(_PROJECT_ROOT, _rel)
            if not os.path.exists(_p):
                continue
            try:
                _d = safe_read_json(_p, default={})
            except (ValueError, OSError) as e:
                print(f"[WARNING] VectorStore.py:606: {type(e).__name__}: {e}")
                continue
            _items = None
            if isinstance(_d, dict):
                for _k in ("experiences", "items", "nodes", "records", "data"):
                    if isinstance(_d.get(_k), list):
                        _items = _d[_k]
                        break
            elif isinstance(_d, list):
                _items = _d
            if _items is None:
                continue
            for _it in _items:
                if not isinstance(_it, dict):
                    continue
                _id = _it.get("node_id") or _it.get("id")
                _flag = _it.get("quality_flag")
                if _id and _flag:
                    _maps[str(_id)] = str(_flag)
        return _maps

    def _provider(node_id):
        if not _loaded["done"]:
            _cache.update(_load())
            _loaded["done"] = True
        return _cache.get(str(node_id))

    return _provider



# ============================ 便捷入口 ============================
def get_vector_store() -> VectorStore:
    return VectorStore.get_instance()


def shutdown_vector_store() -> None:
    """供框架 shutdown 钩子显式调用（atexit 之外的双保险）。"""
    try:
        if VectorStore._instance is not None:
            VectorStore._instance.shutdown()
    except Exception as _exc:
        _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")