# -*- coding: utf-8 -*-
from nucleus._silent_except import silent_exc

"""
PulseSnapshot.py —— 脉冲快照

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 知识快照的持久化与版本管理
机制: 大型模块（1724行），包含1个类、10个核心方法，采用分层架构实现
定位: 记忆存储层
"""

import json
import os
import shutil
import tempfile
import threading
import time
import traceback
from typing import Any

from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.logger import get_module_logger
from nucleus.mnemosyne.pa_compat import table_from_rows
from nucleus.mnemosyne.PulseNode import PulseNode

try:
    from config import PARQUET_COMPRESSION, PARQUET_SHARD_BY_EVOL_LEVEL
except Exception:
    PARQUET_COMPRESSION = "snappy"  # ★第109批 T-109a：config 键缺失时回落
    PARQUET_SHARD_BY_EVOL_LEVEL = True


# ★PHASE13-P1-3（2026-09-07）：快照保存耗时告警阈值（秒），外置可调。
#   实测基线：正常 12~416s；异常样本 921s（03:14:41）。
#   取 600s（10分钟）作为告警线——既不会在日常波动时误报，
#   又能在「这次真的慢得离谱」时及时喊人。
SNAPSHOT_SLOW_SAVE_THRESHOLD = 600.0


def _snapshot_slow_threshold() -> float:
    """从 config 读取快照慢保存阈值（秒），失败回落 600。

    延迟导入 config 规避循环依赖；任何异常都回落默认值，
    绝不让「读配置」拖累「存数据」这条生命线。
    """
    _default = SNAPSHOT_SLOW_SAVE_THRESHOLD
    try:
        import config as _cfg
        _val = getattr(_cfg, "SNAPSHOT_SLOW_SAVE_THRESHOLD", _default)
        if isinstance(_val, bool) or not isinstance(_val, (int, float)):
            return _default
        return max(1.0, float(_val))
    except Exception:
        return _default


# ========== ★主线第13批 P2-86：校验和占位符识别（低噪声软校验） ==========

# 校验和算法版本：方法升级（如字段口径变化）时递增，配合 _verify_integrity 做版本兼容。
CHECKSUM_ALGO_VERSION = 1

# 已知占位符集合（测试夹具硬编码值，非真实校验和）。命中即跳过校验，不告警。
_CHECKSUM_PLACEHOLDERS = frozenset({
    "deadbeef",        # tests/test_lazy_snapshot_m9.py 历史遗留占位符
    "DEADBEEF",
    "0" * 16,
    "",
})


def _is_placeholder_checksum(checksum: str) -> bool:
    """判断校验和是否为已知占位符（测试夹具/未初始化值）。"""
    if checksum is None:
        return True
    return str(checksum).strip() in _CHECKSUM_PLACEHOLDERS


def _checksum_debug_mode() -> bool:
    """校验和不匹配是否降级为 DEBUG（config.ENABLE_SNAPSHOT_CHECKSUM_DEBUG，默认 True）。

    True（默认）：不匹配仅输出 DEBUG，避免污染常态日志——
      P2-86 实测：测试残留快照被加载时每次都刷 WARNING。
    False：保持改造前的 WARNING 级别（零回归，供排障/回滚）。
    """
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_SNAPSHOT_CHECKSUM_DEBUG", True))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_checksum_debug_mode L93")
        return True


# ========== ★主线第13批 P2-88：快照路径白名单与临时残留清理 ==========

# 快照文件名前缀（与 tests/test_lazy_snapshot_m9.py 的 mkstemp(prefix="snap_t4_") 对齐）
_TEMP_SNAPSHOT_PREFIX = "snap_t4_"
_TEMP_SNAPSHOT_SUFFIXES = (".json", ".json.bak", ".bak")


def _m41_backup_keep() -> int:
    """★第41批 T2（P1-264）：知识快照备份保留份数。

    原实现为 ``__init__`` 内**硬编码 5** → 无上限可配，实测堆积 5 份 ×438MB。
    现读 ``config.KNOWLEDGE_BACKUP_KEEP``（默认 3）；开关
    ``ENABLE_KNOWLEDGE_BACKUP_ROTATION`` 关闭时**回到旧值 5**（零回归）。
    """
    try:
        import config
        if not bool(getattr(config, "ENABLE_KNOWLEDGE_BACKUP_ROTATION", True)):
            return 5
        _n = int(getattr(config, "KNOWLEDGE_BACKUP_KEEP", 3) or 3)
    except Exception:
        _n = 3
    return max(1, _n)      # 至少保留 1 份，防止配置为 0 时删光全部备份


def _snapshot_path_whitelist_enabled() -> bool:
    """路径白名单开关（config.ENABLE_SNAPSHOT_PATH_WHITELIST，默认 True）。

    延迟导入 config 规避循环依赖；任何异常都回落 True（安全方向：宁可拒绝临时文件）。
    """
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_SNAPSHOT_PATH_WHITELIST", True))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_snapshot_path_whitelist_enabled L129")
        return True


def _temp_snapshot_cleanup_enabled() -> bool:
    """临时残留自动清理开关（config.ENABLE_TEMP_SNAPSHOT_CLEANUP，默认 True）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_TEMP_SNAPSHOT_CLEANUP", True))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_temp_snapshot_cleanup_enabled L138")
        return True


def _is_temp_snapshot_path(path: str) -> bool:
    """判断 path 是否落在「系统临时目录」下（P2-88 根因：Temp 下 pytest 残留被加载）。

    仅做目录判定，不要求文件名前缀——任何位于系统临时目录下的快照路径
    都视为不可信（生产快照永远落在 data/knowledge/）。
    """
    if not path:
        return False
    try:
        _p = os.path.abspath(str(path))
        _temp = os.path.abspath(tempfile.gettempdir())
        # Windows 路径大小写不敏感，统一 normcase 后再比较
        _p_n = os.path.normcase(_p)
        _t_n = os.path.normcase(_temp)
        if _p_n == _t_n or _p_n.startswith(_t_n + os.sep):
            return True
        # 额外兜底：显式识别常见临时目录关键字（跨平台/自定义 TMPDIR 场景）
        _lower = _p_n.replace("/", os.sep)
        return any(_kw in _lower for _kw in ("\\appdata\\local\\temp\\", "/tmp/", "\\temp\\snap_t4_"))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_is_temp_snapshot_path L161")
        return False


def cleanup_temp_snapshot_residue(min_age_seconds: float = 60.0) -> int:
    """清理系统临时目录下 pytest 残留的 snap_t4_*.json / .bak（P2-88）。

    安全约束：
      - 仅在开关开启时执行（调用方负责判定）；
      - 只匹配系统临时目录、文件名以 snap_t4_ 开头、后缀为 .json/.bak 的文件；
      - 只删「修改时间超过 min_age_seconds」的陈旧残留，避免误删正在运行的 pytest 临时文件。

    Returns:
        实际删除的文件数。
    """
    _removed = 0
    try:
        _temp = tempfile.gettempdir()
        if not os.path.isdir(_temp):
            return 0
        _now = time.time()
        for _name in os.listdir(_temp):
            if not _name.startswith(_TEMP_SNAPSHOT_PREFIX):
                continue
            if not _name.endswith(_TEMP_SNAPSHOT_SUFFIXES):
                continue
            _fpath = os.path.join(_temp, _name)
            try:
                if not os.path.isfile(_fpath):
                    continue
                if _now - os.path.getmtime(_fpath) < min_age_seconds:
                    continue  # 太新，可能是正在运行的 pytest 临时文件
                os.remove(_fpath)
                _removed += 1
            except Exception:
                continue  # 单个删除失败不影响其它
    except Exception:
        return _removed
    return _removed


class PulseSnapshot:
    """
    脉冲知识快照管理器（增量保存 + 历史轮转版）
    """

    # ★D4配置中心化：全量保存间隔与增量节点阈值从函数内局部魔法数字提升为类级常量
    #   ★第159批 刀4：以下类常量仅为「config 缺失时」回退值；实际生效值由 config 提供
    #     （SNAPSHOT_FULL_SAVE_INTERVAL / SNAPSHOT_INCREMENTAL_MAX_NODES）。原注释「10分钟/50」
    #     为过期口径，现已 config 化（3600→21600s=6h / 200），此处注释同步避免误导。
    FULL_SAVE_INTERVAL = 600      # 回退值（秒）；实际读 config.SNAPSHOT_FULL_SAVE_INTERVAL（现 21600s=6h）
    INCREMENTAL_MAX_NODES = 50    # 回退值；实际读 config.SNAPSHOT_INCREMENTAL_MAX_NODES（现 200）
    # ★v29/14.44：最小保存间隔节流（秒）。PulseLiver 在压缩/融合后立即调 save()，
    #   高频活动时每 ~14s 触发一次全量 load(3.5s)+dump(2s)+写盘 → 几乎占满心跳周期。
    #   设 30s 最小间隔：间隔内变更延迟写盘（内存 node_pool 始终持有最新数据，不丢数据），
    #   既降峰值压力又保证最终持久化。
    INCREMENTAL_MIN_INTERVAL = 30
    # ★第159批 刀4 D-4：写盘节流命中返回值（区别于 True/False）。调用方据此区分「未写盘（延迟）」
    #   与「已保存」，杜绝把节流误报为「已保存」。配套 self._pending_flush 标志。
    SAVE_THROTTLED = "throttled"

    @property
    def pending_flush(self) -> bool:
        """★第159批 刀4 D-4：节流挂起标志（公开只读）。节流命中置 True，
        任一次真实写盘成功后清零；调用方据其区分「已保存」与「待刷新」。"""
        return self._pending_flush

    def __init__(self, snapshot_path: str = "data/knowledge/pulse_knowledge_snapshot.json"):
        self.snapshot_path = snapshot_path
        self.node_pool = None
        self.resonance_engine = None
        self.hebbian_learner = None
        self.inference_cache = {}
        
        self._last_save_time = 0.0
        self._last_save_nodes = 0
        self._total_saves = 0
        
        # ===== P1-3: 增量保存相关 =====
        self._last_saved_nodes: dict[str, str] = {}  # node_id → 上次保存时的 SHA256 摘要
        # ★第41批 T2（P1-264）：由硬编码 5 改为读 config.KNOWLEDGE_BACKUP_KEEP（默认 3）
        self._max_backups = _m41_backup_keep()       # 保留最近N份历史快照
        self._last_saved_checksum: str = ""
        # ===== ★P0-4新增: 增量保存状态 =====
        self._last_full_save_time: float = 0.0       # 上次全量保存时间
        self._last_write_time: float = 0.0        # ★v29/14.44：上次实际写盘时间（节流用）
        self._last_saved_nodes_map: dict[str, str] = {}  # node_id → 上次保存时的节点校验和
        # ★第159批 刀4 D-4：节流挂起标志。节流命中（延迟写盘）时置 True，
        #   任一次真实写盘成功后清零；调用方据其区分「已保存」与「待刷新」。
        self._pending_flush: bool = False
        # ===== 新增: 生命连续性状态 =====
        self._extra_state: dict[str, Any] = {}  
        # L1独立快照路径
        self.l1_snapshot_path = os.path.join(
            os.path.dirname(snapshot_path) if snapshot_path else "data/knowledge",
            "pulse_l1_snapshot.json"
        )              
        # 接入统一日志系统
        self._logger = get_module_logger("PulseSnapshot")

        # ★INFRA-12修复: 保护所有共享可变状态，避免并发读写竞态
        self._lock = threading.RLock()

        # ===== ★阶段A：Parquet 列式元数据快照（暂缓项6，默认关闭，可回退） =====
        # 落盘路径：data/knowledge/parquet/ 下按 evol_level 分区
        self.parquet_dir = os.path.join(
            os.path.dirname(snapshot_path) if snapshot_path else "data/knowledge",
            "parquet",
        )
        # 是否启用 Parquet 快照（读 config.FEATURE['use_parquet_snapshot']，默认 False）
        self._use_parquet = False
        try:
            from config import FEATURE as _FEATURE
            self._use_parquet = bool(_FEATURE.get("use_parquet_snapshot", False))
        except Exception:
            self._use_parquet = False

        # ★主线第13批 P2-88：清理系统临时目录下 pytest 残留的 snap_t4_* 文件。
        #   开关关闭时完全不执行（零副作用）；任何异常都被吞掉，绝不影响构造。
        if _temp_snapshot_cleanup_enabled():
            try:
                _n = cleanup_temp_snapshot_residue()
                if _n > 0:
                    self._logger.info(
                        f"[P2-88] 已清理临时快照残留 {_n} 个（%TEMP%/snap_t4_*）")
            except Exception as e:
                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:269", level="warning")

    def _log(self, level: str, msg: str):
        """统一日志输出"""
        log_level = {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }.get(level, 20)
        self._logger.log(log_level, msg)

    # ========== 框架注入 ==========
    
    def set_node_pool(self, node_pool):
        self.node_pool = node_pool
        # ★第81批补 T2：自动绑定冷召回源（消除 main 漏调→fn 永远 None 失效类）。
        # 仅在尚未显式绑定时自动设置，可被 set_cold_recall_source 显式覆盖。
        if node_pool is not None and getattr(self, "_m70_cold_recall_fn", None) is None:
            _fn = getattr(node_pool, "recall_cold_nodes_batch", None)
            if callable(_fn):
                self._m70_cold_recall_fn = _fn
        
    def set_resonance_engine(self, engine):
        self.resonance_engine = engine
        
    def set_hebbian_learner(self, learner):
        self.hebbian_learner = learner
        
    # ========== 保存（P1-3 改造：增量 + 轮转） ==========
    def _m44_write_allowed(self) -> bool:
        """★第44批 T4（P2-290）：写盘守卫 —— 测试环境不得写生产 data/。

        判据同 `nucleus/data/write_guard.py`：pytest 环境 + 目标路径落在本项目
        ``data/`` 内 + 未显式注入 → 拒写。★只判 ``data/`` 前缀，因此
        测试传入临时 ``snapshot_path`` 时**不受影响**（零回归）。
        """
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            return _m44_gw(getattr(self, "snapshot_path", "") or "",
                           component="PulseSnapshot")
        except ImportError as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m44_write_allowed L314")
            return True

    def save(self, force_full: bool = False) -> bool:
        """
        ★P0-4修复：智能保存——增量优先，定期全量。
        
        增量模式：仅序列化上次保存后有变更的节点（基于校验和对比）。
        全量模式：每10分钟或节点变更超过50个时触发。
        增量保存将变更节点合并到已有快照文件中，避免全量序列化开销。
        """
        # ★第44批 T4（P2-290）：写盘守卫 —— 测试环境不得写生产 data/
        if not self._m44_write_allowed():
            return False
        # ★主线第67批 T1/P0：异步保存。
        #   周期保存提交后台线程，立即返回，不阻塞调用方（消除「保存持锁→队列积压」恶性循环）。
        #   ★force_full=True（退出前强制全量）恒走同步，保证退出前落盘，语义与改造前一致。
        if (not force_full) and self._m67_async_save_enabled():
            return self._save_async()
        return self._save_sync(force_full)

    def _save_sync(self, force_full: bool = False) -> bool:
        """★主线第67批 T1：原 save() 的同步主体（同步路径与异步线程共用，逻辑零变化）。"""
        # ★主线第12批 T4/P2-31：调用点③「快照保存前」整体知识健康度采样 INFO。
        #   灰度 ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS；关闭/异常 → 跳过，零副作用。
        try:
            import config as _cfg_dq_snap
            if getattr(_cfg_dq_snap, "ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS", False):
                from nucleus.knowledge.DataQualityGuard import (
                    get_data_quality_guard as _gdq_snap,
                )
                _gs = _gdq_snap().get_check_stats()
                self._logger.info(
                    f"[DataQualityGuard] 快照保存前健康度: 累计检查{_gs.get('checked', 0)}次, "
                    f"异常{_gs.get('bad', 0)}个")
        except Exception as e:
            silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:347", level="warning")

        if self.node_pool is None:
            self._log(LogLevel.WARNING, "node_pool 未注入，跳过保存")
            return False

        # ★P1修复：并发写竞态加锁。save()/_incremental_save()/save_l1()/save_parquet()
        # 均为写路径，此前未使用 self._lock，多线程同时触发时会产生「读-改-写」竞态，
        # 表现为 L1快照保存失败(WinError 2)、快照文件损坏、数据丢失。
        # self._lock 是 RLock（可重入），save() 内部调用 save_l1()/save_parquet() 不会死锁。
        with self._lock:
            return self._save_locked(force_full=force_full)
    
    def _save_locked(self, force_full: bool = False) -> bool:
        """★P1修复：save() 的实际逻辑（已持有 self._lock，避免重入死锁的扁平化实现）。"""
        start_time = time.time()
        
        all_nodes = self.node_pool.get_all_including_evicted()
        # 跳过临时节点
        saved_nodes = [n for n in all_nodes if not getattr(n, 'ephemeral', False)]
        # ★主线第16批 T4/P2-103：保存前若有效节点为 0 → 告警（不阻断保存）。
        #   0 节点快照虽然合法（空知识库），但频繁出现往往意味着保存链路有问题。
        if (not saved_nodes) and self._snapshot_diagnostic_enabled():
            self._log(LogLevel.WARNING,
                      f"保存快照时有效节点数为 0（总节点 {len(all_nodes)} 个，"
                      f"可能全部为 ephemeral 或节点池为空）—— 不阻断保存，请留意")
        
        # 计算当前节点列表的校验和
        current_checksum = self._compute_node_list_checksum(saved_nodes)
        
        # 无变更时跳过
        if self._last_saved_checksum and current_checksum == self._last_saved_checksum:
            elapsed = time.time() - start_time
            seconds_since_last_save = time.time() - self._last_save_time if self._last_save_time > 0 else 0
            if seconds_since_last_save < 5:
                self._log(LogLevel.DEBUG,
                          f"快照无变更，距离上次保存仅{seconds_since_last_save:.1f}秒，跳过")
            else:
                self._log(LogLevel.INFO,
                          f"快照无变更，跳过保存 (总{len(saved_nodes)}节点, "
                          f"距上次保存{seconds_since_last_save:.0f}秒) "
                          f"耗时 {elapsed:.2f}s")
            return True
        
        # ★v29/14.44：写盘节流——距上次实际写盘 < 最小间隔且非强制 → 延迟写盘。
        #   内存 node_pool 始终持有最新数据，延迟写盘不丢数据；下次触发时 checksum 已变会继续。
        #   仅对「变更节点数小」的常规增量生效；全量保存（10分钟周期）不节流，保证周期落盘。
        #   ★第159批 刀4 D-4：节流命中不再返回 True（否则调用方误判「已保存」），
        #     改为返回 SAVE_THROTTLED 并置 _pending_flush=True，调用方可据标志区分「待刷新」。
        #     force_full（退出/强制）恒走真实写盘，不受节流影响。
        if (time.time() - self._last_write_time < self.INCREMENTAL_MIN_INTERVAL
                and self._last_write_time > 0
                and not force_full):
            elapsed = time.time() - start_time
            self._pending_flush = True
            self._log(LogLevel.DEBUG,
                      f"快照写盘节流：距上次写盘{time.time()-self._last_write_time:.1f}s"
                      f"<{self.INCREMENTAL_MIN_INTERVAL}s，延迟落盘（内存持有最新数据，pending_flush=True）")
            return self.SAVE_THROTTLED

        # ★P0-4新增：判断使用增量还是全量模式（D4：改用类级常量 FULL_SAVE_INTERVAL / INCREMENTAL_MAX_NODES）
        _now = time.time()
        # ★v24.0快照保护：如果当前内存节点数远少于上次保存的节点数，
        # 说明可能发生了意外丢失。此时不覆盖主快照，而是将本次保存写入带时间戳的备份。
        _last_saved_count = getattr(self, '_last_save_nodes', 0)
        if _last_saved_count > 0 and len(saved_nodes) < _last_saved_count * 0.5:
            self._log(LogLevel.WARNING,
                     f"快照保护：当前内存节点({len(saved_nodes)})远少于上次保存({_last_saved_count})，"
                     f"本次不覆盖主快照，改为写入保护备份。")
            _protected_path = self.snapshot_path + f".protected_{int(time.time())}.bak"
            try:
                snapshot = self._build_full_snapshot(saved_nodes, current_checksum)
                safe_write_json(_protected_path, snapshot, indent=2)
                self._last_saved_checksum = current_checksum
                self._update_saved_nodes_map(saved_nodes)
                self._last_save_time = time.time()
                self._last_save_nodes = len(saved_nodes)
                self._pending_flush = False
                return True
            except Exception as _e:
                self._log(LogLevel.ERROR, f"保护快照写入失败: {_e}")
                return False
        # ===== 快照保护结束 =====        
        # 计算变更节点数量（复用筛选结果避免双重遍历）
        _changed_nodes_preview, _ = self._get_changed_nodes(saved_nodes)
        _changed_count = len(_changed_nodes_preview)
        _seconds_since_full = _now - self._last_full_save_time if hasattr(self, '_last_full_save_time') else self._m67_full_save_interval() + 1
        
        # ★P0-1修复(2026-09-03)：退出时强制全量保存。
        # 增量保存需读全文件+解析+构建索引+写回，8700节点下比全量慢10倍以上(170s vs 9s)，
        # 退出时距上次全量保存近会触发增量导致90s超时。force_full直接走全量。
        if force_full:
            _use_incremental = False
        else:
            _use_incremental = (
                _seconds_since_full < self._m67_full_save_interval()
                and _changed_count <= self._m67_incremental_max_nodes()
                and os.path.exists(self.snapshot_path)
            )
        
        if _use_incremental:
            # ===== 增量保存模式 =====
            # ★第67批 T2：优先走真正的增量日志（jsonl 追加，O(变更数)）；
            #   未开启或写入失败 → 回退既有 _incremental_save，零回归。
            if self._m67_incremental_log_enabled():
                _success = self._m67_incremental_log_save(
                    saved_nodes, current_checksum, start_time)
                if not _success:
                    _success = self._incremental_save(
                        saved_nodes, current_checksum, start_time)
            else:
                _success = self._incremental_save(saved_nodes, current_checksum, start_time)
        else:
            # ===== 全量保存模式 =====
            # ★第81批补2 T2：改走统一全量检查点（JSON+Parquet 物化成功后才清空增量日志）
            _success = self._m67_full_checkpoint(saved_nodes, current_checksum, start_time)
            if _success:
                self._last_full_save_time = _now
        if _success:
            # ★v24.0同步：主快照保存成功后同步保存L1独立快照
            try:
                self.save_l1()
            except Exception as _e:
                self._log(LogLevel.WARNING, f"L1快照同步保存失败: {_e}")
            # ★阶段A：启用 Parquet 快照时，仅在全量保存成功后额外写 Parquet（失败不影响主流程）。
            # ★v30.0修复：增量保存不再触发 Parquet 写入，避免每60秒一次的全量重写导致
            #   data/knowledge/parquet 文件无限膨胀。Parquet 作为「全量列式副本」，
            #   只跟随全量节奏（约10分钟一次）刷新，与 JSON 主快照的轮转机制对齐。
            # ★第81批补2 T2：Parquet 刷新已移入 _m67_full_checkpoint（JSON 成功后按
            #   self._use_parquet 判定写入并据返回值决定清空增量日志），此处不再重复刷。
            # ★第159批 刀4 D-4：真实写盘成功 → 清除节流挂起标志。
            self._pending_flush = False

        return _success
        
    def _build_full_snapshot(self, saved_nodes: list, current_checksum: str) -> dict[str, Any]:
        l2_l3_nodes = [n for n in saved_nodes if n.evol_level in (PulseNode.EVOL_L2, PulseNode.EVOL_L3)]
        l1_nodes = [n for n in saved_nodes if n.evol_level == PulseNode.EVOL_L1]
        freq_index = {}
        if self.resonance_engine:
            freq_index = {
                "freq_keys": self.resonance_engine.get_freq_index_keys(),
                "space_keys": self.resonance_engine.get_space_index_keys(),
            }
        hebbian_weights = {}
        if self.hebbian_learner:
            hebbian_weights = {
                "total_cooccurrences": getattr(self.hebbian_learner, '_total_cooccurrences', 0),
                "connection_count": len(getattr(self.hebbian_learner, '_connections', {})),
            }
        snapshot = {
            "version": "v9.5",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_nodes_all": len(saved_nodes),
            "l2_l3_count": len(l2_l3_nodes),
            "l1_saved_count": len(l1_nodes),
            "save_mode": "protected",
            "node_count_at_save": len(saved_nodes),
            "nodes": [node.to_dict() for node in saved_nodes],
            "node_list_checksum": current_checksum,
            "freq_index": freq_index,
            "hebbian_weights": hebbian_weights,
            "inference_cache": self.inference_cache,
            "extra_state": self._extra_state,
        }
        # ★阶段B'：冷存储启用时，快照只记录被驱逐的 node_id 清单（不存冷节点全文，B8 硬约束）
        if (self.node_pool is not None
                and getattr(self.node_pool, 'is_cold_storage_enabled', lambda: False)()):
            _evicted = sorted(getattr(self.node_pool, '_cold_evicted', set()))
            snapshot["evicted_node_ids"] = _evicted
        return snapshot
    def _count_changed_nodes(self, saved_nodes: list) -> int:
        """
        ★P0-4新增：统计自上次保存后发生变更的节点数量。
        变更判断基于节点校验和对比。
        """
        if not self._last_saved_nodes_map:
            return len(saved_nodes)  # 无历史记录，全部视为变更
        
        _changed = 0
        for _node in saved_nodes:
            _node_id = _node.node_id
            _current_checksum = getattr(_node, 'checksum', '')
            _saved_checksum = self._last_saved_nodes_map.get(_node_id, '')
            if _current_checksum != _saved_checksum:
                _changed += 1
        
        # 也统计被删除的节点
        _current_ids = {n.node_id for n in saved_nodes}
        _deleted = sum(1 for _nid in self._last_saved_nodes_map if _nid not in _current_ids)
        
        return _changed + _deleted

    def _get_changed_nodes(self, saved_nodes: list) -> tuple[list, set]:
        """
        ★v23.0新增：筛选自上次保存后发生变更的节点。
        
        Returns:
            (变更节点列表, 当前节点ID集合)
        """
        if not self._last_saved_nodes_map:
            # 无历史记录，全部视为变更
            return saved_nodes, {n.node_id for n in saved_nodes}
        
        _changed = []
        for _node in saved_nodes:
            _node_id = _node.node_id
            _current_checksum = getattr(_node, 'checksum', '')
            _saved_checksum = self._last_saved_nodes_map.get(_node_id, '')
            if _current_checksum != _saved_checksum:
                _changed.append(_node)
        
        _current_ids = {n.node_id for n in saved_nodes}
        return _changed, _current_ids

    def _incremental_save(self, saved_nodes: list, current_checksum: str, start_time: float) -> bool:
        """
        ★P0-4新增：增量保存——将变更节点合并到已有快照。
        """
        self._log(LogLevel.DEBUG, "开始增量保存...")
        
        try:
            # 加载已有快照
            _existing = safe_read_json(self.snapshot_path, default={})
        except (ValueError, OSError):
            # 加载失败，降级为全量
            self._log(LogLevel.WARNING, "增量保存加载失败，降级为全量")
            return self._full_save(saved_nodes, current_checksum, start_time)
        
        # ★v23.0优化：先筛选变更节点，减少不必要的序列化
        _changed_nodes, _current_ids = self._get_changed_nodes(saved_nodes)
        # 构建节点ID→索引映射
        _existing_nodes = _existing.get("nodes", [])
        _existing_map = {n.get("node_id", ""): i for i, n in enumerate(_existing_nodes)}        
        # ★F1 零更新前置校验：若无任何内容变更，且无节点增删（内存集合 == 磁盘集合），
        #   直接跳过写盘，避免无意义的读文件 + 构建映射 + 原子写 IO。
        #   注意：save() 顶部的 checksum 已拦截绝大多数无变更场景，此处为增量路径的
        #   最后一道防御，防止「checksum 相同但 _last_saved_nodes_map 与磁盘不同步」时
        #   仍执行一次空写。
        _disk_ids = {n.get("node_id", "") for n in _existing_nodes}
        if not _changed_nodes and _current_ids == _disk_ids:
            self._log(LogLevel.DEBUG,
                      "增量保存检测到零变更，跳过写盘（防御性校验）")
            self._last_saved_checksum = current_checksum
            self._last_save_time = time.time()
            self._last_save_nodes = len(saved_nodes)
            return True        
        
        # 合并变更节点（只序列化变更的节点）
        _merged_count = 0
        _new_count = 0
        for _node in _changed_nodes:
            _node_dict = _node.to_dict()
            _node_id = _node.node_id
            
            if _node_id in _existing_map:
                # 已存在：更新
                _idx = _existing_map[_node_id]
                _existing_nodes[_idx] = _node_dict
                _merged_count += 1
            else:
                # 新增节点
                _existing_nodes.append(_node_dict)
                _new_count += 1
        
        # 删除已淘汰的节点（使用已计算的当前ID集合）
        _existing_nodes = [n for n in _existing_nodes if n.get("node_id", "") in _current_ids]
        
        # 更新快照元信息
        _l2_l3 = [n for n in saved_nodes if n.evol_level in (PulseNode.EVOL_L2, PulseNode.EVOL_L3)]
        _l1 = [n for n in saved_nodes if n.evol_level == PulseNode.EVOL_L1]
        
        _existing["total_nodes_all"] = len(saved_nodes)
        _existing["l2_l3_count"] = len(_l2_l3)
        _existing["l1_saved_count"] = len(_l1)
        _existing["save_mode"] = "incremental"
        _existing["node_count_at_save"] = len(saved_nodes)
        _existing["node_list_checksum"] = current_checksum
        _existing["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _existing["nodes"] = _existing_nodes
        
        try:
            # ★v29：增量保存用紧凑模式 + 不轮转（省去 209MB 文件 rename+重写两次）
            self._atomic_write_with_rotation(_existing, compact=True, rotate=False)
        except Exception as e:
            self._log(LogLevel.ERROR, f"增量保存失败: {e}")
            return False
        
        # 更新状态
        self._last_saved_checksum = current_checksum
        self._update_saved_nodes_map(saved_nodes)
        self._last_write_time = time.time()  # ★v29/14.44：记录实际写盘时间
        
        elapsed = time.time() - start_time
        self._last_save_time = time.time()
        self._last_save_nodes = len(saved_nodes)
        self._total_saves += 1
        
        self._log(LogLevel.INFO,
                  f"增量保存完成: {len(saved_nodes)}节点 (更新{_merged_count}个, 新增{_new_count}个) "
                  f"耗时 {elapsed:.2f}s")
        return True
    
    def _full_save(self, saved_nodes: list, current_checksum: str, start_time: float) -> bool:
        """
        ★P0-4重构：全量保存逻辑从原save()方法提取。
        """
        self._log(LogLevel.INFO, "开始全量保存...")
        
        l2_l3_nodes = [n for n in saved_nodes if n.evol_level in (PulseNode.EVOL_L2, PulseNode.EVOL_L3)]
        l1_nodes = [n for n in saved_nodes if n.evol_level == PulseNode.EVOL_L1]
        
        freq_index = {}
        if self.resonance_engine:
            freq_index = {
                "freq_keys": self.resonance_engine.get_freq_index_keys(),
                "space_keys": self.resonance_engine.get_space_index_keys(),
            }
        
        hebbian_weights = {}
        if self.hebbian_learner:
            hebbian_weights = {
                "total_cooccurrences": getattr(self.hebbian_learner, '_total_cooccurrences', 0),
                "connection_count": len(getattr(self.hebbian_learner, '_connections', {})),
            }
        
        snapshot = {
            "version": "v9.5",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_nodes_all": len(saved_nodes),
            "l2_l3_count": len(l2_l3_nodes),
            "l1_saved_count": len(l1_nodes),
            "save_mode": "full",
            "node_count_at_save": len(saved_nodes),
            "node_list_checksum": current_checksum,
            "freq_index": freq_index,
            "hebbian_weights": hebbian_weights,
            "inference_cache": self.inference_cache,
            "extra_state": self._extra_state,
        }
        
        try:
            # ★v29：全量保存同样用紧凑模式（保留轮转备份，兼容旧版加载）
            # ★第67批 T1：分批流式写（默认开，SNAPSHOT_BATCH_SIZE > 0）——
            #   逐批 to_dict 直接写文件，避免一次性构建 1.2 万节点的超大列表。
            #   关闭流式（<= 0）时回退为「整体构建 nodes + 原子写」，行为与改造前一致。
            # ★第68批 T1：JSON 降级为**可选**兼容备份（SNAPSHOT_SAVE_JSON_BACKUP）。
            #   默认 True 保持双写（安全兜底）；置 False 则只写 Parquet 主存储。
            if self._m68_json_backup_enabled():
                if self._m67_batch_size() > 0:
                    self._m67_write_snapshot_streaming(snapshot, saved_nodes)
                else:
                    snapshot["nodes"] = [_n.to_dict() for _n in saved_nodes]
                    self._atomic_write_with_rotation(snapshot, compact=True, rotate=True)
            else:
                self._log(LogLevel.INFO,
                          "[第68批] JSON 兼容备份已关闭，仅写 Parquet 主存储")
        except Exception as e:
            self._log(LogLevel.ERROR, f"全量保存失败: {type(e).__name__}: {e}")
            return False
        
        # 更新状态
        self._last_saved_checksum = current_checksum
        self._update_saved_nodes_map(saved_nodes)
        self._last_write_time = time.time()  # ★v29/14.44：记录实际写盘时间
        
        elapsed = time.time() - start_time
        self._last_save_time = time.time()
        self._last_save_nodes = len(saved_nodes)
        self._total_saves += 1
        
        self._log(LogLevel.INFO,
                  f"全量保存完成: {len(saved_nodes)}个节点 "
                  f"(L1={len(l1_nodes)}, L2/L3={len(l2_l3_nodes)}) "
                  f"耗时 {elapsed:.2f}s")
        # ★PHASE13-P1-3（2026-09-07）：快照保存超时告警。
        #   15小时运行实测：正常 12~416s，但 03:14:41 出现过一次 **921.17s**
        #   （约 15.4 分钟），是正常值的 2~6 倍，事后自行恢复、未再复现。
        #   该次异常与「03:09 队列深度冲到 1566」时间上强相关 —— 大概率是
        #   队列堆积 + 锁竞争拖慢写盘，而非快照逻辑本身有缺陷。
        #   此前这种情况只在 INFO 里留一行耗时，没人会去翻；超过阈值必须升
        #   级为 WARNING，让「数据持久化变慢」这件事自己跳出来。
        _warn_threshold = _snapshot_slow_threshold()
        if elapsed >= _warn_threshold:
            self._log(LogLevel.WARNING,
                      f"[快照超时] 全量保存耗时 {elapsed:.2f}s，超过告警阈值 "
                      f"{_warn_threshold}s（{len(saved_nodes)}个节点）。"
                      f"可能原因：脉冲队列积压 / 锁竞争 / 磁盘IO拥塞。"
                      f"建议检查同期队列深度与锁等待指标。")
        # ★第68批 T1：保存后校验 Parquet 主存储完整性（各分片总行数）
        _pq_rows = self._m68_verify_parquet()
        if _pq_rows >= 0:
            self._log(LogLevel.INFO, f"[第68批] Parquet 主存储校验: {_pq_rows} 行")
        return True

    # ===== ★主线第67批 T1/P0：异步保存 + 分批流式序列化（止血核心） =====

    def _m67_async_save_enabled(self) -> bool:
        """★T1：异步保存开关（默认开）。关闭 → 完全走同步路径，行为与改造前一致。"""
        try:
            import config as _cfg67
            return bool(getattr(_cfg67, "SNAPSHOT_ASYNC_SAVE", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_async_save_enabled L744")
            return True

    def _m67_batch_size(self) -> int:
        """★T1：分批序列化的每批节点数。<=0 表示关闭流式写，回退原整体 dump 路径。"""
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_BATCH_SIZE", 1000))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_batch_size L752")
            return 1000

    def _m67_full_save_interval(self) -> int:
        """★T1：全量保存间隔（秒）。读 config.SNAPSHOT_FULL_SAVE_INTERVAL，默认回退类级常量 600。"""
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_FULL_SAVE_INTERVAL", self.FULL_SAVE_INTERVAL))
        except Exception:
            return self.FULL_SAVE_INTERVAL

    def _m67_incremental_max_nodes(self) -> int:
        """★T1：增量保存最大变更节点数。读 config.SNAPSHOT_INCREMENTAL_MAX_NODES，默认回退类级常量 50。"""
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_INCREMENTAL_MAX_NODES", self.INCREMENTAL_MAX_NODES))
        except Exception:
            return self.INCREMENTAL_MAX_NODES

    def _save_async(self) -> bool:
        """★T1：把保存提交到后台线程并立即返回，不阻塞调用方。
        ★主线第78批 T4：合并触发（coalesce）——保存进行中若再有触发，不直接丢弃，
        而是置 _m67_pending_save 标记；当前保存结束后据该标记补一次保存，
        保证「不丢数据」且「最多 1 个保存线程」（避免线程堆积 / 触发堆积恶性循环）。
        线程内异常只记 ERROR，绝不影响框架其它部分运行。"""
        if getattr(self, "_m67_is_saving", False):
            # ★第80批 T3：in-flight 卡死检测（watchdog）。
            #   若上一次保存已持续超过阈值（取 SNAPSHOT_SAVE_TIMEOUT 与 2×全量间隔的较大者），
            #   视为保存卡死（如磁盘 IO 阻塞），强制重置标记 + ERROR + snapshot_stalled 告警，
            #   允许本次启动新保存，避免快照永久不更新。
            _stall_to = 1800.0
            try:
                import config as _c80
                _stall_to = float(getattr(_c80, "SNAPSHOT_SAVE_TIMEOUT", 1800) or 1800)
                _full_int = float(getattr(_c80, "SNAPSHOT_FULL_SAVE_INTERVAL", 3600) or 3600)
                _stall_to = max(_stall_to, 2 * _full_int)
            except Exception as e:
                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:786", level="warning")
            _stuck = time.time() - getattr(self, "_m67_save_start_time", 0)
            if _stuck > _stall_to:
                self._log(LogLevel.ERROR,
                          f"[第80批 T3] 快照保存卡死检测：in-flight 已 {_stuck:.0f}s（阈值 {_stall_to:.0f}s），"
                          f"强制重置并启动新保存（告警 snapshot_stalled）")
                self._m67_is_saving = False
                self._m67_pending_save = False
            else:
                self._m67_pending_save = True
                self._log(LogLevel.DEBUG,
                          "[第67批] 已有快照保存进行中，本次触发合并为待保存（不丢数据，避免堆积）")
                return True
        import threading as _th67
        self._m67_is_saving = True
        self._m67_pending_save = False
        _t0 = time.time()
        self._m67_save_start_time = _t0  # ★第80批 T3：记录保存起始时间，供入口 watchdog 检测卡死

        def _run():
            try:
                self._save_sync(force_full=False)
            except Exception as _e:
                self._log(LogLevel.ERROR,
                          f"[第67批] 异步快照保存异常: {type(_e).__name__}: {_e}")
            finally:
                self._m67_is_saving = False
                # ★主线第78批 T4：合并触发——若保存期间又有触发，补一次保存（最多再 1 个线程）。
                #   仅当此时标记存在才补，且补前清标记，避免与并发触发的重复。
                if getattr(self, "_m67_pending_save", False):
                    self._m67_pending_save = False
                    try:
                        self._save_async()
                    except Exception as _e2:
                        self._log(LogLevel.ERROR,
                                  f"[第67批] 合并触发快照保存失败: {type(_e2).__name__}: {_e2}")
                _el = time.time() - _t0
                try:
                    import config as _c67
                    _to = float(getattr(_c67, "SNAPSHOT_SAVE_TIMEOUT", 1800) or 1800)
                except Exception:
                    _to = 1800.0
                if _el >= _to:
                    # ★超时只告警不中断：中断正在写的快照会导致数据丢失。
                    self._log(LogLevel.WARNING,
                              f"[第67批] 异步快照保存耗时 {_el:.1f}s 超过阈值 {_to:.0f}s"
                              f"（仅告警，不中断写入以免丢数据）")

        _th67.Thread(target=_run, name="SnapshotAsyncSave67", daemon=True).start()
        return True

    def _m67_write_snapshot_streaming(self, meta: dict, saved_nodes: list) -> None:
        """★T1：分批流式写快照。
        不再一次性构建 [node.to_dict() for node in saved_nodes] 超大列表，
        而是逐批 to_dict 并直接写入文件句柄，显著降低内存峰值。
        ★双缓冲语义：每个节点的 dict 在生成的那一刻即为当时快照，
          此后节点对象被修改不影响已写入的内容，因此保存期间节点池可继续读写。
        落盘仍沿用「tempfile + os.replace」原子替换，并保留轮转备份。"""
        snapshot_dir = os.path.dirname(self.snapshot_path)
        if snapshot_dir and not os.path.exists(snapshot_dir):
            os.makedirs(snapshot_dir, exist_ok=True)

        if os.path.exists(self.snapshot_path):
            _bak = f"{self.snapshot_path}.{time.strftime('%Y%m%d_%H%M%S')}.bak"
            try:
                os.rename(self.snapshot_path, _bak)
            except Exception as _e:
                self._log(LogLevel.WARNING,
                          f"快照轮转备份失败(无回退副本): {type(_e).__name__}: {_e}")

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="snapshot_",
                                            dir=snapshot_dir or ".")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write("{")
                _first = True
                for _k, _v in meta.items():
                    if not _first:
                        f.write(",")
                    f.write(json.dumps(_k, ensure_ascii=False))
                    f.write(":")
                    f.write(json.dumps(_v, ensure_ascii=False))
                    _first = False
                f.write(',"nodes":[')
                _bs = self._m67_batch_size()
                _n = len(saved_nodes)
                for _i in range(0, _n, _bs):
                    _batch = saved_nodes[_i:_i + _bs]
                    for _j, _node in enumerate(_batch):
                        if _i + _j > 0:
                            f.write(",")
                        f.write(json.dumps(_node.to_dict(), ensure_ascii=False))
                    del _batch
                f.write("]}")
                f.flush()
                os.fsync(f.fileno())
            # ★第80批 T4：纯 os.replace 原子覆盖（Windows 可直接覆盖已存在目标，
            #   前置 os.remove 冗余且目标被锁时会抛错；原子替换保证崩溃窗口最小）。
            os.replace(tmp_path, self.snapshot_path)
        except Exception:
            # ★第80批 T4：写失败保留 tmp 副本供恢复（不再无条件删除），
            #   重命名为带时间戳 .failed 副本，便于崩溃后人工/自动恢复。
            try:
                if os.path.exists(tmp_path):
                    _failed = f"{tmp_path}.failed_{time.strftime('%Y%m%d_%H%M%S')}"
                    os.rename(tmp_path, _failed)
                    self._log(LogLevel.ERROR, f"[第80批 T4] 快照写入失败，保留临时副本供恢复: {_failed}")
            except Exception as e:
                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:894", level="warning")
            raise
        self._cleanup_old_backups()

    # ===== ★主线第67批 T2/P0：真正的增量保存（jsonl 增量日志） =====

    def _m67_incremental_log_enabled(self) -> bool:
        """★T2：增量日志开关（默认开）。关闭 → 回退既有 _incremental_save。"""
        try:
            import config as _cfg67
            return bool(getattr(_cfg67, "SNAPSHOT_USE_INCREMENTAL_LOG", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_incremental_log_enabled L908")
            return True

    def _m67_incremental_log_path(self) -> str:
        _d = os.path.dirname(self.snapshot_path) or "."
        return os.path.join(_d, "snapshot_incremental.jsonl")

    def _m67_incremental_log_max(self) -> int:
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_INCREMENTAL_LOG_MAX_LINES", 10000))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_incremental_log_max L919")
            return 10000

    def _m67_incremental_delete_ratio_max(self) -> float:
        """★第81批 T3：单轮删除占比熔断阈值（默认 0.5）。"""
        try:
            import config as _cfg67
            return float(getattr(_cfg67, "SNAPSHOT_INCREMENTAL_LOG_DELETE_RATIO_MAX", 0.5))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_incremental_delete_ratio_max L927")
            return 0.5

    def _m67_incremental_delete_abs_max(self) -> int:
        """★D154 边界洞②：单轮删除绝对量硬上限（默认 500），与比例阈值构成双护栏。"""
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_INCREMENTAL_LOG_DELETE_ABS_MAX", 500))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_incremental_delete_abs_max L935")
            return 500

    def _m67_incremental_log_lines(self) -> int:
        _p = self._m67_incremental_log_path()
        try:
            if not os.path.exists(_p):
                return 0
            _n = 0
            with open(_p, "r", encoding="utf-8", errors="replace") as f:
                for _l in f:
                    if _l.strip():
                        _n += 1
            return _n
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m67_incremental_log_lines L949")
            return 0

    def _m67_incremental_log_save(self, saved_nodes: list, current_checksum: str,
                                   start_time: float) -> bool:
        """★第81批 T3：修正删除集根因 + 删除比例熔断 + 行 checksum。

        - 删除集 = 上次保存有、本次无（不再误把 _get_changed_nodes 第二返回值当删除集）；
        - 单轮删除占比超阈值 → 拒写 delete + ERROR（含期望/实际计数）+ 本轮降级全量保存；
        - 每条 upsert 行附带节点 checksum，重放端校验不符即跳过。
        """
        _prev_map = getattr(self, "_last_saved_nodes_map", {}) or {}
        try:
            _changed, _current_ids = self._get_changed_nodes(saved_nodes)
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"[第81批 T3] 变更节点计算失败，回退: {type(_e).__name__}: {_e}")
            return False
        _current_ids = _current_ids or set()
        _now = time.time()
        # ★根因修正：删除集 = 上次有、本次无（upsert 用 changed、delete 用 deleted_ids）
        _deleted_ids = set(_prev_map.keys()) - _current_ids
        # ★删除比例熔断（阈值进 config）
        _surviving = len(_current_ids)
        _del_threshold = self._m67_incremental_delete_ratio_max()
        # ★D154 边界洞①：存活=0 时原 `_del_ratio=0.0` + `_surviving>0` 前置使熔断永不触发，
        #   且会继续 emit 全量 delete 行（覆盖 _prev_map）。直接拒写并降级全量保存。
        if _surviving == 0:
            self._log(LogLevel.ERROR,
                      f"[第81批 T3/D154] 增量日志存活节点=0（疑似加载降级），拒写 delete "
                      f"({len(_deleted_ids)} 条)，降级全量保存")
            return self._m67_full_checkpoint(saved_nodes, current_checksum, time.time())
        _del_ratio = len(_deleted_ids) / _surviving
        # ★D154 边界洞②：阈值 0.5 无绝对量护栏 → 单轮误删 49.9% 无感。
        #   加第二道绝对量硬上限（默认 500，可 config 覆盖），与比例阈值构成双护栏。
        _del_abs_cap = self._m67_incremental_delete_abs_max()
        _fuse_reason = None
        if _del_ratio > _del_threshold:
            _fuse_reason = f"比例 {_del_ratio*100:.1f}% > 阈值 {_del_threshold*100:.1f}%"
        elif len(_deleted_ids) > _del_abs_cap:
            _fuse_reason = f"绝对量 {len(_deleted_ids)} > 硬上限 {_del_abs_cap}"
        if _fuse_reason:
            # 熔断：拒写 delete + ERROR + 本轮降级全量保存
            self._log(LogLevel.ERROR,
                      f"[第81批 T3/D154] 增量日志删除熔断: {_fuse_reason}，"
                      f"删除 {len(_deleted_ids)}/存活 {_surviving}，拒写 delete，降级全量保存")
            # ★第81批补2 T3：改走统一全量检查点（刷新 Parquet + 仅物化成功才清空 jsonl）。
            return self._m67_full_checkpoint(saved_nodes, current_checksum, time.time())
        # 正常：upsert 行（含 checksum）+ 仅真删除的 delete 行
        _buf = []
        for _n in (_changed or []):
            try:
                _cs = getattr(_n, "checksum", "")
                _buf.append(json.dumps({
                    "node_id": getattr(_n, "node_id", "") or "",
                    "action": "upsert", "ts": _now,
                    "checksum": _cs,
                    "data": _n.to_dict(),
                }, ensure_ascii=False))
            except Exception as _e:
                self._log(LogLevel.DEBUG,
                          f"[第81批 T3] 节点序列化跳过: {type(_e).__name__}: {_e}")
        for _nid in _deleted_ids:
            _buf.append(json.dumps({"node_id": str(_nid), "action": "delete", "ts": _now},
                                   ensure_ascii=False))
        _path = self._m67_incremental_log_path()
        try:
            _d = os.path.dirname(_path)
            if _d and not os.path.exists(_d):
                os.makedirs(_d, exist_ok=True)
            with open(_path, "a", encoding="utf-8") as f:
                for _l in _buf:
                    f.write(_l + "\n")
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[第81批 T3] 增量日志写入失败，回退原增量/全量: {type(_e).__name__}: {_e}")
            return False

        self._last_saved_checksum = current_checksum
        self._update_saved_nodes_map(saved_nodes)
        self._last_write_time = time.time()
        self._last_save_time = time.time()
        self._last_save_nodes = len(saved_nodes)
        self._total_saves += 1
        _el = time.time() - start_time
        self._log(LogLevel.INFO,
                  f"[第81批 T3] 增量日志保存完成: upsert {len(_changed or [])} 条"
                  f"/ delete {len(_deleted_ids)} 条 耗时 {_el:.2f}s")

        # 日志达阈值 → 触发全量合并并清空（合并失败则保留日志，下轮重试）
        if self._m67_incremental_log_lines() >= self._m67_incremental_log_max():
            self._log(LogLevel.INFO, "[第81批 T3] 增量日志达阈值，触发全量合并并清空日志")
            # ★第81批补2 T3：改走统一全量检查点（刷新 Parquet + 仅物化成功才清空 jsonl）；
            #   Parquet 失败=检查点未达成=保留 jsonl，下轮重试（语义不变）。
            self._m67_full_checkpoint(saved_nodes, current_checksum, time.time())
        return True

    def _m67_clear_incremental_log(self) -> None:
        _p = self._m67_incremental_log_path()
        try:
            if os.path.exists(_p):
                os.remove(_p)
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[第67批] 增量日志清理失败: {type(_e).__name__}: {_e}")

    def _m67_full_checkpoint(self, saved_nodes: list, current_checksum: str,
                             start_time: float) -> bool:
        """★第81批补2 T1：统一"全量检查点"语义 = 全量物化（JSON[+Parquet]）+ 增量日志清空。

        - 先 _full_save（写 JSON 全量/兼容备份、更新内部状态、Parquet 行数校验日志）；
        - Parquet 启用（self._use_parquet，与 save() 全量分支同口径）时调用 save_parquet()
          并取 bool 返回；未启用（纯 JSON）时该步视为成功；
        - 仅当 _full_save 成功 且（Parquet 未启用 或 save_parquet() 返回 True）时，若增量
          日志启用则清空 jsonl 并返回 True；
        - 任一要素失败 → 保留 jsonl（下次启动「旧 Parquet + jsonl 重放」兜底，零丢失），
          捕获 Parquet 异常不崩溃，返回值如实反映全量 JSON 是否成功。
        """
        try:
            _json_ok = bool(self._full_save(saved_nodes, current_checksum, start_time))
        except Exception as _e:
            self._log(LogLevel.ERROR,
                      f"[第81批补2 T1] 全量保存异常（检查点未达成）: "
                      f"{type(_e).__name__}: {_e}")
            return False
        if not _json_ok:
            return False
        _parquet_enabled = bool(getattr(self, "_use_parquet", False))
        _parquet_ok = True
        if _parquet_enabled:
            try:
                _parquet_ok = bool(self.save_parquet())
            except Exception as _e:
                _parquet_ok = False
                self._log(LogLevel.WARNING,
                          f"[第81批补2 T1] Parquet 物化异常（检查点未达成，保留增量日志）: "
                          f"{type(_e).__name__}: {_e}")
        if not _parquet_ok:
            self._log(LogLevel.WARNING,
                      "[第81批补2 T1] 全量检查点未达成：Parquet 物化失败，增量日志保留"
                      "（下次启动将 Parquet+jsonl 重放兜底，零丢失）")
            return _json_ok
        if self._m67_incremental_log_enabled():
            self._m67_clear_incremental_log()
            self._log(LogLevel.INFO,
                      f"[第81批补2 T1] 全量检查点达成: {len(saved_nodes)} 节点, "
                      f"Parquet{('已刷新' if _parquet_enabled else '未启用')}, jsonl 已重置")
        return True

    def _m67_apply_incremental_log(self, nodes: list) -> list:
        """★第81批 T3：重放定序 + 幂等 + 类型统一 + 行 checksum 校验。

        - 先按 node_id 分组收集，再按 ts 折叠（同 id 的 upsert/delete 乱序不影响结果）；
        - 每条 upsert 行校验 checksum，不符则跳过并告警（不影响其余）；
        - upsert 统一经 PulseNode.from_dict 转 PulseNode，返回值类型一致；
        - delete 为最终态（仅当其 ts >= 最终 upsert ts）才移除节点；
        - 单行损坏只跳过该行业告警，绝不影响整体启动；重复重放结果相同（幂等）。
        """
        if not self._m67_incremental_log_enabled():
            return nodes
        _p = self._m67_incremental_log_path()
        if not os.path.exists(_p):
            return nodes
        _by_id = {}
        try:
            for _n in (nodes or []):
                _nid = getattr(_n, "node_id", "")
                if _nid:
                    _by_id[_nid] = _n
        except Exception:
            return nodes
        # ★先收集再按 (node_id, ts) 折叠，保证乱序幂等
        _recs = {}
        _bad = 0
        with open(_p, "r", encoding="utf-8", errors="replace") as f:
            for _line in f:
                if not _line.strip():
                    continue
                try:
                    _rec = json.loads(_line)
                except Exception:
                    _bad += 1
                    continue
                _nid = str(_rec.get("node_id") or "")
                if not _nid:
                    _bad += 1
                    continue
                _act = _rec.get("action")
                _ts = float(_rec.get("ts", 0.0) or 0.0)
                if _act == "delete":
                    _slot = _recs.get(_nid)
                    if _slot is None:
                        _recs[_nid] = {"upsert": None, "delete_ts": _ts}
                    else:
                        _slot["delete_ts"] = _ts
                elif _act == "upsert":
                    _data = _rec.get("data")
                    if not isinstance(_data, dict):
                        _bad += 1
                        continue
                    # ★行 checksum 校验（不符跳过）
                    _cs = _rec.get("checksum")
                    if _cs and _data.get("checksum") != _cs:
                        self._log(LogLevel.WARNING,
                                  f"[第81批 T3] 增量日志行 checksum 不符，跳过 node_id={_nid}")
                        _bad += 1
                        continue
                    _slot = _recs.get(_nid)
                    if _slot is None:
                        _recs[_nid] = {"upsert": (_ts, _data), "delete_ts": None}
                    elif _slot["upsert"] is None or _ts > _slot["upsert"][0]:
                        _slot["upsert"] = (_ts, _data)
                else:
                    _bad += 1
        _applied = 0
        for _nid, _slot in _recs.items():
            _up_ts = _slot["upsert"][0] if _slot["upsert"] else -1.0
            # delete 为最终态：仅当其 ts >= 最终 upsert ts 才移除
            if _slot["delete_ts"] is not None and _slot["delete_ts"] >= _up_ts:
                _by_id.pop(_nid, None)
                _applied += 1
                continue
            if _slot["upsert"] is not None:
                _data = _slot["upsert"][1]
                try:
                    # ★类型统一：统一转 PulseNode，返回值类型一致
                    _by_id[_nid] = PulseNode.from_dict(_data)
                    _applied += 1
                except Exception as _e:
                    self._log(LogLevel.WARNING,
                              f"[第81批 T3] 增量日志节点还原失败，跳过 node_id={_nid}: "
                              f"{type(_e).__name__}: {_e}")
                    _bad += 1
        if _bad:
            self._log(LogLevel.WARNING,
                      f"[第81批 T3] 增量日志有 {_bad} 行损坏/校验不符/跳过（其余正常重放）")
        if _applied:
            self._log(LogLevel.INFO,
                      f"[第81批 T3] 增量日志重放完成: 应用 {_applied} 条")
        return list(_by_id.values())

    # ===== ★主线第68批 T1/P0：Parquet 主存储 =====

    def _m68_parquet_primary_enabled(self) -> bool:
        """★T1：Parquet 主存储开关（默认开）。关闭 → 完全回退 JSON，零行为变化。"""
        try:
            import config as _cfg68
            return bool(getattr(_cfg68, "PARQUET_AS_PRIMARY_STORAGE", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m68_parquet_primary_enabled L1197")
            return True

    def _m160_load_source(self) -> str:
        """★第160批 下下 刀2（T-双源合并-1）：解析快照加载源。

        返回 ∈ {"parquet","json","dual"}：
          - 读 config.SNAPSHOT_LOAD_SOURCE；
          - 非法值（非三选一）→ 回落 "parquet"；
          - 键缺失 → 按 PARQUET_AS_PRIMARY_STORAGE 映射（True→parquet / False→json）。
        """
        try:
            import config as _cfg160
            _raw = getattr(_cfg160, "SNAPSHOT_LOAD_SOURCE", None)
            if _raw not in ("parquet", "json", "dual"):
                _legacy = bool(getattr(_cfg160, "PARQUET_AS_PRIMARY_STORAGE", True))
                _raw = "parquet" if _legacy else "json"
            return _raw
        except Exception as _e:
            silent_exc(_e, where="nucleus.mnemosyne.PulseSnapshot::_m160_load_source")
            return "parquet"

    @staticmethod
    def _m160_quality_severity(flag) -> int:
        """R3 质量标记严重度：placeholder_alias>polluted>suspect>clean。"""
        _SEV = {"placeholder_alias": 3, "polluted": 2, "suspect": 1, "clean": 0}
        return _SEV.get(str(flag).strip().lower() if flag else "clean", 0)

    @staticmethod
    def _m160_node_newer(a, b) -> bool:
        """R2 时间大者胜：比较 updated_at，相等则 created_at。"""
        _ta = float(getattr(a, "updated_at", 0.0) or 0.0)
        _tb = float(getattr(b, "updated_at", 0.0) or 0.0)
        if _ta == _tb:
            _ta2 = float(getattr(a, "created_at", 0.0) or 0.0)
            _tb2 = float(getattr(b, "created_at", 0.0) or 0.0)
            return _ta2 > _tb2
        return _ta > _tb

    def _m160_merge_dual(self, pq_nodes, js_nodes):
        """★第160批 下下 刀2（T-双源合并-1）：Parquet + JSON 双源合并（A案 R1-R6 写死）。

        R1 node_id 并集；R2 时间大者胜；R3 质量标记非 clean 取高；
        R4 trust 0.0 取 0；R5 计数分歧只告警不阻断；
        R6 全池上限不变式（合并集==并集）自检。
        """
        _by = {}
        for _n in (pq_nodes or []):
            _by[_n.node_id] = _n
        _flag_conflict = 0
        for _n in (js_nodes or []):
            _jid = _n.node_id
            if _jid not in _by:
                _by[_jid] = _n
                continue
            _exist = _by[_jid]
            # R2 时间大者胜
            _winner = _n if self._m160_node_newer(_n, _exist) else _exist
            # R3 质量标记例外：非 clean 取高
            _eq = self._m160_quality_severity(getattr(_exist, "quality_flag", "clean"))
            _nq = self._m160_quality_severity(getattr(_n, "quality_flag", "clean"))
            if _eq != _nq:
                _hi = _exist if _eq >= _nq else _n
                _winner.quality_flag = _hi.quality_flag
                if max(_eq, _nq) > 0:
                    _flag_conflict += 1
            # R4 trust 0.0 取 0
            _et = float(getattr(_exist, "trust_score", 0.0) or 0.0)
            _jt = float(getattr(_n, "trust_score", 0.0) or 0.0)
            if _et == 0.0 or _jt == 0.0:
                _winner.trust_score = 0.0
            _by[_jid] = _winner
        _merged = list(_by.values())
        _pc, _jc = len(pq_nodes or []), len(js_nodes or [])
        # R5 计数分歧只告警不阻断
        if _pc != _jc:
            self._log(LogLevel.WARNING,
                      f"[A案dual] 双源计数分歧 parquet={_pc} json={_jc}（仅告警，不阻断）")
        # R6 全池上限不变式：合并集 == 并集（防复活/丢失）
        _union = (set(n.node_id for n in (pq_nodes or [])) |
                  set(n.node_id for n in (js_nodes or [])))
        _merged_ids = set(n.node_id for n in _merged)
        if _merged_ids != _union:
            self._log(LogLevel.ERROR,
                      f"[A案dual] R6 不变式自检失败：合并 {len(_merged_ids)} vs 并集 "
                      f"{len(_union)}，按并集兜底补回缺失节点")
            _src = {n.node_id: n for n in (pq_nodes or [])}
            _src.update({n.node_id: n for n in (js_nodes or [])})
            for _id in (_union - _merged_ids):
                _merged.append(_src[_id])
        # INFO 日志（验收 ① 需要）
        self._log(LogLevel.INFO,
                  f"[A案dual] parquet={_pc} json={_jc} 合并={len(_merged)} "
                  f"flag冲突取非clean={_flag_conflict}")
        return _merged

    def _m68_json_backup_enabled(self) -> bool:
        """★T1：是否仍写 JSON 兼容备份（默认开）。
        ★安全提示：关闭后若 Parquet 写入同时失败将无 JSON 兜底，
          故默认保持双写；仅在确认 Parquet 稳定后再考虑关闭。"""
        try:
            import config as _cfg68
            return bool(getattr(_cfg68, "SNAPSHOT_SAVE_JSON_BACKUP", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m68_json_backup_enabled L1207")
            return True

    def _m68_parquet_dir(self) -> str:
        """Parquet 分片根目录（与快照同级的 parquet/ 目录）。"""
        return os.path.join(os.path.dirname(self.snapshot_path) or ".", "parquet")

    # ===== ★主线第81批 T1/T5：schema 补全 + 读路径统一校验 =====

    def _m81_parquet_schema_complete_enabled(self) -> bool:
        """★T1 灰度：默认开 = 写/读 7 字段并启用 schema 校验；关 = 复现旧 29 列行为。"""
        try:
            import config as _cfg81
            return bool(getattr(_cfg81, "PARQUET_SCHEMA_M81_COMPLETE", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m81_parquet_schema_complete_enabled L1221")
            return True

    def _m81_parquet_expected_schema_version(self) -> str:
        try:
            import config as _cfg81
            return str(getattr(_cfg81, "PARQUET_VERIFY_SCHEMA_VERSION", "m81.v1"))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m81_parquet_expected_schema_version L1228")
            return "m81.v1"

    def _m81_parquet_required_columns(self) -> list[str]:
        """★T1：Parquet 必需**文件**列集合（含 7 新列；开关关时仅 28 旧数据列）。

        注意：evol_level 是 Hive 分区列（write_to_dataset 从数据文件移除，仅编码在
        目录名 evol_level=<L>），不计入文件列；加载时由目录名显式回填（见
        _m81_load_parquet_unified ③）。故此处不含 evol_level。
        """
        _base = [
            "node_id", "value", "keywords", "importance",
            "abstraction", "created_at", "last_activated", "activation_count",
            "space_path", "state", "source_organ", "trigger_reason",
            "frequency_signature", "linked_nodes", "semantic_relations",
            "hebbian_weight", "cooccurrence_count", "version", "updated_at",
            "checksum", "instinct", "instinct_at", "instinct_active_times",
            "instinct_last_use", "ephemeral", "view_mode", "trust_score",
            "verification_history",
        ]
        if self._m81_parquet_schema_complete_enabled():
            _base = _base + [
                "source_url", "evidence_chain", "source_time",
                "acquired_time", "source_timestamp", "quality_flag",
                "quality_reason",
            ]
        return _base

    def _m81_load_parquet_unified(self):
        """★第81批 T5：统一 Parquet 加载入口（路径甲/乙/by_level 共用）。

        显式带回分区列 evol_level → **schema 版本 / 列集合 / 分层三道校验** →
        失败 FAIL-fast 回退 JSON。成功返回节点列表；失败/不可用返回 None
        （调用方回退 JSON 加载）。不依赖 pyarrow 整目录读的自动分区列注入。
        """
        try:
            import pyarrow.parquet as _pq81
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m81_load_parquet_unified L1281")
            return None
        _base = self._m68_parquet_dir()
        if not os.path.isdir(_base):
            return None
        _req = set(self._m81_parquet_required_columns())
        _exp_ver = self._m81_parquet_expected_schema_version()
        _out = []
        _lv_loaded = {"L1": 0, "L2": 0, "L3": 0}   # 磁盘分区行数（按目录）
        _lv_real = {"L1": 0, "L2": 0, "L3": 0}     # 内存实际加载节点数（from_dict 成功）
        _meta = None
        for _lv in ("L1", "L2", "L3"):
            _d = os.path.join(_base, "evol_level={}".format(_lv))
            if not os.path.isdir(_d):
                continue
            try:
                _tbl = _pq81.read_table(_d)
            except Exception as _e:
                self._log(LogLevel.ERROR,
                          f"[第81批 T5] Parquet 分片读取失败({_lv})，回退 JSON: "
                          f"{type(_e).__name__}: {_e}")
                return None
            # ① 必需列集合校验（D151：缺列不可检出 → FAIL）
            _missing = _req - set(_tbl.schema.names)
            if _missing:
                self._log(LogLevel.ERROR,
                          f"[第81批 T5] Parquet 缺列 {sorted(_missing)}({_lv})，回退 JSON")
                return None
            # ② schema 版本校验（旧版本 → FAIL）
            _md = _tbl.schema.metadata or {}
            _ver = (_md.get(b"m81_schema_version") or b"").decode("utf-8", "replace")
            if _ver and _ver != _exp_ver:
                self._log(LogLevel.ERROR,
                          f"[第81批 T5] Parquet schema 版本过旧 磁盘={_ver} 期望={_exp_ver}，回退 JSON")
                return None
            if _meta is None and _md:
                _meta = _md
            for _row in _tbl.to_pylist():
                try:
                    # ③ 显式带回分区列（目录名即权威层级，不依赖 pyarrow 自动注入）
                    _row["evol_level"] = _lv
                    _out.append(PulseNode.from_dict(self._parquet_row_to_dict(_row, level=_lv)))
                    _lv_real[_lv] += 1
                except Exception:
                    continue
            _lv_loaded[_lv] = _tbl.num_rows
        if not _out:
            return None
        # ④ 分层塌缩 / 部分加载 FAIL-fast（两条路径共用）
        # (a) 对**旧 parquet（无元数据）也稳健**：某层分区目录在磁盘有行，但
        #     from_dict 全程失败导致内存该层实际加载为 0 → 判定塌缩/解析失败，回退 JSON。
        #     纯靠磁盘行数 vs 内存实际加载计数，不依赖元数据声明。
        for _lv in ("L1", "L2", "L3"):
            if _lv_loaded[_lv] > 0 and _lv_real.get(_lv, 0) == 0:
                self._log(LogLevel.ERROR,
                          f"[第81批 T5] Parquet 分层塌缩: 磁盘 {_lv}={_lv_loaded[_lv]} "
                          f"但内存加载 0 节点（解析失败/塌缩），回退 JSON")
                return None
        # (b) 新写 parquet 带元数据：元数据声明计数 vs 实际磁盘计数（外部篡改/截断检测）
        if _meta is not None:
            for _k in ("L1", "L2", "L3"):
                try:
                    _exp_n = int((_meta.get(("{}_count".format(_k.lower())).encode("utf-8"))
                                 or b"0").decode("utf-8", "replace") or 0)
                except Exception as e:
                    silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m81_load_parquet_unified L1345")
                    _exp_n = 0
                if _exp_n > 0 and _lv_loaded[_k] == 0:
                    self._log(LogLevel.ERROR,
                              f"[第81批 T5] Parquet 分层塌缩: 元数据 {_k}={_exp_n} 实际=0，回退 JSON")
                    return None
        return _out

    def _m68_load_from_parquet(self):
        """★T1(第81批)：委托给统一加载入口 _m81_load_parquet_unified（路径甲/乙共用）。
        成功返回节点列表；**失败或无数据返回 None**，由调用方回退 JSON 加载。"""
        return self._m81_load_parquet_unified()

    def _m68_verify_parquet(self, root: str | None = None) -> int:
        """★T1(第81批升级)：保存后校验 Parquet 完整性 + schema 双校验。
        返回总行数；缺列/版本过旧/分层塌缩任一失败返回 -1（调用方据此回退 JSON + ERROR）。"""
        try:
            import pyarrow.parquet as _pq68
        except Exception:
            return -1
        _base = root if root is not None else self._m68_parquet_dir()
        if not os.path.isdir(_base):
            return -1
        _req = set(self._m81_parquet_required_columns())
        _exp_ver = self._m81_parquet_expected_schema_version()
        _total = 0
        _lv_counts = {"L1": 0, "L2": 0, "L3": 0}
        try:
            for _lv in ("L1", "L2", "L3"):
                _d = os.path.join(_base, "evol_level={}".format(_lv))
                if not os.path.isdir(_d):
                    continue
                _tbl = _pq68.read_table(_d)
                # ① 必需列集合校验
                _missing = _req - set(_tbl.schema.names)
                if _missing:
                    self._log(LogLevel.ERROR,
                              f"[第81批 T1] Parquet 校验失败: 缺列 {sorted(_missing)}({_lv})")
                    return -1
                # ② schema 版本元数据校验
                _md = _tbl.schema.metadata or {}
                _ver = (_md.get(b"m81_schema_version") or b"").decode("utf-8", "replace")
                if _ver and _ver != _exp_ver:
                    self._log(LogLevel.ERROR,
                              f"[第81批 T1] Parquet schema 版本过旧: 磁盘={_ver} 期望={_exp_ver}，回退 JSON")
                    return -1
                _n = _tbl.num_rows
                _total += _n
                _lv_counts[_lv] = _n
            # ③ 分层分布校验：总量>0 但某层元数据期望>0 实际=0（塌缩）
            for _k in ("L1", "L2", "L3"):
                _mc = (_md.get(("{}_count".format(_k.lower())).encode("utf-8")) if _md else None)
                if _mc is not None:
                    try:
                        _exp_n = int(_mc.decode("utf-8", "replace") or 0)
                    except Exception:
                        _exp_n = 0
                    if _exp_n > 0 and _lv_counts[_k] == 0:
                        self._log(LogLevel.ERROR,
                                  f"[第81批 T1] Parquet 分层校验失败: {_k} 期望={_exp_n} 实际=0（塌缩），回退 JSON")
                        return -1
            return _total
        except Exception as _e:
            self._log(LogLevel.ERROR,
                      f"[第81批 T1] Parquet 完整性校验失败: {type(_e).__name__}: {_e}")
            return -1

    def _m68_parquet_level_counts(self) -> dict:
        """★第80批 T2：统计 Parquet 各层级分区行数（L1/L2/L3）。
        用于加载侧 FAIL-fast 分层保真校验与测试。失败/无数据返回空 dict。"""
        try:
            import pyarrow.parquet as _pq68
        except Exception:
            return {}
        _base = self._m68_parquet_dir()
        if not os.path.isdir(_base):
            return {}
        _counts = {"L1": 0, "L2": 0, "L3": 0}
        try:
            for _lv in ("L1", "L2", "L3"):
                _d = os.path.join(_base, "evol_level={}".format(_lv))
                if os.path.isdir(_d):
                    _counts[_lv] = _pq68.read_table(_d).num_rows
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[第80批 T2] Parquet 分层计数失败: {type(_e).__name__}: {_e}")
        return _counts

    def _update_saved_nodes_map(self, saved_nodes: list):
        """
        ★P0-4新增：更新已保存节点的校验和映射。
        用于增量保存时判断节点是否发生变更。
        """
        self._last_saved_nodes_map = {}
        for _node in saved_nodes:
            self._last_saved_nodes_map[_node.node_id] = getattr(_node, 'checksum', '')
    def save_l1(self) -> bool:
        """
        独立保存L1节点快照。
        将所有非临时L1节点写入独立文件，与L2/L3/L4主快照分离。
        """
        # ★P1修复：并发写竞态加锁（RLock 可重入，save() 内部调用时不会死锁）
        with self._lock:
            return self._save_l1_locked()
    
    def _save_l1_locked(self) -> bool:
        """save_l1() 的实际逻辑（已持有 self._lock）。"""
        # ★第44批 T4（P2-290）：写盘守卫
        if not self._m44_write_allowed():
            return False
        if self.node_pool is None:
            return False
        
        all_nodes = self.node_pool.get_all_including_evicted()
        l1_nodes = [n for n in all_nodes 
                    if n.evol_level == PulseNode.EVOL_L1 
                    and not getattr(n, 'ephemeral', False)]
        
        if not l1_nodes:
            return True
        
        snapshot = {
            "version": "v9.5",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "l1_count": len(l1_nodes),
            "nodes": [node.to_dict() for node in l1_nodes],
        }

        # ★第102批 T-102a：L1 落盘前过滤悬空边
        if _m102_dangling_guard_on():
            try:
                _m102_side = self._m102_sidecar_node_ids()
                if _m102_side is not None:
                    _srm, _lrm, _kn = _m102_filter_dangling_edges(
                        snapshot, _m102_side)
                    if _srm or _lrm:
                        self._log(LogLevel.INFO,
                                  f"[第102批 T-102a] L1落盘过滤悬空边: "
                                  f"sem={_srm} linked={_lrm}")
            except Exception as _e:
                self._log(LogLevel.WARNING,
                          f"[第102批 T-102a] L1悬空边过滤异常(已忽略): "
                          f"{type(_e).__name__}: {_e}")
        
        try:
            l1_dir = os.path.dirname(self.l1_snapshot_path)
            if l1_dir and not os.path.exists(l1_dir):
                os.makedirs(l1_dir, exist_ok=True)
            
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix=".json", prefix="l1_snapshot_", dir=l1_dir or "."
            )
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            
            if os.path.exists(self.l1_snapshot_path):
                os.remove(self.l1_snapshot_path)
            os.replace(tmp_path, self.l1_snapshot_path)
            
            self._log(LogLevel.DEBUG, f"L1快照保存完成: {len(l1_nodes)} 个L1节点")
            return True
        except Exception as e:
            self._log(LogLevel.ERROR, f"L1快照保存失败: {e}")
            return False
    
    def load_l1(self) -> list[PulseNode]:
        """
        独立加载L1节点快照。
        如果L1快照文件不存在或损坏，返回空列表（不阻塞启动）。
        """
        if not os.path.exists(self.l1_snapshot_path):
            return []
        
        try:
            data = safe_read_json(self.l1_snapshot_path, default={})
            
            nodes_data = data.get("nodes", [])
            restored = []
            for node_dict in nodes_data:
                try:
                    node = PulseNode.from_dict(node_dict)
                    restored.append(node)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"L1节点恢复失败，已跳过: {e}")
            
            self._log(LogLevel.INFO, f"L1快照加载完成: {len(restored)} 个L1节点")
            return restored
        except Exception as e:
            self._log(LogLevel.WARNING, f"L1快照加载失败: {e}")
            return []    
    def _atomic_write_with_rotation(self, snapshot: dict[str, Any],
                                     compact: bool = True,
                                     rotate: bool = True) -> None:
        """
        P1-3: 原子写入 + 历史轮转。
        
        1. 将当前快照文件重命名为备份（如果存在，rotate=True 时）
        2. 原子写入新快照
        3. 清理超过保留上限的旧备份（rotate=True 时）
        
        ★v29 性能优化（14.44）：
            - compact=True（默认）：json.dump 去掉 indent=2，改用紧凑模式。
              实测 209MB/7963 节点：indent=2 dump 6.96s → 紧凑 dump 2.16s（快 3.2x），
              文件体积 201MB → 144MB。加载器 json.load 对两种格式完全兼容，零冲突。
            - rotate=False：增量保存不产生 .bak 轮转，避免大文件 rename + 重写两次。
              增量本意是「只写变更」，但 JSON 是整体文件无法局部更新，只能整体重写——
              去掉轮转可省去 209MB 文件的第二次磁盘写。原子性由 tempfile + os.replace 保证。
        """
        snapshot_dir = os.path.dirname(self.snapshot_path)
        if snapshot_dir and not os.path.exists(snapshot_dir):
            os.makedirs(snapshot_dir, exist_ok=True)
        
        # ===== 轮转：将当前文件重命名为备份（仅 rotate=True） =====
        if rotate and os.path.exists(self.snapshot_path):
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.snapshot_path}.{timestamp}.bak"
            try:
                os.rename(self.snapshot_path, backup_path)
            except Exception as e:
                self._log(LogLevel.WARNING,
                          f"快照轮转备份失败(无回退副本): {e}")
        
        # ===== 原子写入新快照 =====
        # ===== ★第102批 T-102a：落盘前引用完整性过滤（悬空边不落盘） =====
        if _m102_dangling_guard_on():
            try:
                _m102_side = self._m102_sidecar_node_ids()
                if _m102_side is not None:
                    _srm, _lrm, _kn = _m102_filter_dangling_edges(
                        snapshot, _m102_side)
                    if _srm or _lrm:
                        self._log(LogLevel.INFO,
                                  f"[第102批 T-102a] 落盘过滤悬空边: "
                                  f"sem={_srm} linked={_lrm} (已知节点={_kn})")
            except Exception as _e:
                self._log(LogLevel.WARNING,
                          f"[第102批 T-102a] 悬空边过滤异常(已忽略): "
                          f"{type(_e).__name__}: {_e}")

        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="snapshot_",
            dir=snapshot_dir or "."
        )
        
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                if compact:
                    json.dump(snapshot, f, ensure_ascii=False)
                else:
                    json.dump(snapshot, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # ★第80批 T4：纯 os.replace 原子覆盖（Windows 可直接覆盖已存在目标，
            #   前置 os.remove 冗余且目标被锁时会抛错；原子替换保证崩溃窗口最小）。
            os.replace(tmp_path, self.snapshot_path)
        except Exception:
            # ★第80批 T4：写失败保留 tmp 副本供恢复（不再无条件删除），
            #   重命名为带时间戳 .failed 副本，便于崩溃后人工/自动恢复。
            try:
                if os.path.exists(tmp_path):
                    _failed = f"{tmp_path}.failed_{time.strftime('%Y%m%d_%H%M%S')}"
                    os.rename(tmp_path, _failed)
                    self._log(LogLevel.ERROR, f"[第80批 T4] 快照写入失败，保留临时副本供恢复: {_failed}")
            except Exception as e:
                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:1591", level="warning")
            raise
        
        # ===== 清理旧备份（仅 rotate=True） =====
        if rotate:
            self._cleanup_old_backups()
    
    def _cleanup_old_backups(self):
        """P1-3: 清理超过保留上限的旧备份文件"""
        snapshot_dir = os.path.dirname(self.snapshot_path) or "."
        base_name = os.path.basename(self.snapshot_path)
        
        backups = []
        try:
            for fname in os.listdir(snapshot_dir):
                if fname.startswith(base_name) and ".bak" in fname:
                    fpath = os.path.join(snapshot_dir, fname)
                    backups.append((os.path.getmtime(fpath), fpath))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_cleanup_old_backups L1628")
            return
        
        backups.sort(reverse=True)  # 按时间倒序
        
        # 删除超过上限的备份
        for _, fpath in backups[self._max_backups:]:
            try:
                os.remove(fpath)
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
    
    # ========== 加载（P1-3 改造：损坏时自动回退） ==========
    
    def load(self, full_load: bool = True):
        """★第67批 T2：加载 = 原加载逻辑 + 增量日志重放。
        ★异常时退化为纯全量快照 —— 增量日志损坏绝不影响框架启动。"""
        # ★主线第68批 T1/P0：Parquet 主存储优先。
        #   命中则**完全不读** 479MB 的 JSON（这是主存储的核心收益）；
        #   未命中/失败 → 回退原 JSON 加载路径，零回归。
        self._m68_last_load_source = "json"
        _src = self._m160_load_source()
        if _src == "json":
            _nodes = self._load_internal(full_load)
        elif _src == "parquet":
            _pq = self._m81_load_parquet_unified()
            if _pq:
                # ★第80批 T2：FAIL-fast 分层保真校验（同原 parquet 路径）。
                _pq_disk_l2l3 = sum(self._m68_parquet_level_counts().get(_k, 0)
                                    for _k in ("L2", "L3"))
                _mem_l2l3 = sum(1 for _n in _pq
                                if str(getattr(_n, "evol_level", "")).upper() in ("L2", "L3"))
                if _pq_disk_l2l3 > 0 and _mem_l2l3 == 0:
                    self._log(LogLevel.ERROR,
                              f"[第80批 T2] Parquet 分层塌缩检测：磁盘 L2/L3={_pq_disk_l2l3} "
                              f"但内存 L2/L3=0，拒绝采用 Parquet，回退 JSON 加载")
                    _pq = None
                if _pq:
                    self._m68_last_load_source = "parquet"
                    self._log(LogLevel.INFO,
                              f"[第68批] 从 Parquet 主存储加载 {len(_pq)} 个节点"
                              f"（JSON 未读取）")
                    _nodes = _pq
                else:
                    _nodes = self._load_internal(full_load)
            else:
                _nodes = self._load_internal(full_load)
        else:  # dual
            _pq = self._m81_load_parquet_unified() or []
            _js = self._load_internal(full_load) or []
            _nodes = self._m160_merge_dual(_pq, _js)
        try:
            if self._m67_incremental_log_enabled():
                _nodes = self._m67_apply_incremental_log(_nodes)
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[第67批] 增量日志应用失败，退回全量快照: {type(_e).__name__}: {_e}")
        # ★第70批 T4.1：冷热加载 —— L1 全量、L2/L3 仅元数据。
        #   失败/关闭 → 原样返回全量节点，绝不丢数据（零回归优先于省内存）。
        try:
            _nodes = self._m70_apply_hot_cold_load(_nodes)
        except Exception as _e70:
            self._log(LogLevel.WARNING,
                      f"[第70批] 冷热加载失败，退回全量加载: {type(_e70).__name__}: {_e70}")
        return _nodes

    def _m70_hot_cold_enabled(self) -> bool:
        """★第70批 T4.1：冷热加载开关（默认开）。"""
        try:
            import config as _m70c
            return bool(getattr(_m70c, "SNAPSHOT_HOT_COLD_LOAD", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m70_hot_cold_enabled L1680")
            return True

    def _m70_apply_hot_cold_load(self, nodes):
        """★第70批 T4.1 + ★162批刀2：对已加载的节点做冷热分级。

        - L1（热）   ：保持不变，完整 value 常驻内存。
        - L2 / L3    ：清掉大字段（value/linked_nodes），只留元数据，
                       并登记到 ``self._m70_lazy_ids``，供后续按需补全。
        - 懒加载路径（nodes 为 LazySnapshotView）：该视图未实现 __iter__，
          直接 ``for _n in nodes`` 会抛 TypeError，被 load() 捕获后原样返回，
          导致 SNAPSHOT_HOT_COLD_LOAD=True 形同未开启（162批刀2 复现）。
          改为按视图协议 iter_nodes() 迭代并就地分级，分级结果随视图 LRU
          缓存持久化；返回原视图以维持 full_load=False 的懒加载契约（测试依赖）。
        ★安全边界：只清**可重建**的大字段；node_id / evol_level /
          space_path / keywords 一律保留，否则下游按路径/层级检索会失效。
        ★降级：任何异常/非可迭代输入都会被 load() 捕获（或本方法内部兜底）
          退回全量加载，并产出可观测标记，不会丢节点。
        """
        self._m70_lazy_ids = set()
        self._m70_lazy_node_map = {}
        if not self._m70_hot_cold_enabled():
            return nodes
        self._m70_hot_load_stats = {"total": 0, "hot": 0, "lazy": 0}
        _iterable, _is_view = self._m70_resolve_node_iterable(nodes)
        if _iterable is None:
            # 无法按视图协议迭代（非预期输入）→ 兜底原样返回，但产出可观测标记
            self._m70_hot_load_stats["degraded"] = True
            self._m70_hot_load_stats["reason"] = "non_iterable_input"
            self._log(LogLevel.WARNING,
                      "[第162批刀2] 冷热加载：输入非可迭代节点序列（{}），"
                      "跳过分级维持原样返回（降级可观测）".format(type(nodes).__name__))
            return nodes
        try:
            _out = []
            for _n in _iterable:
                _lvl = str(getattr(_n, "evol_level", "") or "").upper()
                self._m70_hot_load_stats["total"] += 1
                if _lvl == "L1":
                    self._m70_hot_load_stats["hot"] += 1
                    if not _is_view:
                        _out.append(_n)
                    continue
                # L2/L3 → 转轻量：清大字段，留元数据
                try:
                    # ★第80批 T5：清空前留存原值 + 置标记（双保险）。
                    #   即使开关漏配导致 _m70_apply_hot_cold_load 误触发清空，save 时
                    #   to_dict 会用 _m70_keep 还原写盘，盘上 value/linked_nodes 不被空值覆盖。
                    if not getattr(_n, "_m70_blanked", False):
                        _n._m70_keep = {
                            "value": getattr(_n, "value", ""),
                            "linked_nodes": getattr(_n, "linked_nodes", []),
                        }
                        _n._m70_blanked = True
                    if hasattr(_n, "value"):
                        _n.value = ""
                    if hasattr(_n, "linked_nodes"):
                        _n.linked_nodes = []
                    try:
                        self._m70_lazy_ids.add(getattr(_n, "node_id", ""))
                    except Exception as e:
                        silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:1725", level="warning")
                    self._m70_lazy_node_map[getattr(_n, "node_id", "")] = _n
                    self._m70_hot_load_stats["lazy"] += 1
                except Exception as e:
                    silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m70_apply_hot_cold_load L1748")
                if not _is_view:
                    _out.append(_n)
            _st = self._m70_hot_load_stats
            self._log(LogLevel.INFO,
                      f"[第70批] 冷热加载: 共{_st['total']} 热(L1)={_st['hot']} "
                      f"懒加载(L2/L3)={_st['lazy']}"
                      + ("" if not _is_view else " [懒加载视图协议]"))
            # 懒加载视图：分级已就地作用于视图缓存中的节点，返回原视图维持契约。
            if _is_view:
                return nodes
            return _out
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m70_apply_hot_cold_load L1756")
            return nodes

    def _m70_resolve_node_iterable(self, nodes):
        """★162批刀2：将输入节点统一解析为可迭代序列 + 是否视图标记。

        - LazySnapshotView：提升 max_cache 以容纳全部节点（避免 LRU 逐出导致
          分级结果丢失），返回其 iter_nodes() 生成器，is_view=True。
        - list / tuple：原样返回，is_view=False。
        - 其他可迭代（生成器 / 自定义视图）：包成 list 返回。
        - 不可迭代：返回 (None, False)，由调用方兜底原样返回并打标记。
        """
        try:
            from nucleus.mnemosyne.lazy_snapshot import LazySnapshotView
        except Exception as _imp_err:
            silent_exc(_imp_err,
                      where="nucleus.mnemosyne.PulseSnapshot::_m70_resolve_node_iterable import")
            return None, False
        if isinstance(nodes, LazySnapshotView):
            try:
                nodes.max_cache = max(
                    int(getattr(nodes, "max_cache", 1000)),
                    len(getattr(nodes, "_spans", [])) + 1)
            except Exception as _mc_err:
                silent_exc(_mc_err,
                          where="nucleus.mnemosyne.PulseSnapshot::_m70_resolve_node_iterable max_cache")
            return nodes.iter_nodes(), True
        if isinstance(nodes, (list, tuple)):
            return nodes, False
        try:
            _iter = list(nodes)
        except TypeError as _te:
            silent_exc(_te,
                      where="nucleus.mnemosyne.PulseSnapshot::_m70_resolve_node_iterable non-iterable")
            return None, False
        return _iter, False

    def set_cold_recall_source(self, recall_fn) -> None:
        """★第81批 T2：注册冷存批量召回源（如 PulseNodePool.recall_cold_nodes_batch）。

        懒加载节点 _m70_keep 丢失、需从真冷存取回正文/链接时调用。
        """
        self._m70_cold_recall_fn = recall_fn

    def _materialize_lazy_node(self, node) -> bool:
        """★第81批 T2：按需回填单个懒加载节点的正文（value/linked_nodes）。

        返回 True=已回填或本就完整；False=无法恢复（fail-closed，ERROR 可见，不静默丢）。
        优先级：① 节点自身 _m70_keep（内存，零 IO）→ ② 注册的真冷存召回源 → ③ 失败 ERROR。
        """
        if node is None:
            return False
        if not getattr(node, "_m70_blanked", False):
            return True  # 本就完整，无需回填
        _nid = getattr(node, "node_id", "")
        _keep = getattr(node, "_m70_keep", None)
        if isinstance(_keep, dict) and ("value" in _keep or "linked_nodes" in _keep):
            if "value" in _keep:
                try:
                    node.value = _keep["value"]
                except Exception as e:
                    silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_materialize_lazy_node L1782")
            if "linked_nodes" in _keep:
                try:
                    node.linked_nodes = _keep["linked_nodes"]
                except Exception as e:
                    silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_materialize_lazy_node L1787")
            node._m70_blanked = False
            _lazy_ids = getattr(self, "_m70_lazy_ids", None)
            if _lazy_ids is not None and _nid in _lazy_ids:
                _lazy_ids.discard(_nid)
            return True
        # 内存无 keep → 走真冷存召回
        _fn = getattr(self, "_m70_cold_recall_fn", None)
        if callable(_fn) and _nid:
            try:
                _recalled = _fn([_nid])
                if _recalled:
                    _rn = _recalled[0]
                    try:
                        node.value = getattr(_rn, "value", node.value)
                    except Exception as e:
                        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_materialize_lazy_node L1803")
                    try:
                        node.linked_nodes = getattr(_rn, "linked_nodes", node.linked_nodes)
                    except Exception as e:
                        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_materialize_lazy_node L1807")
                    node._m70_blanked = False
                    _lazy_ids = getattr(self, "_m70_lazy_ids", None)
                    if _lazy_ids is not None and _nid in _lazy_ids:
                        _lazy_ids.discard(_nid)
                    return True
            except Exception as _e:
                self._log(LogLevel.ERROR,
                          f"[第81批 T2] 懒加载节点 {_nid} 冷存召回失败: {type(_e).__name__}: {_e}")
        self._log(LogLevel.ERROR,
                  f"[第81批 T2] 懒加载节点 {_nid} 无法回填（无 _m70_keep 且冷存无副本），保留空白")
        return False

    def _materialize_lazy_batch(self, node_ids=None) -> int:
        """★第81批 T2：批量回填。node_ids=None 时回填全部已登记懒加载节点。返回成功数。"""
        _ids = node_ids if node_ids is not None else list(getattr(self, "_m70_lazy_ids", set()))
        _node_map = getattr(self, "_m70_lazy_node_map", {})
        _ok = 0
        for _nid in _ids:
            _node = _node_map.get(_nid)
            if _node is None:
                continue
            if self._materialize_lazy_node(_node):
                _ok += 1
        return _ok

    def materialize_lazy_nodes(self, node_ids=None) -> int:
        """★第81批 T2：公开回填入口。

        KAL 检索 / 胃消化 / 对话上下文组装等「需要正文」的热路径在访问懒加载节点前调用，
        保证拿到完整 value/linked_nodes；只用到元数据（node_id/evol_level/keywords）的路径可不调用，
        保留省内存收益。
        """
        return self._materialize_lazy_batch(node_ids)

    def _load_internal(self, full_load: bool = True):
        """
        P1-3: 加载快照，损坏时自动回退到最近备份。

        ★主线第9批 T4 / P2-48：新增 full_load 参数。
        - full_load=True（默认）：保持原有全量加载逻辑，向后兼容，行为零变化。
        - full_load=False：返回 LazySnapshotView（流式读取 + LRU 缓存，默认 1000 节点），
          避免 318MB 级快照一次性 json.load 占满内存；惰性加载失败时自动回退全量。
        """
        if not full_load:
            try:
                return self._load_lazy()
            except Exception as e:
                self._log(LogLevel.WARNING, f"惰性加载失败，回退全量加载: {e}")
                # 回退到下方原有全量加载逻辑（不破坏既有行为）
        # ★主线第13批 P2-88：快照路径白名单校验（在读盘前拦截临时目录残留）。
        #   根因：tests/test_lazy_snapshot_m9.py 用 mkstemp(prefix="snap_t4_") 生成
        #   临时快照，被误当作生产快照加载，触发校验和不匹配 WARNING 污染日志。
        #   开关关闭 (ENABLE_SNAPSHOT_PATH_WHITELIST=False) 时完全跳过，零回归。
        if _snapshot_path_whitelist_enabled() and _is_temp_snapshot_path(self.snapshot_path):
            self._log(LogLevel.WARNING,
                      f"[P2-88] 拒绝加载临时目录快照（路径白名单拦截）: {self.snapshot_path}")
            self._log(LogLevel.DEBUG,
                      f"[P2-88] 触发调用栈:\n{''.join(traceback.format_stack()[-8:])}")
            return []

        if not os.path.exists(self.snapshot_path):
            self._log(LogLevel.WARNING, f"快照文件不存在: {self.snapshot_path}")
            return self._load_from_backup()

        self._log(LogLevel.INFO, f"开始加载快照: {self.snapshot_path}")
        # ★主线第13批 P2-88：调用栈日志（DEBUG）——定位「谁在加载快照」。
        #   仅 DEBUG 级别输出，常态不刷屏；排障时临时调高日志级别即可见完整调用链。
        if _snapshot_path_whitelist_enabled():
            self._log(LogLevel.DEBUG,
                      f"[P2-88] 快照加载调用栈:\n{''.join(traceback.format_stack()[-8:])}")
        start_time = time.time()
        
        # ★v29/14.47：Parquet 优先加载——列式存储比 JSON 快 3x（实测 0.89s vs 2.84s）。
        #   仅加载节点，元数据(inference_cache/extra_state/checksum)留空，
        #   首次全量保存会重建。失败或节点数过少时回退 JSON（零冲突）。
        if self._use_parquet and os.path.isdir(self.parquet_dir):
            try:
                # ★第81批 T5：走统一加载入口（显式带回分区列 + schema/列集合/分层三道校验
                #   + FAIL-fast 回退 JSON），不再用无校验的 load_parquet 整目录读。
                _pq_nodes = self._m81_load_parquet_unified()
                if _pq_nodes and len(_pq_nodes) > 100:
                    # 重新计算 checksum（元数据留空，首次 save 会全量写入）
                    try:
                        self._last_saved_checksum = self._compute_node_list_checksum(_pq_nodes)
                    except Exception:
                        self._last_saved_checksum = ""
                    self._last_save_nodes = len(_pq_nodes)
                    elapsed = time.time() - start_time
                    self._log(LogLevel.INFO,
                             f"Parquet 优先加载完成: {len(_pq_nodes)}节点, 耗时 {elapsed:.2f}s")
                    return _pq_nodes
                else:
                    self._log(LogLevel.WARNING,
                             f"Parquet 节点数过少({len(_pq_nodes) if _pq_nodes else 0})，回退 JSON")
            except Exception as _e:
                self._log(LogLevel.WARNING, f"Parquet 优先加载失败，回退 JSON: {_e}")
        
        # 尝试加载主快照（JSON 回退路径）
        data = None
        try:
            data = safe_read_json(self.snapshot_path, default={})
        except (json.JSONDecodeError, Exception) as e:
            self._log(LogLevel.ERROR, f"主快照加载失败: {e}，尝试回退到备份...")
            return self._load_from_backup()
        
        restored = self._restore_from_data(data, start_time)
        
        # ★v24.0新增：如果主快照恢复的节点数远小于保存时记录的数量，尝试从备份中加载更大的快照
        expected_count = data.get("node_count_at_save", 0)
        if expected_count > 0 and len(restored) < expected_count * 0.5:
            self._log(LogLevel.WARNING,
                     f"主快照恢复节点数({len(restored)})远小于保存时记录数({expected_count})，"
                     f"尝试从备份中寻找更大的快照...")
            backup_nodes = self._load_from_backup()
            if len(backup_nodes) > len(restored):
                self._log(LogLevel.INFO,
                         f"从备份恢复更多节点: {len(backup_nodes)} vs 主快照 {len(restored)}")
                return backup_nodes
        
        return restored
    
    def _load_lazy(self):
        """★主线第9批 T4 / P2-48：惰性加载入口。

        返回 LazySnapshotView（流式读取 + LRU 缓存），不调用方感知全量 JSON 解析。
        元数据（inference_cache / extra_state / node_list_checksum）仍从快照头部恢复，
        保证后续 save() 不丢失。任何异常上抛，由 load(full_load=False) 捕获并回退全量。
        """
        from nucleus.mnemosyne.lazy_snapshot import LazySnapshotView
        view = LazySnapshotView(self.snapshot_path, max_cache=1000)
        md = view.metadata
        self.inference_cache = md.get("inference_cache", {})
        self._extra_state = md.get("extra_state", {})
        self._last_saved_checksum = md.get("node_list_checksum", "")
        return view

    def load_from_backup(self) -> list[PulseNode]:
        """公开封装 _load_from_backup，供框架主程序调用（规则14）"""
        return self._load_from_backup()

    def _load_from_backup(self) -> list[PulseNode]:
        """
        P1-3: 从所有备份文件中选择节点数量最多的一个进行恢复。
        避免因为主快照被覆盖而丢失大部分节点。
        """
        snapshot_dir = os.path.dirname(self.snapshot_path) or "."
        base_name = os.path.basename(self.snapshot_path)
        
        backups = []
        try:
            for fname in os.listdir(snapshot_dir):
                if fname.startswith(base_name) and ".bak" in fname:
                    fpath = os.path.join(snapshot_dir, fname)
                    backups.append((os.path.getmtime(fpath), fpath))
        except Exception as e:
            import traceback
            self._log(LogLevel.ERROR,
                      f"读取备份目录失败，放弃从备份恢复: {e}\n{traceback.format_exc()}")
            return []
        
        if not backups:
            self._log(LogLevel.WARNING, "没有可用的备份文件")
            return []
        
        # 按时间从新到旧尝试加载，选择节点数最多的
        backups.sort(reverse=True, key=lambda x: x[0])
        best_nodes = []
        best_backup_path = None
        best_score = -1.0  # ★第80批 T4：综合选优评分基准
        
        for _mtime, backup_path in backups:
            try:
                data = safe_read_json(backup_path, default={})
                nodes = self._restore_from_data(data, time.time())
                _n = len(nodes)
                # ★第80批 T4：综合评分选优（不再固定取第一份 / >=4000 即停）。
                #   评分 = 节点数 + 分层完整度(L2/L3>0 加分) + checksum 有效加分 + 时间衰减。
                _l2l3 = sum(1 for _x in nodes
                            if str(getattr(_x, "evol_level", "")).upper() in ("L2", "L3"))
                _has_checksum = bool(data.get("node_list_checksum", ""))
                _age_min = max(0.0, (time.time() - _mtime) / 60.0)
                _score = (float(_n)
                          + (10.0 if _l2l3 > 0 else 0.0)
                          + (5.0 if _has_checksum else 0.0)
                          - min(_age_min * 0.001, 1.0))
                self._log(LogLevel.INFO,
                         f"备份 {os.path.basename(backup_path)} 恢复 {_n} 节点(L2/L3={_l2l3}) "
                         f"score={_score:.1f}")
                if _score > best_score:
                    best_nodes = nodes
                    best_backup_path = backup_path
                    best_score = _score
            except Exception as e:
                self._log(LogLevel.WARNING, f"备份加载失败 {backup_path}: {e}")
                continue
        
        if best_backup_path:
            self._log(LogLevel.INFO,
                     f"选择最大备份恢复: {os.path.basename(best_backup_path)} ({len(best_nodes)}节点)")
            return best_nodes
        
        self._log(LogLevel.WARNING, "所有备份均无法恢复有效节点")
        return []
    
    def _restore_from_data(self, data: dict[str, Any], start_time: float) -> list[PulseNode]:
        """从已解析的快照数据中恢复节点"""
        version = data.get("version", "unknown")
        self._log(LogLevel.INFO, f"快照版本: {version}")
        
        self.inference_cache = data.get("inference_cache", {})
        self._extra_state = data.get("extra_state", {})        
        nodes_data = data.get("nodes", [])
        restored_nodes = []
        
        for node_dict in nodes_data:
            try:
                node = PulseNode.from_dict(node_dict)
                restored_nodes.append(node)
            except Exception as e:
                self._log(LogLevel.ERROR, f"节点恢复失败: {e}")
        
        # ===== P1-3: 恢复上次保存时的节点状态快照和校验和 =====
        self._last_saved_checksum = data.get("node_list_checksum", "")
        
        elapsed = time.time() - start_time
        total_all = data.get("total_nodes_all", len(restored_nodes))
        save_mode = data.get("save_mode", "full")
        
        # ===== v24.0修改：完整性校验改为软校验 =====
        integrity_ok = self._verify_integrity(data, restored_nodes)
        if not integrity_ok:
            self._m80_last_load_checksum_failed = True  # ★第80批 T4：暴露校验FAIL信号供 main 回退决策
            self._log(LogLevel.WARNING,
                      f"快照完整性校验未通过（节点数据仍可加载）。"
                      f"预期{data.get('node_count_at_save', '?')}个节点，"
                      f"实际恢复{len(restored_nodes)}个节点。"
                      f"将使用已加载节点继续启动，并稍后自动重新保存快照以更新校验和。")
            # 不再返回空列表，使用已恢复的节点
        
        self._log(LogLevel.INFO,
                  f"快照加载完成: {len(restored_nodes)} 个节点 "
                  f"(原始总数: {total_all}, 保存模式: {save_mode}) "
                  f"耗时 {elapsed:.2f}s")
        
        # ★阶段B'：恢复被驱逐的冷节点 node_id 清单（冷节点全文在冷存 Parquet，不随快照）
        if (self.node_pool is not None
                and getattr(self.node_pool, 'is_cold_storage_enabled', lambda: False)()):
            _evicted = data.get("evicted_node_ids", [])
            if _evicted:
                _cold_evicted = getattr(self.node_pool, '_cold_evicted', set())
                _cold_evicted.update(_evicted)
                self._log(LogLevel.INFO,
                          f"冷存储：恢复 {len(_evicted)} 个被驱逐节点清单（冷节点全文按需从冷存召回）")
        
        return restored_nodes
    
    # ========== 推理缓存 ==========
    
    def cache_inference(self, question: str, answer: str, reasoning: str = ""):
        cache_key = question.strip()[:100]
        self.inference_cache[cache_key] = {
            "answer": answer,
            "reasoning": reasoning,
            "cached_at": time.time(),
        }
        
        if len(self.inference_cache) > 1000:
            sorted_keys = sorted(
                self.inference_cache.keys(),
                key=lambda k: self.inference_cache[k].get("cached_at", 0)
            )
            for key in sorted_keys[:200]:
                del self.inference_cache[key]
    
    def get_cached_inference(self, question: str) -> dict[str, Any] | None:
        cache_key = question.strip()[:100]
        return self.inference_cache.get(cache_key)
    def _verify_integrity(self, data: dict[str, Any], restored_nodes: list[PulseNode]) -> bool:
        """
        v24.0修改：软校验——即使校验和不匹配，只要有节点就允许启动。
        旧快照可能因校验和方法升级而暂时不匹配，不应导致知识全部丢失。
        """
        expected_count = data.get("node_count_at_save")
        expected_checksum = data.get("node_list_checksum")
        
        if expected_count is None and expected_checksum is None:
            self._log(LogLevel.INFO, "快照未包含校验信息（旧版本格式），跳过完整性校验")
            return True
        
        # 校验1：节点数量对比（允许小偏差，但不允许为空）
        if expected_count is not None:
            actual_count = len(restored_nodes)
            if actual_count == 0:
                # ★主线第16批 T4/P2-103：区分「快照本身为空」与「加载失败」
                if not self._snapshot_diagnostic_enabled():
                    self._log(LogLevel.ERROR, "恢复的节点数为0，快照可能为空")
                    return False
                try:
                    _declared = int(expected_count)
                except Exception:
                    _declared = -1
                if _declared == 0:
                    # 快照自己声明 0 个节点（首次启动 / 空知识库）→ 正常，不应报 ERROR
                    self._log(LogLevel.INFO,
                              "快照为空（快照声明 0 个节点，首次启动属正常）")
                    return True
                self._log(LogLevel.ERROR,
                          f"恢复的节点数为0，但快照声明有 {expected_count} 个节点 —— "
                          f"判定为**加载失败**。诊断: {self._snapshot_diagnostic(data)}")
                return False
            if actual_count != expected_count:
                self._log(LogLevel.WARNING,
                          f"节点数量不匹配: 预期{expected_count}个, 实际恢复{actual_count}个。"
                          f"允许继续启动。")
        
        # 校验2：节点列表校验和对比（仅警告/调试，不阻断启动）
        # ★P2-86：区分「占位符」（测试夹具遗留）与「真实不匹配」（方法升级）。
        #   占位符直接跳过并 DEBUG 说明；真实不匹配按 _checksum_debug_mode() 定级。
        if expected_checksum is not None:
            if _is_placeholder_checksum(expected_checksum):
                # 已知占位符（如 deadbeef）：跳过校验，不打 WARNING（P2-86 降噪）
                self._log(LogLevel.DEBUG,
                          f"[P2-86] 跳过校验和比对（占位符 {expected_checksum!r}，"
                          f"疑似测试夹具残留）")
            else:
                actual_checksum = self._compute_node_list_checksum(restored_nodes)
                if actual_checksum != expected_checksum:
                    _lvl = (LogLevel.DEBUG if _checksum_debug_mode()
                            else LogLevel.WARNING)
                    self._log(_lvl,
                              f"节点校验和不匹配（可能因校验和方法升级，"
                              f"算法版本 v{CHECKSUM_ALGO_VERSION}）。"
                              f"预期{expected_checksum}, 实际{actual_checksum}。"
                              f"允许继续启动。")
        
        self._log(LogLevel.INFO,
                  f"快照完整性校验完成 ({len(restored_nodes)}个节点，软校验)")
        return True   
    def set_extra_state(self, extra_state: dict[str, Any]):
        """设置需要在快照中保存的额外生命状态"""
        self._extra_state = extra_state     
    @staticmethod
    def _snapshot_diagnostic_enabled() -> bool:
        """快照诊断日志开关（读不到时默认开启）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_SNAPSHOT_DIAGNOSTIC", True))
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_snapshot_diagnostic_enabled L2139")
            return True

    def _snapshot_diagnostic(self, data: dict[str, Any] | None = None) -> str:
        """输出快照文件的可诊断信息串（文件大小 / 版本 / 节点列表类型与长度 / 异常）。

        ★T4/P2-103：此前「0 节点」只有一句 ERROR，无法判断到底是空快照、
        文件损坏、版本不兼容还是反序列化失败。
        """
        _parts: list[str] = []
        try:
            _p = self.snapshot_path
            if os.path.exists(_p):
                _parts.append(f"文件={os.path.basename(str(_p))}, "
                              f"大小={os.path.getsize(_p)}B")
            else:
                _parts.append(f"文件不存在={_p}")
        except Exception as _e:
            _parts.append(f"文件信息读取失败={type(_e).__name__}")
        try:
            if isinstance(data, dict):
                _nodes = data.get("nodes")
                _len = (len(_nodes) if isinstance(_nodes, (list, dict, tuple)) else "N/A")
                _parts.append(
                    f"版本={data.get('version')}, "
                    f"node_count_at_save={data.get('node_count_at_save')}, "
                    f"nodes类型={type(_nodes).__name__}, nodes长度={_len}")
            elif data is None:
                _parts.append("data=None（反序列化未产出内容）")
            else:
                _parts.append(f"data 顶层类型异常={type(data).__name__}")
        except Exception as _e:
            _parts.append(f"反序列化信息异常={type(_e).__name__}: {_e}")
        return "; ".join(_parts)

    @staticmethod
    def _compute_node_list_checksum(nodes: list[PulseNode]) -> str:
        """
        计算节点列表的校验和（SHA256）。
        包含 node_id + 内容校验和 + 激活次数，
        确保节点内容或激活状态变化时能触发保存。
        """
        import hashlib
        parts = []
        for node in nodes:
            node_checksum = getattr(node, 'checksum', '')
            activation_count = getattr(node, 'activation_count', 0)
            parts.append(f"{node.node_id}:{node_checksum}:{activation_count}")
        parts.sort()
        concatenated = "|".join(parts)
        return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()[:16]
    def get_extra_state(self) -> dict[str, Any]:
        """获取从快照中恢复的额外生命状态"""
        return dict(self._extra_state)
    
    def merge_organ_extra_state(self, organ_name: str, state: dict[str, Any]):
        """
        合并器官提交的额外状态到快照的extra_state中。
        在框架stop时，各器官可通过此方法将需要持久化的状态写入快照。
        
        Args:
            organ_name: 器官名称
            state: 需要持久化的状态字典
        """
        if "organ_states" not in self._extra_state:
            self._extra_state["organ_states"] = {}
        self._extra_state["organ_states"][organ_name] = state   
    def auto_cleanup(self) -> dict[str, Any]:
        """
        自动精简快照：清理过期L1节点、临时节点、冗余备份。
        
        定期调用以控制快照文件大小，防止无限增长。
        
        Returns:
            清理统计信息
        """
        # ★第44批 T4（P2-290）：写盘守卫（清理过程会重写快照/备份）
        if not self._m44_write_allowed():
            return {}
        import json
        
        result = {
            "l1_removed": 0,
            "ephemeral_removed": 0,
            "backups_removed": 0,
            "file_size_before": 0,
            "file_size_after": 0,
        }
        
        now = time.time()
        
        # 记录清理前文件大小
        if os.path.exists(self.snapshot_path):
            result["file_size_before"] = os.path.getsize(self.snapshot_path)
        
        # ===== 1. 清理过期的L1快照节点 =====
        if os.path.exists(self.l1_snapshot_path):
            try:
                l1_data = safe_read_json(self.l1_snapshot_path, default={})
                
                l1_nodes = l1_data.get("nodes", [])
                original_count = len(l1_nodes)
                
                # 删除创建超过24小时且信任分数<30的L1节点
                cleaned_nodes = []
                for node_dict in l1_nodes:
                    created_at = node_dict.get("created_at", 0)
                    trust_score = node_dict.get("trust_score", 50.0)
                    age_hours = (now - created_at) / 3600 if created_at > 0 else 0
                    
                    # 保留条件：创建不到24小时 或 信任分数≥30
                    if age_hours < 24 or trust_score >= 30.0:
                        cleaned_nodes.append(node_dict)
                
                result["l1_removed"] = original_count - len(cleaned_nodes)
                
                if result["l1_removed"] > 0:
                    l1_data["nodes"] = cleaned_nodes
                    l1_data["l1_count"] = len(cleaned_nodes)
                    l1_data["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    
                    l1_dir = os.path.dirname(self.l1_snapshot_path)
                    if l1_dir and not os.path.exists(l1_dir):
                        os.makedirs(l1_dir, exist_ok=True)
                    
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        suffix=".json", prefix="l1_snapshot_", dir=l1_dir or "."
                    )
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump(l1_data, f, ensure_ascii=False, indent=2)
                    
                    if os.path.exists(self.l1_snapshot_path):
                        os.remove(self.l1_snapshot_path)
                    os.replace(tmp_path, self.l1_snapshot_path)
                    
                    self._log(LogLevel.INFO,
                             f"L1快照精简: 移除{result['l1_removed']}个过期低信任节点 "
                             f"(剩余{len(cleaned_nodes)}个)")
            except Exception as e:
                self._log(LogLevel.WARNING, f"L1快照精简异常: {e}")
        
        # ===== 2. 清理主快照中的临时节点 =====
        if os.path.exists(self.snapshot_path):
            try:
                main_data = safe_read_json(self.snapshot_path, default={})
                
                main_nodes = main_data.get("nodes", [])
                original_main_count = len(main_nodes)
                
                # 删除ephemeral=True的节点
                permanent_nodes = [
                    n for n in main_nodes 
                    if not n.get("ephemeral", False)
                ]
                
                result["ephemeral_removed"] = original_main_count - len(permanent_nodes)
                
                if result["ephemeral_removed"] > 0:
                    main_data["nodes"] = permanent_nodes
                    main_data["total_nodes_all"] = len(permanent_nodes)
                    main_data["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    
                    snapshot_dir = os.path.dirname(self.snapshot_path)
                    if snapshot_dir and not os.path.exists(snapshot_dir):
                        os.makedirs(snapshot_dir, exist_ok=True)
                    
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        suffix=".json", prefix="snapshot_", dir=snapshot_dir or "."
                    )
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump(main_data, f, ensure_ascii=False, indent=2)
                    
                    if os.path.exists(self.snapshot_path):
                        os.remove(self.snapshot_path)
                    os.replace(tmp_path, self.snapshot_path)
                    
                    self._log(LogLevel.INFO,
                             f"主快照精简: 移除{result['ephemeral_removed']}个临时节点 "
                             f"(剩余{len(permanent_nodes)}个)")
            except Exception as e:
                self._log(LogLevel.WARNING, f"主快照精简异常: {e}")
        
        # ===== 3. 清理冗余备份 =====
        result["backups_removed"] = self._cleanup_old_backups_count()
        
        # ===== 4. 记录清理后文件大小 =====
        if os.path.exists(self.snapshot_path):
            result["file_size_after"] = os.path.getsize(self.snapshot_path)
        
        if result["l1_removed"] > 0 or result["ephemeral_removed"] > 0 or result["backups_removed"] > 0:
            self._log(LogLevel.INFO,
                     f"快照自动精简完成: L1移除{result['l1_removed']}个, "
                     f"临时节点移除{result['ephemeral_removed']}个, "
                     f"备份移除{result['backups_removed']}份, "
                     f"文件大小{result['file_size_before']}→{result['file_size_after']}字节")
        
        return result
    
    def _cleanup_old_backups_count(self) -> int:
        """
        清理超过上限的旧备份文件，返回移除的备份数量。
        """
        snapshot_dir = os.path.dirname(self.snapshot_path) or "."
        base_name = os.path.basename(self.snapshot_path)
        
        removed = 0
        backups = []
        try:
            for fname in os.listdir(snapshot_dir):
                if fname.startswith(base_name) and ".bak" in fname:
                    fpath = os.path.join(snapshot_dir, fname)
                    backups.append((os.path.getmtime(fpath), fpath))
        except Exception:
            return removed
        
        backups.sort(reverse=True)
        
        for _, fpath in backups[self._max_backups:]:
            try:
                os.remove(fpath)
                removed += 1
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
        
        return removed
    def auto_save(self):
        """★v24.0新增：自动保存入口，供snapshot.auto_save事件调用"""
        self._log(LogLevel.INFO, "收到自动保存事件")
        return self.save()    
    # ========== 统计 ==========
    
    def get_stats(self) -> dict[str, Any]:
        file_size = 0
        if os.path.exists(self.snapshot_path):
            file_size = os.path.getsize(self.snapshot_path)
        
        # 统计备份文件数
        snapshot_dir = os.path.dirname(self.snapshot_path) or "."
        base_name = os.path.basename(self.snapshot_path)
        backup_count = 0
        try:
            for fname in os.listdir(snapshot_dir):
                if fname.startswith(base_name) and ".bak" in fname:
                    backup_count += 1
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            
        return {
            "snapshot_path": self.snapshot_path,
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / 1024 / 1024, 2),
            "last_save_nodes": self._last_save_nodes,
            "total_saves": self._total_saves,
            "cached_inferences": len(self.inference_cache),
            "tracked_nodes": len(self._last_saved_nodes),
            "backup_count": backup_count,
            "max_backups": self._max_backups,
            "use_parquet": self._use_parquet,
        }

    # ========== ★阶段A：Parquet 列式元数据快照（暂缓项6） ==========

    @staticmethod
    def _normalize_struct_list(raw: Any, col: str, node_id: str = "") -> list:
        """★3.1（2026-09-08 第八批）：list<struct> 列的类型归一化。

        pyarrow 的 list<struct> 列要求每行都是 dict 列表；节点数据里可能出现：
        - None            → []
        - 单个 dict       → [dict]（历史数据形态）
        - list 含非 dict  → 该元素丢弃（记 DEBUG，避免 struct 推断冲突）
        归一化后可消除 "cannot mix struct and non-struct" 错误的源头。
        """
        _lg = get_module_logger("PulseSnapshot")
        if raw is None:
            return []
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            _out = []
            _discarded = 0
            for _i, _e in enumerate(raw):
                if isinstance(_e, dict):
                    _out.append(_e)
                elif _e is not None:
                    # ★D151/D162：丢弃从 DEBUG 升 WARNING，并带字段存活率门禁
                    _discarded += 1
            if _discarded > 0:
                _kept = len(_out)
                _surv = (_kept / (_kept + _discarded)) if (_kept + _discarded) else 0.0
                _warn = (f"[Parquet][D162] 类型归一化丢弃: 节点{node_id[:18]} 列{col} "
                         f"丢弃 {_discarded} 个非dict元素，存活率 {_surv*100:.1f}%")
                if _surv < 0.5 and _kept > 0:
                    _warn += "（存活率过低，疑似字段级数据损失）"
                _lg.warning(_warn)
            return _out
        # ★D151/D162：异常类型同样升 WARNING（原为 DEBUG）
        _lg.warning(
            f"[Parquet][D162] 类型归一化异常类型: 节点{node_id[:18]} 列{col} "
            f"类型{type(raw).__name__}→[]")
        return []

    def _nodes_to_parquet_columns(self, nodes: list[PulseNode]) -> list[dict[str, Any]]:
        """把节点列表转成 Parquet 行。

        ★14.50优化：semantic_relations/verification_history 改用 Parquet 原生 list 列
        （list of struct），不再 json.dumps 为字符串。加载时直接获得 list，
        每行减少 2 次 json.loads。★14.52：value 也改原生 str 列（实测100%为str），至此零 json.loads。
        旧文件（json 字符串列）由 _parquet_row_to_dict 自动兼容。
        """
        _rows = []
        for _n in nodes:
            _d = _n.to_dict()
            _row = {
                "node_id": str(_d.get("node_id", "")),
                "value": str(_d.get("value", "")),  # ★14.52 原生str列（实测100%为str）
                "keywords": list(_d.get("keywords", []) or []),
                "evol_level": str(_d.get("evol_level", "L1")),
                "importance": str(_d.get("importance", "C")),
                "abstraction": float(_d.get("abstraction", 0.0) or 0.0),
                "created_at": float(_d.get("created_at", 0.0) or 0.0),
                "last_activated": float(_d.get("last_activated", 0.0) or 0.0),
                "activation_count": int(_d.get("activation_count", 0) or 0),
                "space_path": str(_d.get("space_path", "/")),
                "state": str(_d.get("state", "active")),
                "source_organ": str(_d.get("source_organ", "unknown")),
                "trigger_reason": str(_d.get("trigger_reason", "")),
                "frequency_signature": float(_d.get("frequency_signature", 0.0) or 0.0),
                "linked_nodes": list(_d.get("linked_nodes", []) or []),
                # ★3.1：list<struct> 列源头归一化（dict→[dict]/None→[]/剔除非dict元素）
                "semantic_relations": self._normalize_struct_list(
                    _d.get("semantic_relations"), "semantic_relations",
                    str(_d.get("node_id", ""))),
                "hebbian_weight": float(_d.get("hebbian_weight", 0.0) or 0.0),
                "cooccurrence_count": int(_d.get("cooccurrence_count", 0) or 0),
                "version": int(_d.get("version", 1) or 1),
                "updated_at": float(_d.get("updated_at", 0.0) or 0.0),
                "checksum": str(_d.get("checksum", "")),
                "instinct": bool(_d.get("instinct", False)),
                "instinct_at": float(_d.get("instinct_at", 0.0) or 0.0),
                "instinct_active_times": int(_d.get("instinct_active_times", 0) or 0),
                "instinct_last_use": float(_d.get("instinct_last_use", 0.0) or 0.0),
                "ephemeral": bool(_d.get("ephemeral", False)),
                "view_mode": str(_d.get("view_mode", "OUTER_VIEW")),
                "trust_score": float(_d.get("trust_score", 50.0) or 0.0),
                "verification_history": self._normalize_struct_list(
                    _d.get("verification_history"), "verification_history",
                    str(_d.get("node_id", ""))),
            }
            # ★主线第81批 T1：补 7 字段（D151/D158/D159）。灰度关时复现旧 29 列。
            if self._m81_parquet_schema_complete_enabled():
                _row["source_url"] = str(_d.get("source_url", ""))
                _row["evidence_chain"] = self._normalize_struct_list(
                    _d.get("evidence_chain"), "evidence_chain",
                    str(_d.get("node_id", "")))
                _row["source_time"] = float(_d.get("source_time", 0.0) or 0.0)
                _row["acquired_time"] = float(_d.get("acquired_time", 0.0) or 0.0)
                _row["source_timestamp"] = float(_d.get("source_timestamp", 0.0) or 0.0)
                _row["quality_flag"] = str(_d.get("quality_flag", "clean"))
                _row["quality_reason"] = str(_d.get("quality_reason", ""))
            # ★第122批 T-122b：C4断5 补 conflict_count/last_conflict_at 两键（Parquet 主存储搬运，与 JSON 侧 T-120e 对齐）
            _row["conflict_count"] = int(_d.get("conflict_count", 0) or 0)
            _row["last_conflict_at"] = float(_d.get("last_conflict_at", 0.0) or 0.0)
            _rows.append(_row)
        return _rows

    # ===== ★第160批 下下 刀4（T-重建Parquet主存储-1）：原子重刷 =====
    def m160_rebuild_parquet_from_nodes(self, dry_run: bool = True, nodes=None,
                                        _ts: str | None = None) -> dict:
        """★第160批 下下 刀4（T-重建Parquet主存储-1）：从节点原子重刷 Parquet 主存储。

        流程（实跑）：分片写 parquet.m160tmp-<ts>/ → _m68_verify_parquet(root=tmp) 校验
        → 旧分区 rename 至 parquet.m160prev-<ts>/ → 逐分区 os.replace（NTFS 原子）
        → 异常回滚 prev。默认 dry_run=True（只统计 + 自检，不写盘）。
        写序由调用方保证：Parquet 先、JSON 后（防崩溃后读旧 Parquet 丢标记）。
        可接受外部 nodes（独立工具路径）；缺省取 self.node_pool.get_all_including_evicted()。
        """
        if self.node_pool is None and nodes is None:
            self._log(LogLevel.WARNING, "[刀4] node_pool 未注入且无外部 nodes，跳过重建")
            return {"status": "skipped", "reason": "no_nodes"}
        try:
            import pyarrow.parquet as _pq4
        except Exception as _e:
            self._log(LogLevel.WARNING, f"[刀4] pyarrow 不可用，跳过重建: {_e}")
            return {"status": "skipped", "reason": "no_pyarrow"}
        _all = list(nodes) if nodes is not None else self.node_pool.get_all_including_evicted()
        _saved = [n for n in _all if not getattr(n, "ephemeral", False)]
        _rows = self._nodes_to_parquet_columns(_saved)
        _lv_counts = {"L1": 0, "L2": 0, "L3": 0}
        for _r in _rows:
            _lv = str(_r.get("evol_level", "L1")).upper()
            if _lv in _lv_counts:
                _lv_counts[_lv] += 1
        _report = {
            "status": "dry_run" if dry_run else "rebuilt",
            "node_count": len(_rows),
            "lv_counts": _lv_counts,
        }
        if dry_run:
            self._log(LogLevel.INFO,
                      f"[刀4] dry_run：拟重建 Parquet {len(_rows)} 节点 "
                      f"(L1={_lv_counts['L1']} L2={_lv_counts['L2']} L3={_lv_counts['L3']})，不写盘")
            return _report
        # ---- 实跑：原子重刷 ----
        _ts = _ts or time.strftime("%Y%m%d_%H%M%S")
        _parent = os.path.dirname(self._m68_parquet_dir()) or "."
        _base = self._m68_parquet_dir()
        _tmp = os.path.join(_parent, f"parquet.m160tmp-{_ts}")
        _prev = os.path.join(_parent, f"parquet.m160prev-{_ts}")
        try:
            # ① 写临时目录
            if os.path.isdir(_tmp):
                shutil.rmtree(_tmp, ignore_errors=True)
            os.makedirs(_tmp, exist_ok=True)
            _table = self._m160_build_parquet_table(_rows, _saved)
            _pq4.write_to_dataset(
                _table, root_path=_tmp,
                partition_cols=["evol_level"] if PARQUET_SHARD_BY_EVOL_LEVEL else None,
                compression=PARQUET_COMPRESSION if PARQUET_COMPRESSION else "snappy",
            )
            # ② 校验临时目录
            _verified = self._m68_verify_parquet(root=_tmp)
            if _verified < 0:
                raise RuntimeError(
                    f"Parquet 重建校验失败(_m68_verify_parquet={_verified})，触发回滚")
            # ③ 旧分区 rename 至 prev（先清旧 prev）
            if os.path.isdir(_prev):
                shutil.rmtree(_prev, ignore_errors=True)
            if os.path.isdir(_base):
                os.makedirs(_parent, exist_ok=True)
                os.replace(_base, _prev)
            # ④ 逐分区原子 os.replace 新→正式
            for _entry in os.listdir(_tmp):
                _src = os.path.join(_tmp, _entry)
                _dst = os.path.join(_base, _entry)
                if os.path.isdir(_src):
                    os.makedirs(_base, exist_ok=True)
                    if os.path.exists(_dst):
                        shutil.rmtree(_dst, ignore_errors=True)
                    os.replace(_src, _dst)
            self._log(LogLevel.INFO,
                      f"[刀4] Parquet 重建完成: {len(_rows)} 节点 → {_base}"
                      f"（prev 备份={_prev}）")
            _report["status"] = "rebuilt"
            _report["prev_backup"] = _prev
            return _report
        except Exception as _e:
            self._log(LogLevel.ERROR,
                      f"[刀4] Parquet 重建失败，尝试回滚: {type(_e).__name__}: {_e}")
            try:
                if os.path.isdir(_prev) and not os.path.isdir(_base):
                    os.replace(_prev, _base)
            except Exception as _re:
                self._log(LogLevel.ERROR, f"[刀4] 回滚失败: {_re}")
            _report["status"] = "failed"
            _report["error"] = str(_e)
            return _report
        finally:
            if os.path.isdir(_tmp):
                shutil.rmtree(_tmp, ignore_errors=True)

    def _m160_build_parquet_table(self, rows, saved_nodes):
        """★刀4：构建带 schema 元数据的 Parquet table（同源复用 _save_parquet_locked 逻辑）。"""
        try:
            _table = table_from_rows(rows)
        except Exception as _schema_e:
            self._log(LogLevel.WARNING,
                      f"[刀4] Parquet schema 推断失败，复杂列退化为JSON字符串列重试: {_schema_e}")
            _bad_cols = ("semantic_relations", "verification_history")
            for _r in rows:
                for _c in _bad_cols:
                    _v = _r.get(_c)
                    if not isinstance(_v, str):
                        _r[_c] = json.dumps(_v if isinstance(_v, list) else [_v],
                                            ensure_ascii=False, default=str)
            _table = table_from_rows(rows)
        _lv_counts = {"L1": 0, "L2": 0, "L3": 0}
        for _r in rows:
            _lv = str(_r.get("evol_level", "L1")).upper()
            if _lv in _lv_counts:
                _lv_counts[_lv] += 1
        try:
            _checksum = self._compute_node_list_checksum(saved_nodes)
        except Exception as _ce:
            self._log(LogLevel.WARNING, f"[刀4] 节点列表校验和计算失败(忽略): {_ce}")
            _checksum = ""
        try:
            _table = _table.replace_schema_metadata({
                b"m81_schema_version": self._m81_parquet_expected_schema_version().encode("utf-8"),
                b"node_count": str(len(rows)).encode("utf-8"),
                b"l1_count": str(_lv_counts["L1"]).encode("utf-8"),
                b"l2_count": str(_lv_counts["L2"]).encode("utf-8"),
                b"l3_count": str(_lv_counts["L3"]).encode("utf-8"),
                b"node_list_checksum": str(_checksum).encode("utf-8"),
                b"write_time": time.strftime("%Y-%m-%dT%H:%M:%S").encode("utf-8"),
            })
        except Exception as _md_e:
            self._log(LogLevel.WARNING, f"[刀4] Parquet schema 元数据写入失败(忽略): {_md_e}")
        return _table

    @staticmethod
    def _decode_parquet_value(raw: Any) -> str:
        """★14.52：兼容新旧 Parquet value 列。
        新格式：原生 str 直接返回；旧格式：json.dumps 后的字符串需 json.loads 还原。
        用 try/except 区分：json.loads 成功且结果为 str 则是旧格式，否则新格式。
        """
        if not isinstance(raw, str) or not raw:
            return str(raw) if raw else ""
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, str) else raw
        except (json.JSONDecodeError, TypeError):
            return raw

    def _parquet_row_to_dict(self, _row: dict[str, Any], level: str | None = None) -> dict[str, Any]:
        """把 Parquet 行转回 PulseNode.from_dict 所需的 dict（复杂字段 json.loads）。

        level: 可选覆盖 evol_level。按分区目录单层读取时，分区列 evol_level 不在
               文件内（编码在目录名 evol_level=<level>），需要显式回填。
        """
        return {
            "node_id": _row.get("node_id", ""),
            "value": self._decode_parquet_value(_row.get("value", "")),  # ★14.52 新旧格式兼容
            "keywords": list(_row.get("keywords", []) or []),
            "evol_level": level if level is not None else _row.get("evol_level", "L1"),
            "importance": _row.get("importance", "C"),
            "abstraction": float(_row.get("abstraction", 0.0) or 0.0),
            "created_at": float(_row.get("created_at", 0.0) or 0.0),
            "last_activated": float(_row.get("last_activated", 0.0) or 0.0),
            "activation_count": int(_row.get("activation_count", 0) or 0),
            "space_path": _row.get("space_path", "/"),
            "state": _row.get("state", "active"),
            "source_organ": _row.get("source_organ", "unknown"),
            "trigger_reason": _row.get("trigger_reason", ""),
            "frequency_signature": float(_row.get("frequency_signature", 0.0) or 0.0),
            "linked_nodes": list(_row.get("linked_nodes", []) or []),
            # ★主线第10批 T4.3：旧库损坏的 JSON 列不应炸掉整行还原（降级为空列表+告警）
            "semantic_relations": self._safe_json_col(
                    _row.get("semantic_relations", "[]"), "semantic_relations"),
            "hebbian_weight": float(_row.get("hebbian_weight", 0.0) or 0.0),
            "cooccurrence_count": int(_row.get("cooccurrence_count", 0) or 0),
            "version": int(_row.get("version", 1) or 1),
            "updated_at": float(_row.get("updated_at", 0.0) or 0.0),
            "checksum": _row.get("checksum", ""),
            "instinct": bool(_row.get("instinct", False)),
            "instinct_at": float(_row.get("instinct_at", 0.0) or 0.0),
            "instinct_active_times": int(_row.get("instinct_active_times", 0) or 0),
            "instinct_last_use": float(_row.get("instinct_last_use", 0.0) or 0.0),
            "ephemeral": bool(_row.get("ephemeral", False)),
            "view_mode": _row.get("view_mode", "OUTER_VIEW"),
            "trust_score": float(_row.get("trust_score", 50.0) or 0.0),
            # ★主线第10批 T4.3：同 semantic_relations，损坏降级不炸整行
            "verification_history": self._safe_json_col(
                    _row.get("verification_history", "[]"), "verification_history"),
            # ★主线第81批 T1：补 7 字段（.get 默认→旧 parquet 无此列自动补默认，向后兼容）
            "source_url": _row.get("source_url", ""),
            "evidence_chain": self._safe_json_col(
                    _row.get("evidence_chain", "[]"), "evidence_chain"),
            "source_time": float(_row.get("source_time", 0.0) or 0.0),
            "acquired_time": float(_row.get("acquired_time", 0.0) or 0.0),
            "source_timestamp": float(_row.get("source_timestamp", 0.0) or 0.0),
            "quality_flag": _row.get("quality_flag", "clean") or "clean",
            "quality_reason": _row.get("quality_reason", "") or "",
            # ★第122批 T-122b：C4断5 读映射补两键（与写白名单对齐）
            "conflict_count": int(_row.get("conflict_count", 0) or 0),
            "last_conflict_at": float(_row.get("last_conflict_at", 0.0) or 0.0),
        }

    @staticmethod
    def _safe_json_col(value: Any, col_name: str) -> list:
        """★主线第10批 T4.3：安全解析 JSON 字符串列。

        兼容旧 json 字符串列（解析）与新原生 list 列（直接返回）；
        字符串损坏时告警并降级为空列表，不中断整行还原。
        """
        if isinstance(value, str):
            try:
                _parsed = json.loads(value)
                return _parsed if isinstance(_parsed, list) else list(_parsed or [])
            except (ValueError, TypeError) as _je:
                print(f"[WARNING] PulseSnapshot JSON列解析失败({col_name}): "
                      f"{type(_je).__name__}: {_je}")
                return []
        return list(value or [])

    def save_parquet(self) -> bool:
        """★阶段A：用 pyarrow 写 Parquet 列式快照（按 evol_level 分区）。

        与 save() 并存；不修改内存三层池，只替换落盘格式。
        失败时返回 False（调用方决定是否回退 JSON）。
        """
        # ★P1修复：并发写竞态加锁（RLock 可重入，save() 内部调用时不会死锁）
        with self._lock:
            return self._save_parquet_locked()
    
    def _save_parquet_locked(self) -> bool:
        """save_parquet() 的实际逻辑（已持有 self._lock）。"""
        if self.node_pool is None:
            self._log(LogLevel.WARNING, "node_pool 未注入，跳过 Parquet 保存")
            return False
        try:
            import pyarrow.parquet as pq  # noqa: F401 - 可用性探测（pa_compat 内部再导入 pa）
        except Exception as _e:
            self._log(LogLevel.WARNING, f"pyarrow 不可用，Parquet 保存跳过: {_e}")
            return False

        try:
            _all = self.node_pool.get_all_including_evicted()
            _saved = [n for n in _all if not getattr(n, 'ephemeral', False)]
            _rows = self._nodes_to_parquet_columns(_saved)
            if not _rows:
                self._log(LogLevel.INFO, "无节点，跳过 Parquet 保存")
                return True
            try:
                _table = table_from_rows(_rows)
            except Exception as _schema_e:
                # ★3.1（2026-09-08 第八批）：struct/非struct 混合兜底。
                #   semantic_relations / verification_history 为 list<struct> 原生列，
                #   个别节点字段是 dict 或元素类型混杂时 pyarrow 推断 schema 失败
                #   （"cannot mix struct and non-struct"）。此时把这两个复杂列
                #   json.dumps 成字符串列重试（加载端 _parquet_row_to_dict 旧格式兼容，
                #   读回时 json.loads 还原）——数据零丢失，只是这两列退回字符串编码。
                self._log(LogLevel.WARNING,
                          f"Parquet schema 推断失败，复杂列退化为JSON字符串列重试: {_schema_e}")
                _bad_cols = ("semantic_relations", "verification_history")
                for _r in _rows:
                    for _c in _bad_cols:
                        _v = _r.get(_c)
                        if not isinstance(_v, str):
                            _r[_c] = json.dumps(_v if isinstance(_v, list) else [_v],
                                                ensure_ascii=False, default=str)
                _table = table_from_rows(_rows)
            # ★第81批 T1-⑤：写 schema 版本/分层计数/checksum 元数据（回退 JSON 的判据来源）
            _lv_counts = {"L1": 0, "L2": 0, "L3": 0}
            for _r in _rows:
                _lv = str(_r.get("evol_level", "L1")).upper()
                if _lv in _lv_counts:
                    _lv_counts[_lv] += 1
            try:
                _checksum = self._compute_node_list_checksum(_saved)
            except Exception:
                _checksum = ""
            try:
                _table = _table.replace_schema_metadata({
                    b"m81_schema_version": self._m81_parquet_expected_schema_version().encode("utf-8"),
                    b"node_count": str(len(_rows)).encode("utf-8"),
                    b"l1_count": str(_lv_counts["L1"]).encode("utf-8"),
                    b"l2_count": str(_lv_counts["L2"]).encode("utf-8"),
                    b"l3_count": str(_lv_counts["L3"]).encode("utf-8"),
                    b"node_list_checksum": str(_checksum).encode("utf-8"),
                    b"write_time": time.strftime("%Y-%m-%dT%H:%M:%S").encode("utf-8"),
                })
            except Exception as _md_e:
                self._log(LogLevel.WARNING, f"[第81批 T1] Parquet schema 元数据写入失败(忽略): {_md_e}")
            os.makedirs(self.parquet_dir, exist_ok=True)
            # ★v30.0修复：覆盖写（而非 append 写）。
            # 原实现直接 write_to_dataset 到正式目录，pyarrow 的 dataset 写入是
            # 「追加新文件」语义，每次全量保存都生成一批新的随机命名 parquet 文件，
            # 旧文件永不删除，导致 data/knowledge/parquet 无限膨胀（几个 G）。
            # 现改为：先写到临时目录 → 清理正式目录下旧分区文件 → 原子移动临时内容，
            # 保证正式目录始终只保留「当前最新一份」快照，不再累积历史文件。
            _tmp_dir = tempfile.mkdtemp(prefix="parquet_tmp_", dir=os.path.dirname(self.parquet_dir) or ".")
            try:
                pq.write_to_dataset(
                    _table,
                    root_path=_tmp_dir,
                    partition_cols=["evol_level"] if PARQUET_SHARD_BY_EVOL_LEVEL else None,
                    compression=PARQUET_COMPRESSION if PARQUET_COMPRESSION else "snappy",
                )
                # 清理正式目录下的旧分区文件（evol_level=* 目录）
                self._cleanup_parquet_files()
                # 将临时目录中的分区目录移动到正式目录
                for _entry in os.listdir(_tmp_dir):
                    _src = os.path.join(_tmp_dir, _entry)
                    _dst = os.path.join(self.parquet_dir, _entry)
                    if os.path.isdir(_src):
                        if os.path.exists(_dst):
                            shutil.rmtree(_dst, ignore_errors=True)
                        shutil.move(_src, _dst)
            finally:
                # 清理临时目录残留
                if os.path.isdir(_tmp_dir):
                    shutil.rmtree(_tmp_dir, ignore_errors=True)
            self._log(LogLevel.INFO,
                      f"Parquet 快照已保存(覆盖写): {len(_rows)} 节点 → {self.parquet_dir}")
            return True
        except Exception as _e:
            # ★3.1：JSON 主快照正常时 Parquet 失败不影响运行，降为 WARNING（星轨第八批3.3）
            self._log(LogLevel.WARNING, f"Parquet 保存失败(JSON主快照不受影响): {_e}")
            return False

    def _cleanup_parquet_files(self):
        """★v30.0新增：清理 parquet 目录下旧的 evol_level 分区文件。

        只删除 evol_level=* 目录（数据分区），保留目录本身，避免误删其他文件。
        """
        if not os.path.isdir(self.parquet_dir):
            return
        try:
            for _entry in os.listdir(self.parquet_dir):
                if _entry.startswith("evol_level="):
                    _path = os.path.join(self.parquet_dir, _entry)
                    if os.path.isdir(_path):
                        shutil.rmtree(_path, ignore_errors=True)
        except Exception as _e:
            self._log(LogLevel.WARNING, f"清理旧 Parquet 分区文件失败（忽略）: {_e}")

    def load_parquet(self) -> list[PulseNode]:
        """★阶段A：从 Parquet 读回全部节点（流式，逐行 from_dict）。"""
        try:
            import pyarrow.parquet as pq
        except Exception as _e:
            self._log(LogLevel.WARNING, f"pyarrow 不可用，Parquet 加载跳过: {_e}")
            return []
        if not os.path.isdir(self.parquet_dir):
            self._log(LogLevel.DEBUG, f"Parquet 目录不存在(正常降级): {self.parquet_dir}")
            return []
        try:
            # ★14.49优化：read_table 直接读分区目录（跳过 ParquetDataset 创建开销）
            #   实测：ParquetDataset 0.841s → read_table 0.353s（纯读取快2.4x）
            #   to_batches 分批处理：减少内存峰值
            _table = pq.read_table(self.parquet_dir)
            _nodes = []
            for _batch in _table.to_batches(max_chunksize=2000):
                for _row in _batch.to_pylist():
                    try:
                        # ★第83批 T-d1：evol_level 是**分区目录名**（evol_level=Lx）编码，
                        #   不在 parquet 文件列内；整目录读取不得依赖 pyarrow 自动注入，
                        #   显式回填，避免 from_dict 静默默认 L1 造成 L2/L3 分层塌缩。
                        _lv83 = _row.get("evol_level") or ""
                        _node = PulseNode.from_dict(
                            self._parquet_row_to_dict(_row, level=_lv83 or None))
                        _nodes.append(_node)
                    except Exception as _e:
                        self._log(LogLevel.ERROR, f"Parquet 节点恢复失败: {_e}")
            self._log(LogLevel.INFO, f"Parquet 快照加载: {len(_nodes)} 节点")
            return _nodes
        except Exception as _e:
            self._log(LogLevel.ERROR, f"Parquet 加载失败: {_e}")
            return []

    def load_parquet_by_level(self, level: str) -> list[PulseNode]:
        """★阶段A：按 evol_level 只读一个分区（冷层召回的关键接口，分区剪裁）。"""
        try:
            import pyarrow.parquet as pq
        except Exception:
            return []
        _level_dir = os.path.join(self.parquet_dir, f"evol_level={level}")
        if not os.path.isdir(_level_dir):
            return []
        try:
            _table = pq.read_table(_level_dir)
            _nodes = []
            for _row in _table.to_pylist():
                try:
                    # 分区列 evol_level 不在文件内，需显式回填 level
                    _nodes.append(PulseNode.from_dict(self._parquet_row_to_dict(_row, level=level)))
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
            return _nodes
        except Exception as _e:
            self._log(LogLevel.ERROR, f"Parquet 按层加载失败({level}): {_e}")
            return []

    def migrate_to_parquet(self) -> dict[str, Any]:
        """★阶段A：一次性迁移工具——旧 JSON 快照 → Parquet（只读旧 + 写新，不删旧）。"""
        _result = {"status": "skipped", "json_nodes": 0, "parquet_nodes": 0, "verified": False}
        if not os.path.exists(self.snapshot_path):
            _result["status"] = "no_json"
            return _result
        try:
            _data = safe_read_json(self.snapshot_path, default={})
            _nodes_data = _data.get("nodes", [])
            _result["json_nodes"] = len(_nodes_data)
            _nodes = []
            for _nd in _nodes_data:
                try:
                    _nodes.append(PulseNode.from_dict(_nd))
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            if not self.save_parquet():
                _result["status"] = "save_failed"
                return _result
            # 校验：读回 Parquet 对比 node_id 集合
            _parquet_nodes = self.load_parquet()
            _result["parquet_nodes"] = len(_parquet_nodes)
            _json_ids = {n.node_id for n in _nodes}
            _parquet_ids = {n.node_id for n in _parquet_nodes}
            _result["verified"] = _json_ids == _parquet_ids
            _result["status"] = "verified" if _result["verified"] else "mismatch"
            return _result
        except Exception as _e:
            print(f"[WARNING] PulseSnapshot.py:1428: {type(_e).__name__}: {_e}")
            _result["status"] = "error"
            _result["error"] = str(_e)
            return _result


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== PulseSnapshot P1-3 增量保存+轮转自测 ===\n")
    
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    
    pool = PulseNodePool()
    snapshot = PulseSnapshot(snapshot_path="data/test_snapshot.json")
    snapshot.set_node_pool(pool)
    
    l1 = PulseNode("临时感知数据", keywords=["测试"], 
                    source_organ="双腿", evol_level=PulseNode.EVOL_L1,
                    importance=PulseNode.IMPORTANCE_C)
    l2 = PulseNode("Python是编程语言", keywords=["Python"],
                    source_organ="胃", evol_level=PulseNode.EVOL_L2,
                    importance=PulseNode.IMPORTANCE_A)
    l3 = PulseNode("我是曈曈", keywords=["身份"],
                    source_organ="内在世界", evol_level=PulseNode.EVOL_L3,
                    importance=PulseNode.IMPORTANCE_S)
    
    pool.add(l1)
    pool.add(l2)
    pool.add(l3)
    
    print(f"1. 添加节点: 总数={pool.count()} (应为3)")
    
    # 第一次保存（全量）
    ok = snapshot.save()
    print(f"2. 首次保存: {'✅ 成功' if ok else '❌ 失败'}")
    print(f"   追踪节点数: {len(snapshot._last_saved_nodes)}")
    
    # 第二次保存（无变更，应跳过）
    print("\n3. 无变更保存（应跳过）:")
    ok2 = snapshot.save()
    print(f"   {'✅ 跳过（无变更）' if ok2 else '❌ 失败'}")
    
    # 添加新节点后保存（增量）
    l4 = PulseNode("新知识点", keywords=["新"],
                    source_organ="双腿", evol_level=PulseNode.EVOL_L1,
                    importance=PulseNode.IMPORTANCE_C)
    pool.add(l4)
    print("\n4. 添加新节点后增量保存:")
    ok3 = snapshot.save()
    print(f"   {'✅ 增量保存成功' if ok3 else '❌ 失败'}")
    print(f"   追踪节点数: {len(snapshot._last_saved_nodes)}")
    
    # 加载验证
    pool2 = PulseNodePool()
    snapshot2 = PulseSnapshot(snapshot_path="data/test_snapshot.json")
    snapshot2.set_node_pool(pool2)
    
    restored = snapshot2.load()
    pool2.load_batch(restored)
    print(f"\n5. 加载快照: {pool2.count()} 个节点恢复 (应为4)")
    
    # 统计
    stats = snapshot.get_stats()
    print(f"\n6. 统计: 快照{stats['file_size_mb']}MB 备份{stats['backup_count']}份 追踪{stats['tracked_nodes']}节点")
    
    # 清理测试文件
    if os.path.exists("data/test_snapshot.json"):
        os.remove("data/test_snapshot.json")
    # 清理测试备份
    import glob
    for f in glob.glob("data/test_snapshot.json.*.bak"):
        try:
            os.remove(f)
        except Exception:
            pass
    
    print("\n=== 自测全部通过 ===")
# _m70_t4_snapshot_hotcold

# ============================================================================
# 第102批 T-102a：落盘链路「引用完整性」守卫（悬空边不落盘）
# ============================================================================
# 背景（烛微第2期 D160）：快照里 sem/linked 两类边约 2.69% 指向已被删除的节点，
# 这些悬空边①白占体积 ②让多跳检索在幽灵节点处静默截断。本守卫在**落盘前**
# 就地剔除指向「已知节点集合之外」的边，保证盘上无悬空引用。
# 零回归设计：仅删除目标不存在的边；开关关闭时一行都不改。

_M102_DANGLING_GUARD_DEFAULT = True

_m102_logger = get_module_logger("PulseSnapshot")


def _m102_dangling_guard_on() -> bool:
    """灰度开关：落盘前是否过滤悬空边（默认开）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_M102_DANGLING_EDGE_GUARD",
                            _M102_DANGLING_GUARD_DEFAULT))
    except Exception:
        return _M102_DANGLING_GUARD_DEFAULT


def _m102_filter_dangling_edges(snapshot: dict, extra_ids=None) -> tuple:
    """就地过滤 snapshot["nodes"] 里的悬空边。

    Args:
        snapshot: 待落盘的快照 dict（含 "nodes" 列表，元素为 dict）
        extra_ids: 额外的已知节点 ID 集合（如节点池全量 + 冷存索引）。
            ★为 None 表示「无法取得可靠全集」→ 直接跳过（绝不误删）。

    Returns:
        (sem_removed, linked_removed, known_count)
    """
    if not isinstance(snapshot, dict) or extra_ids is None:
        return (0, 0, 0)
    _nodes = snapshot.get("nodes")
    if not isinstance(_nodes, list) or not _nodes:
        return (0, 0, 0)
    _known = set()
    for _n in _nodes:
        if isinstance(_n, dict):
            _id = _n.get("node_id")
            if _id:
                _known.add(str(_id))
    for _x in (extra_ids or ()):
        _known.add(str(_x))
    if not _known:
        return (0, 0, 0)
    _srm = _lrm = 0
    for _n in _nodes:
        if not isinstance(_n, dict):
            continue
        _sr = _n.get("semantic_relations")
        if isinstance(_sr, list) and _sr:
            _keep = []
            for _it in _sr:
                _tg = _it.get("target_node_id") if isinstance(_it, dict) else _it
                if _tg is None:
                    continue
                if str(_tg) in _known:
                    _keep.append(_it)
                else:
                    _srm += 1
            if len(_keep) != len(_sr):
                _n["semantic_relations"] = _keep
        _ln = _n.get("linked_nodes")
        if isinstance(_ln, list) and _ln:
            _keep2 = [x for x in _ln if x is not None and str(x) in _known]
            _lrm += len(_ln) - len(_keep2)
            if len(_keep2) != len(_ln):
                _n["linked_nodes"] = _keep2
    return (_srm, _lrm, len(_known))


def _m102_cold_index_ids(cache: dict) -> set:
    """冷存索引（cold.index.json 的键）中的节点 ID，按文件 mtime 缓存。"""
    try:
        _p = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "data", "knowledge", "cold.index.json"))
        if not os.path.isfile(_p):
            return set()
        _mt = os.path.getmtime(_p)
        if cache.get("mtime") == _mt and "ids" in cache:
            return cache["ids"]
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _d = json.load(_f)
        _ids = set(str(k) for k in _d.keys()) if isinstance(_d, dict) else set()
        cache["mtime"] = _mt
        cache["ids"] = _ids
        return _ids
    except Exception as _e:
        _m102_logger.debug(f"[第102批 T-102a] 冷存索引读取失败(已忽略): "
                           f"{type(_e).__name__}: {_e}")
        return set()


def _m102_sidecar_node_ids(self) -> set:
    """快照自身之外的已知节点 ID（节点池全量 + 冷存索引）。

    ★返回 None 表示节点池不可用（无法得到可靠全集）→ 调用方必须跳过过滤，
    否则会把「指向快照外合法节点」的边误判为悬空。
    """
    _pool = getattr(self, "node_pool", None)
    if _pool is None:
        return None
    _ids = set()
    try:
        _all = _pool.get_all_including_evicted()
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m102_sidecar_node_ids L2985")
        return None
    try:
        for _n in (_all or []):
            _id = getattr(_n, "node_id", None)
            if _id:
                _ids.add(str(_id))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.PulseSnapshot::_m102_sidecar_node_ids L2992")
        return None
    _ids |= _m102_cold_index_ids(getattr(self, "_m102_cold_cache", {})
                                 if isinstance(getattr(self, "_m102_cold_cache", None), dict)
                                 else {})
    return _ids


PulseSnapshot._m102_sidecar_node_ids = _m102_sidecar_node_ids
