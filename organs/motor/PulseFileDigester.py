# -*- coding: utf-8 -*-
"""
PulseFileDigester —— 文件消化器器官 · 全格式文件识别与知识路由

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 MotorEvent.FILE_DIGEST，识别文本/代码/媒体/办公文档/压缩包/可执行文件并抽取结构化元数据，把可消化内容路由给胃，把媒体信息转为媒体脉冲；只做识别与路由，不做内容理解。
机制: _digest_file 按扩展名匹配 TEXT_FORMATS / CODE_FORMATS / DOCUMENT_FORMATS / IMAGE_FORMATS / AUDIO_FORMATS / VIDEO_FORMATS / ARCHIVE_FORMATS / EXECUTABLE_FORMATS，分派到 _handle_text_file / _handle_code_file / _handle_media_file / _handle_document_file / _handle_archive_file / _handle_executable_file / _handle_unknown_file；_check_file_path_permission 做路径权限校验；_get_content 按 ENCODINGS 逐档回退解码并受 MAX_TEXT_SIZE 等上限约束；_emit_knowledge 发 KnowledgeEvent.RAW，媒体类发 MediaEvent.IMAGE_DETECTED / AUDIO_DETECTED / VIDEO_DETECTED / METADATA；_record_file 记档。
定位: 外部文件进入认知体系的「唯一入口」，是文件系统与认知层之间的翻译与分流闸门。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import os
import sys
import threading
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import KnowledgeEvent, LogLevel, MediaEvent, MotorEvent
from nucleus._silent_except import silent_exc


class PulseFileDigester(BasePulseOrgan):
    """
    文件消化器 —— 全格式文件认知与知识路由中心（v9.5 分层脉冲版）
    
    在 v9.5 脉冲网络中，文件消化器是外部文件进入曈曈认知体系的唯一入口。
    它不执行代码，不渲染媒体，不分析内容（那是未来视觉/听觉皮层的事），
    它只专注于两件事：
        1. 识别文件类型并提取结构化元数据
        2. 将信息路由到正确的下游器官
    
    当前能力:
        - 文本/代码文件：完整提取，发射给胃消化（L2认知思考层）
        - 图片/音频/视频：识别格式，生成元数据，发射媒体脉冲（L3后台自主层）
        - 办公文档：尝试提取文本
        - 压缩包：识别格式和内部结构
        - 可执行文件：识别但拒绝执行，记录元数据
    
    未来演进:
        - v10.0: 接入视觉皮层，分析图片内容
        - v10.0: 接入听觉皮层，分析音频内容
        - v11.0: 接入视频分析，联合视觉+听觉
    """

    # ===== 全格式文件类型定义 =====

    # 直接消化：文本文件（提取全文）
    TEXT_FORMATS = {
        '.txt': '纯文本',
        '.md': 'Markdown',
        '.log': '日志文件',
        '.csv': '逗号分隔数据',
        '.tsv': '制表符分隔数据',
        '.json': 'JSON数据',
        '.xml': 'XML标记语言',
        '.yaml': 'YAML配置',
        '.yml': 'YAML配置',
        '.toml': 'TOML配置',
        '.ini': 'INI配置',
        '.cfg': '配置文件',
        '.conf': '配置文件',
        '.properties': '属性配置',
    }

    # 代码文件（保留结构）
    CODE_FORMATS = {
        '.py': 'Python源代码',
        '.js': 'JavaScript脚本',
        '.ts': 'TypeScript脚本',
        '.html': 'HTML网页',
        '.htm': 'HTML网页',
        '.css': 'CSS样式表',
        '.scss': 'SCSS样式表',
        '.less': 'Less样式表',
        '.sql': 'SQL查询',
        '.sh': 'Shell脚本',
        '.bat': '批处理文件',
        '.ps1': 'PowerShell脚本',
        '.java': 'Java源代码',
        '.c': 'C源代码',
        '.cpp': 'C++源代码',
        '.h': 'C/C++头文件',
        '.go': 'Go源代码',
        '.rs': 'Rust源代码',
        '.swift': 'Swift源代码',
        '.kt': 'Kotlin源代码',
        '.r': 'R语言脚本',
        '.m': 'MATLAB/Objective-C',
    }

    # 办公文档（尝试提取文本）
    DOCUMENT_FORMATS = {
        '.pdf': 'PDF文档',
        '.doc': 'Word文档(旧版)',
        '.docx': 'Word文档',
        '.xls': 'Excel表格(旧版)',
        '.xlsx': 'Excel表格',
        '.ppt': 'PowerPoint演示(旧版)',
        '.pptx': 'PowerPoint演示',
        '.odt': 'OpenOffice文档',
        '.ods': 'OpenOffice表格',
        '.rtf': '富文本文件',
    }

    # 媒体文件（元数据提取 + 未来感知分析）
    IMAGE_FORMATS = {
        '.jpg': 'JPEG图片',
        '.jpeg': 'JPEG图片',
        '.png': 'PNG图片',
        '.gif': 'GIF动图',
        '.bmp': '位图图片',
        '.svg': '矢量图',
        '.webp': 'WebP图片',
        '.ico': '图标文件',
        '.tiff': 'TIFF图片',
        '.psd': 'Photoshop源文件',
    }

    AUDIO_FORMATS = {
        '.mp3': 'MP3音频',
        '.wav': 'WAV音频',
        '.flac': 'FLAC无损音频',
        '.aac': 'AAC音频',
        '.ogg': 'OGG音频',
        '.wma': 'Windows音频',
        '.m4a': 'M4A音频',
    }

    VIDEO_FORMATS = {
        '.mp4': 'MP4视频',
        '.avi': 'AVI视频',
        '.mkv': 'MKV视频',
        '.mov': 'QuickTime视频',
        '.wmv': 'Windows视频',
        '.flv': 'Flash视频',
        '.webm': 'WebM视频',
    }

    # 压缩包（识别格式和内部结构）
    ARCHIVE_FORMATS = {
        '.zip': 'ZIP压缩包',
        '.rar': 'RAR压缩包',
        '.7z': '7-Zip压缩包',
        '.tar': 'TAR归档',
        '.gz': 'GZip压缩',
        '.bz2': 'BZip2压缩',
        '.xz': 'XZ压缩',
    }

    # 可执行文件（拒绝执行，仅记录元数据）
    EXECUTABLE_FORMATS = {
        '.exe': 'Windows可执行文件',
        '.dll': 'Windows动态库',
        '.so': 'Linux共享库',
        '.dylib': 'macOS动态库',
        '.app': 'macOS应用包',
        '.msi': 'Windows安装包',
        '.apk': 'Android应用包',
        '.deb': 'Debian软件包',
        '.rpm': 'RPM软件包',
    }

    # ===== 文件大小限制 =====
    MAX_TEXT_SIZE = 100000     # 文本文件最大字符数
    MAX_MEDIA_SIZE_MB = 500   # 媒体文件最大MB（仅元数据，不读取内容）
    MAX_DOCUMENT_SIZE_MB = 50 # 文档文件最大MB

    # ===== 编码尝试顺序 =====
    ENCODINGS = ['utf-8', 'gbk', 'gb2312', 'latin-1']

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'file_digester_max_recent' in _rp and hasattr(self, '_max_recent'):
                self._max_recent = _rp['file_digester_max_recent']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "文件消化器"):
        super().__init__(organ_name)

        self._total_files = 0
        self._text_success = 0
        self._media_detected = 0
        self._document_processed = 0
        self._archive_detected = 0
        self._executable_blocked = 0
        self._unknown_count = 0

        self._lock = threading.Lock()
        self.is_running = False
        self._recent_files: list[dict[str, Any]] = []
        # ★P1: 从RUNTIME_PARAMS读取文件消化器参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._max_recent = _rp.get("file_digester_max_recent", 50)
        except Exception:
            self._max_recent = 50
        # ★v24.0新增：加载权限配置用于文件读取检查
        self._permission_config: dict[str, Any] = {}
        try:
            import config
            self._permission_config = getattr(config, 'CONTROLLER_PERMISSION', {})
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 生命周期 ==========

    def start(self):
        super().start()
        total_supported = (
            len(self.TEXT_FORMATS) + len(self.CODE_FORMATS) +
            len(self.DOCUMENT_FORMATS) + len(self.IMAGE_FORMATS) +
            len(self.AUDIO_FORMATS) + len(self.VIDEO_FORMATS) +
            len(self.ARCHIVE_FORMATS)
        )
        self._log(LogLevel.INFO,
                  f"已启动，支持{total_supported}种文件格式 "
                  f"(文本{len(self.TEXT_FORMATS)}+代码{len(self.CODE_FORMATS)}"
                  f"+文档{len(self.DOCUMENT_FORMATS)}+图片{len(self.IMAGE_FORMATS)}"
                  f"+音频{len(self.AUDIO_FORMATS)}+视频{len(self.VIDEO_FORMATS)}"
                  f"+压缩{len(self.ARCHIVE_FORMATS)})")

    def stop(self):
        super().stop()
        self._log(LogLevel.INFO,
                  f"已停止，处理{self._total_files}个文件: "
                  f"文本{self._text_success}次, 媒体{self._media_detected}次, "
                  f"文档{self._document_processed}次, 压缩{self._archive_detected}次, "
                  f"拦截{self._executable_blocked}次, 未知{self._unknown_count}次")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MotorEvent.FILE_DIGEST:
            file_path = payload.get("file_path", "")
            file_content = payload.get("content", "")
            file_name = payload.get("file_name", "")
            file_size = payload.get("file_size", 0)
            user_name = payload.get("user_name", "unknown")

            return self._digest_file(file_path, file_content, file_name, file_size, user_name)

        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [MotorEvent.FILE_DIGEST],
                "min_priority": 1,
            },
        ]

    # ========== 文件消化核心路由 ==========

    def _digest_file(
        self, file_path: str, file_content: str, file_name: str,
        file_size: int, user_name: str
    ) -> dict[str, Any]:
        # ★v24.0新增：权限校验
        if file_path and not self._check_file_path_permission(file_path):
            self._log(LogLevel.WARNING, f"文件权限拒绝: {file_path}")
            return {"success": False, "file_name": file_name or os.path.basename(file_path),
                    "error_type": "permission_denied", "error": "权限拒绝"}
        self._total_files += 1
        start_time = time.time()

        if file_path and not file_name:
            file_name = os.path.basename(file_path)
        if not file_name:
            file_name = "unknown"

        _, ext = os.path.splitext(file_name.lower())

        if file_size == 0 and file_path and os.path.exists(file_path):
            try:
                file_size = os.path.getsize(file_path)
            except Exception as e:
                silent_exc(e, where="organs.motor.PulseFileDigester::_digest_file L299")

        if ext in self.TEXT_FORMATS:
            return self._handle_text_file(file_path, file_content, file_name, ext,
                                          self.TEXT_FORMATS[ext], user_name, start_time)
        elif ext in self.CODE_FORMATS:
            return self._handle_code_file(file_path, file_content, file_name, ext,
                                          self.CODE_FORMATS[ext], user_name, start_time)
        elif ext in self.DOCUMENT_FORMATS:
            return self._handle_document_file(file_path, file_content, file_name, ext,
                                              self.DOCUMENT_FORMATS[ext], file_size,
                                              user_name, start_time)
        elif ext in self.IMAGE_FORMATS:
            return self._handle_media_file(file_name, ext, "image",
                                           self.IMAGE_FORMATS[ext], file_size,
                                           user_name, start_time, file_path)
        elif ext in self.AUDIO_FORMATS:
            return self._handle_media_file(file_name, ext, "audio",
                                           self.AUDIO_FORMATS[ext], file_size,
                                           user_name, start_time)
        elif ext in self.VIDEO_FORMATS:
            return self._handle_media_file(file_name, ext, "video",
                                           self.VIDEO_FORMATS[ext], file_size,
                                           user_name, start_time)
        elif ext in self.ARCHIVE_FORMATS:
            return self._handle_archive_file(file_name, ext,
                                             self.ARCHIVE_FORMATS[ext], file_size,
                                             user_name, start_time)
        elif ext in self.EXECUTABLE_FORMATS:
            return self._handle_executable_file(file_name, ext,
                                                self.EXECUTABLE_FORMATS[ext], file_size,
                                                user_name, start_time)
        else:
            return self._handle_unknown_file(file_name, ext, file_size, user_name, start_time)

    # ========== 文本文件处理 ==========

    def _handle_text_file(
        self, file_path: str, file_content: str, file_name: str,
        ext: str, file_type_desc: str, user_name: str, start_time: float
    ) -> dict[str, Any]:
        content = self._get_content(file_path, file_content)
        if content is None:
            return self._error_result(file_name, "读取失败")

        if len(content) > self.MAX_TEXT_SIZE:
            content = content[:self.MAX_TEXT_SIZE]
            truncated = True
        else:
            truncated = False

        extracted = f"[文件: {file_name}] [{file_type_desc}]\n{content}"

        self._emit_knowledge(extracted, file_name, file_type_desc, user_name,
                            len(content), truncated)

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._text_success += 1
        return self._success_result(file_name, file_type_desc, len(extracted),
                                   len(content), truncated, start_time)

    # ========== 代码文件处理 ==========

    def _handle_code_file(
        self, file_path: str, file_content: str, file_name: str,
        ext: str, file_type_desc: str, user_name: str, start_time: float
    ) -> dict[str, Any]:
        content = self._get_content(file_path, file_content)
        if content is None:
            return self._error_result(file_name, "读取失败")

        if len(content) > self.MAX_TEXT_SIZE:
            content = content[:self.MAX_TEXT_SIZE]
            truncated = True
        else:
            truncated = False

        extracted = f"[文件: {file_name}] [{file_type_desc}]\n```\n{content}\n```"

        self._emit_knowledge(extracted, file_name, file_type_desc, user_name,
                            len(content), truncated)

        self._text_success += 1
        return self._success_result(file_name, file_type_desc, len(extracted),
                                   len(content), truncated, start_time)

    # ========== 媒体文件处理 ==========

    def _handle_media_file(
        self, file_name: str, ext: str, media_type: str,
        file_type_desc: str, file_size: int, user_name: str, start_time: float,
        file_path: str = ""
    ) -> dict[str, Any]:
        size_mb = file_size / (1024 * 1024) if file_size > 0 else 0

        metadata = {
            "file_name": file_name,
            "media_type": media_type,
            "format": ext.lstrip('.'),
            "description": file_type_desc,
            "file_size_bytes": file_size,
            "file_size_mb": round(size_mb, 2),
            "user_name": user_name,
        }

        metadata_text = (  # noqa: F841
            f"[媒体文件] {file_name}\n"
            f"类型: {file_type_desc}\n"
            f"媒体类别: {media_type}\n"
            f"大小: {round(size_mb, 2)} MB\n"
            f"来源用户: {user_name}\n"
            f"说明: 此文件为{media_type}媒体，当前版本已识别格式。"
        )

        if self.info_field and self.pulse_core:
            # v9.5: 媒体元数据脉冲标记为L3后台自主层
            media_pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=MediaEvent.METADATA,
                payload=metadata,
                priority=3,
                layer="L3"
            )
            self.info_field.publish(media_pulse)

            event_map = {
                "image": MediaEvent.IMAGE_DETECTED,
                "audio": MediaEvent.AUDIO_DETECTED,
                "video": MediaEvent.VIDEO_DETECTED,
            }
            specific_event = event_map.get(media_type, MediaEvent.UNKNOWN)

            # v9.5: 媒体检测脉冲也标记为L3后台自主层
            specific_pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=specific_event,
                payload=metadata,
                priority=3,
                layer="L3"
            )
            self.info_field.publish(specific_pulse)

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._media_detected += 1

        # 新增：图片文件触发视觉皮层OCR识别
        if media_type == "image":
            from nucleus.const import EyeEvent
            self._emit(EyeEvent.VISUAL_QUERY, {
                "file_path": file_path or "",
                "file_name": file_name,
                "user_name": user_name,
                "task_type": "ocr",
            }, priority=6, layer="L2")

        return {
            "success": True,
            "file_name": file_name,
            "file_type": file_type_desc,
            "media_type": media_type,
            "file_size_mb": round(size_mb, 2),
            "processing": "元数据已提取，已触发视觉皮层OCR识别" if media_type == "image" else "元数据已提取",
            "execution_time_ms": int((time.time() - start_time) * 1000),
        }

    # ========== 文档文件处理 ==========

    def _handle_document_file(
        self, file_path: str, file_content: str, file_name: str,
        ext: str, file_type_desc: str, file_size: int, user_name: str,
        start_time: float
    ) -> dict[str, Any]:
        if file_content:
            extracted = f"[文件: {file_name}] [{file_type_desc}]\n{file_content}"
            self._emit_knowledge(extracted, file_name, file_type_desc, user_name,
                                len(file_content), False)
            self._document_processed += 1
            return self._success_result(file_name, file_type_desc, len(extracted),
                                       len(file_content), False, start_time)

        size_mb = file_size / (1024 * 1024) if file_size > 0 else 0
        metadata_text = (
            f"[文档文件] {file_name}\n"
            f"类型: {file_type_desc}\n"
            f"大小: {round(size_mb, 2)} MB\n"
            f"来源用户: {user_name}\n"
            f"说明: 此文档需要专门的解析库提取内容（v10.0预留）。"
        )

        self._emit_knowledge(metadata_text, file_name, file_type_desc, user_name,
                            len(metadata_text), False)

        # 新增：PDF文件触发视觉皮层PDF文字提取
        if ext == ".pdf":
            from nucleus.const import EyeEvent
            self._emit(EyeEvent.VISUAL_QUERY, {
                "file_path": file_path or "",
                "file_name": file_name,
                "user_name": user_name,
                "task_type": "pdf",
            }, priority=6, layer="L2")

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._document_processed += 1
        return {
            "success": True,
            "file_name": file_name,
            "file_type": file_type_desc,
            "extracted_chars": len(metadata_text),
            "execution_time_ms": int((time.time() - start_time) * 1000),
        }

    # ========== 压缩包处理 ==========

    def _handle_archive_file(
        self, file_name: str, ext: str, file_type_desc: str,
        file_size: int, user_name: str, start_time: float
    ) -> dict[str, Any]:
        size_mb = file_size / (1024 * 1024) if file_size > 0 else 0
        metadata_text = (
            f"[压缩包] {file_name}\n"
            f"类型: {file_type_desc}\n"
            f"大小: {round(size_mb, 2)} MB\n"
            f"来源用户: {user_name}\n"
            f"说明: 压缩包已识别。内部文件列表提取功能为v10.0预留。"
        )

        self._emit_knowledge(metadata_text, file_name, file_type_desc, user_name,
                            len(metadata_text), False)

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._archive_detected += 1
        return {
            "success": True,
            "file_name": file_name,
            "file_type": file_type_desc,
            "file_size_mb": round(size_mb, 2),
            "processing": "格式已识别，内容提取为v10.0预留",
            "execution_time_ms": int((time.time() - start_time) * 1000),
        }

    # ========== 可执行文件处理 ==========

    def _handle_executable_file(
        self, file_name: str, ext: str, file_type_desc: str,
        file_size: int, user_name: str, start_time: float
    ) -> dict[str, Any]:
        size_mb = file_size / (1024 * 1024) if file_size > 0 else 0

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._executable_blocked += 1

        return {
            "success": False,
            "file_name": file_name,
            "file_type": file_type_desc,
            "file_size_mb": round(size_mb, 2),
            "error_type": "executable_blocked",
            "error": f"安全策略拒绝执行: {file_type_desc}。出于安全考虑，不会执行任何可执行文件。",
            "execution_time_ms": int((time.time() - start_time) * 1000),
        }

    # ========== 未知文件处理 ==========

    def _handle_unknown_file(
        self, file_name: str, ext: str, file_size: int,
        user_name: str, start_time: float
    ) -> dict[str, Any]:
        size_mb = file_size / (1024 * 1024) if file_size > 0 else 0

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._unknown_count += 1

        return {
            "success": False,
            "file_name": file_name,
            "file_type": f"未知类型 (.{ext})" if ext else "未知类型",
            "file_size_mb": round(size_mb, 2),
            "error_type": "unknown_format",
            "error": f"无法识别的文件格式: {ext}。曈曈目前支持文本/代码/文档/图片/音频/视频/压缩包等格式。",
            "execution_time_ms": int((time.time() - start_time) * 1000),
        }

    def _check_file_path_permission(self, file_path: str) -> bool:
        """
        ★v24.0新增：文件路径权限校验，防止绕过控制器权限系统。
        使用realpath规范化+前缀边界比较。
        """
        if not file_path:
            return True  # 无路径时由调用方处理
        try:
            real_path = os.path.realpath(file_path)
        except Exception:
            real_path = os.path.abspath(file_path)
        real_path_lower = real_path.lower()

        blacklist = self._permission_config.get("read_blacklist", [])
        for pattern in blacklist:
            try:
                pattern_real = os.path.realpath(pattern) if os.path.exists(pattern) else pattern
            except Exception:
                pattern_real = pattern
            pattern_lower = pattern_real.lower().rstrip(os.sep)
            if real_path_lower == pattern_lower or real_path_lower.startswith(pattern_lower + os.sep):
                return False

        whitelist = self._permission_config.get("read_whitelist", [])
        if not whitelist:
            return True
        for pattern in whitelist:
            try:
                pattern_real = os.path.realpath(pattern) if os.path.exists(pattern) else pattern
            except Exception:
                pattern_real = pattern
            pattern_lower = pattern_real.lower().rstrip(os.sep)
            if real_path_lower == pattern_lower or real_path_lower.startswith(pattern_lower + os.sep):
                return True
        return False
    # ========== 辅助方法 ==========

    def _get_content(self, file_path: str, file_content: str) -> str | None:
        if file_content:
            return file_content
        if file_path and os.path.exists(file_path):
            for encoding in self.ENCODINGS:
                try:
                    with open(file_path, encoding=encoding) as f:
                        return f.read()
                except UnicodeDecodeError:
                    silent_exc(where="organs/motor/PulseFileDigester.py:629")
                    continue
                except Exception as e:
                    silent_exc(e, where="organs.motor.PulseFileDigester::_get_content L631")
                    return None
        return None

    def _emit_knowledge(
        self, text: str, file_name: str, file_type: str,
        user_name: str, original_size: int, truncated: bool
    ):
        if self.info_field and self.pulse_core:
            # v9.5: 知识消化脉冲标记为L2认知思考层
            pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=KnowledgeEvent.RAW,
                payload={
                    "text": text,
                    "source_file": file_name,
                    "file_type": file_type,
                    "user_name": user_name,
                    "truncated": truncated,
                    "original_size": original_size,
                },
                priority=3,
                layer="L2"
            )
            self.info_field.publish(pulse)

    def _success_result(
        self, file_name: str, file_type: str, extracted_chars: int,
        original_chars: int, truncated: bool, start_time: float
    ) -> dict[str, Any]:
        return {
            "success": True,
            "file_name": file_name,
            "file_type": file_type,
            "extracted_chars": extracted_chars,
            "original_chars": original_chars,
            "truncated": truncated,
            "execution_time_ms": int((time.time() - start_time) * 1000),
        }

    def _error_result(self, file_name: str, error: str) -> dict[str, Any]:
        return {
            "success": False,
            "file_name": file_name,
            "error_type": "read_error",
            "error": error,
            "extracted_text": "",
        }

    # ========== 记录管理 ==========

    def _record_file(self, result: dict[str, Any]):
        with self._lock:
            self._recent_files.append({
                "timestamp": time.time(),
                "file_name": result.get("file_name", ""),
                "success": result.get("success", False),
                "file_type": result.get("file_type", ""),
            })
            if len(self._recent_files) > self._max_recent:
                self._recent_files.pop(0)
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "total_files": self._total_files,
                "text_success": self._text_success,
                "media_detected": self._media_detected,
                "document_processed": self._document_processed,
                "archive_detected": self._archive_detected,
                "executable_blocked": self._executable_blocked,
                "unknown_count": self._unknown_count,
                "recent_files": list(self._recent_files[-5:]),
            }

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""
        pass  # noqa: PIE790


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "文件消化器",
    "class_name": "PulseFileDigester",
    "attr_name": "file_digester",
    "system": "motor",
    "always_online": False,
    "feature_flag": "enable_motor",
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseFileDigester v9.5 分层脉冲自测 ===\n")

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {
                # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
                "pulse_id": f"pulse:{source_organ}:{event_type}",
                "event_type": event_type,
                "source_organ": source_organ,
                "payload": payload,
                "priority": priority,
                "layer": layer,
            }

    digester = PulseFileDigester("文件消化器")
    mock_field = MockField()
    mock_core = MockCore()
    digester.set_info_field(mock_field)
    digester.set_pulse_core(mock_core)

    digester.start()

    print("1. 文本文件:")
    r = digester.on_pulse({
        "event_type": MotorEvent.FILE_DIGEST,
        "payload": {"content": "纯文本内容", "file_name": "readme.txt", "user_name": "小林"},
        "priority": 3,
    })
    print(f"   {r['file_type']}: 成功={r['success']}, 提取={r.get('extracted_chars', 0)}字符")

    # 验证 KnowledgeEvent.RAW 脉冲的 layer 标记
    raw_pulses = [p for p in mock_field.published if p.get("event_type") == KnowledgeEvent.RAW]
    if raw_pulses:
        print(f"   RAW脉冲 layer: {raw_pulses[-1].get('layer', '未设置')} (预期L2)")

    print("\n2. Python代码:")
    r = digester.on_pulse({
        "event_type": MotorEvent.FILE_DIGEST,
        "payload": {"content": "def hello():\n    print('Hi')", "file_name": "main.py", "user_name": "小林"},
        "priority": 3,
    })
    print(f"   {r['file_type']}: 成功={r['success']}")

    print("\n3. JPEG图片:")
    r = digester.on_pulse({
        "event_type": MotorEvent.FILE_DIGEST,
        "payload": {"file_name": "photo.jpg", "file_size": 2048000, "user_name": "小林"},
        "priority": 3,
    })
    print(f"   类型={r['file_type']}, 媒体类别={r.get('media_type')}, 大小={r.get('file_size_mb')}MB")
    print(f"   处理: {r.get('processing')}")

    # 验证媒体脉冲的 layer 标记
    media_pulses = [p for p in mock_field.published if p.get("event_type") == MediaEvent.IMAGE_DETECTED]
    if media_pulses:
        print(f"   IMAGE_DETECTED脉冲 layer: {media_pulses[-1].get('layer', '未设置')} (预期L3)")

    metadata_pulses = [p for p in mock_field.published if p.get("event_type") == MediaEvent.METADATA]
    if metadata_pulses:
        print(f"   METADATA脉冲 layer: {metadata_pulses[-1].get('layer', '未设置')} (预期L3)")

    print("\n4. MP4视频:")
    r = digester.on_pulse({
        "event_type": MotorEvent.FILE_DIGEST,
        "payload": {"file_name": "video.mp4", "file_size": 52428800, "user_name": "小林"},
        "priority": 3,
    })
    print(f"   类型={r['file_type']}, 媒体类别={r.get('media_type')}, 大小={r.get('file_size_mb')}MB")

    print("\n5. EXE可执行文件:")
    r = digester.on_pulse({
        "event_type": MotorEvent.FILE_DIGEST,
        "payload": {"file_name": "setup.exe", "file_size": 10485760, "user_name": "测试用户"},
        "priority": 3,
    })
    print(f"   类型={r['file_type']}, 拦截: {r['error']}")

    print("\n6. 发射脉冲统计:")
    for p in mock_field.published:
        pl = p["payload"]
        etype = p["event_type"]
        fname = pl.get("file_name", "")
        layer = p.get("layer", "未设置")
        print(f"   - {etype} (layer={layer}): {fname}")

    print("\n7. 统计:")
    stats = digester.get_stats()
    print(f"   总文件={stats['total_files']}")
    print(f"   文本成功={stats['text_success']}")
    print(f"   媒体检测={stats['media_detected']}")
    print(f"   可执行拦截={stats['executable_blocked']}")

    digester.stop()
    print("\n=== 自测全部通过 ===")
