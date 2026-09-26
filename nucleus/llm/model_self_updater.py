"""模型自更新器框架 —— 内在模型 **L4** 第二个落地（主线第43批 T3 / P1-256）。

背景
----
内在模型已有 3 个组件落地（L1 语义缓存器 / L2 经验检索器 / L4 补丁质量评估器），
但它们**没有统一的自我更新机制** —— 数据在变、反馈在积累，模型却不会跟着变。
本模块建立「**每日增量 + 每周全量**」的框架，并配套**版本管理**与**退化防护**。

★ 本批**只做框架，不做实际训练**（``full_retrain`` 仅产出"训练计划"，不执行训练）。

★★ 铁律：内在模型的输出**永不入训练集**
----------------------------------------
P1-256（第三方评估）：学生模型输出被记入训练数据 → 下一代在自己输出上学 →
**错误固化放大的自我蒸馏退化螺旋**（AutoGPT 式自我污染的模型版）。
本模块用 :func:`training_guard` 做**结构性拦截**，并由门控测试强制验证：
凡标记为"内在模型产出"的样本一律剔除（默认判据见 :func:`is_internal_model_output`）。

退化防护
--------
``incremental_update(quality_score=...)`` 提交前比对当前版本质量：
**下降超过 ``MODEL_SELF_UPDATER_DEGRADE_LIMIT``（默认 10%）→ 拒绝提交并回滚态**。

L1 仅观测
---------
只读数据源 + 只写 ``data/models/versions/`` 下的版本元数据；
**不改动任何模型文件、不触发训练**（开关关闭即零行为）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from typing import Any
from nucleus._silent_except import silent_exc

__all__ = [
    "DEFAULT_BASE_DIR",
    "ORIGIN_INTERNAL_MODEL",
    "internal_model_origin",
    "is_internal_model_output",
    "training_guard",
    "ModelSelfUpdater",
    "get_updater",
    "reset_updater",
    "updater_enabled",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 版本根目录。
DEFAULT_BASE_DIR = os.path.join(_PROJECT_ROOT, "data", "models", "versions")

#: ★新增来源标记：**内在模型自身的产出**（永不入训练集）。
ORIGIN_INTERNAL_MODEL = "internal_model"

_BASE_VERSION = "v1.0.0"


# ------------------------------------------------------------------ 配置
def updater_enabled() -> bool:
    """灰度开关（默认 True，仅框架运行）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_MODEL_SELF_UPDATER", True))
    except Exception as e:
        silent_exc(e, "nucleus/llm/model_self_updater.py:66:模型自更新异常", level="warning")
        return True


def _cfg_int(name: str, default: int) -> int:
    try:
        import config
        _v = int(getattr(config, name, default))
        return _v if _v > 0 else default
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        import config
        _v = float(getattr(config, name, default))
        return _v if _v > 0 else default
    except Exception:
        return default


def _in_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception:
        return False


def _is_production_path(path: str) -> bool:
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = os.path.abspath(_PROJECT_ROOT).replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception:
        return True


def internal_model_origin() -> str:
    """内在模型产出的来源标记（供调用方标注）。"""
    return ORIGIN_INTERNAL_MODEL


# ------------------------------------------------------------------ 铁律
def is_internal_model_output(record: Any) -> bool:
    """★判定一条样本是否为**内在模型自身的输出**（此类样本永不入训练集）。

    判据（任一成立即真）：
      · ``origin`` 或 ``channel`` == ``"internal_model"``
      · 记录含真值 ``_internal_model`` / ``is_model_output``
      · ``origin`` 以 ``"model_"`` 开头（内在模型产出的约定前缀）
    """
    if not isinstance(record, dict):
        return False
    _o = str(record.get("origin") or "")
    _c = str(record.get("channel") or "")
    if _o == ORIGIN_INTERNAL_MODEL or _c == ORIGIN_INTERNAL_MODEL:
        return True
    if bool(record.get("_internal_model")) or bool(record.get("is_model_output")):
        return True
    return _o.startswith("model_")


def training_guard(records: Any) -> list:
    """★**训练集准入过滤**：剔除全部内在模型产出样本，返回可入训练集的样本。

    这是 P1-256「自我蒸馏退化螺旋」的**结构性防线** —— 任何训练/增量更新路径
    都必须先过此函数（门控测试强制验证）。
    """
    if not isinstance(records, (list, tuple)):
        return []
    return [r for r in records if isinstance(r, dict) and not is_internal_model_output(r)]


# ------------------------------------------------------------------ 主体
class ModelSelfUpdater:
    """「每日增量 + 每周全量」的模型自更新框架（仅框架，不做实际训练）。"""

    def __init__(self, base_dir: str | None = None,
                 quality_fn: Any = None, data_probe: Any = None,
                 now_fn: Any = None) -> None:
        self._base_override = base_dir
        self._quality_fn = quality_fn
        self._data_probe = data_probe
        self._now_fn = now_fn or time.time
        self._increments = 0
        self._retrains = 0
        self._rollbacks = 0
        self._last: dict = {}

    # -------------------------------------------------- 路径与配置
    def base_dir(self) -> str:
        return self._base_override or DEFAULT_BASE_DIR

    def index_path(self) -> str:
        return os.path.join(self.base_dir(), "index.json")

    def keep_versions(self) -> int:
        return _cfg_int("MODEL_SELF_UPDATER_KEEP_VERSIONS", 3)

    def degrade_limit(self) -> float:
        return _cfg_float("MODEL_SELF_UPDATER_DEGRADE_LIMIT", 0.10)

    def enabled(self) -> bool:
        return updater_enabled()

    def _writable(self) -> bool:
        """测试环境 + 生产路径 → 拒写（防污染）。"""
        return not (_in_test_env() and _is_production_path(self.base_dir()))

    # -------------------------------------------------- 索引读写
    def _load_index(self) -> dict:
        _p = self.index_path()
        try:
            if os.path.isfile(_p):
                with io.open(_p, encoding="utf-8") as _f:
                    _d = json.loads(_f.read())
                if isinstance(_d, dict) and isinstance(_d.get("versions"), list):
                    return _d
        except (OSError, ValueError):
            pass
        return {"current": None, "versions": []}

    def _save_index(self, idx: dict) -> bool:
        if not self._writable():
            return False
        try:
            os.makedirs(self.base_dir(), exist_ok=True)
            with io.open(self.index_path(), "w", encoding="utf-8") as _f:
                _f.write(json.dumps(idx, ensure_ascii=False, indent=2))
            return True
        except OSError:
            return False

    def list_versions(self) -> list:
        """全部版本元数据（按时间升序）。"""
        return list(self._load_index().get("versions") or [])

    def current_version(self) -> dict | None:
        """当前版本元数据。"""
        _idx = self._load_index()
        _cur = _idx.get("current")
        for _v in (_idx.get("versions") or []):
            if _v.get("version") == _cur:
                return _v
        return None

    def current_quality(self) -> float | None:
        """当前版本质量评分（注册或记录在版本元数据里）。"""
        if callable(self._quality_fn):
            try:
                _v = self._quality_fn()
                return float(_v) if isinstance(_v, (int, float)) else None
            except Exception:
                return None
        _cur = self.current_version() or {}
        _q = _cur.get("quality")
        return float(_q) if isinstance(_q, (int, float)) else None

    # -------------------------------------------------- 语义化版本
    @staticmethod
    def _bump(version: str, level: str = "patch") -> str:
        _v = str(version or _BASE_VERSION).lstrip("v")
        _parts = (_v.split(".") + ["0", "0", "0"])[:3]
        try:
            _a, _b, _c = (int(x) for x in _parts)
        except ValueError:
            _a, _b, _c = 1, 0, 0
        if level == "major":
            _a, _b, _c = _a + 1, 0, 0
        elif level == "minor":
            _b, _c = _b + 1, 0
        else:
            _c += 1
        return "v%d.%d.%d" % (_a, _b, _c)

    # -------------------------------------------------- 检查
    def check_for_updates(self) -> dict:
        """检查是否有新数据/新反馈可供更新（只读）。"""
        _probe = {}
        if callable(self._data_probe):
            try:
                _probe = self._data_probe() or {}
            except Exception as _e:
                _probe = {"error": "%s: %s" % (type(_e).__name__, _e)}
        _new_samples = int(_probe.get("new_samples") or 0)
        _new_feedback = int(_probe.get("new_feedback") or 0)
        _last = (self.current_version() or {}).get("updated_at") or 0
        _elapsed_h = (float(self._now_fn()) - float(_last)) / 3600.0 if _last else None
        _weekly_due = _elapsed_h is None or _elapsed_h >= 168.0
        return {
            "status": "ok",
            "enabled": self.enabled(),
            "current_version": (self.current_version() or {}).get("version"),
            "new_samples": _new_samples,
            "new_feedback": _new_feedback,
            "elapsed_hours": round(_elapsed_h, 2) if _elapsed_h is not None else None,
            "incremental_due": bool(_new_samples or _new_feedback),
            "full_retrain_due": bool(_weekly_due),
            "probe": _probe,
        }

    # -------------------------------------------------- 增量更新
    def incremental_update(self, quality_score: float | None = None,
                           payload: dict | None = None,
                           samples: Any = None, *, force: bool = False) -> dict:
        """每日增量更新（**框架**：登记版本 + 退化防护；不做实际训练）。

        Args:
            quality_score: 更新后的质量评分；**下降超过 degrade_limit 则拒绝提交**。
            payload: 任意元数据（增量产物描述）。
            samples: 本次增量所用样本；**会先过 :func:`training_guard`**。

        Returns:
            ``{"status": "ok"|"unchanged"|"rolled_back"|"skipped"|"disabled", ...}``
        """
        if not self.enabled():
            return {"status": "disabled"}
        _kept = training_guard(samples) if samples is not None else []
        _rejected = (len(samples) - len(_kept)) if isinstance(samples, (list, tuple)) else 0
        _cur_q = self.current_quality()
        _new_q = float(quality_score) if isinstance(quality_score, (int, float)) else _cur_q

        # ★退化防护：质量下降超过阈值 → 拒绝提交（自动回滚态）
        if (_cur_q is not None and _new_q is not None and _cur_q > 0):
            _drop = (_cur_q - _new_q) / _cur_q
            if _drop > self.degrade_limit() and not force:
                self._rollbacks += 1
                _res = {
                    "status": "rolled_back",
                    "reason": "质量下降 %.1f%% 超过阈值 %.1f%%（已拒绝提交）"
                              % (_drop * 100.0, self.degrade_limit() * 100.0),
                    "current_quality": _cur_q,
                    "candidate_quality": _new_q,
                    "version": (self.current_version() or {}).get("version"),
                }
                self._last = _res
                return _res

        _idx = self._load_index()
        _cur_v = _idx.get("current") or _BASE_VERSION
        _new_v = self._bump(_cur_v, "patch")
        _meta = {
            "version": _new_v,
            "parent": _cur_v,
            "kind": "incremental",
            "quality": _new_q,
            "kept_samples": len(_kept),
            "rejected_model_output": _rejected,
            "payload": dict(payload or {}),
            "updated_at": float(self._now_fn()),
            "note": "★仅登记框架版本；本批不做实际训练",
        }
        _idx.setdefault("versions", []).append(_meta)
        _idx["current"] = _new_v
        _saved = self._save_index(_idx)
        _pruned = self.prune_versions()
        self._increments += 1
        _res = {"status": "ok" if _saved or not self._writable() else "save_failed",
                "version": _new_v, "parent": _cur_v, "quality": _new_q,
                "kept_samples": len(_kept), "rejected_model_output": _rejected,
                "pruned": _pruned, "saved": _saved}
        self._last = _res
        return _res

    # -------------------------------------------------- 全量重训（仅框架）
    def full_retrain(self, quality_score: float | None = None,
                     payload: dict | None = None, samples: Any = None) -> dict:
        """每周全量重训**框架**：产出训练计划，**不执行任何训练**。"""
        if not self.enabled():
            return {"status": "disabled"}
        _kept = training_guard(samples) if samples is not None else []
        _rejected = (len(samples) - len(_kept)) if isinstance(samples, (list, tuple)) else 0
        _idx = self._load_index()
        _cur_v = _idx.get("current") or _BASE_VERSION
        _new_v = self._bump(_cur_v, "minor")
        _meta = {
            "version": _new_v,
            "parent": _cur_v,
            "kind": "full_retrain",
            "quality": (float(quality_score) if isinstance(quality_score, (int, float))
                        else self.current_quality()),
            "kept_samples": len(_kept),
            "rejected_model_output": _rejected,
            "payload": dict(payload or {}),
            "updated_at": float(self._now_fn()),
            "note": "★仅产出训练计划；实际训练留待后续批次",
        }
        _idx.setdefault("versions", []).append(_meta)
        _idx["current"] = _new_v
        _saved = self._save_index(_idx)
        _pruned = self.prune_versions()
        self._retrains += 1
        _res = {"status": "planned", "version": _new_v, "parent": _cur_v,
                "kept_samples": len(_kept), "rejected_model_output": _rejected,
                "pruned": _pruned, "saved": _saved,
                "plan": ["① 收集：经 training_guard 过滤的样本",
                         "② 标注：仅收教师直答样本（内在模型产出永不入集）",
                         "③ 训练：**本批不执行**",
                         "④ 评估：与 current 版本对比质量",
                         "⑤ 退化防护：下降 > %.0f%% 自动回滚" % (self.degrade_limit() * 100.0)]}
        self._last = _res
        return _res

    # -------------------------------------------------- 回滚
    def rollback(self, version: str | None = None) -> dict:
        """回滚到上一版本（或指定版本）。"""
        if not self.enabled():
            return {"status": "disabled"}
        _idx = self._load_index()
        _vers = _idx.get("versions") or []
        _cur = _idx.get("current")
        _target = version
        if not _target:
            _before = [v for v in _vers if v.get("version") != _cur]
            if not _before:
                return {"status": "no_previous", "current": _cur}
            _target = _before[-1].get("version")
        if not any(v.get("version") == _target for v in _vers):
            return {"status": "not_found", "target": _target}
        _idx["current"] = _target
        _saved = self._save_index(_idx)
        self._rollbacks += 1
        _res = {"status": "ok", "from": _cur, "to": _target, "saved": _saved}
        self._last = _res
        return _res

    # -------------------------------------------------- 版本裁剪
    def prune_versions(self) -> list:
        """保留最近 ``keep_versions`` 个版本（含 current），返回被裁剪的版本号。"""
        _idx = self._load_index()
        _vers = list(_idx.get("versions") or [])
        _keep = self.keep_versions()
        if len(_vers) <= _keep:
            return []
        _pruned = _vers[:-_keep]
        _kept = _vers[-_keep:]
        # 保证 current 一定在保留集内
        if _idx.get("current") and not any(v.get("version") == _idx["current"] for v in _kept):
            _pruned = _vers[:-max(1, _keep - 1)] if _keep > 1 else []
            _kept = _vers[len(_pruned):]
        _idx["versions"] = _kept
        self._save_index(_idx)
        return [v.get("version") for v in _pruned]

    # -------------------------------------------------- 状态
    def stats(self) -> dict:
        _idx = self._load_index()
        return {
            "enabled": self.enabled(),
            "base_dir": self.base_dir(),
            "writable": self._writable(),
            "current": _idx.get("current"),
            "version_count": len(_idx.get("versions") or []),
            "keep_versions": self.keep_versions(),
            "degrade_limit": self.degrade_limit(),
            "increments": self._increments,
            "retrains": self._retrains,
            "rollbacks": self._rollbacks,
            "last": self._last,
        }


# ------------------------------------------------------------------ 单例
_updater: ModelSelfUpdater | None = None


def get_updater() -> ModelSelfUpdater:
    """进程内单例（可注入替身用于测试）。"""
    global _updater
    if _updater is None:
        _updater = ModelSelfUpdater()
    return _updater


def reset_updater() -> None:
    """重置单例（测试用）。"""
    global _updater
    _updater = None
