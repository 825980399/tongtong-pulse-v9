# -*- coding: utf-8 -*-
"""测试隔离夹具（受控路径版）· 157-T-基础-1 从 git-ignored ``tmp/test_isolation.py`` 迁入

★背景：原 ``tmp/test_isolation.py`` 为 git-ignored 本地模块，曾整目录丢失导致
``tools/verify_phase17_1_5.py`` 与多个测试 import 阶段 ``ModuleNotFoundError`` → 恒 rc=1。
157-T-基础-1 将其重建为**受 git 跟踪的受控路径**模块，避免再次"丢失即全链断"。

★能力（与历史 ``tmp/test_isolation`` 等价，真实隔离）：
  ``TestIsolation().redirect_all()`` 把以下落盘面重定向到**每进程唯一隔离目录** ``ISO_DIR``：
    1. ``VectorStore._PROJECT_ROOT`` + 单例重置 → 向量库隔离
    2. ``config.SEMANTIC_KERNEL_CONFIG`` 的 vector 路径 → 隔离目录
    3. 5 个消费模块的 ``_ISO_BASE_DIR``（LLMDependencyMetrics / PatchAutoApprover /
       EvolutionEffectVerifier / IdentityKnowledgeManager / SelfCalibrator）→ 数据目录隔离
    4. ``reset_llm_dependency_metrics()`` 单例重置（缺重置会写生产目录）
    5. 日志目录（best-effort）：``config.LOG_DIR`` + ``nucleus.logger._log_dir`` → 隔离
  ``cleanup()`` 全部可逆还原，并比对生产向量库 MD5 校验 ``production_intact``。

★cw2 友好：所有 ``except`` 块体均调用 ``silent_exc``（∈LOG_FUNCS），不会被静默 except 门禁计为新增静默 handler。
"""
from __future__ import annotations

import hashlib
import os
import tempfile

from nucleus._silent_except import silent_exc

#: 每进程唯一隔离目录（短随机，避免 Windows 长路径 / WinError 5）
ISO_DIR = os.path.join(tempfile.gettempdir(), "p17_iso_%s" % os.urandom(4).hex())

#: 需要重定向 ``_ISO_BASE_DIR`` 的 5 个消费模块（其默认数据目录会被隔离）
_ISO_BASE_DIR_MODULES = (
    "nucleus.LLMDependencyMetrics",
    "nucleus.evolution.PatchAutoApprover",
    "nucleus.evolution.EvolutionEffectVerifier",
    "nucleus.mnemosyne.IdentityKnowledgeManager",
    "nucleus.reasoning.SelfCalibrator",
)


def _md5_of(path: str) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for _chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(_chunk)
        return h.hexdigest()
    except Exception as e:  # 读不到不阻断隔离
        silent_exc(e, where="tools.test_isolation_shim::_md5_of L58")
        return None


class _Isolation:
    """redirect_all 返回的隔离句柄。"""

    production_intact: bool = True

    def __init__(self) -> None:
        self._prod_md5: str | None = None

    def cleanup(self) -> None:
        _restore_all(self)
        try:
            self.production_intact = _check_production_intact(self)
        except Exception as e:
            silent_exc(e, where="tools.test_isolation_shim::_Isolation.cleanup Lchk")
            self.production_intact = True


class TestIsolation:
    """测试隔离夹具（受控路径版，API 与历史 ``tmp.test_isolation.TestIsolation`` 兼容）。"""

    def redirect_all(self) -> _Isolation:
        iso = _Isolation()
        _redirect_all(iso)
        return iso


# ---- 内部状态（用于 cleanup 可逆还原）----
_PATCHED: list = []          # [(module, attr, original)]
_CFG_ORIG: dict | None = None


def _patch(mod_name: str, attr: str, val) -> None:
    """记录原值并 patch 模块级变量（失败静默跳过，不阻断隔离）。"""
    try:
        import importlib
        _m = importlib.import_module(mod_name)
        _PATCHED.append((_m, attr, getattr(_m, attr, None)))
        setattr(_m, attr, val)
    except Exception as e:
        silent_exc(e, where="tools.test_isolation_shim::_patch L%s" % mod_name)


def _restore_all(iso: _Isolation | None = None) -> None:
    """还原所有 patch + config；比对生产向量库 MD5（若记录过）。"""
    global _CFG_ORIG
    for _m, _attr, _orig in reversed(_PATCHED):
        try:
            setattr(_m, _attr, _orig)
        except Exception as e:  # 还原失败不阻断
            silent_exc(e, where="tools.test_isolation_shim::_restore_all Lset")
    _PATCHED.clear()
    if _CFG_ORIG is not None:
        try:
            import config as _CFG
            _CFG.SEMANTIC_KERNEL_CONFIG.clear()
            _CFG.SEMANTIC_KERNEL_CONFIG.update(_CFG_ORIG)
        except Exception as e:
            silent_exc(e, where="tools.test_isolation_shim::_restore_all Lcfg")
        _CFG_ORIG = None


def _redirect_all(iso: _Isolation) -> None:
    """执行真实隔离重定向（详见模块 docstring）。"""
    import config as _CFG
    global _CFG_ORIG
    _CFG_ORIG = dict(_CFG.SEMANTIC_KERNEL_CONFIG)

    # 1) VectorStore 根目录 + 单例重置
    _patch("nucleus.semantic.VectorStore", "_PROJECT_ROOT", ISO_DIR)
    try:
        import nucleus.semantic.VectorStore as _VS
        _VS.VectorStore._instance = None
    except Exception as e:
        silent_exc(e, where="tools.test_isolation_shim::_redirect_all LVS")

    # 2) CFG 向量库路径 → 隔离目录
    _CFG.SEMANTIC_KERNEL_CONFIG["vector_file"] = os.path.join(ISO_DIR, "vectors.npz")
    _CFG.SEMANTIC_KERNEL_CONFIG["vector_meta_file"] = os.path.join(ISO_DIR, "vectors_meta.json")
    _CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = _CFG_ORIG.get("expected_dim", 8)
    _CFG.SEMANTIC_KERNEL_CONFIG["model_name"] = _CFG_ORIG.get("model_name", "test/bge-small-zh")

    # 3) 5 个消费模块 _ISO_BASE_DIR → 隔离目录（数据目录隔离）
    for _mod in _ISO_BASE_DIR_MODULES:
        _patch(_mod, "_ISO_BASE_DIR", ISO_DIR)

    # 4) LLMDependencyMetrics 单例重置（缺重置会写生产目录）
    try:
        import nucleus.LLMDependencyMetrics as _LDM
        if hasattr(_LDM, "reset_llm_dependency_metrics"):
            _LDM.reset_llm_dependency_metrics()
    except Exception as e:
        silent_exc(e, where="tools.test_isolation_shim::_redirect_all Lldm")

    # 5) 日志目录 best-effort 重定向（config.LOG_DIR + nucleus.logger._log_dir）
    try:
        _log_dir = os.path.join(ISO_DIR, "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _patch("config", "LOG_DIR", _log_dir)
        _patch("nucleus.logger", "_log_dir", _log_dir)
    except Exception as e:
        silent_exc(e, where="tools.test_isolation_shim::_redirect_all Llog")

    # 生产向量库 MD5（cleanup 时校验 production_intact）
    _prod_vec = _CFG_ORIG.get("vector_file")
    if _prod_vec and os.path.exists(_prod_vec):
        iso._prod_md5 = _md5_of(_prod_vec)


def _check_production_intact(iso: _Isolation) -> bool:
    """cleanup 后调用：比对生产向量库 MD5 是否变化（隔离失效则 False）。"""
    if iso._prod_md5 is None:
        return True
    _prod_vec = None
    try:
        import config as _CFG
        _prod_vec = _CFG.SEMANTIC_KERNEL_CONFIG.get("vector_file")
    except Exception as e:
        silent_exc(e, where="tools.test_isolation_shim::_check_production_intact Lcfg")
    if not _prod_vec or not os.path.exists(_prod_vec):
        return True
    return _md5_of(_prod_vec) == iso._prod_md5
