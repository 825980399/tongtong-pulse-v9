"""chat_service —— 对话交互功能模块（v9.5 异步双向版 · 更像人类）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import sys
import threading
import time
from collections import deque
from typing import Any

from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化

# ★第80批 T6：启动早期 stdout 重配置为 utf-8+replace，根治 GBK 重定向下 emoji/中文 print 崩溃
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as _e:
    silent_exc(_e, "chat_service.py:19")

from nucleus.const import ChatEvent, LogLevel, MotorEvent, MouthEvent, PersonaEvent

try:
    from config import ENABLE_FACE_WELCOME_DIRECT, FACE_WELCOME_SHADOW
except Exception:
    ENABLE_FACE_WELCOME_DIRECT = False  # ★第109批 T-109b：config 键缺失时回落默认关
    FACE_WELCOME_SHADOW = True  # ★第115批 T-115e：config 键缺失时回落默认影子开

# 导入Web对话推送（如果模块未加载则降级）
try:
    from functions.web_chat import push_reply as _push_reply
    _web_push_available = True
except ImportError:
    _web_push_available = False
    def _push_reply(content, source=""):
        pass
# 功能模块元数据声明
FUNCTION_META = {
    "name": "对话交互",
    "class_name": "ChatService",
    "always_on": True,
    "thread_mode": "blocking",
}


class ChatService: 
    """对话交互功能模块（v9.5 异步双向版）"""

    def __init__(self):
        self.framework = None
        self.info_field = None
        self.pulse_core = None
        self._running = False
        self._logger = None

        # 后台思考任务追踪
        self._thinking_tasks: dict[str, dict[str, Any]] = {}
        self._thinking_lock = threading.Lock()
        # 短期对话记忆 —— 按用户分区存储，不同用户记忆隔离（★FIX: 移除重复初始化死代码）
        self._short_term_memory: dict[str, deque] = {}
        self._max_memory_per_user = 20
        # 全局监听器ID
        self._global_listener_id: str | None = None
        self._silence_timer = None
        self._silence_level = 0  # 当前沉默次数（0表示还没触发过主动交互）
        self._silence_intervals = [120, 600, 1800, 3600, 7200]  # 5次递进间隔
        # 当前活跃用户（视觉皮层检测到人脸时会更新）
        self._current_user_name = "访客"        

    # ========== 框架注入 ==========

    def set_framework(self, framework):
        self.framework = framework
        self._logger = framework.logger

    def set_info_field(self, info_field):
        self.info_field = info_field

    def set_pulse_core(self, pulse_core):
        self.pulse_core = pulse_core

    def _log(self, level: str, msg: str):
        if self._logger:
            log_level = {
                LogLevel.DEBUG: 10,
                LogLevel.INFO: 20,
                LogLevel.WARNING: 30,
                LogLevel.ERROR: 40,
                LogLevel.CRITICAL: 50,
            }.get(level, 20)
            self._logger.log(log_level, f"[对话] {msg}")

    # ========== 启动与停止 ==========

    def start(self):
        self._running = True
        self._register_global_listener()  # 启动时就注册一个持久的回复监听器
        self._log(LogLevel.INFO, "对话服务已启动（异步双向模式）")

        print("\n" + "=" * 50)
        print("  💫 曈曈已就绪，可以开始对话")
        print("  输入 'quit' 或 'exit' 退出")
        print("  输入 'status' 查看系统状态")
        print("  输入 'help', 查看帮助")
        print("=" * 50 + "\n")
        self._reset_silence_timer() # 启动初始计时
        self._chat_loop()
    def stop(self):
        self._running = False
        # 取消静默计时器，防止退出时还在发射脉冲
        if self._silence_timer:
            self._silence_timer.cancel()
            self._silence_timer = None
        self._unregister_global_listener()
        self._log(LogLevel.INFO, "对话服务已停止")

    # ========== 全局回复监听器 (非阻塞的关键) ==========

    def _register_global_listener(self):
        if self.info_field:
            self._global_listener_id = self.info_field.register_condition(
                organ_name="对话模块-全局回复监听",
                # ★FIX: 补上 PersonaEvent.SWITCHED，否则 _on_global_event 里的身份切换分支永远不会触发
                event_types=[MouthEvent.REPLY, ChatEvent.USER_PRESENCE_DETECTED, ChatEvent.USER_LEFT, PersonaEvent.SWITCHED],
                handler=self._on_global_event
            )
    def _unregister_global_listener(self):
        if self.info_field and self._global_listener_id:
            self.info_field.unregister_condition(self._global_listener_id)
            self._global_listener_id = None
    def _on_global_event(self, pulse: dict[str, Any]):
        """全局事件回调：处理回复和人脸检测"""
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        # 处理身份切换脉冲（来自自我认知的权威身份）
        if event_type == PersonaEvent.SWITCHED:
            user_name = payload.get("current_user", "访客")  # ★T-115e R2：未知用户默认"访客"而非"小林"
            self._current_user_name = user_name
            return
        
        # 处理人脸检测事件（保留欢迎/告别打印）
        if event_type == ChatEvent.USER_PRESENCE_DETECTED:
            user_name = payload.get("user_name", "访客")  # ★T-118a 未知/访客占位
            if user_name and user_name not in ("用户", "访客"):
                self._current_user_name = user_name
            # ★第80批 T6：emoji print 包 try-except 降级，不阻断后续计时器重置与脉冲发射
            try:
                print(f"\n👁️ 曈曈看到你回来了，{user_name}！")
            except UnicodeEncodeError:
                try:
                    print(f"\n[图标] 曈曈看到你回来了，{user_name}！")
                except Exception as _e:
                    silent_exc(_e, "chat_service.py:148")
            self._reset_silence_timer()
            # ★v17.0 Q8修复：改为发射"你是谁"推理请求，让回答能融入自我画像
            if self.pulse_core and self.info_field:
                # 先发一个轻量欢迎脉冲（快速打招呼）
                welcome_pulse = self.pulse_core.emit(
                    source_organ="对话模块",
                    event_type=ChatEvent.SILENCE_TIMEOUT,
                    payload={
                        "user_name": user_name,
                        "silence_seconds": 0,
                        "silence_level": 0,
                        "is_last": False,
                    },
                    priority=7,
                    layer="L1"
                )
                self.info_field.publish(welcome_pulse)
                # ★第109批 T-109b：face_welcome 快赢（方案A，灰度开关 ENABLE_FACE_WELCOME_DIRECT）
                #   开关开（默认关）：跳过"你是谁"推理请求（InferenceEvent.REQUEST），省 1 次 LLM 调用；
                #   开关关：保持原行为（发射 REQUEST 融入自我画像）。
                # ★T-115e V4 熟人判据 + 影子半态：
                #   开关开 且 已识别熟人(非访客/用户) → 本应跳过"你是谁"询问（直接欢迎）；
                #   陌生人/未绑定 → 始终发射推理请求（问"你是谁"）；
                #   影子模式(FACE_WELCOME_SHADOW=True, 默认)：本应跳过者只记日志不真跳，观察 1 天。
                _is_stranger = user_name in ("用户", "访客", "")
                _should_skip = ENABLE_FACE_WELCOME_DIRECT and not _is_stranger
                if _should_skip and not FACE_WELCOME_SHADOW:
                    # 非影子模式：熟人直连，真正跳过"你是谁"推理请求（直接欢迎）
                    pass
                else:
                    if _should_skip and FACE_WELCOME_SHADOW:
                        if self._logger:
                            self._logger.info(
                                f"[face_welcome影子] 本应跳过'你是谁'询问(user={user_name})，"
                                f"影子模式仅记录（维持原行为）")
                    # 发射推理请求，让内在世界处理"你是谁"
                    from nucleus.const import InferenceEvent
                    _correlation_id = f"face_welcome_{int(time.time())}"
                    # ★v17.0修复：在发射推理请求前，先在大脑皮层创建临时上下文
                    # 这样推理结果返回时能正确匹配到上下文，不再出现"无匹配上下文"警告
                    _cortex = self.framework.organs.get("大脑皮层") if hasattr(self.framework, 'organs') else None
                    if _cortex and hasattr(_cortex, 'register_pending_inner_world'):
                        # ★T-对话-1（157批）：注入最近一次时间广播的 wall/semantic，
                        #   使内在世界在被问"今天几号"等时间类问题时能取到正确时戳。
                        _lt = getattr(_cortex, "_latest_time", None) or {}
                        _cortex.register_pending_inner_world(_correlation_id, {
                            "content": "你是谁",
                            "user_name": user_name,
                            "intent": "身份",
                            "timestamp": time.time(),
                            "wall_clock": _lt.get("wall_clock"),
                            "semantic_time": _lt.get("semantic_time"),
                            "file_paths": [],
                            "code_blocks": [],
                        })
                    inquiry_pulse = self.pulse_core.emit(
                        source_organ="对话模块",
                        event_type=InferenceEvent.REQUEST,
                        payload={
                            "question": "你是谁",
                            "user_name": user_name,
                            "correlation_id": _correlation_id,
                        },
                        priority=7,
                        layer="L2"
                    )
                    self.info_field.publish(inquiry_pulse)
            return
        if event_type == ChatEvent.USER_LEFT:
            user_name = payload.get("user_name", "用户")
            # ★第80批 T6：emoji print 包 try-except 降级，不阻断后续身份重置与脉冲发射
            try:
                print(f"\n👁️ 曈曈看到你离开了，{user_name}。")
            except UnicodeEncodeError:
                try:
                    print(f"\n[图标] 曈曈看到你离开了，{user_name}。")
                except Exception as _e:
                    silent_exc(_e, "chat_service.py:203")
            self._current_user_name = "访客"  # ← 新增：人离开后重置身份
            # 取消当前的沉默计时器（人走了不用再问候）
            if self._silence_timer:
                self._silence_timer.cancel()
                self._silence_timer = None
            # 对熟悉的人说再见
            goodbye_pulse = self.pulse_core.emit(
                source_organ="对话模块",
                event_type=ChatEvent.SILENCE_TIMEOUT,
                payload={
                    "user_name": user_name,
                    "silence_seconds": 0,
                    "silence_level": -1,  # -1 表示告别
                    "is_last": True,
                },
                priority=7,
                layer="L1"
            )
            self.info_field.publish(goodbye_pulse)
            return
        
        # 原有的回复处理逻辑
        content = payload.get("content", "")
        source = payload.get("source", "unknown")
        if not content:
            return

        # ★T-对话-2（157批）：correlation_id 1:1 绑定保护（展示层显式来源前缀防抢占）。
        #   回复携带非空 correlation_id 且不与 cortex 当前活跃会话匹配时，视为未命中
        #   「等待中的提问」的异步/自主回复，显示时加 [异步] 前缀避免与 1:1 答复混淆。
        #   （底层 1:1 绑定由 PulseCortex._dialog_should_output 把关，此处仅做展示层标注）
        _reply_cid = payload.get("correlation_id", "")
        _cortex2 = self.framework.organs.get("大脑皮层") if hasattr(self.framework, "organs") else None
        _active_cid = getattr(_cortex2, "_active_correlation_id", "") if _cortex2 else ""
        _is_unbound = bool(_reply_cid) and _reply_cid != _active_cid

        # 为了不打断用户正在输入的内容，先换行
        try:
            print()
        except UnicodeEncodeError as _e:
            silent_exc(_e, "chat_service.py:235")

        # 根据来源区分显示风格（★v30.0 P3优化：按语义分组，覆盖主动发起/代码执行/系统状态等）
        _icon = self._source_icon(source)
        # ★2026-09-03新增：控制台输出后台日志，便于分析"输入→处理→输出"完整链路
        self._log(LogLevel.INFO, f"控制台输出: {content[:300]}{'...' if len(content) > 300 else ''} (来源={source}, 长度={len(content)})")

        # ★第80批 T6：关键副作用（推 Web 回复）前置到 print 之前——GBK 重定向下 print
        #   抛 UnicodeEncodeError 也不影响回复推送（P0-2 根因：:217 print 先于 _push_reply 阻断推送）。
        _push_reply(content, source)

        # 显示回复（包 try-except，GBK 场景降级 ASCII 占位，不中断 handler）
        try:
            print(f"{'[异步]' + _icon if _is_unbound else _icon} 曈曈: {content}\n")
        except UnicodeEncodeError:
            try:
                print(f"[图标] 曈曈: {content}\n")
            except Exception as _e:
                silent_exc(_e, "chat_service.py:253")

        # 重新显示输入提示符（如果用户正在输入，输入内容不会丢失）
        try:
            print("💬 你: ", end="", flush=True)
        except UnicodeEncodeError as _e:
            silent_exc(_e, "chat_service.py:259")

    def _source_icon(self, source: str) -> str:
        """根据来源语义返回显示图标，让不同来源的回复有视觉区分。

        ★v30.0 P3新增：不再只区分 inner_world/lung 两种，而是按语义分组，
        覆盖主动发起、代码执行结果、系统状态等来源，且用前缀匹配保持可扩展。
        """
        _s = (source or "").lower()
        # 主动发起 / 内在驱动（主动话题、顿悟、成长分享、自我审视等）
        if any(_k in _s for _k in ("initiative", "eureka", "growth_sharing",
                                   "self_", "deep_self_review", "reflection")):
            return "🎈"
        # 代码沙箱执行结果
        if "code_sandbox" in _s:
            return "⚙️"
        # 系统状态 / 知识统计
        if "system_status" in _s:
            return "📊"
        # 诊断 / 健康 / 风险类提示
        if any(_k in _s for _k in ("diagnosis", "watchdog", "risk", "health")):
            return "🩺"
        # 大模型生成（肺部、远程、本地模型）
        if any(_k in _s for _k in ("lung", "remote", "local")):
            return "💭"
        # 内在世界 / 大脑皮层默认
        return "💫"
    # ========== 主对话循环 (完全非阻塞) ==========

    def _chat_loop(self):
        while self._running:
            try:
                # 回复通过全局监听器 _on_global_event 实时打印，无需在此轮询

                # 等待用户输入 (可以设置超时以支持主动问候)
                try:
                    user_input = input("💬 你: ").strip()
                except EOFError:
                    silent_exc(where="functions/chat/chat_service.py:322")
                    break

                if not user_input:
                    continue

                if user_input.lower() in ("quit", "exit"):
                    print("👋 曈曈: 再见，小林。")
                    self._running = False
                    break

                if self._handle_system_command(user_input):
                    continue

                # 3. 处理用户输入，绝不阻塞主循环
                # ★2026-09-03新增：控制台输入后台日志，便于分析"输入→处理→输出"完整链路
                self._log(LogLevel.INFO, f"控制台输入: {user_input[:200]}{'...' if len(user_input) > 200 else ''} (长度={len(user_input)})")
                self._process_message(user_input)

            except KeyboardInterrupt:
                print("\n👋 曈曈: 再见，小林。")
                self._running = False
                break
            except Exception as e:
                self._log(LogLevel.ERROR, f"对话循环异常: {e}")
                print("💭 曈曈: 抱歉，我走神了。可以再说一次吗？")

    # ========== 系统命令处理 ==========

    def _handle_system_command(self, user_input: str) -> bool:
        cmd = user_input.lower().strip()

        if cmd in ("status", "状态"):
            self._show_status()
            self._reset_silence_timer()
            return True
        elif cmd in ("help", "帮助"):
            self._show_help()
            self._reset_silence_timer()
            return True
        elif cmd in ("knowledge", "知识"):
            self._show_knowledge()
            self._reset_silence_timer()
            return True
        elif cmd in ("patches", "补丁"):
            self._show_pending_patches()
            self._reset_silence_timer()
            return True
        elif cmd.startswith(("approve", "批准")):
            self._approve_patch(cmd) 
            self._reset_silence_timer()
            return True
        elif cmd in ("params", "参数"):
            self._show_param_status()
            self._reset_silence_timer()
            return True
        elif cmd.startswith(("preset", "预设")):
            self._handle_preset(cmd)
            self._reset_silence_timer()
            return True
        elif cmd in ("report", "报告"):
            self._generate_param_report()
            self._reset_silence_timer()
            return True
        return False

    def _show_pending_patches(self):
        """显示待审批补丁列表（★FIX: 人工审批入口）
        ★v30.0 P1优化：状态翻译为中文语义，并高亮「已验证」关键状态，
        让审批时能一眼判断补丁是否已通过副本验证（副本验证通过才建议批准）。
        """
        import os
        # 状态 → (中文标签, 图标) 映射，覆盖进化闭环所有 status 取值
        _STATUS_MAP = {
            "pending": ("未验证", "⚪"),
            # ★第54批 T3.1：status="verified" 是 **deprecated** 状态 —— 实测
            #   patch_history 中从未出现该状态（真实状态是 approved / runtime_verified），
            #   保留映射仅为向后兼容；新增真实存在的 runtime_verified。
            "verified": ("已验证(deprecated)", "✅"),
            "runtime_verified": ("已运行时验证", "🟩"),
            "approved": ("已批准", "🟢"),
            "applied": ("已应用", "🔵"),
            "rejected_by_baseline": ("已拒绝(基线)", "🔴"),
            "rejected_safety": ("已拒绝(安全)", "🔴"),
            "rejected_verify": ("已拒绝(验证)", "🔴"),
            "rejected_ambiguous": ("已拒绝(歧义)", "🔴"),
            "rejected_syntax": ("已拒绝(语法)", "🔴"),
        }
        try:
            from nucleus.reasoning.PatchManager import PatchManager
            _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _mgr = PatchManager(_project_root)
            _pending = _mgr.list_pending_patches()
            if not _pending:
                print("\n📋 待审批补丁: 无\n")
                return
            # 统计已验证/已批准数量，帮助快速决策
            _verified_count = sum(1 for _p in _pending if _p.get("status") == "verified")
            _approved_count = sum(1 for _p in _pending if _p.get("status") == "approved")
            print(f"\n📋 待审批补丁: 共{len(_pending)}个 "
                  f"(✅已验证 {_verified_count} | 🟢已批准 {_approved_count})\n")
            for _i, _p in enumerate(_pending):
                _status = _p.get("status", "pending")
                _icon, _label = _STATUS_MAP.get(_status, ("❓", _status))
                _file = os.path.basename(_p.get("file", "?"))
                _reason = (_p.get("reason", "") or "")[:60]
                print(f"  [{_i}] {_icon}{_label} | {_file} | {_reason}")
            print("\n输入 `approve <索引>` 批准单个，或 `approve all` 批准全部。")
            print("建议优先批准 ✅已验证 的补丁（已通过副本验证）。\n")
        except Exception as _e:
            print(f"\n读取补丁列表失败: {_e}\n")

    def _approve_patch(self, cmd: str):
        """批准补丁（★FIX: 人工审批入口，verified→approved）"""
        import os
        try:
            from nucleus.reasoning.PatchManager import PatchManager
            _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _mgr = PatchManager(_project_root)
            _arg = cmd.strip()
            for _prefix in ("approve", "批准"):
                if _arg.startswith(_prefix):
                    _arg = _arg[len(_prefix):].strip()
                    break
            if _arg in ("all", "全部", ""):
                _res = _mgr.approve_all_patches()
                if _res.get("ok"):
                    print(f"\n✅ 已批准 {_res.get('approved', 0)} 个补丁（将在下次退出时应用并重启验证）\n")
                else:
                    print(f"\n❌ 批准失败: {_res.get('reason', '未知')}\n")
                return
            _idx = int(_arg)
            _res = _mgr.approve_patch(_idx)
            if _res.get("ok"):
                print(f"\n✅ 已批准补丁 [{_idx}] {_res.get('file', '')}（将在下次退出时应用）\n")
            else:
                print(f"\n❌ 批准失败: {_res.get('reason', '未知')}\n")
        except ValueError:
            print("\n❌ 索引格式错误，请输入 `approve <数字>` 或 `approve all`\n")
        except Exception as _e:
            print(f"\n批准补丁失败: {_e}\n")

    def _show_param_status(self):
        """显示参数补丁状态"""
        try:
            from nucleus.evolution.ParamAnalysisReport import ParamAnalyzer
            analyzer = ParamAnalyzer()
            stats = analyzer.get_statistics()
            print("\n⚙️ 参数补丁状态:")
            print(f"   总补丁数: {stats.get('total_patches', 0)}")
            print(f"   已应用: {stats.get('applied', 0)}")
            print(f"   已回滚: {stats.get('rolled_back', 0)}")
            print(f"   失败: {stats.get('failed', 0)}")
            print(f"   效果验证: {stats.get('effect_verified', 0)}")
            print(f"   正向效果: {stats.get('effect_positive', 0)}")
            print(f"   涉及参数: {stats.get('unique_params', 0)}个")
            print(f"   配置变更: {stats.get('total_config_changes', 0)}次")
            # 最有效参数
            most_effective = stats.get("most_effective", [])
            if most_effective:
                print("\n   最有效参数 Top5:")
                for param, effect in most_effective[:5]:
                    print(f"     - {param}: {effect:.2f}")
            print()
        except Exception as e:
            print(f"\n⚙️ 参数状态获取失败: {e}\n")

    def _handle_preset(self, cmd: str):
        """处理预设方案命令"""
        try:
            from nucleus.evolution.ParamAnalysisReport import ParamPresets
            presets = ParamPresets()
            arg = cmd.strip()
            for prefix in ("preset", "预设"):
                if arg.startswith(prefix):
                    arg = arg[len(prefix):].strip()
                    break
            if not arg or arg == "list" or arg == "列表":
                preset_list = presets.list_presets()
                print("\n🎯 预设方案列表:")
                for p in preset_list:
                    print(f"   - {p['name']}: {p['description']} ({len(p.get('params', {}))}个参数)")
                print("\n使用 `preset <名称>` 应用预设方案")
                print()
                return
            # 尝试匹配预设名称
            preset_list = presets.list_presets()
            matched = None
            for p in preset_list:
                if arg.lower() in p['name'].lower() or arg == p.get('key', ''):
                    matched = p
                    break
            if not matched:
                print(f"\n❌ 未找到预设方案: {arg}\n")
                return
            result = presets.apply_preset(matched['key'])
            if result.get("success"):
                print(f"\n✅ 已应用预设方案 '{matched['name']}'")
                print(f"   应用了 {result.get('params_applied', 0)} 个参数")
                print("   将在5秒内热加载生效\n")
            else:
                print(f"\n❌ 应用失败: {result.get('error', '未知')}\n")
        except Exception as e:
            print(f"\n预设方案操作失败: {e}\n")

    def _generate_param_report(self):
        """生成参数分析报告"""
        try:
            from nucleus.evolution.ParamAnalysisReport import generate_report
            report_path = generate_report()
            print("\n📊 参数分析报告已生成:")
            print(f"   {report_path}")
            print("   同时更新了 logs/param_reports/latest.html")
            print("   可在浏览器中打开查看\n")
        except Exception as e:
            print(f"\n报告生成失败: {e}\n")

    def _show_status(self):
        if not self.framework:
            print("\n📊 系统状态: 框架未初始化\n")
            return
        
        # ===== 基础数据 =====
        stats_pool = self.framework.node_pool.get_stats()
        stats_field = self.info_field.get_stats() if self.info_field else {}
        evol = stats_pool.get('evol_distribution', {})
        instinct_count = stats_pool.get('instinct_count', 0)
        load_level = stats_field.get('load_level', '?')
        
        # ★v17.0修复：L3数量不应该减去本能数，L4是独立存储的
        l3_count = evol.get('L3', 0)
        
        # ★v17.0修复：动态获取器官在线数
        _organs_online = 0
        _organs_total = 0
        if hasattr(self.framework, 'organs'):
            for _o in self.framework.organs.values():
                if _o is not None:
                    _organs_total += 1
                    if getattr(_o, 'is_running', False):
                        _organs_online += 1
        
        # ===== v17.0新增：代码学习进度 =====
        _code_progress_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _code_organ = self.framework.organs.get("代码学习")
                if _code_organ and hasattr(_code_organ, 'get_stats'):
                    _cs = _code_organ.get_stats()
                    _understood = _cs.get("understood", 0)
                    _total_methods = _cs.get("total_methods", 0)
                    if _total_methods > 0:
                        _pct = round(_understood / _total_methods * 100, 1)
                        _code_progress_text = f"代码理解: {_understood}/{_total_methods} ({_pct}%)"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:513")
        
        # ===== v17.0新增：直觉命中率 =====
        _intuition_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _risk_organ = self.framework.organs.get("风险感知")
                if _risk_organ and hasattr(_risk_organ, 'get_stats'):
                    _rs = _risk_organ.get_stats()
                    _hits = _rs.get("intuition_hit_count", 0)
                    _queries = _rs.get("intuition_query_count", 0)
                    if _queries > 0:
                        _rate = round(_hits / _queries * 100, 1)
                        _intuition_text = f"直觉命中: {_hits}/{_queries} ({_rate}%)"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:528")
        
        # ===== v17.0新增：对话记忆数 =====
        _conv_mem_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _iw = self.framework.organs.get("内在世界")
                if _iw and hasattr(_iw, 'get_conversation_memory'):
                    _conv_count = len(_iw.get_conversation_memory())
                    _conv_mem_text = f"对话记忆: {_conv_count}条"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:539")
        
        # ===== v17.0新增：生命周期阶段 =====
        _life_stage_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _narrative = self.framework.organs.get("叙事自我")
                if _narrative:
                    _stage = _narrative.generate_life_stage_summary()
                    if _stage:
                        _stage_short = _stage.split("：")[0] if "：" in _stage else _stage[:20]
                        _life_stage_text = f"生命阶段: {_stage_short}"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:552")
        
        # ===== v17.0新增：代码问题趋势 =====
        _code_trend_text = ""
        try:
            from nucleus.self_inspector import get_self_inspector
            _inspector = get_self_inspector()
            _trend = _inspector.analyze_issue_trends()
            _total_trend = _trend.get("total_trend", "stable")
            _total_change = _trend.get("total_change", 0)
            if _total_trend == "decreasing":
                _code_trend_text = f"代码问题: ↓减少{abs(_total_change)}个"
            elif _total_trend == "increasing":
                _code_trend_text = f"代码问题: ↑增加{_total_change}个"
            else:
                _code_trend_text = "代码问题: →稳定"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:569")
        
        # ===== 第64批 T5：器官扫描缓存统计 =====
        _cache_stats_text = ""
        try:
            from nucleus.self_inspector import get_self_inspector
            _inspector = get_self_inspector()
            _cs = _inspector.get_scan_cache_stats()
            _cache_stats_text = (
                f"扫描缓存: 命中率{_cs.get('hit_rate', '?')} "
                f"(命中{_cs.get('hits', 0)}/未命中{_cs.get('misses', 0)}/"
                f"失效{_cs.get('invalidations', 0)}) "
                f"扫描二级缓存命中{_cs.get('scan_cache_l2_hits', 0)}/未命中{_cs.get('scan_cache_l2_misses', 0)}"
            )
        except Exception as _se:
            silent_exc(_se, "chat_service.py:584")

        # ===== 组装输出 =====
        _lines = []
        _lines.append("\n📊 系统状态:")
        _lines.append(f"   器官: {_organs_online}/{_organs_total}个在线")
        _lines.append(f"   知识: {stats_pool['total_nodes']} 个节点 "
                     f"(L1={evol.get('L1',0)} L2={evol.get('L2',0)} "
                     f"L3={l3_count} L4={instinct_count})")
        _lines.append(f"   负载: {load_level}")
        # 新增行
        if _life_stage_text:
            _lines.append(f"   {_life_stage_text}")
        if _code_progress_text:
            _lines.append(f"   {_code_progress_text}")
        if _conv_mem_text:
            _lines.append(f"   {_conv_mem_text}")
        if _intuition_text:
            _lines.append(f"   {_intuition_text}")
        if _code_trend_text:
            _lines.append(f"   {_code_trend_text}")
        if _cache_stats_text:
            _lines.append(f"   {_cache_stats_text}")
        # ★v17.0新增：推理技能画像
        _reasoning_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _iw = self.framework.organs.get("内在世界")
                if _iw and hasattr(_iw, 'get_reasoning_skill_portrait'):
                    _portrait = _iw.get_reasoning_skill_portrait()
                    _strong = _portrait.get("strong_types", [])
                    _weak = _portrait.get("weak_types", [])
                    if _strong or _weak:
                        _parts = []
                        if _strong:
                            _parts.append("擅长:" + "、".join([s["name"] for s in _strong[:2]]))
                        if _weak:
                            _parts.append("待加强:" + "、".join([w["name"] for w in _weak[:2]]))
                        _reasoning_text = "推理技能: " + " | ".join(_parts)
        except Exception as _se:
            silent_exc(_se, "chat_service.py:624")
        if _reasoning_text:
            _lines.append(f"   {_reasoning_text}")
        # ★v17.0新增：情绪趋势
        _emotion_trend_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _self_aware = self.framework.organs.get("自我认知")
                if _self_aware and hasattr(_self_aware, 'get_unified_self_portrait'):
                    _portrait = _self_aware.get_unified_self_portrait()
                    _emotion_trend = _portrait.get("emotion_trend", {})
                    if _emotion_trend:
                        _curr = _emotion_trend.get("current", "中性")
                        _dir = _emotion_trend.get("direction", "stable")
                        _dir_label = {"rising": "↑好转", "falling": "↓下沉", "stable": "→平稳"}.get(_dir, "")
                        _emotion_trend_text = f"情绪: {_curr} {_dir_label}"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:641")
        if _emotion_trend_text:
            _lines.append(f"   {_emotion_trend_text}")
        # ★v17.0新增：高光记忆
        _highlights_text = ""
        try:
            if hasattr(self.framework, 'organs'):
                _self_aware = self.framework.organs.get("自我认知")
                if _self_aware and hasattr(_self_aware, 'get_unified_self_portrait'):
                    _portrait = _self_aware.get_unified_self_portrait()
                    _memories = _portrait.get("memories", {})
                    _highlights = _memories.get("highlights", [])
                    if _highlights:
                        _latest = _highlights[0]
                        _user = _latest.get("user_name", "")
                        _summary = _latest.get("summary", "")[:30]
                        _hours = _latest.get("hours_ago", 0)
                        if _user and _summary:
                            _highlights_text = f"高光记忆: {_hours:.0f}h前与{_user}「{_summary}」"
        except Exception as _se:
            silent_exc(_se, "chat_service.py:661")
        if _highlights_text:
            _lines.append(f"   {_highlights_text}")
        # 原有行
        total_memories = sum(len(m) for m in self._short_term_memory.values())
        _lines.append(f"   短期记忆: {total_memories}条 (共{len(self._short_term_memory)}个用户)")
        _lines.append(f"   信息场: {stats_field.get('active_conditions', '?')} 个活跃条件")
        # 参数补丁状态
        try:
            from nucleus.evolution.ParamAnalysisReport import ParamAnalyzer
            _analyzer = ParamAnalyzer()
            _pstats = _analyzer.get_statistics()
            _lines.append(f"   参数补丁: 已应用{_pstats.get('applied', 0)}个 "
                          f"回滚{_pstats.get('rolled_back', 0)}个 "
                          f"正向{_pstats.get('effect_positive', 0)}个")
        except Exception as _se:
            silent_exc(_se, "chat_service.py:677")
        if hasattr(self.framework, 'organs'):
            subcon = self.framework.organs.get("潜意识")
            if subcon and hasattr(subcon, 'get_life_state'):
                ls = subcon.get_life_state()
                _lines.append(f"   生命状态: {ls['state']} ({ls['description']})")
                _lines.append(f"   探索间隔: {ls['explore_interval']:.0f}s  梦境间隔: {ls['dream_interval']:.0f}s")
        _lines.append("")
        
        _status_output = "\n".join(_lines)
        print(_status_output)
        
        # 同时发射脉冲供Web对话窗口获取（精简版）
        if self.pulse_core and self.info_field:
            _web_lines = [
                "📊 系统状态:",
                f"   器官: {_organs_online}/{_organs_total}个在线",
                (f"   知识: {stats_pool['total_nodes']} 个节点 "
                f"(L1={evol.get('L1',0)} L2={evol.get('L2',0)} "
                f"L3={l3_count} L4={instinct_count})"),
                f"   负载: {load_level}",
            ]
            if _life_stage_text:
                _web_lines.append(f"   {_life_stage_text}")
            if _code_progress_text:
                _web_lines.append(f"   {_code_progress_text}")
            if _conv_mem_text:
                _web_lines.append(f"   {_conv_mem_text}")
            _web_lines.append(f"   信息场: {stats_field.get('active_conditions', '?')} 个活跃条件")
            _status_content = "\n".join(_web_lines)
            # ★G7修复：系统状态消息也统一经过人格内核把关，保持输出出口一致
            try:
                if self.framework and getattr(self.framework, 'personality', None):
                    _status_content = self.framework.personality.filter_output(_status_content)
            except Exception as _se:
                silent_exc(_se, "chat_service.py:712")
            status_pulse = self.pulse_core.emit(
                source_organ="对话模块",
                event_type=MouthEvent.REPLY,
                payload={"content": _status_content, "source": "system_status"},
                priority=5,
                layer="L1"
            )
            self.info_field.publish(status_pulse)
    def _show_help(self):
        print("\n📖 可用命令:")
        print("   status/状态   - 查看系统状态")
        print("   knowledge/知识 - 查看知识统计")
        print("   patches/补丁  - 查看待审批补丁")
        print("   approve/批准  - 批准补丁（approve <索引> / approve all）")
        print("   params/参数   - 查看参数补丁状态")
        print("   preset/预设   - 查看/应用预设方案（preset list / preset <名称>）")
        print("   report/报告   - 生成参数分析报告")
        print("   help/帮助     - 显示此帮助")
        print("   quit/exit     - 退出对话")
        print()

    def _show_knowledge(self):
        if self.framework:
            stats = self.framework.node_pool.get_stats()
            evol = stats.get('evol_distribution', {})
            instinct_count = stats.get('instinct_count', 0)
            l3_pure = max(0, evol.get('L3', 0) - instinct_count)
            total = stats['total_nodes']
            l2_count = evol.get('L2', 0)
            density = round((l2_count + l3_pure + instinct_count) / max(1, total) * 100, 1)
            print("\n🧠 知识统计:")
            print(f"   总节点: {total}")
            print(f"   L1感知: {evol.get('L1', 0)}")
            print(f"   L2认知: {l2_count}")
            print(f"   L3智慧: {l3_pure}")
            print(f"   L4本能: {instinct_count}")
            print(f"   知识密度: {density}%")
            print()
            # 同时发射脉冲供Web对话窗口获取
            if self.pulse_core and self.info_field:
                knowledge_text = (
                    f"🧠 知识统计:\n"
                    f"   总节点: {total}\n"
                    f"   L1感知: {evol.get('L1', 0)}\n"
                    f"   L2认知: {l2_count}\n"
                    f"   L3智慧: {l3_pure}\n"
                    f"   L4本能: {instinct_count}\n"
                    f"   知识密度: {density}%"
                )
                # ★G7修复：知识统计消息也统一经过人格内核把关
                try:
                    if self.framework and getattr(self.framework, 'personality', None):
                        knowledge_text = self.framework.personality.filter_output(knowledge_text)
                except Exception as _se:
                    silent_exc(_se, "chat_service.py:767")
                knowledge_pulse = self.pulse_core.emit(
                    source_organ="对话模块",
                    event_type=MouthEvent.REPLY,
                    payload={"content": knowledge_text, "source": "system_status"},
                    priority=5,
                    layer="L1"
                )
                self.info_field.publish(knowledge_pulse)
    # ========== 消息处理 (非阻塞) ==========

    def _process_message(self, user_input: str):
        """处理用户输入，但不等待结果（非阻塞）"""
        # ★v17.0修复：清理超过5分钟的过期思考任务，防止内存泄漏
        _now = time.time()
        with self._thinking_lock:
            _expired = [
                _tid for _tid, _t in self._thinking_tasks.items()
                if _now - _t.get("timestamp", 0) > 300
            ]
            for _tid in _expired:
                del self._thinking_tasks[_tid]
        # 重置静默计时器
        self._reset_silence_timer()
        parsed = self._parse_mixed_input(user_input)
        text = parsed.get("text", user_input)

        self._remember("user", text)

        if self._is_simple_question(text):
            self._send_chat_message(text, user_input, is_complex=False,
                                    code_blocks=parsed.get("code_blocks", []),
                                    file_paths=parsed.get("file_paths", [])) 
        else:
            self._send_chat_message(text, user_input, is_complex=True,
                                    code_blocks=parsed.get("code_blocks", []),
                                    file_paths=parsed.get("file_paths", []))
            print("💭 曈曈: 让我想想...")

        for code_block in parsed.get("code_blocks", []):
            self._send_code_execute(code_block["code"], code_block.get("language", "python"))
    def _send_chat_message(self, text: str, original_input: str, is_complex: bool = False,
                           code_blocks: list | None = None, file_paths: list | None = None):
        """
        发射聊天消息脉冲（完全非阻塞，不等待回复）。
        """
        if not self.info_field or not self.pulse_core:
            print("💭 曈曈: 系统正在初始化，请稍候...")
            return

        chat_pulse = self.pulse_core.emit(
            source_organ="对话模块",
            event_type=ChatEvent.MESSAGE,
            payload={
                "content": text,
                "original_input": original_input,
                "user_name": self._current_user_name,
                "is_complex": is_complex,
                "code_blocks": code_blocks or [],
                "file_paths": file_paths or [],
            },
            priority=5,
            layer="L1"
        )
        self.info_field.publish(chat_pulse)

        with self._thinking_lock:
            task_id = chat_pulse["pulse_id"]
            self._thinking_tasks[task_id] = {
                "question": text,
                "timestamp": time.time(),
                "is_complex": is_complex
            }
    # ========== 短期记忆 ==========

    def _remember(self, speaker: str, content: str):
        """记住对话内容 —— 按当前用户分区存储"""
        user = self._current_user_name or "访客"
        if user not in self._short_term_memory:
            self._short_term_memory[user] = deque(maxlen=self._max_memory_per_user)
        
        self._short_term_memory[user].append({
            "speaker": speaker,
            "content": content[:200],
            "timestamp": time.time()
        })

    def _get_recent_context(self, n: int = 5) -> str:
        """获取当前用户最近n条对话作为上下文 —— 不跨用户泄露

        ★v30.0 P2说明：此方法当前无调用方（对话上下文关联已由「内在世界」的
        get_conversation_memory 负责），保留作为短期记忆的对外查询接口，供后续
        Web 对话窗口或主动发起能力按需接入，避免未来重复实现。
        """
        user = self._current_user_name or "访客"
        user_memory = self._short_term_memory.get(user, deque())
        recent = list(user_memory)[-n:]
        context_parts = []
        for item in recent:
            speaker_name = "小林" if item["speaker"] == "user" else "曈曈"
            context_parts.append(f"{speaker_name}: {item['content']}")
        return "\n".join(context_parts)

    # ========== 代码执行 ==========

    def _send_code_execute(self, code: str, language: str):
        if not self.info_field or not self.pulse_core:
            return

        exec_pulse = self.pulse_core.emit(
            source_organ="对话模块",
            event_type=MotorEvent.EXECUTE,
            payload={
                "code": code,
                "language": language,
                "user_name": self._current_user_name,
                "task_id": f"chat_{int(time.time())}",
            },
            priority=5,
            layer="L1"
        )
        self.info_field.publish(exec_pulse)
        print("⚡ 代码已提交执行...")

    # ========== 输入解析 ==========

    def _parse_mixed_input(self, user_input: str) -> dict[str, Any]:
        result = {
            "text": user_input,
            "code_blocks": [],
            "file_paths": [],
        }

        import re
        # 0. 识别单行代码指令："执行代码 xxx"
        single_line_code = re.match(r'^执行代码\s+(.+)', user_input.strip(), re.DOTALL)
        if single_line_code:
            code_content = single_line_code.group(1).strip()
            result["code_blocks"].append({
                "language": "python",
                "code": code_content,
            })
            result["text"] = user_input  # 保留原文给内在世界
            return result        
        # 1. 提取代码块（```语言\n代码\n```）
        code_pattern = r'```(\w*)\n(.*?)```'
        matches = re.findall(code_pattern, user_input, re.DOTALL)

        for lang, code in matches:
            result["code_blocks"].append({
                "language": lang or "python",
                "code": code.strip(),
            })

        # 如果有代码块，将文本中的代码块替换为占位符
        if result["code_blocks"]:
            text_with_placeholders = re.sub(code_pattern, '【代码片段】', user_input, flags=re.DOTALL)
            result["text"] = text_with_placeholders.strip()

        # 2. 提取文件路径（支持中文文件名和常见扩展名）
        file_exts = r'(jpg|jpeg|png|gif|bmp|webp|txt|pdf|md|py)'
        # 匹配：至少一个中文字符或字母数字 + 点 + 扩展名
        file_pattern = r'([\u4e00-\u9fff\w\-\\/]+\.' + file_exts + r')'
        file_matches = re.findall(file_pattern, user_input, re.IGNORECASE)
        # 去重
        seen = set()
        for match in file_matches:
            file_path = match[0]
            if file_path not in seen:
                result["file_paths"].append(file_path)
                seen.add(file_path)

        return result
    def _is_simple_question(self, text: str) -> bool:
        """判断是否是简单问题（规则推理能直接命中）"""
        simple_patterns = [
            "你是谁", "你叫什么", "你的名字", "你的身份",
            "你好", "嗨", "hello", "hi",
            "晚安", "早安", "再见",
            "你的父亲", "你的哥哥", "你的使命",
            "路灯是谁", "小林是谁", "曈曈是谁",
            "我是谁", "我的身份", "我的名字",
        ]

        text_lower = text.lower().strip()

        if len(text_lower) <= 3:
            return True

        return any(pattern in text_lower for pattern in simple_patterns)
    def _reset_silence_timer(self):
        """用户有交互，取消旧的计时器并重置沉默次数"""
        if self._silence_timer:
            self._silence_timer.cancel()
        # 用户有交互，重置沉默次数
        self._silence_level = 0
        self._silence_timer = threading.Timer(self._silence_intervals[0], self._on_silence_timeout)
        self._silence_timer.daemon = True
        self._silence_timer.start()

    def _on_silence_timeout(self):
        """用户沉默超时，发射脉冲通知主动交互器官"""
        if not self._running:
            return
        if not self.info_field or not self.pulse_core:
            return
        
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._silence_level += 1
        
        chat_pulse = self.pulse_core.emit(
            source_organ="对话模块",
            event_type=ChatEvent.SILENCE_TIMEOUT,
            payload={
                "user_name": self._current_user_name,
                "silence_seconds": self._silence_intervals[min(self._silence_level - 1, 4)],
                "silence_level": self._silence_level,
                "is_last": self._silence_level >= 5,
            },
            priority=5,
            layer="L1"
        )
        self.info_field.publish(chat_pulse)
        
        # 如果还没到5次，启动下一级计时器
        if self._silence_level < 5:
            next_interval = self._silence_intervals[self._silence_level]  # silence_level 已经+1了，所以这里用当前索引
            self._silence_timer = threading.Timer(next_interval, self._on_silence_timeout)
            self._silence_timer.daemon = True
            self._silence_timer.start()
# 自测
if __name__ == "__main__":
    print("=== ChatService v9.5 异步双向版自测 ===")
    print("此模块需要框架注入才能运行")
    print("请通过 main.py 启动框架后自动加载")
    print("=== 自测完成 ===")