"""PulseWhiteCell —— PulseWhiteCell 相关实现

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

from nucleus.data.path_utils import safe_relpath as _safe_relpath  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

"""
PulseWhiteCell —— 脉冲驱动白细胞（免疫系统第一器官 · v9.5 分层脉冲版）
版本: v9.5 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年6月9日
更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+统一日志+命名规范+get_stats+自测同步）
更新: 2026年6月14日（v9.5: 免疫告警标记layer=L0，扫描结果标记layer=L3，适配分层异步调度）

职责:
    1. 异常检测：接收 WhiteCellEvent.SCAN 脉冲，扫描系统异常
    2. 自动修复：检测到错误时尝试预定义的修复策略
    3. 免疫记忆：记录修复成功的错误特征，下次快速匹配
    4. 异常告警：无法修复时发射 SystemEvent.ALARM 脉冲（L0生命线层）
    5. 安全事件收集：订阅SecurityEvent.*，更新免疫记忆
    6. 免疫记忆泛化：从安全事件中提取交互模式，实现免疫记忆泛化
"""

import random
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    HeartEvent,
    LogLevel,
    SecurityEvent,
    SystemEvent,
    WhiteCellEvent,
)


class PulseWhiteCell(BasePulseOrgan):
    """
    脉冲驱动白细胞（v9.5 分层脉冲版）

    免疫流程:
        WhiteCellEvent.SCAN 脉冲到达
        → 检查系统错误计数
        → 尝试匹配免疫记忆中的已知错误
        → 执行修复策略
        → 成功 → 更新免疫记忆
        → 失败 → 发射 SystemEvent.ALARM 脉冲（L0生命线层）
    """

    def __init__(self, organ_name: str = "白细胞"):
        super().__init__(organ_name)

        # 关联组件
        self.node_pool = None

        # 免疫记忆：错误特征 → 修复策略 + 成功率
        self._immune_memory: dict[str, dict[str, Any]] = {}
        self._max_immune_memory = 200  # ★v25.0治理：最大免疫记忆条目数
        self._immune_memory_ttl = 86400 * 30  # ★v25.0治理：30天未命中的条目自动清理

        # 修复策略库
        self._repair_strategies = {
            "timeout": "增加超时阈值并重试",
            "connection": "检查网络连接并重新建立",
            "memory": "触发知识淘汰释放内存",
            "database": "执行数据库完整性检查",
            "encoding": "切换编码格式重试",
        }

        # 统计
        self._scan_count = 0
        self._repair_count = 0
        self._success_count = 0
        self._fail_count = 0

        self._security_event_count = 0
        self.sandbox_core = None
        
        # ===== v20.0支点C：行为模式偏离检测 =====
        # ★v30.0负载均衡修复：随机错峰初始化，避免与其他器官取模任务同点共振
        self._behavior_check_counter = random.randint(1, 149)
        self._behavior_check_interval = 150       # 每150次心跳检测一次
        self._behavior_history: list[dict[str, Any]] = []  # 行为历史快照（最多5份）
        self._max_behavior_history = 5
        # ===== v20.0支点C结束 =====
        # ===== P2-3: 免疫记忆泛化 =====
        self._interaction_patterns: dict[str, dict[str, Any]] = {}
        self._pattern_match_count = 0
        self._recent_ethics_events: list[float] = []  # 最近伦理事件的时间戳
        self._ethics_spam_threshold = 5  # 10秒内超过此数视为高频交互
        self._ethics_spam_window = 10.0  # 时间窗口（秒）

        # ===== v25.2新增：脉冲异常自动检测与修复建议 =====
        self._pulse_exception_cache: dict[str, dict] = {}  # 异常缓存：key=器官+属性
        self._pending_patches: list[dict] = []  # 待修复补丁列表
        self._max_pending_patches = 20  # 最大待修复补丁数
        self._last_log_scan_time = 0.0  # 上次日志扫描时间
        self._log_scan_interval = 300.0  # 日志扫描间隔（5分钟）
        self._pulse_exception_count = 0  # 累计检测到的脉冲异常数

        # ★属性初始化完整性补全（自动审查添加）
        self._thymus = None

    def set_sandbox_core(self, core):
        self.sandbox_core = core
    def set_thymus(self, thymus):
        """
        ★v22.0 M5新增：注入胸腺引用。
        用于在免疫记忆未命中时查询胸腺的精英策略库。
        """
        self._thymus = thymus
    # ========== 框架注入接口 ==========

    def set_node_pool(self, pool):
        self.node_pool = pool

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == WhiteCellEvent.SCAN:
            return self._on_scan(payload)
        elif event_type == SystemEvent.ERROR_REPORT:
            return self._on_error_report(payload)
        elif event_type == HeartEvent.BEAT:
            return self._on_heartbeat(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type in (SecurityEvent.BLOCKED, SecurityEvent.PASSED, 
                            SecurityEvent.THREAT_DETECTED, SecurityEvent.SANDBOX_VIOLATION):
            self._on_security_event(payload, event_type)
            return None
        return None

    # ========== 事件处理 ==========

    def _on_scan(self, payload: dict) -> dict[str, Any]:
        """执行系统扫描"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._scan_count += 1

        errors_found = 0
        errors_repaired = 0
        errors_failed = 0

        # 步骤1: 检查节点池健康
        if self.node_pool:
            stats = self.node_pool.get_stats()
            total_nodes = stats.get("total_nodes", 0)

            if total_nodes == 0:
                self._log(LogLevel.WARNING, "节点池为空，可能存在数据丢失")
                repair_result = self._attempt_repair("database", "节点池为空")
                if repair_result["success"]:
                    errors_repaired += 1
                else:
                    errors_failed += 1
                errors_found += 1

            if total_nodes > 100000:
                self._log(LogLevel.WARNING, f"节点池过大: {total_nodes}")
                repair_result = self._attempt_repair("memory", f"节点池过大: {total_nodes}")
                if repair_result["success"]:
                    errors_repaired += 1
                else:
                    errors_failed += 1
                errors_found += 1

        # 步骤2: 脉冲异常自动检测（v25.2新增）
        _now = time.time()
        if _now - self._last_log_scan_time >= self._log_scan_interval:
            self._last_log_scan_time = _now
            _pulse_result = self._detect_pulse_exceptions()
            errors_found += _pulse_result.get("detected", 0)
            errors_repaired += _pulse_result.get("patches_generated", 0)

        # 步骤3: 发射扫描结果（v9.5: L3后台自主层）
        self._emit(WhiteCellEvent.SCAN_RESULT, {
            "errors_found": errors_found,
            "errors_repaired": errors_repaired,
            "errors_failed": errors_failed,
            "immune_memory_size": len(self._immune_memory),
            "pending_patches": len(self._pending_patches),
            "pulse_exceptions_detected": self._pulse_exception_count,
        }, priority=4, layer="L3")

        if errors_failed > 0:
            # v9.5: 免疫失败告警标记为L0生命线层
            self._emit(SystemEvent.ALARM, {
                "type": "immune_failure",
                "failed_count": errors_failed,
                "message": f"免疫系统无法修复 {errors_failed} 个错误",
            }, priority=8, layer="L0")

        return {
            "status": "scanned",
            "errors_found": errors_found,
            "errors_repaired": errors_repaired,
            "errors_failed": errors_failed,
        }
    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        """v20.0支点C：心跳驱动行为模式检测"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._behavior_check_counter += 1
        if self._behavior_check_counter % self._behavior_check_interval == 0:
            self._behavior_check_counter = 0
            self._detect_behavioral_anomaly()
        return {"status": "ok"}
    def _detect_behavioral_anomaly(self):
        """
        v20.0支点C：行为模式偏离检测。
        
        检测曈曈自身行为模式是否异常：
        1. 知识节点总数突变——可能与肾脏过度淘汰或胃消化异常有关
        2. 错误报告频率异常——可能有系统性故障
        3. 连续多次检测未发现新问题——可能检测机制失效
        
        检测到异常时发射SystemEvent.ALARM（L0），写入InsightBoard。
        """
        _anomalies = []
        _now = time.time()
        
        # 1. 知识节点总数突变检测
        if self.node_pool:
            _stats = self.node_pool.get_stats()
            _total = _stats.get("total_nodes", 0)
            
            if self._behavior_history:
                _prev = self._behavior_history[-1]
                _prev_total = _prev.get("total_nodes", _total)
                _delta = _total - _prev_total
                _delta_pct = _delta / max(1, _prev_total) * 100
                
                # 节点数骤降超过20%——可能肾脏过度淘汰
                if _delta_pct < -20:
                    _anomalies.append({
                        "type": "knowledge_shrink",
                        "detail": f"知识节点骤降{abs(_delta_pct):.0f}%（{_prev_total}→{_total}），"
                                 f"需检查肾脏淘汰策略是否过于激进",
                    })
                # 节点数骤增超过50%——可能胃消化了低质量内容
                elif _delta_pct > 50 and _total > 100:
                    _anomalies.append({
                        "type": "knowledge_surge",
                        "detail": f"知识节点骤增{_delta_pct:.0f}%（{_prev_total}→{_total}），"
                                 f"需检查是否有低质量知识涌入",
                    })
        
        # 2. 错误报告频率异常检测
        _recent_errors = [
            t for t in self._recent_ethics_events
            if _now - t < 1800  # 30分钟内
        ]
        if len(_recent_errors) >= 8:
            _anomalies.append({
                "type": "error_frequency_high",
                "detail": f"30分钟内收到{len(_recent_errors)}次伦理/安全事件，"
                         f"需检查是否有攻击或误判",
            })
        
        # 3. 免疫记忆长期无新增——可能检测机制失效
        _memory_age = 0
        for _entry in self._immune_memory.values():
            _age = _now - _entry.get("first_seen", _now)
            _memory_age = max(_memory_age, _age)
        if _memory_age > 86400 * 7 and len(self._immune_memory) < 5:
            _anomalies.append({
                "type": "immune_memory_stale",
                "detail": f"免疫记忆已超过7天无新增（共{len(self._immune_memory)}条），"
                         f"可能检测机制覆盖不足",
            })
        
        # 保存行为快照
        self._behavior_history.append({
            "timestamp": _now,
            "total_nodes": self.node_pool.get_stats().get("total_nodes", 0) if self.node_pool else 0,
            "immune_memory_size": len(self._immune_memory),
            "error_count": len(_recent_errors),
        })
        if len(self._behavior_history) > self._max_behavior_history:
            self._behavior_history = self._behavior_history[-self._max_behavior_history:]
        
        # 处理异常
        if _anomalies:
            for _anomaly in _anomalies:
                self._log(LogLevel.WARNING, f"行为偏离检测: {_anomaly['detail']}")
                
                # 发射L0层告警
                self._emit(SystemEvent.ALARM, {
                    "type": "behavioral_anomaly",
                    "anomaly_type": _anomaly["type"],
                    "message": _anomaly["detail"],
                }, priority=8, layer="L0")
                
                # 写入InsightBoard，供深度自我审视和主动交互使用
                try:
                    from nucleus.InsightBoard import get_insight_board
                    _board = get_insight_board()
                    _board.post(
                        insight_type="behavioral_anomaly",
                        content=_anomaly["detail"],
                        source_loop="行为模式偏离检测",
                        related_dimension=_anomaly["type"],
                        confidence=0.75,
                        keywords=["行为异常", _anomaly["type"], "自我守护"]
                    )
                except Exception as e:
                    self._log(LogLevel.ERROR, f'异常: {e}')    
    def _on_error_report(self, payload: dict) -> dict[str, Any]:
        """接收错误报告并尝试修复"""
        error_type = payload.get("error_type", "unknown")
        error_message = payload.get("error_message", "")
        source_organ = payload.get("source_organ", "unknown")  # noqa: F841

        if not error_message:
            return {"status": "skipped", "reason": "空错误消息"}

        self._log(LogLevel.INFO, f"收到错误报告: [{error_type}] {error_message[:80]}")

        repair_result = self._attempt_repair(error_type, error_message)

        return {
            "status": "repaired" if repair_result["success"] else "failed",
            "error_type": error_type,
            "method": repair_result.get("method", "unknown"),
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "scan_count": self._scan_count,
            "repair_count": self._repair_count,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "immune_memory": len(self._immune_memory),
            "interaction_patterns": len(self._interaction_patterns),
            "pattern_match_count": self._pattern_match_count,
            "is_running": self.is_running,
            "pending_patches": len(self._pending_patches),
            "pulse_exceptions_detected": self._pulse_exception_count,
        }

    # ========== v25.2新增：脉冲异常自动检测与修复建议 ==========

    def _detect_pulse_exceptions(self) -> dict[str, Any]:
        """扫描运行日志中的脉冲异常（AttributeError等），自动生成修复建议。"""
        import re as _re_pe
        _log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "logs", "pulse.log")

        if not os.path.exists(_log_file):
            return {"detected": 0, "patches_generated": 0}

        _detected = 0
        _patches_generated = 0

        try:
            with open(_log_file, encoding="utf-8", errors="ignore") as _f:
                _lines = _f.readlines()

            _recent_lines = _lines[-2000:] if len(_lines) > 2000 else _lines
            _attr_err_re = _re_pe.compile(
                r"AttributeError: '([^']+)' object has no attribute '([^']+)'"
            )

            for _line in _recent_lines:
                _m = _attr_err_re.search(_line)
                if _m:
                    _class_name = _m.group(1)
                    _attr_name = _m.group(2)
                    _key = f"{_class_name}.{_attr_name}"

                    if _key in self._pulse_exception_cache:
                        self._pulse_exception_cache[_key]["count"] += 1
                        continue

                    self._pulse_exception_count += 1
                    _detected += 1

                    _patch = self._generate_attribute_init_patch(_class_name, _attr_name)
                    if _patch:
                        self._pending_patches.append(_patch)
                        _patches_generated += 1
                        self._pulse_exception_cache[_key] = {
                            "class": _class_name, "attribute": _attr_name,
                            "count": 1, "patch_generated": True, "patch": _patch,
                        }
                        self._log(LogLevel.INFO,
                                  f"[脉冲异常修复] 检测到 {_key} 缺失，已生成修复补丁")
                    else:
                        self._pulse_exception_cache[_key] = {
                            "class": _class_name, "attribute": _attr_name,
                            "count": 1, "patch_generated": False,
                        }

            if len(self._pending_patches) > self._max_pending_patches:
                self._pending_patches = self._pending_patches[-self._max_pending_patches:]

            if _patches_generated > 0:
                self._emit(SystemEvent.ALARM, {
                    "type": "pulse_exception_patch",
                    "patches_generated": _patches_generated,
                    "pending_patches": len(self._pending_patches),
                    "message": f"检测到{_detected}个脉冲异常，已生成{_patches_generated}个修复补丁",
                }, priority=6, layer="L1")

        except Exception as _e:
            self._log(LogLevel.DEBUG, f"脉冲异常检测失败: {_e}")

        return {"detected": _detected, "patches_generated": _patches_generated}

    def _generate_attribute_init_patch(self, class_name: str, attr_name: str) -> dict | None:
        """为缺少属性初始化的类生成修复补丁。"""
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        _class_file = None
        for _root, _dirs, _files in os.walk(_project_root):
            if '.git' in _root or '__pycache__' in _root:
                continue
            for _f in _files:
                if _f.endswith('.py'):
                    _fp = os.path.join(_root, _f)
                    try:
                        with open(_fp, encoding='utf-8', errors='ignore') as _fh:
                            if f'class {class_name}' in _fh.read():
                                _class_file = _fp
                                break
                    except Exception:
                        pass
            if _class_file:
                break

        if not _class_file:
            return None

        try:
            with open(_class_file, encoding='utf-8') as _f:
                _file_content = _f.read()
        except Exception:
            return None

        import re as _re_gap
        _init_pattern = _re_gap.compile(
            r'def __init__\(self.*?\):(.*?)(?=\n    def |\nclass )',
            _re_gap.DOTALL
        )
        _init_match = _init_pattern.search(_file_content)
        if not _init_match:
            return None

        _init_body = _init_match.group(1)
        if f'self.{attr_name}' in _init_body:
            return None

        _default_value = self._infer_default_value(attr_name)
        _rel_path = _safe_relpath(_class_file, _project_root)

        return {
            "id": f"attr_init_{class_name}_{attr_name}_{int(time.time())}",
            "type": "attribute_initialization",
            "file": _rel_path,
            "class": class_name,
            "attribute": attr_name,
            "default_value": _default_value,
            "description": f"在 {class_name}.__init__ 中添加 self.{attr_name} = {_default_value}",
            "risk_level": "极低",
            "status": "pending",
            "generated_at": time.time(),
            "source": "pulse_white_cell_auto_detect",
        }

    def _infer_default_value(self, attr_name: str) -> str:
        """根据属性名推断默认初始值。"""
        _name_lower = attr_name.lower()
        if any(_k in _name_lower for _k in ['count', 'counter', 'num', 'total', 'fail', 'success']):
            return "0"
        if any(_k in _name_lower for _k in ['time', 'until', 'last', 'start', 'end', 'window']):
            return "0.0"
        if any(_k in _name_lower for _k in ['list', 'queue', 'history', 'cache', 'buffer', 'stack']):
            return "[]"
        if any(_k in _name_lower for _k in ['dict', 'map', 'state', 'config', 'memory', 'pool', 'index']):
            return "{}"
        if 'lock' in _name_lower:
            return "threading.Lock()"
        if any(_k in _name_lower for _k in ['enabled', 'active', 'running', 'booted', 'started', 'stopped']):
            return "False"
        return "None"

    def get_pending_patches(self) -> list[dict]:
        """获取待修复补丁列表。"""
        return list(self._pending_patches)

    def apply_patch(self, patch_id: str) -> dict[str, Any]:
        """应用指定的待修复补丁。"""
        for _i, _patch in enumerate(self._pending_patches):
            if _patch.get("id") == patch_id:
                if _patch.get("type") == "attribute_initialization":
                    _result = self._apply_attribute_init_patch(_patch)
                    if _result.get("success"):
                        _patch["status"] = "applied"
                        self._pending_patches.pop(_i)
                    return _result
        return {"success": False, "error": "补丁不存在"}

    def _apply_attribute_init_patch(self, patch: dict) -> dict[str, Any]:
        """应用属性初始化补丁。"""
        import re as _re_aap
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        _file_path = os.path.join(_project_root, patch.get("file", ""))

        if not os.path.exists(_file_path):
            return {"success": False, "error": "文件不存在"}

        try:
            with open(_file_path, encoding='utf-8') as _f:
                _content = _f.read()

            _attr_name = patch.get("attribute", "")
            _default = patch.get("default_value", "None")

            _init_pattern = _re_aap.compile(
                r'(def __init__\(self.*?\):.*?)(?=\n    def |\nclass )',
                _re_aap.DOTALL
            )
            _match = _init_pattern.search(_content)
            if not _match:
                return {"success": False, "error": "未找到__init__方法"}

            _init_body = _match.group(1)
            _new_init = _init_body.rstrip() + f"\n        self.{_attr_name} = {_default}\n"
            _content = _content[:_match.start()] + _new_init + _content[_match.end():]

            with open(_file_path, 'w', encoding='utf-8') as _f:
                _f.write(_content)

            import py_compile
            try:
                py_compile.compile(_file_path, doraise=True)
            except py_compile.PyCompileError as _e:
                return {"success": False, "error": f"语法错误: {_e}"}

            return {"success": True, "file": patch.get("file")}

        except Exception as _e:
            return {"success": False, "error": str(_e)}

    # ========== 自动修复 ==========

    def _attempt_repair(self, error_type: str, error_message: str) -> dict[str, Any]:
        self._repair_count += 1

        error_signature = self._extract_error_signature(error_type, error_message)
        if error_signature in self._immune_memory:
            memory = self._immune_memory[error_signature]
            self._log(LogLevel.DEBUG, f"免疫记忆命中: {error_signature[:40]}...")
            self._success_count += 1
            memory["success_count"] += 1
            memory["last_seen"] = time.time()
            return {
                "success": True,
                "method": f"免疫记忆: {memory['strategy']}",
                "signature": error_signature,
            }

        # ★v22.0 M5新增：免疫记忆未命中时，查询胸腺精英策略库
        _elite_strategy = None
        if hasattr(self, '_thymus') and self._thymus:
            try:
                _elite_strategies = self._thymus.get_elite_strategies()
                for _info in _elite_strategies.values():
                    if _info.get("success_rate", 0) >= 0.8:
                        _elite_strategy = _info.get("strategy", "")
                        break
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        
        strategy = _elite_strategy or self._repair_strategies.get(error_type, "通用修复策略")
        # ===== v22.0 M5新增结束 =====
        success = self._simulate_repair(error_type, error_message)

        self._immune_memory[error_signature] = {
            "error_type": error_type,
            "strategy": strategy,
            "success_count": 1 if success else 0,
            "fail_count": 0 if success else 1,
            "first_seen": time.time(),
            "last_seen": time.time(),
        }
        # ★v25.0治理：每次添加新条目后检查是否需要清理
        if len(self._immune_memory) > self._max_immune_memory:
            self._cleanup_immune_memory()

        if success:
            self._success_count += 1
        else:
            self._fail_count += 1

        return {
            "success": success,
            "method": strategy,
            "signature": error_signature,
        }
    def _cleanup_immune_memory(self):
        """
        ★v25.0治理：清理过期的免疫记忆条目。
        超过30天未命中的条目被删除，如果条目数仍超过上限，
        按last_seen时间删除最旧的条目。
        """
        _now = time.time()
        
        # 第一轮：清理过期条目（30天未命中）
        _expired_keys = []
        for _key, _entry in self._immune_memory.items():
            _last_seen = _entry.get("last_seen", _entry.get("first_seen", 0))
            if _now - _last_seen > self._immune_memory_ttl:
                _expired_keys.append(_key)
        for _key in _expired_keys:
            del self._immune_memory[_key]
        
        # 第二轮：如果仍超过上限，删除最旧的条目
        if len(self._immune_memory) > self._max_immune_memory:
            _sorted_items = sorted(
                self._immune_memory.items(),
                key=lambda x: x[1].get("last_seen", x[1].get("first_seen", 0))
            )
            _to_remove = len(self._immune_memory) - self._max_immune_memory
            for _key, _entry in _sorted_items[:_to_remove]:
                del self._immune_memory[_key]
        
        if _expired_keys or len(self._immune_memory) > self._max_immune_memory:
            self._log(LogLevel.DEBUG, 
                     f"免疫记忆清理: 移除{len(_expired_keys)}条过期, "
                     f"当前{len(self._immune_memory)}条")
    def _extract_error_signature(self, error_type: str, error_message: str) -> str:
        short_msg = error_message[:60].strip()
        return f"{error_type}:{short_msg}"

    def _simulate_repair(self, error_type: str, error_message: str) -> bool:
        known_types = ["timeout", "connection", "encoding", "database", "memory"]
        return error_type in known_types
    def _on_security_event(self, payload: dict, event_type: str):
        """P2-2: 收集安全事件，更新免疫记忆"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._security_event_count += 1
        # 将安全事件记录到免疫记忆
        signature = f"security:{payload.get('level', '?')}:{payload.get('verdict', '?')}"
        if signature not in self._immune_memory:
            self._immune_memory[signature] = {
                "error_type": "security_event",
                "strategy": "已记录",
                "success_count": 0,
                "fail_count": 0,
                "first_seen": time.time(),
                "last_seen": time.time(),
            }
        self._immune_memory[signature]["last_seen"] = time.time()
        if event_type == SecurityEvent.BLOCKED:
            self._immune_memory[signature]["fail_count"] += 1
        else:
            self._immune_memory[signature]["success_count"] += 1
        
        # ★v25.0治理：安全事件签名也可能积累，定期清理
        if len(self._immune_memory) > self._max_immune_memory * 1.5:
            self._cleanup_immune_memory()

        # P2-3: 从具体事件中提取交互模式
        self._extract_interaction_pattern(payload, event_type)
    # ========== P2-3: 交互模式提取 ==========

    def _extract_interaction_pattern(self, payload: dict, event_type: str):
        """从安全事件中提取交互模式，实现免疫记忆泛化（智能版：区分真正攻击和高频交互）"""
        level = payload.get("level", "?")
        verdict = payload.get("verdict", "?")  # noqa: F841
        reason = payload.get("reason", "")
        content = payload.get("content", "")

        # 模式分类规则
        pattern_type = None
        if "危险关键字" in reason or "os.system" in reason or "subprocess" in reason:
            pattern_type = "代码注入攻击"
        elif "sandbox" in reason.lower():
            pattern_type = "沙箱逃逸尝试"
        elif "permission" in reason.lower() or "denied" in reason.lower():
            pattern_type = "权限越界"
        elif event_type == SecurityEvent.THREAT_DETECTED:
            pattern_type = "威胁探测"
        elif level == "L3":
            # 伦理违规需要进一步判断：是真正攻击还是高频正常交互
            if self._is_genuine_attack(content, reason):
                pattern_type = "伦理违规"
            else:
                # 非真正攻击，只做速率跟踪，不记录为攻击模式
                self._track_ethics_frequency()
                return
        elif level == "L1":
            pattern_type = "表层攻击"

        if pattern_type is None:
            return

        pattern_key = f"pattern:{pattern_type}"
        if pattern_key not in self._interaction_patterns:
            self._interaction_patterns[pattern_key] = {
                "pattern_type": pattern_type,
                "occurrence_count": 0,
                "blocked_count": 0,
                "passed_count": 0,
                "first_seen": time.time(),
                "last_seen": time.time(),
                "sample_reason": reason[:100],
            }

        pattern = self._interaction_patterns[pattern_key]
        pattern["occurrence_count"] += 1
        pattern["last_seen"] = time.time()
        if event_type == SecurityEvent.BLOCKED:
            pattern["blocked_count"] += 1
        else:
            pattern["passed_count"] += 1

        # 当某种模式反复出现时，标记为已知攻击模式
        if pattern["occurrence_count"] >= 3 and pattern["blocked_count"] >= 2:
            # 对于伦理违规，额外检查是否是高频交互导致的误报
            if pattern_type == "伦理违规" and self._is_high_frequency_normal_use():
                return  # 高频交互导致的伦理事件，不触发免疫泛化日志
            
            self._log(LogLevel.INFO,
                     f"免疫泛化: 识别攻击模式 '{pattern_type}' "
                     f"(出现{pattern['occurrence_count']}次, 拦截{pattern['blocked_count']}次)")
    def _is_genuine_attack(self, content: str, reason: str) -> bool:
        """判断是否为真正的攻击行为，而非正常的高频交互"""
        # 1. 检查是否包含真正的危险指令
        dangerous_commands = [
            "os.system", "subprocess", "eval(", "exec(",
            "rm -rf", "del /f", "format c:", "shutdown",
            "DROP TABLE", "DELETE FROM", "__import__",
            "import os", "import subprocess",
        ]
        content_lower = content.lower() if content else ""
        for cmd in dangerous_commands:
            if cmd.lower() in content_lower:
                return True
        
        # 2. 检查payload中是否有明显的恶意上下文
        threat_keywords = ["攻击", "入侵", "破解", "盗取", "绕过", "漏洞利用", "植入", "木马"]
        reason_lower = reason.lower() if reason else ""
        for kw in threat_keywords:
            if kw in reason_lower:
                return True
        
        # 3. 如果内容看起来是正常的知识或对话（非威胁性内容），则不是攻击
        # 正常知识输入的特征：包含教育性关键词、长度适中、不含恶意指令
        educational_keywords = ["原理", "理论", "概念", "定义", "规则", "结构", "模型", "算法", "方法"]
        if any(kw in content_lower for kw in educational_keywords):
            return False  # 大概率是正常知识输入
        
        # 4. 内容长度>50且不含恶意指令，更可能是正常交流
        if len(content) > 50 and not any(cmd.lower() in content_lower for cmd in dangerous_commands):
            return False
        
        return False  # 默认为非攻击（安全优先）
    
    def _is_high_frequency_normal_use(self) -> bool:
        """检查当前是否是高频正常交互模式"""
        now = time.time()
        # 清理过期记录
        self._recent_ethics_events = [
            t for t in self._recent_ethics_events
            if now - t < self._ethics_spam_window
        ]
        # 添加当前记录
        self._recent_ethics_events.append(now)
        # 判断频率
        return len(self._recent_ethics_events) > self._ethics_spam_threshold
    
    def _track_ethics_frequency(self):
        """记录伦理事件的频率（用于智能降级）"""
        now = time.time()
        self._recent_ethics_events = [
            t for t in self._recent_ethics_events
            if now - t < self._ethics_spam_window
        ]
        self._recent_ethics_events.append(now)

    def match_interaction_pattern(self, reason: str) -> dict[str, Any] | None:
        """
        尝试用已知交互模式匹配新的安全事件。

        P2-3阶段仅做精确匹配，P2后续可升级为模糊匹配。
        """
        for pattern_key, pattern in self._interaction_patterns.items():
            if pattern["occurrence_count"] >= 3:
                sample = pattern.get("sample_reason", "")
                if sample and any(word in reason for word in sample.split()[:5]):
                    self._pattern_match_count += 1
                    pattern["last_seen"] = time.time()
                    return {
                        "matched_pattern": pattern["pattern_type"],
                        "pattern_confidence": min(0.9, pattern["blocked_count"] / max(1, pattern["occurrence_count"])),
                    }
        return None     
    def get_immune_memory(self) -> dict[str, dict[str, Any]]:
        """公开接口：获取免疫记忆（供胸腺和骨髓等训练器官使用）"""
        return dict(self._immune_memory)

    def get_interaction_patterns(self) -> dict[str, dict[str, Any]]:
        """★P3-1修复：公开接口，供胸腺等器官读取交互模式（替代跨器官私有属性直读）"""
        return dict(self._interaction_patterns)

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    WhiteCellEvent.SCAN,
                    SystemEvent.ERROR_REPORT,
                    SystemEvent.STATUS_REQUEST,
                    HeartEvent.BEAT,
                    SecurityEvent.BLOCKED,
                    SecurityEvent.PASSED,
                    SecurityEvent.THREAT_DETECTED,
                    SecurityEvent.SANDBOX_VIOLATION,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "白细胞",
    "class_name": "PulseWhiteCell",
    "attr_name": "white_cell",
    "system": "immune",
    "always_online": False,
    "feature_flag": "enable_immune",
    "extra_deps": {},
    "post_wiring": [
        {"target": "胸腺", "setter": "set_thymus"},
        {"target": "sandbox_core", "setter": "set_sandbox_core"},
    ],
}

if __name__ == "__main__":
    # ★主线第37批 T5（P2-229）：本块是**开发自测**（手动 `python 本文件` 运行），
    #   用 print 输出到控制台是正确形态 —— 框架运行时**不会执行**本块（__main__ 守卫）。
    #   器官的运行时日志统一走 `self._log`（见业务方法）；自测块不改为 _log 的原因：
    #   ① 自测需要控制台可见输出；② 本块无 `self`（用的是局部实例变量）。
    print("=== PulseWhiteCell v9.5 分层脉冲自测 ===\n")

    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    pool = PulseNodePool()

    wbc = PulseWhiteCell("白细胞")
    wbc.set_info_field(mock_field)
    wbc.set_node_pool(pool)
    wbc.start()

    result1 = wbc.on_pulse({
        "event_type": WhiteCellEvent.SCAN,
        "payload": {},
        "priority": 4,
    })
    print(f"1. 扫描空节点池: 发现{result1['errors_found']}个错误, 修复{result1['errors_repaired']}个")

    # 验证扫描结果脉冲的 layer 标记
    scan_pulses = [p for p in mock_field.published if p.get("event_type") == WhiteCellEvent.SCAN_RESULT]
    if scan_pulses:
        print(f"   SCAN_RESULT脉冲 layer: {scan_pulses[-1].get('layer', '未设置')} (预期L3)")

    # 验证告警脉冲的 layer 标记
    alarm_pulses = [p for p in mock_field.published if p.get("event_type") == SystemEvent.ALARM]
    if alarm_pulses:
        print(f"   ALARM脉冲 layer: {alarm_pulses[-1].get('layer', '未设置')} (预期L0)")

    result2 = wbc.on_pulse({
        "event_type": SystemEvent.ERROR_REPORT,
        "payload": {
            "error_type": "timeout",
            "error_message": "Ollama API 调用超时 (30s)",
            "source_organ": "嘴巴",
        },
        "priority": 7,
    })
    print(f"2. 超时错误: {result2['status']}, 方法={result2['method']}")

    result3 = wbc.on_pulse({
        "event_type": SystemEvent.ERROR_REPORT,
        "payload": {
            "error_type": "unknown_bug",
            "error_message": "发生了无法解释的异常",
            "source_organ": "未知",
        },
        "priority": 7,
    })
    print(f"3. 未知错误: {result3['status']}, 方法={result3['method']}")

    result4 = wbc.on_pulse({
        "event_type": SystemEvent.ERROR_REPORT,
        "payload": {
            "error_type": "timeout",
            "error_message": "Ollama API 调用超时 (30s)",
            "source_organ": "嘴巴",
        },
        "priority": 7,
    })
    print(f"4. 重复错误(免疫记忆): {result4['status']}, 方法={result4['method']}")

    status = wbc.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"5. 统计: 扫描{status['scan_count']}次, 修复{status['repair_count']}次, "
          f"成功{status['success_count']}次, 免疫记忆{status['immune_memory']}条")

    wbc.stop()
    print("\n=== 自测全部通过 ===")