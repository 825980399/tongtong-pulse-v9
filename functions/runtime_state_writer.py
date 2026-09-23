"""runtime_state_writer —— 运行时状态写入器（RuntimeStateWriter）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from config import TIMEOUT_CONFIG

import os
import threading
import time
from datetime import datetime
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json


class RuntimeStateWriter:
    """运行时状态写入器：定期把内存数据汇总到文件"""

    def __init__(self, interval: float = 2.0, output_path: str | None = None):
        self.interval = interval
        self.output_path = output_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "runtime_state.json"
        )
        self._thread = None
        self._stop_event = threading.Event()
        self._running = False
        self._write_count = 0
        self._last_error = None

    def start(self):
        """启动后台写入线程"""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="RuntimeStateWriter",
            daemon=True
        )
        self._thread.start()

    def stop(self):
        """停止后台写入线程"""
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=TIMEOUT_CONFIG['hardware_probe'])

    def _run(self):
        """后台线程主循环"""
        # 延迟启动，等框架初始化完成
        time.sleep(5)
        while not self._stop_event.is_set():
            try:
                self._write_once()
            except Exception as e:
                self._last_error = str(e)
                # 不打印错误，避免污染日志
            self._stop_event.wait(self.interval)

    def _write_once(self):
        """执行一次写入"""
        state = self._collect_state()
        if not state:
            return

        # 经安全写通道（路径锁 + 退避重试 + 唯一 tmp + 失败清理），保留 default=str 兜底
        if not safe_write_json(self.output_path, state, default=str):
            self._last_error = "runtime_state.json 写入失败"
            return
        self._write_count += 1

    def _collect_state(self) -> dict:
        """收集运行时状态"""
        state = {
            "timestamp": datetime.now().isoformat(),
            "write_count": self._write_count,
        }

        # 1. 运行时指标
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            metrics_obj = get_runtime_metrics()
            if metrics_obj and hasattr(metrics_obj, "get_snapshot"):
                metrics = metrics_obj.get_snapshot()
                state["runtime_metrics"] = metrics
        except Exception:
            pass

        # 2. 自检器（代码问题统计）
        try:
            from nucleus.self_inspector import get_self_inspector
            inspector = get_self_inspector()
            if inspector:
                issues = getattr(inspector, "get_issue_summary", dict)()
                state["self_inspector"] = {
                    "total_issues": issues.get("total", 0),
                    "by_severity": issues.get("by_severity", {}),
                    "by_category": issues.get("by_category", {}),
                }
        except Exception:
            pass

        # 3. 任务管道
        try:
            from nucleus.TaskPipeline import get_recent_pipelines
            pipelines = get_recent_pipelines(limit=10)
            state["task_pipeline"] = {
                "recent_count": len(pipelines) if pipelines else 0,
                "recent": pipelines[:5] if pipelines else [],
            }
        except Exception:
            pass

        # 4. 洞察板
        try:
            from nucleus.InsightBoard import get_insight_board
            board = get_insight_board()
            if board:
                insights = getattr(board, "get_recent_insights", list)()
                state["insight_board"] = {
                    "insight_count": len(insights) if insights else 0,
                    "recent": insights[:5] if insights else [],
                }
        except Exception:
            pass

        # 5. 进化状态（从健康快照读取，避免直接访问进化引擎）
        try:
            snapshot_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "monitor", "health_snapshot.json"
            )
            if os.path.exists(snapshot_path):
                snapshot = safe_read_json(snapshot_path, default={})
                state["evolution"] = {
                    "knowledge_evolution": snapshot.get("knowledge_evolution", {}),
                    "organs": snapshot.get("organs", {}),
                    "liver": snapshot.get("liver", {}),
                }
        except Exception as e:
            print(f"[WARNING] runtime_state_writer.py:148: {type(e).__name__}: {e}")

        # 6. 参数预设列表
        try:
            from nucleus.evolution.ParamAnalysisReport import ParamPresets
            presets = getattr(ParamPresets, "list_presets", list)()
            state["param_presets"] = {
                "count": len(presets) if presets else 0,
                "names": [p.get("name", "") for p in presets[:10]] if presets else [],
            }
        except Exception:
            pass

        return state

    def get_status(self) -> dict:
        """获取写入器状态"""
        return {
            "running": self._running,
            "write_count": self._write_count,
            "interval": self.interval,
            "output_path": self.output_path,
            "last_error": self._last_error,
        }


# 单例
_writer = None
_writer_lock = threading.Lock()


def get_runtime_state_writer() -> RuntimeStateWriter:
    """获取运行时状态写入器单例"""
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = RuntimeStateWriter()
    return _writer


def start_runtime_state_writer(interval: float = 2.0):
    """启动运行时状态写入器"""
    writer = get_runtime_state_writer()
    writer.interval = interval
    writer.start()
    return writer


if __name__ == "__main__":
    # 测试
    writer = RuntimeStateWriter(interval=1)
    writer.start()
    time.sleep(5)
    writer.stop()
    print(f"写入次数: {writer._write_count}")
    if os.path.exists(writer.output_path):
        data = safe_read_json(writer.output_path, default={})
        print(f"文件大小: {os.path.getsize(writer.output_path)} bytes")
        print(f"顶层字段: {list(data.keys())}")
