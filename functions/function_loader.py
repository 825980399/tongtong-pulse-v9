"""function_loader —— 功能模块自动发现与加载器（v9.5 分层脉冲版）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import importlib
import os
import threading
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel, PulseLayer, SystemEvent


class FunctionLoader:
    """功能模块加载器（v9.5 分层脉冲版）"""

    def __init__(self, framework):
        self.framework = framework
        self._loaded_modules: dict[str, Any] = {}
        self._threads: dict[str, threading.Thread] = {}

    def _log(self, level: str, msg: str):
        """使用框架日志器输出"""
        log_level = {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }.get(level, 20)
        self.framework.logger.log(log_level, f"[FunctionLoader] {msg}")

    def scan_functions_directory(self) -> list:
        """扫描 functions/ 目录，通过导入模块获取 FUNCTION_META"""
        functions_dir = os.path.join(os.path.dirname(__file__))
        metas = []

        for func_dir in os.listdir(functions_dir):
            func_path = os.path.join(functions_dir, func_dir)
            if not os.path.isdir(func_path) or func_dir.startswith(('_', '__')):
                continue
            if func_dir in ('__pycache__',):
                continue

            for filename in os.listdir(func_path):
                if not filename.endswith('.py') or filename.startswith('_'):
                    continue

                module_name = filename[:-3]
                full_module_path = f"functions.{func_dir}.{module_name}"

                try:
                    module = importlib.import_module(full_module_path)
                    if hasattr(module, 'FUNCTION_META'):
                        meta_dict = module.FUNCTION_META
                        from functions.function_meta import FunctionMeta
                        meta = FunctionMeta(
                            name=meta_dict.get('name', module_name),
                            module_path=full_module_path,
                            class_name=meta_dict.get('class_name', module_name),
                            always_on=meta_dict.get('always_on', True),
                            feature_flag=meta_dict.get('feature_flag'),
                            thread_mode=meta_dict.get('thread_mode', 'daemon'),
                        )
                        metas.append(meta)
                        self._log(LogLevel.INFO, f"发现功能模块: {meta.name} ({full_module_path})")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"加载功能模块 {full_module_path} 失败: {e}")

        return metas

    def load_all(self, metas: list | None = None):
        """加载所有功能模块（v9.5: 所有模块均在独立线程中启动）"""
        if metas is None:
            metas = self.scan_functions_directory()

        feature = self.framework.config.FEATURE

        for meta in metas:
            if meta.feature_flag and not feature.get(meta.feature_flag, True):
                self._log(LogLevel.INFO, f"功能模块 {meta.name} 已跳过（功能开关关闭）")
                continue

            try:
                module = importlib.import_module(meta.module_path)
                func_class = getattr(module, meta.class_name)
            except Exception as e:
                self._log(LogLevel.ERROR, f"加载功能模块类失败 {meta.module_path}.{meta.class_name}: {e}")
                continue

            try:
                instance = func_class()
                if hasattr(instance, 'set_framework'):
                    instance.set_framework(self.framework)
                if hasattr(instance, 'set_info_field'):
                    instance.set_info_field(self.framework.info_field)
                if hasattr(instance, 'set_pulse_core'):
                    instance.set_pulse_core(self.framework.pulse_core)

                self._loaded_modules[meta.name] = instance

                # v9.5: 所有功能模块均在独立线程中启动，避免阻塞框架主线程
                thread = threading.Thread(
                    target=self._run_module,
                    args=(instance, meta.name),
                    daemon=True
                )
                self._threads[meta.name] = thread
                thread.start()
                self._log(LogLevel.INFO, f"功能模块 {meta.name} 已启动（{'阻塞' if meta.thread_mode == 'blocking' else '守护'}线程）")

            except Exception as e:
                self._log(LogLevel.ERROR, f"启动功能模块 {meta.name} 失败: {e}")

    def _run_module(self, instance, name: str):
        """在独立线程中运行功能模块，隔离异常"""
        try:
            instance.start()
        except Exception as e:
            self._log(LogLevel.ERROR, f"功能模块 {name} 运行时异常: {e}")
            # v9.5: 告警脉冲标记为L0生命线层
            try:
                if self.framework.info_field and self.framework.pulse_core:
                    alarm_pulse = self.framework.pulse_core.emit(
                        source_organ="FunctionLoader",
                        event_type=SystemEvent.ALARM,
                        payload={
                            "type": "function_module_error",
                            "module": name,
                            "error": str(e),
                        },
                        priority=7,
                        layer=PulseLayer.L0_LIFELINE,
                    )
                    self.framework.info_field.publish(alarm_pulse)
            except Exception as e:
                silent_exc(e, "function_loader.py:138:_run_module", level="warning")

    def stop_all(self):
        """停止所有功能模块"""
        for name, instance in self._loaded_modules.items():
            try:
                if hasattr(instance, 'stop'):
                    instance.stop()
            except Exception as e:
                self._log(LogLevel.WARNING, f"停止功能模块 {name} 时异常: {e}")

    def get_loaded_modules(self) -> list[str]:
        """返回已加载的功能模块名称列表"""
        return list(self._loaded_modules.keys())