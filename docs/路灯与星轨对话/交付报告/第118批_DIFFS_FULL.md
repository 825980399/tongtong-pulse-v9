# 第118批 改动 DIFF（完整）
> 生成方式：逐文件对比 `.bak_batch118/<rel>`（改前快照，sha256 见 BACKUP_MANIFEST）与当前工作副本。
> 字节级 CRLF 保全：所有文件改前/改后换行一致（见门禁 行尾保全）。

## main.py  （T-118b 裸logging→pulse树）
```diff
--- a/main.py
+++ b/main.py
@@ -2245,7 +2245,7 @@
                                 .get("discover_max_issues", 60))
                         except Exception as e:
                             _discover_max = 60
-                            logging.warning(f"进化发现上限回退失败(沿用60): {type(e).__name__}: {e}")
+                            logging.getLogger("pulse").warning(f"进化发现上限回退失败(沿用60): {type(e).__name__}: {e}")
                         _raw = self.evolution_loop.discover_all_issues(
                             log_file="logs/pulse.log",
                             max_issues=_discover_max,
@@ -2302,7 +2302,7 @@
                                     _inspector = get_self_inspector()
                                 except Exception as e:
                                     _inspector = None
-                                    logging.warning(f"代码审查器初始化失败(跳过): {type(e).__name__}: {e}")
+                                    logging.getLogger("pulse").warning(f"代码审查器初始化失败(跳过): {type(e).__name__}: {e}")
                                 _executor = get_safe_evolution_executor()
                                 _result = _executor.repair_with_distillation(
                                     _issues, self_inspector=_inspector)
@@ -3502,7 +3502,7 @@
             f"曈曈已成功启动\n{_online_organs}/{_total_organs}个器官在线（实时扫描）\n知识节点: {framework.node_pool.count()}个"
         )
     except Exception as e:
-        logging.warning(f"[企业微信] 桥接器启动失败（不影响框架运行）: {e}")
+        logging.getLogger("pulse").warning(f"[企业微信] 桥接器启动失败（不影响框架运行）: {e}")
         framework.wecom_bridge = None
 
     # ★v23.0新增：自我验证
@@ -3615,7 +3615,7 @@
         health_ui.set_node_pool(framework.node_pool)
         health_ui.start()
     except Exception as e:
-        logging.warning(f"[框架] 人体UI启动失败 (端口5051): {e}")
+        logging.getLogger("pulse").warning(f"[框架] 人体UI启动失败 (端口5051): {e}")
     
     # 启动Web对话窗口（独立Web服务）
     web_chat = None
@@ -3624,7 +3624,7 @@
         web_chat = WebChatServer(port=5052)
         web_chat.start(info_field=framework.info_field, pulse_core=framework.pulse_core)
     except Exception as e:
-        logging.warning(f"[框架] Web对话窗口启动失败 (端口5052): {e}")
+        logging.getLogger("pulse").warning(f"[框架] Web对话窗口启动失败 (端口5052): {e}")
     
     # 启动功能模块加载器
     framework.function_loader = FunctionLoader(framework)
@@ -3653,7 +3653,7 @@
         if _hot_reload_organs:
             print(f"[Config] 热重载回调已注册（{len(_hot_reload_organs)}个器官: {', '.join(_hot_reload_organs)}）")
     except Exception as _hre:
-        logging.warning(f"[Config] 热重载回调注册失败: {_hre}")
+        logging.getLogger("pulse").warning(f"[Config] 热重载回调注册失败: {_hre}")
 
     # ========== 假死探测器（P1） ==========
     # 框架启动后若长时间无任何脉冲被实际处理（疑似卡死/死锁），自动 dump 所有线程
@@ -3664,7 +3664,7 @@
             _crash_fh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pulse_crash.log"), "a", encoding="utf-8", errors="replace")  # noqa: SIM115 - 有意持有句柄供 faulthandler 常驻
         except Exception as e:
             _crash_fh = None
-            logging.warning(f"崩溃日志句柄初始化失败(留空): {type(e).__name__}: {e}")
+            logging.getLogger("pulse").warning(f"崩溃日志句柄初始化失败(留空): {type(e).__name__}: {e}")
         # 关键：把原生崩溃（如 PortAudio 的 access violation）堆栈也重定向到日志文件。
         # 否则 faulthandler 只打印到控制台，不会写入 pulse_crash.log（这正是上次日志为空的原因）。
         try:
@@ -3680,7 +3680,7 @@
     except Exception as e:
         _fh = None
         _crash_fh = None
-        logging.warning(f"日志句柄初始化失败(禁用落盘): {type(e).__name__}: {e}")
+        logging.getLogger("pulse").warning(f"日志句柄初始化失败(禁用落盘): {type(e).__name__}: {e}")
 
     _lv_fw = framework  # 捕获闭包引用
 
@@ -3772,7 +3772,7 @@
                         if _apply_pending_patches_and_restart(framework):
                             sys.exit(0)
                 except Exception as _apply_check_e:
-                    logging.warning(f"[进化] 应用请求检测异常(忽略): {_apply_check_e}")
+                    logging.getLogger("pulse").warning(f"[进化] 应用请求检测异常(忽略): {_apply_check_e}")
     except KeyboardInterrupt as _se:
         silent_exc(_se, "main.py:3646")
     except Exception as _main_loop_e:
```

## organs/senses/PulseVisualCortex.py  （T-118a face R2 层1）
```diff
--- a/organs/senses/PulseVisualCortex.py
+++ b/organs/senses/PulseVisualCortex.py
@@ -86,7 +86,7 @@
         self._global_lock = threading.Lock()
         
         self.is_running = False
-        self._current_user_name = "小林"  # 当前检测到的用户
+        self._current_user_name = "访客"  # 当前检测到的用户（T-118a：未知默认访客）
         # 摄像头监测线程
         self._camera_thread = None
         self._camera_running = False
```

## organs/identity/PulseSelfAwareness.py  （T-118a face R2 层1）
```diff
--- a/organs/identity/PulseSelfAwareness.py
+++ b/organs/identity/PulseSelfAwareness.py
@@ -660,9 +660,9 @@
         }
     def _on_user_presence(self, payload: dict) -> dict[str, Any]:
         """摄像头检测到人脸出现，确认身份并发射SWITCHED"""
-        user_name = payload.get("user_name", "小林")
+        user_name = payload.get("user_name", "访客")  # ★T-118a 未知用户默认访客
         if not user_name or user_name == "用户":
-            user_name = "小林"
+            user_name = "访客"  # ★T-118a "用户"占位或未知→访客
 
         self._active_user = user_name
         self._last_activity_time = time.time()
```

## organs/senses/PulseEars.py  （T-118a face R2 层1）
```diff
--- a/organs/senses/PulseEars.py
+++ b/organs/senses/PulseEars.py
@@ -158,7 +158,7 @@
         }
     def _on_persona_switched(self, payload: dict) -> dict[str, Any]:
         """收到身份切换脉冲，更新当前用户"""
-        self._current_user_name = payload.get("current_user", "小林")
+        self._current_user_name = payload.get("current_user", "访客")  # ★T-118a 未知用户默认访客
         return {"status": "ok", "user": self._current_user_name}
     def _on_status_request(self) -> dict[str, Any]:
         return self.get_stats()
```

## organs/body/PulseHeart.py  （T-118a face R2 层1）
```diff
--- a/organs/body/PulseHeart.py
+++ b/organs/body/PulseHeart.py
@@ -105,7 +105,7 @@
         self._reply_guidance_provider = None      # (user_name) -> dict
         self._existential_state_provider = None   # () -> dict
         self._survival_orchestrator = None  # ★R4阶段二：存续编排器引用（main.py注入，可选）
-        self._current_user_name = "小林"    # 当前用户
+        self._current_user_name = "访客"    # 当前用户（T-118a：未知用户默认访客）
         self._last_activity_time = time.time()  # 最后活跃时间
         self._interest_level = 0.0          # 当前兴趣水平
         # 任务调度队列: {task_id: {"interval": seconds, "last_run": timestamp, "event_type": str}}
```

## functions/chat/chat_service.py  （T-118a face R2 层1）
```diff
--- a/functions/chat/chat_service.py
+++ b/functions/chat/chat_service.py
@@ -142,8 +142,8 @@
         
         # 处理人脸检测事件（保留欢迎/告别打印）
         if event_type == ChatEvent.USER_PRESENCE_DETECTED:
-            user_name = payload.get("user_name", "用户")
-            if user_name and user_name != "用户":
+            user_name = payload.get("user_name", "访客")  # ★T-118a 未知/访客占位
+            if user_name and user_name not in ("用户", "访客"):
                 self._current_user_name = user_name
             # ★第80批 T6：emoji print 包 try-except 降级，不阻断后续计时器重置与脉冲发射
             try:
```

## functions/web_chat.py  （T-118a face R2 层1）
```diff
--- a/functions/web_chat.py
+++ b/functions/web_chat.py
@@ -646,10 +646,10 @@
             from nucleus.const import ChatEvent as _ChatEvent
             from nucleus.const import PersonaEvent as _PersonaEvent
             def on_persona_switched(pulse):
-                user_name = pulse.get("payload", {}).get("current_user", "小林")
+                user_name = pulse.get("payload", {}).get("current_user", "访客")  # ★T-118a
                 self._server.current_user_name = user_name
             def on_user_presence(pulse):
-                user_name = pulse.get("payload", {}).get("user_name", "小林")
+                user_name = pulse.get("payload", {}).get("user_name", "访客")  # ★T-118a
                 if user_name and user_name != "用户":
                     self._server.current_user_name = user_name
             def on_user_left(pulse):
```

## tests/test_m95_followups.py  （T-118d① m95 期望键同步）
```diff
--- a/tests/test_m95_followups.py
+++ b/tests/test_m95_followups.py
@@ -50,7 +50,7 @@
 _RES = os.path.join(ROOT, "nucleus", "synapsys", "ResonanceEngine.py")
 _CR = os.path.join(ROOT, "nucleus", "llm", "call_recorder.py")
 
-_EXPECT_COOLDOWN_KEYS = {"高危·安全拦截", "本地无规则·转LLM", "_default"}
+_EXPECT_COOLDOWN_KEYS = {"高危·安全拦截", "本地无规则·转LLM", "_default", "验证失败", "验证失败·3轮"}  # ★T-118d① 补 114a 新增两键
 # `_cooldown_classify` 实际可达的 reason（前缀匹配用真实形态）
 _REACHABLE_REASONS = ("高危·安全拦截", "高危·安全拦截(unsafe_eval)",
                       "高危·安全拦截(sql_injection)", "本地无规则·转LLM")
```

## docs/分析报告/技术债务台账_代码实查_20260919.csv  （T-118c 账本三件套）
```diff
--- a/docs/分析报告/技术债务台账_代码实查_20260919.csv
+++ b/docs/分析报告/技术债务台账_代码实查_20260919.csv
@@ -1,186 +1,208 @@
-﻿id,优先级,模块,问题简述,首次章节,最近章节,文档声称状态,原文关键句,实查状态,代码证据,置信度,处置建议
-D001,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-1),第八十章,第一百三十六章,状态未知,| P0-1 | （早期运行时阻塞问题） | ❓ 状态未知 |,已修复,总账:119『P0-1~P0-7运行时阻塞第1-15批逐步修复已闭环』；main.py:3364 PulseFramework构造+回退+崩溃钩子已加固,高,关闭，标记已闭环
-D002,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-2),第八十章,第一百三十六章,观察中,| P0-2 | （早期运行时阻塞问题） | 🟡 观察中 |,已修复,总账:119 P0-1~P0-7已闭环；启动路径含SafeEvolutionExecutor回退(main.py:3376 rollback_last),高,关闭
-D003,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-3),第八十章,第一百三十六章,状态未知,| P0-3 | （早期运行时阻塞问题） | ❓ 状态未知 |,已修复,main.py:3385注释『P0-3统一自重启入口』→_spawn_self_restart():3226+PID登记已实现,高,关闭
-D004,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-4),第八十章,第一百三十六章,观察中,| P0-4 | （早期运行时阻塞问题） | 🟡 观察中 |,已修复,总账:119 P0-1~P0-7已闭环；main.py:3415全局crash hook写pulse_crash.log,高,关闭
-D005,P0,交互/任务两端,交互与任务两端系统性断链(P0-9),第四十一章,第一百三十六章,部分修复(剩余),| P0-9 | 交互与任务两端系统性断链 | ✅ 部分修复（第24-27批） |,大部分修复有残留,PulseCortex.py:3241 P0-9长答腰斩已修；PulseExpression.py:293 DIALOG_REPLY_TRUNCATE_CHARS默认1000可0不截断；多模态vision consts存在(const.py:47-382),中,复核多模态入模与TaskPipeline真实闭环
-D006,P0,工程纪律,工程纪律崩坏(P0-10),第四十一章,第一百三十六章,待处理(部分子问题已修复),| P0-10 | 工程纪律崩坏 | ⏳ 待处理（部分子问题已修复） |,部分修复,根目录无CI workflow(仅pulses/pulse_config.yaml)；PIW仍23145行；但logger.py:317 SafeRotatingFileHandler轮转已上,中,补CI/覆盖率门禁；God文件拆分仍挂
-D007,P0,智能外包/知识体系,智能100%外包+知识体系名实不符(P0-11),第四十一章,第一百三十六章,待处理,P0-11（智能外包）等大项分解为可执行的子任务,部分修复,self_inspector.py:2186 L1→L2(30条)→L3(20条)→L4本能(30天冷却)管道已建；但自主修复率仅~1.8%仍高度外包,中,L4本能真实产出待运行期验证
-D008,P0,自学习闭环,自学习闭环未有效降低大模型依赖(依赖度反升97%)(P0-250),第七十四章,第一百三十六章,状态未知,| P0-250 | 自学习闭环未有效降低大模型依赖（依赖度反升97%） | ❓ 状态未知 |,叙事/文档名实不符,闭环代码已建(trace_evolution_call/semantic_cache)，但自主修复率~1.8%、适合本地自动修~5.8%，依赖度未实质下降,中,按真实修复率重定义闭环成效，勿称已降依赖
-D009,P0,内在模型,框架孕育内在模型(自有灵魂)(P0-251),第七十四章,第一百三十六章,状态未知,| P0-251 | 框架孕育内在模型（自有灵魂） | ❓ 状态未知 |,叙事/文档名实不符,总账:201仅有战略方向+设计文档，无实际训练流程/自有模型产出代码；models/无训练产物,高,维持设计文档，勿宣称已孕育
-D010,P0,内在模型,内在模型孕育计划分析文档已生成(P0-252),第一百三十六章,第一百三十六章,待第三方反馈,| P0-252 | 内在模型孕育计划分析文档已生成 | ⏳ 待第三方反馈 |,已失效/不再适用,为里程碑事项(分析文档已交付)，非代码缺陷；后续已转入P0-251实施讨论,高,关闭为历史里程碑
-D011,P0,内在模型,内在模型方案数据前提失实(留存率仅2.4%)(P0-253),第七十四章,第一百三十六章,状态未知,| P0-253 | 内在模型方案数据前提失实（留存率仅2.4%） | ❓ 状态未知 |,大部分修复有残留,call_recorder.py已建(530行默认开)，data/llm_traces/calls_*.jsonl落盘149文件/9.84MB；历史2.4%为既成事实,中,数据管道已补，历史低留存不可逆
-D012,P0,LLM留存管道,LLM调用全程留存管道(零号工程)(P0-254),第七十四章,第一百三十六章,状态未知,| P0-254 | LLM调用全程留存管道（零号工程） | ❓ 状态未知 |,大部分修复有残留,nucleus/llm/call_recorder.py 530行存在，ENABLE_LLM_CALL_RECORDER默认True(:180)；LLMEvolutionEngine.py:16/SelfReflectionEngine.py:16导入；残留calls_20260913.jsonl 231条中146条为测试桩(write_guard.py:5),高,管道已建，需清洗测试桩污染
-D013,P0,依赖度指标,大模型依赖度指标口径错位(P0-262),第七十四章,第一百三十六章,状态未知,| P0-262 | 大模型依赖度指标口径错位 | ❓ 状态未知 |,大部分修复有残留,SCENE_LUNG=肺回答定义(LLMDependencyMetrics.py:32)；PulseLung.py:1742 record_llm_call(SCENE_LUNG)已补原『对话=0』埋点,中,埋点已补，待真实运行数据重算基线
-D014,P0,进化验证空转,进化验证空转是依赖度97%的根因(P0-263),第七十四章,第101批 T-101b,状态未知,| P0-263 | 进化验证空转是依赖度97%的根因 | ❓ 状态未知 |,已闭环(部分)：baseline 恒 0 根因已确证修复(patch_verification_split.py)；第101批 T-101a 打通 批准→落盘 闭环，补丁可走完全链(提交→批准→落盘→复验),patch_verification_split.py 确证 baseline 恒 0；SafeEvolutionExecutor.py:1454/1471/1778/1975；PatchManager._m101_low_risk_release_path(第101批 T-101a),中,判据已修，真实修复率待长期观测；★第101批 T-101b 回写：D014 记已闭环(部分)，进化闭环末环由 T-101a 打通
-D015,P0,埋点验证,T2埋点需重启框架才能采集真实数据(P0-271),第七十五章,第七十五章,待重启验证,P0-271 ...本批仅代码+单测，未重启 → 重启后应见 calls_*.jsonl 增长,大部分修复有残留,实测data/llm_traces/calls_*.jsonl已149文件/9.84MB，证明重启后真实数据已增长(非空转),中,关闭待重启项，数据已采集
-D016,P0,未展开新登记,(新增P0级债务，正文未展开)(P0-272),第七十六章,第一百三十六章,状态未知,| P0-272 | （新增P0级债务） | ❓ 状态未知 |,已修复,总账:4069 第42批T1完成『根因定性外部原地截断；新增超期清理+被截断必留痕』；logger.py:175 LOG_RETENTION_PROTECT永不删,中,关闭
-D017,P0,日志治理,日志清空执行者仍未定位(P0-278→P1),第七十七章,第七十七章,待下次发生时定位,P0-278→P1 | 日志清空执行者仍未定位，但已具备留痕能力 | 下次发生时定位,大部分修复有残留,总账:4069根因=外部原地截断已定性并加留痕；logger.py:175保护pulse_crash.log；但具体外部进程/执行者未点名,中,已具留痕，待下次发生抓现行
-D018,P0,InfluxDB真实落库,InfluxDB时序库真实落库仍被凭证401硬阻(第79批T3),第一百七十六章,第一百七十六章,待token注入真实环境复跑(移交第80批),数据真实落库且可查询仍被凭证硬阻，移交第80批「外部存储收口」待 token 注入真实环境复跑,已失效/不再适用,ENABLE_INFLUXDB_TIMESERIES=False(已核实)；PulseNodePool.py:1111与PulseStomach.py:302双门禁；influxdb_store.py:46 _influx_enabled()恒False→生产0次真实connect，非运行时阻塞,高,降档为休眠特性，移出P0阻塞
-D019,P1,测试真实回归,全量125例失败中B类真实回归16例+C类Cython未编译4例(第79批T4揭示),第一百七十五章,第一百七十六章,"待修复(分诊已完成,真实回归未清)",B 真实回归...16 / C 构建依赖（Cython 未编译）4；其余 B 类候选隔离单跑仍失败→确属真实回归,待核,按指令未跑测试套件；cython_status.py:34 use_cython_extensions默认False(4例C类为环境性),低,缺：真实跑一次测试套件确认16例是否仍失败
-D020,P1,推理相似度,历史推理相似度占位0.5(P1-5),第六十一章,第一百三十六章,状态未知,| P1-5 | 历史推理相似度占位0.5 | ❓ 状态未知 |,大部分修复有残留,SelfCalibrator.py:338 history_similarity()已实现真实命中，:348仅无命中回退0.5(:360)，不再恒0.5占位,高,占位已破，回退仍0.5可接受
-D021,P1,PulseInnerWorld上帝类,PulseInnerWorld.py 上帝文件(实测23145行未拆分)【主票】,第四十五章,第一百七十五章,未启动(战略高风险专项),P1-60 PulseInnerWorld上帝类拆分：高风险专项，需要单独批次；23145行=brain系统55%，13个>200行方法,未动,organs\brain\PulseInnerWorld.py 实测23145行未拆分；D174并入本票,高,第82批上帝文件拆分专项(主票，D174并此)
-D022,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-173),第五十章,第一百三十六章,待处理,| P1-173 | （新增P1级债务） | ⏳ 待处理 |,大部分修复有残留,总账:1170 zhipu/doubao曾明文泄露需用户控制台轮换；main.py:3446凭证改从config/env读取不再硬编码,中,代码已改env，轮换为用户操作
-D023,P1,渠道,ark火山方舟渠道成功率低(P1-247),第七十三章,第一百三十六章,部分修复(状态未更新),P1-247（ark渠道成功率低）虽已部分修复（第58批T2），但状态未更新,已修复,PulseLung.py:1053 第25批T2/P2-162按渠道裁剪字段修doubao/ark v3 400，:1058实测HTTP200,中,关闭，状态更新为已修复
-D024,P1,自检视,self_inspector方法体定位精确命中率0%(P1-248),第七十三章,第一百三十六章,待处理,| P1-248 | self_inspector方法体定位精确命中率0% | ⏳ 待处理 |,部分修复,self_inspector现175处命中，exploration_audit.py:230 run_parallel_audit接入12检测器；但SafeEvolutionExecutor.py:1499注『仍缺method无法方法级修复』,中,框架已接，方法级定位精度仍待提
-D025,P1,置信度校准,置信度校准机制缺失(P1-255),第七十四章,第一百三十六章,状态未知,| P1-255 | 置信度校准机制缺失 | ❓ 状态未知 |,大部分修复有残留,SelfCalibrator.calibrate被CausalVerifier.py:314与semantic_cache.py:122阈值校准接入，机制已建,中,机制已上，校准曲线待数据验证
-D026,P1,自我蒸馏,自我蒸馏退化螺旋风险(P1-256),第七十四章,第一百三十六章,状态未知,| P1-256 | 自我蒸馏退化螺旋风险 | ❓ 状态未知 |,待核,蒸馏基础设施存在(TaskPipeline DISTILL；self_inspector.py:275 P3-12自增强)，但无针对退化螺旋的主动缓解/监控代码；自主修复率~1.8%佐证风险,低,缺：退化螺旋监控指标与刹车机制
-D027,P1,数据规模,数据规模严重低估(110MB→4.8GB)(P1-264),第七十四章,第一百三十六章,状态未知,| P1-264 | 数据规模严重低估（110MB→4.8GB） | ❓ 状态未知 |,待核,规模为运行期量；代码侧未见规模自适应治理，与D038/D041快照增长同源,低,缺：实测data目录当前体量
-D028,P1,ONNX资产,已有3套ONNX嵌入模型未被当作资产(P1-265),第七十四章,第一百三十六章,状态未知,| P1-265 | 已有3套ONNX嵌入模型未被当作资产 | ❓ 状态未知 |,部分修复,semantic_cache.py:7已复用90MB ONNX/512维；但models/目录实测未找到.onnx实物(疑运行时下载)，3套资产未集中管理,中,1套已接入，资产台账待建
-D029,P1,隔离区可见性,T2隔离区1274文件沙箱执行后不可见(manifest完整)(P1-273),第七十六章,第七十六章,待真实终端复核,P1-273 | T2隔离区1274文件沙箱执行后不可见（manifest完整） | 真实终端复核,待重启验证,总账:3964标记『真实终端复核data/_quarantine/』；沙箱可见性为运行期现象，代码层未见修复,低,缺：真实终端ls data/_quarantine/复核
-D030,P1,重启生效,T2/T3生成侧改动需重启框架生效(P1-274),第七十六章,第七十六章,待重启验证,P1-274 | T2/T3生成侧改动需重启框架生效 | 重启窗口,待重启验证,总账:3965标记重启窗口；生成侧改动运行期生效项，代码层无法静态判定,低,缺：重启后确认.bak不再增/cache非空
-D031,P1,补丁验证口径,baseline_errors为文件级口径无法反映方法级修复效果(P1-281),第七十七章,第七十七章,待日志格式改善,P1-281 | baseline_errors是文件级口径，无法反映方法级修复效果 | 改善日志格式后提升,大部分修复有残留,patch_verification_split.py:10/110引入post_apply_errors区分Problem-Fixed；patch_quality_evaluator.py:188记录；但SafeEvolutionExecutor.py:1499仍缺方法级,中,口径已加post_apply，方法级定位待补
-D032,P1,进化循环埋点,进化循环埋点仅部分闭环(P1-292),第七十九章,第八十章,文档称已完成(待代码复核),⇒ 进化循环埋点已生效（P1-292 部分闭环）,大部分修复有残留,LLMEvolutionEngine.py:16与SelfReflectionEngine.py:16 import trace_evolution_call；总账:4752『已生效部分闭环』,中,部分闭环属实，待全链路验证
-D033,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-313),第八十四章,第一百三十六章,状态未知,| P1-313 | （新增P1级债务） | ❓ 状态未知 |,未动,总账:5502队列深度318次WARNING(1006→1240)需背压/细分统计；未见背压代码,低,补队列背压与积压类型细分
-D034,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-327),第八十七章,第一百三十六章,状态未知,| P1-327 | （新增P1级债务） | ❓ 状态未知 |,已修复,总账:6830 第49批T1对话路由修复(肺调用LLM来源=background_learning→无罪推定),中,关闭
-D035,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-347),第九十四章,第一百三十六章,状态未知,| P1-347 | （新增P1级债务） | ❓ 状态未知 |,部分修复,总账:8300第52批器官单测框架搭建+核心器官覆盖；tests/实测208个py存在,中,框架已搭，覆盖率持续补
-D036,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-357),第九十六章续,第一百三十六章,状态未知,| P1-357 | （新增P1级债务） | ❓ 状态未知 |,部分修复,与D041同源(快照482MB过大)；第52批评估；PulseSnapshot.py:201 INCREMENTAL_MAX_NODES=50增量保存已上,低,并入D041快照治理
-D037,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-370),第一百章,第一百三十六章,状态未知,| P1-370 | （新增P1级债务） | ❓ 状态未知 |,部分修复,总账:8863 T2选3-5核心器官(PulseStomach/Lung/Heart)写pytest；分批覆盖中,低,器官单测分批推进
-D038,P1,冷热分离空转,"第69批冷热分离0%生效,_m70_lazy_ids只写不读(第三方P1)",第一百七十五章,第一百七十六章,待重新评估(第80批),冷热分离功能空转：第69批做的冷热分离0%生效！_m70_lazy_ids只写不读,重复挂账,"PulseSnapshot.py:1606 _m70_lazy_ids现被:1679/1698 discard、:1710 list()真实消费,:1648新增冷存召回；但SNAPSHOT_HOT_COLD_LOAD=False(已核实)生产未触发；与D152同项",中,并表D152；代码已改读待重启开关验证
-D039,P1,关系图谱膨胀,"semantic_relations占428MB/501MB,同一邻接关系存两遍(第三方P1)",第一百七十五章,第一百七十六章,待治理(第81-85批),关系图谱膨胀89.6%：semantic_relations占428MB，linked_nodes与target集合Jaccard 0.932——同一邻接关系存两遍,未动,"semantic_relations仍与linked_nodes并存：PulseNode.py:320-321 to_dict双写,:2241 parquet双列；去重治理未做，计划第81-85批(路灯施工中)",中,并入第81-85批去重治理
-D040,P1,L3降级通道形同虚设,"conflict_count不进to_dict,should_downgrade_l3需≥3永不满足(第三方P1宪法)",第一百七十五章,第一百七十五章,待实现,L3降级通道形同虚设：conflict_count不进to_dict，should_downgrade_l3需≥3永不满足——宪法条款写了但没实现,部分修复,PulseLiver.py:2933-2936已回写conflict_count(原恒0)；但PulseNode.py:300-336 to_dict仍不含conflict_count→重启归零；should_downgrade_l3(:285阈值3)会话内可达,高,补to_dict持久化conflict_count
-D041,P1,快照数据增长治理,"478MB快照数据持续增长,需冷热分离落地/快照轮转裁剪/节点池上限",第一百七十六章,第一百七十六章,待治理,478MB快照数据增长治理：冷热分离落地/快照轮转裁剪/节点池上限,部分修复,增量保存INCREMENTAL_MAX_NODES=50(PulseSnapshot.py:201)已上；冷热分离gated off；节点池硬上限/轮转裁剪未见；与D036同源,低,并入第81批存储核心治理
-D042,P1,静默except剩余,全库仍剩490处静默except(第78批仅清理核心4文件105处),第一百七十六章,第一百七十六章,待清理,全库剩余490处静默except：本批仅清理核心4文件105处,部分修复,活代码308py实测：except Exception全量2829处/228文件(未绑定1128/189+as绑定1701/188)，裸except:0处；真正静默吞噬(后跟pass/...)240处/103文件,高,台账490口径过时，按240处静默吞噬续清
-D043,P2,并发模型,并发模型混合治理(P2-64),第六十章,第六十三章,排期(第35批后),| P2-64 | 并发模型混合治理 | 中 | 第35批后 |,未动,threading.Lock命中207处/128文件;Thread70/42;Queue16/10;asyncio.Lock=0;三套scheduler(parallel/hybrid/structured)并存,中,"按器官收敛为单一调度原语,淘汰asyncio混用"
-D044,P2,配置中心,配置中心拆分config.py 3547行(P2-65),第六十章,第六十三章,排期(第35批后),| P2-65 | 配置中心拆分（config.py 3547行） | 中 | 第35批后 |,未动,"实测config.py=4854行,较3547反增;注:config.py为第81批动态文件,行数不稳定",高,"第81批存储核心改完后再排拆分,按域切分"
-D045,P2,except_pass,except_pass剩余约294处(P2-79),第六十章,第六十章,分批治理(未完成),| P2-79 | except_pass剩余约294处 | 分批治理（按器官推进） |,部分修复,"实测静默except:pass=286处/119文件(旧294处基本未降);已引入nucleus/_silent_except.py的silent_exc(e,where)可见化基建(灰度enable_silent_except_logging默认True),但286处仍为裸pass未迁移",高,"批量机械替换except:pass->silent_exc(e,where)"
-D046,P2,LLM依赖度,LLM依赖度系统性降低(P2-102),第六十三章,第六十三章,长期/持续,| P2-102 | LLM依赖度系统性降低 | 长期 | 持续 |,部分修复,nucleus/LLMDependencyMetrics.py存在;channel_speed_profiler/call_pattern_analyzer存在,中,设季度下降目标并在LLMDependencyMetrics出报表
-D047,P2,EventTap,EventTap时间窗口过滤(P2-115),第四十章,第四十章,待后续,| P2-115 | EventTap 时间窗口过滤 | 📋 待后续 |,未动,"nucleus/events/EventTap.py=379行;全文无window/stale/cooldown/min_interval/expire,仅timestamp透传(L164/204/281)",高,在EventTap入口加最小时间间隔/过期丢弃
-D048,P2,call_graph落盘,call_graph落盘体积控制(P2-117),第四十章,第一百三十章,待评估后实施,P2-117 call_graph落盘体积控制：需评估影响后实施,大部分修复有残留,"CallGraphAnalyzer.py=1072行;已有_MAX_SCC_NODES=20000(L119)/_MAX_DEPTH_LIMIT=200(L120)节点上限,但落盘体积无prune/容量告警",中,在落盘前按节点数/字节数截断并记录体积
-D049,P2,硬件自适应,硬件自适应动态资源分配5项子任务(P2-124~128/P3-5),第六十章,第六十章,设计已完成待实施,P2-124~128 | 硬件自适应动态资源分配（5项子任务） | P3-5子任务，设计已完成待实施,大部分修复有残留,utils/safe_hw_probe.py=128;nucleus/hardware_probe.py=255;device_router.py=384;GPUCore.py=243 均落地,中,在真机上跑动态降载回归验证
-D050,P2,能力整合路线图,能力整合路线图8项子任务(P2-135~142),第六十章,第六十三章,排期(第35批后),| P2-135~142 | 能力整合路线图（8项子任务） | 中 | 第35批后 |,待核,"路线图项,无单一代码落点;需对照总账逐项核",低,开批次时按总账子任务清单逐项销项
-D051,P2,代码分析服务,独立进程代码分析服务11项子任务(P2-144~154/P3-7),第六十章,第六十三章,设计已完成待实施,P2-144~154 | 独立进程代码分析服务（11项子任务） | 设计已完成待实施,未动,无独立分析服务进程;仅exploration_audit.py:149 run_in_subprocess零星调用;write_guard的is_framework_process是进程判别非分析服务,中,单开批次落地独立子进程分析服务与IPC
-D052,P2,对话超时,对话处理超时优化(P2-155),第四十二章,第四十八章,部分完成(超时降级/队列可视化待后续),| P2-155 | 对话处理超时优化 | 🔄 部分完成（T6进度提示已闭环，超时降级/队列可视化待后续） |,部分修复,T6进度提示闭环:config.py:1365 DIALOG_PROGRESS_HINT_CONFIG;PulseLung.py:1492读取;但timeout_degrade/队列可视化grep=0命中,高,补超时降级(回退短句)与队列进度可见
-D053,P2,知识质量打分,KnowledgeQualityScorer抽样验证整合(P2-156),第四十一章,第四十一章,待后续批次,| P2-156 | KnowledgeQualityScorer抽样验证整合 | 📋 待后续批次 |,大部分修复有残留,self_awareness/KnowledgeQualityAnalyzer.py=863;quality_score_v2.py=474存在,中,补抽样验证回路并接回写
-D054,P2,渠道路由,用户对话优先付费渠道(P2-158),第四十二章,第四十四章,待验证(可能已落地),| P2-158 | 用户对话优先付费渠道 | ⏳ 待验证（日志显示...可能已落地） |,已修复,"llm/ChannelConcurrency.py:152 paid_channel_names=[deepseek,advanced];:191-192 if name in _paid 判付费优先",高,日志取证一次确认用户对话实际走付费渠道
-D055,P2,渠道监控,大模型渠道并发状态监控(P2-159),第四十二章,第四十四章,待验证,| P2-159 | 大模型渠道并发状态监控 | ⏳ 待验证 |,已修复,ChannelConcurrency.py=459;ChannelQuotaMonitor.py=536;channel_speed_profiler.py=88 三模块均落地,高,运行期导出一次并发快照确认
-D056,P2,RESULT双发,上游RESULT双发路径治理(P2-164),第四十五章,第五十章,"取证完成,重构待单开批次",| P2-164 | 上游RESULT双发路径治理 | 🔄 取证完成（第26批T4），重构待单开批次 |,部分修复,PulseInnerWorld.py 仍在631/691/811/834/870/896/968/986/1086/1179/1235/1261等12+处_emit(InferenceEvent.RESULT),高,单开批次收口RESULT发射点到单一出口
-D057,P2,深度思考超时,深度思考超时致长问题无实质回答(P2-170),第四十九章,第五十二章,文档称已完成(待代码复核),✅ 已完成...端到端待重启验证,待重启验证,代码改动存在但需重启后端到端验证;无运行期证据,中,重启后注入长问题取证
-D058,P2,输出长度,大模型输出长度远低于要求(P2-171),第四十九章,第五十四章,文档称已完成(待代码复核),✅ 已完成...端到端待重启验证,待重启验证,"同上,长度约束改动待运行期确认",中,重启后统计实际输出token长度分布
-D059,P2,检索重复执行,"on_inference_request检索流程同秒重复2~3次(P2-172,与P2-164同源)",第四十九章,第五十章,待处理,| P2-172 | 内在世界_on_inference_request检索流程重复执行 | ⏳ 待处理...建议合并处理 |,大部分修复有残留,PulseInnerWorld.py=23145行;L245-246 _dedup_cleanup_interval/max_size;L4543 _cleanup_dedup_marks;nucleus/field/RequestDeduplicator.py=375,中,运行期统计同秒检索命中数确认去重生效
-D060,P2,自动化优化,"自动化优化(P2-173,第35批计划项)",第五十一章,第六十三章,排期(第35批),第35批：...P2-173（自动化优化）...,部分修复,evolution/AutoParamApplier.py=196;PeriodicTestScheduler.py=400 自动化调度存在,中,出自动化闭环报表
-D061,P2,模型自动配置,框架自动管理模型配置阶段2/3(P2-183星轨版),第五十七章,第六十三章,排期(第33-34批),| P2-183（星轨版） | 框架自动管理模型配置（阶段2/3） | 高 | 第33-34批 |,大部分修复有残留,llm/model_self_updater.py=444存在,中,补阶段3切换与回滚取证
-D062,P2,操作指令准入,操作指令准入默认关闭后仍存残余风险与判据缺口(P2-191),第六十一章,第六十六章,待完善(残余风险),T5 操作指令准入评估（P2-191）...实测关闭后仍存残余风险与判据缺口,待核,"按OPERATION_GATE/command_gate/shell_gate/操作指令准入等现名grep活代码=0命中;机制可能已重构或移除,需对照总账确认落点",低,对照总账定位现机制后再判残余风险
-D063,P2,测试断言形态,源码文本计数型断言需统一改为调用形态计数(P2-195),第六十四章,第一百二十章,待改造,P2-195：源码文本计数型断言（需统一改为调用形态计数）,大部分修复有残留,"与D064同源;RUF100 noqa由210->1,表明文本计数型断言已大批迁移",中,抽查剩余文本计数断言改为调用形态
-D064,P2,RUF100承重项,"RUF100门禁承重项210处(P2-201,单开专项批次)",第六十七章,第一百二十三章,待单开专项批次,P2-201：RUF100门禁承重项专项（210处，单开专项批次）,已修复,实测活代码noqa RUF100仅1处/1文件(原210处),高,收尾最后1处
-D065,P2,v2探针延迟,T1探针每次进v2前多一次本地检索固定延迟(P2-205),第六十九章,第七十章,待P95评估是否加缓存,P2-205...按P95延迟评估是否加缓存,未动,无专项缓存落点;待P95实测,低,先测P95再决定是否加缓存
-D066,P2,扫描极限标定,PROBE_SCAN_LIMIT=800为经验值未在1万+节点真实库标定(P2-206),第六十九章,第七十章,待真实快照反标定,P2-206...未在1万+节点真实库标定 | 用真实快照按延迟反标定,未动,"config.py:1618 MULTI_STEP_ENTRY_PROBE_SCAN_LIMIT=800 仍为经验值,未反标定",高,用真实快照按延迟重标定该值
-D067,P2,端到端验证,T1端到端需重启框架验证0次v2模型调用(P2-209),第六十九章,第七十章,待重启验证,P2-209 | T1端到端需重启框架验证...重启后注入问题取证,待重启验证,"需重启运行期取证,无静态证据",中,重启后统计v2模型调用次数=0
-D068,P2,自认知接线,evolution_health维度接SafeEvolutionExecutor(P2-214),第七十一章,第七十二章,待实施(T-list),T2（P1）：P2-214 evolution_health维度接SafeEvolutionExecutor,已修复,SafeEvolutionExecutor.py:952 def _log_evolution_health;:1606调用;:4430-4432 integrate_evolution_health收口为公共接口,高,运行期确认health维度非空
-D069,P2,阶段二接入设计,"阶段二结果接入(设计文档354行,未改生产代码)(P2-216)",第七十一章,第七十四章,"文档称已完成(待代码复核,仅设计)",T5（P2）P2-216 阶段二结果接入设计 ✅ 设计文档354行...未改生产代码,叙事/文档名实不符,文档自述仅设计354行未改生产代码;生产无对应接入点,中,"设计已完成,落地时再排批次"
-D070,P2,自我观察噪声,自我观察噪声排除(引擎自身产物目录)(P2-217),第七十一章,第七十二章,待实施(T-list),T4（P2）：P2-217 自我观察噪声排除（引擎自身产物目录）,待核,未grep到引擎自身产物目录排除的明确落点,低,在SelfAwarenessEngine排除引擎产物目录
-D071,P2,器官print,"6个器官未实现print改log(P2-230,自P2-212拆出)",第七十一章v9.10,第七十一章v9.10,待处理,P2-212（6个器官未实现）→ P2-230,未动,"实测organs下print(=519处/54文件,远大于文档所述6个;含Lung/Cortex/Stomach/VisualCortex等",高,"按器官print->logger批量迁移,先五脏五感"
-D072,P2,配对器信噪比,配对器信噪比优化(目录/扩展名排除白名单)(P2-231),第七十二章,第七十二章,待实施(T-list),T5（P2）：P2-231 配对器信噪比优化...,部分修复,self_awareness/ProductionConsumptionMatcher.py=1212;FakeLoopDetector.py=730 存在,中,确认目录/扩展名白名单已进matcher
-D073,P2,磁盘枚举标定,磁盘枚举未在10万级文件规模标定(P2-234),第七十二章,第七十二章,后续批次,| P2-234 | 磁盘枚举未在10万级文件规模标定 | 后续批次 |,未动,"性能标定项,无代码缺陷落点",低,在10万级目录实测枚举耗时
-D074,P2,调度耗时,"每日调度全流程5.4s(P2-235,当前可接受)",第七十二章,第七十二章,后续优化,| P2-235 | 每日调度全流程5.4s（当前每日1次可接受） | 后续优化 |,未动,"性能观测项,当前每日1次可接受",低,随调度量级增长再优化
-D075,P2,返回值区分,返回值未区分两类no_consumer(P2-236),第七十二章,第七十二章,后续批次,| P2-236 | 返回值未区分两类no_consumer | 后续批次 |,待核,"无明确落点,需对照report_bus/publishers确认",低,在consumer返回处细分no_consumer原因码
-D076,P2,测试写生产目录,"测试隐式写入生产目录,根因未100%锁定(P2-237)",第七十二章,第七十二章,持续观察,| P2-237 | 测试隐式写入生产目录（已加防御，根因未100%锁定） | 持续观察 |,大部分修复有残留,data/write_guard.py=315行(第44批T4):pytest环境拒写data/前缀;L1-22判据;根因观察中,中,"持续观察守卫告警,定位剩余写点"
-D077,P2,白名单目录,T2目标依赖任务书外11个目录(P2-244),第七十三章,第七十四章,待第40批确认,| P2-244 | T2 目标依赖任务书之外的11个目录... | 🔄 第40批确认 |,待核,"需对照任务书白名单清单,静态无法判定11目录现状",低,对照任务书逐项确认11目录去留
-D078,P2,运行态文件白名单,data根下仍有13个运行态文件未纳入白名单(P2-245),第七十三章,第七十四章,后续批次,| P2-245 | data 根下仍有 13 个运行态文件...未纳入白名单 | 后续批次 |,未动,"白名单项,需运行期扫data根确认",低,运行期扫data根补白名单
-D079,P2,路线图排期,PHASE18阶段二3批路线图待排期(P2-248),第七十四章,第七十四章,排期,| P2-248 | 阶段二 3 批路线图（第40/41/42）待排期 | 排期 |,待核,路线图排期项,低,按40/41/42批排期
-D080,P2,胃活跃度异常,胃器官活跃度异常高32.6%(P2-249),第七十三章,第七十四章,待排查,- P2-249：胃器官活跃度异常高（32.6%）,待核,"PulseStomach.py=2536行,已有噪声过滤(L525-609);活跃度偏高无回归点",低,运行期采胃活跃度基线排查触发源
-D081,P2,内在模型优先级,内在模型优先级与收益倒挂(P2-266),第七十四章,第七十四章,待处理,- P2-266：内在模型优先级与收益倒挂,未动,无明确代码落点,低,按收益重排内在模型优先级
-D082,P2,反馈回填,CallRecorder.record_feedback接口已提供但回填逻辑未接入(P2-267),第七十五章,第七十五章,后续批次,P2-267 ...回填逻辑未接入（消费者：cortex路由/补丁验证器） | 后续批次,部分修复,call_recorder.py:366 def record_feedback存在;:378-385写feedback_YYYYMMDD.jsonl;但cortex路由/补丁验证器消费接入未见,中,把feedback jsonl接到cortex路由与补丁验证器
-D083,P2,tmp非规范脚本,tmp/28+个并发会话遗留非规范前缀脚本未动(P2-269),第七十五章,第七十五章,待裁决,P2-269 ...未动，待裁决 | 待裁决,未动,"实测tmp/*.py=40个(文档28+,反增)",高,裁决:归档/删除/迁入tools
-D084,P2,语义缓存阈值,"语义缓存阈值0.92偏保守,两条验收互斥(P2-275)",第七十六章,第七十六章,"文档称已完成(待代码复核,L2启用前重标定)",P2-275 | 语义缓存阈值0.92偏保守... | L2启用前重标定,已修复,llm/semantic_cache.py:43 DEFAULT_THRESHOLD=0.85(第45批实测校准0.92->0.85);:119-135 threshold()带灰度回退到BEFORE_CALIBRATION=0.92,高,"保持灰度开关,L2启用前再标定"
-D085,P2,unverifiable补丁,62条unverifiable需等baseline判据改善(P2-282),第七十七章,第七十七章,后续批次复查,P2-282 | 62条unverifiable需等baseline判据改善后才能给出有效结论 | 后续批次复查,部分修复,evolution/patch_quality_evaluator.py=395;EvolutionEffectVerifier.py=365;ParamPatchEffectVerifier.py=212 判据模块在,中,改善baseline判据后复查62条
-D086,P2,经验库污染清洗,经验库污染率79.5%(1193条)只做过滤未清洗(P2-283/298),第七十七章,第七十九章,待专项批次清洗,P2-283 ...只做过滤未清洗 | 专项批次清洗；P2-298 ...数据清洗专项,部分修复,evolution/ExperiencePollutionGuard.py=205;data/experience_cleanup.py=294;knowledge/PollutionTagger.py=391 清洗/打标模块均在,中,"单开清洗专项,对1193条跑experience_cleanup"
-D087,P2,调度接线重启,T2/T3 DailyScheduler接线需重启框架才生效(P2-284),第七十七章,第七十七章,待重启验证,P2-284 ...DailyScheduler接线需重启框架才生效 | 重启窗口,待重启验证,self_awareness/DailyScheduler.py=548行存在;接线需运行期确认,中,重启后确认T2/T3调度注册
-D088,P2,数据多样性,"真实数据diversity仅5.7/15,origin仅2类(P2-287)",第七十八章,第七十八章,待扩大采集场景,P2-287 | 真实数据 diversity 仅 5.7/15（origin 仅 2 类） | 扩大采集场景,未动,"数据采集项,非代码缺陷;需扩origin来源",低,扩大采集场景增origin类别
-D089,P2,语义缓存升L2,语义缓存升L2仅设计文档274行不实施(P2-288),第七十八章,第七十九章,"文档称已完成(待代码复核,仅设计)",T2 语义缓存升L2设计...完成 设计文档274行...仅设计不实施,叙事/文档名实不符,"semantic_cache.py已用ONNX512维(L7);L2为更高阶设计文档,生产未实施",中,"设计已完成,需要时再实施"
-D090,P2,调度接线重启,T1/T3 DailyScheduler接线需重启框架(P2-289),第七十八章,第七十八章,待重启验证,P2-289 ...DailyScheduler 接线需重启框架生效 | 重启窗口,待重启验证,与D087同源(DailyScheduler重启);T1/T3接线待运行期,中,与D087同一重启窗口合并验证
-D091,P2,历史回填,测试污染防护推广中历史回填延后(P2-290),第七十八章,第七十九章,部分完成(历史回填延后),T4 测试污染防护推广...完成（1项延后）...历史回填延后,部分修复,write_guard.py=315行已推广到写盘型组件;历史污染数据回填延后,中,补历史污染数据回填清理
-D092,P2,重启窗口,第44批全部改动需重启框架生效(P2-295),第七十九章,第七十九章,待重启验证,P2-295 | 本批全部改动需重启框架生效 | 重启窗口,待重启验证,第44批写盘守卫等改动需运行期验证,中,与D087/D090同一重启窗口统一取证
-D093,P2,写盘守卫,"写盘守卫为黑名单式,新增写盘组件仍会漏(P2-297)",第七十九章,第七十九章,待评估白名单式,P2-297 | 写盘守卫为黑名单式；新增写盘组件仍会漏 | 评估「白名单式」,大部分修复有残留,"write_guard.py实为data/路径前缀守卫(L16只判data/前缀,L11-14三条件),非组件黑名单,新增写data/组件自动覆盖;但仅pytest环境且非全白名单",中,升级为全白名单并覆盖非pytest误写
-D094,P2,pulse.log增长,pulse.log实测4.2MB/33094行且持续增长(P2-299),第七十九章,第七十九章,继续观察留痕,P2-299 | logs/pulse.log 实测 4.2MB...持续增长 | 继续观察留痕,已修复,"nucleus/logger.py:317 SafeRotatingFileHandler + :471 挂载；实测 logs/pulse.log=2,073,509B/17,617行（原4.2MB/33,094行，已轮转下降）",高,维持轮转，定期观察
-D095,P2,tmp测试隔离,旧批次测试持续在tmp创建隔离目录(P2-301/307),第八十章,第八十一章,待与P0修复并行/合并治理,P2-301 | 旧批次测试持续在tmp创建隔离目录...第46批与P0修复并行；P2-307 与P2-301合并,未动,.bak_batch64~81 目录仍在持续累积；tmp隔离目录为历史测试产物，无清理代码,中,纳入备份目录策略(D114)统一治理
-D096,P2,探针集扩充,探针集扩充至≥100对(P2-303),第八十章,第八十一章,待实施,探针集扩充至≥100对（P2-303）+ 硬下限复核（P2-302）,待核,nucleus/probe_strategy.py 存在 ProbeStrategyMemory，但未实跑无法核对探针对数是否≥100,中,需运行探针计数脚本核实
-D097,P2,重启验证阈值,重启框架窗口验证T1阈值0.85生效+守卫/埋点复验(P2-304),第八十章,第八十一章,待重启验证,重启框架窗口（P2-304）：验证T1阈值0.85生效 + 第44批守卫/埋点复验,待重启验证,T1阈值0.85配置存在；框架已停止，运行期守卫/埋点未验,中,框架重启后复验
-D098,P2,误删脚本覆盖,"抢救误删重建3个tmp脚本(非原文),待找到原脚本覆盖(P2-305)",第八十章,第八十一章,待后续覆盖,P2-305 ...后续找到原脚本可覆盖,未动,tmp下重建脚本为非原文，原脚本未找到；无覆盖动作,低,找到原脚本再覆盖，否则归档
-D099,P2,执行产物契约,清理执行产物被当作长期硬契约(P2-312),第八十二章,第八十三章,后续批次,P2-312 ...清理执行产物被当作长期硬契约 | 后续批次,未动,描述性条目，无对应代码改造点,低,并入架构治理
-D100,P2,后台线程未关,框架停止后后台线程未关闭(P2-314),第八十四章,第八十五章,待处理,- P2-314：框架停止后后台线程未关闭,大部分修复有残留,main.py:2826 停机时枚举存活非daemon线程并告警；IntentGenerator/蒸馏/health/evolution 均 daemon=True(:593/2105/2151/2337)，随进程退出,高,daemon设计可接受，保留诊断日志
-D101,P2,经验库覆盖率0,"生产经验库raw_summary覆盖率0%,历史未回填且1179条原文永久丢失(P2-315/321)",第八十四章,第八十五章,待回填(原文已永久丢失),P2-315/P2-321 | 生产经验库raw_summary覆盖率0%...1179条污染记录原文已永久丢失,大部分修复有残留,M47止血：experience_pool.py:323-327 raw_summary永久保留不覆盖；tests/test_experience_summary_hemostasis_m47.py:68 验证；历史1179条原文永久丢失不可回填,高,新增已止血，历史不可恢复，归档
-D102,P2,生命周期统一,架构级生命周期管理不统一(P2-316),第八十四章,第八十五章,待治理,- P2-316：架构级-生命周期管理不统一,未动,架构级生命周期统一，无对应改造提交,低,远期架构专项
-D103,P2,baseline调参无效,"EVOLUTION_BASELINE_WINDOW_DAYS调参无效,需主动复现探针(P2-322)",第八十五章,第八十五章,待探针复现,P2-322 ...调参无效——需主动复现探针才能真正提升可判定率,未动,EVOLUTION_BASELINE_WINDOW_DAYS 仍为配置项，无主动复现探针代码,中,需补探针复现
-D104,P2,能力分散整合,架构级能力分散没有整合(P2-317),第八十四章,第八十五章,待治理,- P2-317：架构级-能力分散没有整合,未动,架构级能力分散整合，无对应改造,低,远期专项
-D105,P2,实施生效缺口,代码实施与生效之间的人工操作缺口(P2-323),第八十六章,第一百零四章,待根治,根因：P2-323（代码实施与生效之间的人工操作缺口）的具体实例,未动,描述性根因条目，无具体代码点,低,随批次治理
-D106,P2,自净机制文档,"经验库自净机制存在但未文档化,数字易被误读(P2-325)",第八十六章,第八十六章,待文档完善,P2-325 ...自净机制...但未文档化 | 文档完善,部分修复,experience_pool.py:114 _auto_clean_thread / :477 run_pollution_cleanup(第65批) / :393 _auto_clean_enabled 机制已存在；文档化程度未核,中,补自净机制文档
-D107,P2,架构级路由治理,架构级路由治理:请求/数据/信号路由与来源治理不足(P2-330),第八十七章,第九十章,待P0后专项,P2-330 | 架构级路由治理 | ... | P0后专项,未动,架构级路由治理，无对应改造,低,P0后专项
-D108,P2,待修复项,第49批验收列出的待修复债务(P2-336),第九十一章,第九十一章,待修复,待修复债务：P0×4、P2-329、P2-334、P2-335、P2-336、P2-337（共9项）,待核,第49批验收待修复项 P2-336，正文未展开,低,需验收报告核对
-D109,P2,待修复项,第49批验收列出的待修复债务(P2-337),第九十一章,第九十一章,待修复,待修复债务：...P2-336、P2-337（共9项）,待核,第49批验收待修复项 P2-337，正文未展开,低,需验收报告核对
-D110,P2,摸底新登记,"第二轮摸底新发现债务,正文未展开(P2-340)",第九十二章,第九十二章,状态未知(未展开),| 新发现技术债务 | P2-340、P3-338、P3-339、P3-341（共4项） |,待核,第二轮摸底新登记 P2-340，正文未展开,低,展开后再判
-D111,P2,摸底新登记,"第三轮摸底新发现债务,正文未展开(P2-342/343)",第九十三章,第九十三章,状态未知(未展开),| 新发现技术债务 | P2-342、P2-343、P3-344...（共5项） |,待核,第三轮摸底新登记 P2-342/343，正文未展开,低,展开后再判
-D112,P2,静默异常治理,静默异常捕获治理(第53批计划项)(P2-348),第九十四章,第九十六章续,待治理(第53批),第53批：静默异常捕获治理...（P2-348、P2-349、P2-358）,部分修复,silent_exc 在 chat_service.py 等广泛使用；静默异常仍需全量梳理,中,继续清理 silent_exc 覆盖
-D113,P2,print清理,print调试输出清理(第53批计划项)(P2-349),第九十四章,第九十六章续,待清理(第53批),第53批：...print调试输出清理...（P2-348、P2-349、P2-358）,未动,仍存在裸 print：PatchAutoApprover.py:307/330、consumers.py:65,高,替换为 get_module_logger
-D114,P2,备份目录策略,备份目录清理策略(第53批计划项)(P2-350),第九十四章,第九十四章,待治理(第53批),| 新发现技术债务 | P1-347、P2-348、P2-349、P2-350...（共6项） |,未动,.bak_batch64~81 共18个批次备份目录仍在堆积，无清理策略代码,高,制定备份目录清理策略
-D115,P2,全局变量并发,全局变量并发访问检查(第54批计划项)(P2-353),第九十五章,第九十六章续,待检查(第54批),第54批：死代码价值挖掘（一）+ 全局变量并发访问检查（P2-353、P2-359）,未动,第54批计划项，无并发检查代码落地,低,远期专项
-D116,P2,直连写生产库,PulseHormones.py:390与main.py:510直连get_experience_pool()写生产库未经注入隔离(P2-356),第九十六章,第九十九章,待修复,P2-356 ...直连 get_experience_pool() 写生产库（未经注入隔离）,未动,organs/endocrine/PulseHormones.py:389-391 仍直调 get_experience_pool().record_experience()；main.py:540/571 同样直连写生产库，无注入隔离,高,改走注入隔离通道
-D117,P2,摸底新登记,"第五轮摸底新发现债务,正文未展开(P2-358/359/360)",第九十六章续,第九十六章续,状态未知(未展开),| 新发现技术债务 | P1-357、P2-358、P2-359、P2-360...（共7项） |,待核,第五轮摸底新登记 P2-358/359/360，正文未展开,低,展开后再判
-D118,P2,chat_service接线,"chat_service架构缺口需详细设计(P2-364,第50批单独立项)",第九十七章,第九十九章,待详细设计,P2-364 chat_service 接线启动 ...架构缺口，需详细设计（第50批单独立项）,部分修复,main.py:3585-3587 WebChatServer(port=5052) 已启动并注入 info_field/pulse_core；chat_service.py 存在,高,架构缺口已接线，补详细设计文档
-D119,P2,face_welcome优化,face_welcome流程改chat_service.py+灰度开关需重启(P2-365),第九十八章,第一百零三章,待重启验证,face_welcome 优化实施（方案A）...改 chat_service.py + 灰度开关（需重启）,待重启验证,functions/chat/chat_service.py:169 face_welcome_{ts} 相关id；灰度开关需重启验,中,重启后验灰度
-D120,P2,partial_fix范围,partial_fix剩余项未按diff区域限定(P2-367),第九十九章,第一百章,待评估,P2-367 | partial_fix的剩余项未按diff区域限定... | 🟡待评估,部分修复,config.py:4522 改动区域统计开关；patch_active_reprobe.py:468-472 说明 diff 区域口径,中,开启开关收敛 partial_fix
-D121,P2,补丁静态检测,code_optimization无静态检测器致2条补丁不可判定(P2-368),第九十九章,第一百章,待评估,P2-368 | code_optimization无静态检测器 → 该2条补丁不可判定 | 🟡待评估,部分修复,patch_active_reprobe.py:255-256 code_optimization 无可靠静态判据→恒返回空→not_applicable，已从不可判定改为显式不可用,高,补静态判据或保持 not_applicable
-D122,P2,旧体系评分,旧体系evolution_health有模块就给分(v2替代后消除)(P2-369),第九十九章,第一百章,待观察(v2替代后消除),P2-369 | 旧体系evolution_health有模块就给分 | v2替代后消除 | 🟡待观察,已修复,main.py:1038-1045 evolution_health 接线 SafeEvolutionExecutor；SelfAwarenessEngine.py:1016 integrate_evolution_health / :1817 _score_evolution_health；tests/test_evolution_health_m58.py,高,旧体系已被v2替代
-D123,P2,框架改写守卫,P2-370族测试框架改写守卫扩展,第一百零三章,第一百二十五章,待扩展,属 P2-370 族的扩展（第54批已为 test_serp_cleanup_m50 加过框架改写守卫）,待核,P2-370族框架改写守卫扩展，无对应代码点核到,低,逐测试核对
-D124,P2,测试依赖停机,test_serp_cleanup_m50生产现状断言依赖框架停机(P2-372),第一百零三章,第一百零三章,待解(3例skip),P2-372 ...生产现状断言依赖框架停机...3例 skip,未动,tests/test_serp_cleanup_m50.py 生产现状断言仍依赖框架停机，3例skip,中,解耦停机依赖
-D125,P2,flaky分片测试,分片测试与框架审计并发干扰(复发第2次)(P2-373),第一百零三章,第一百零三章,待根治(重跑即过但复发),P2-373 ...分片测试与框架审计并发干扰（复发第2次）...原样重跑 445 passed,未动,flaky分片测试与框架审计并发，无根治代码,中,隔离分片审计
-D126,P2,过期检测并存,"两套过期检测并存,启用PatchAutoApprover时处理(P2-382)",第一百零九章,第一百一十章,待后续批次,P2-382 两套过期检测并存 → 后续批次（启用PatchAutoApprover时）,部分修复,PatchManager.py:531-532 注释明指 PatchAutoApprover.is_stale 已deprecated且零生产调用；:552 第54批T3.2 在 PatchManager 内新建 stale 标记,高,旧检测随AutoApprover退役清理
-D127,P2,债务总览表过时,"新增债务P2-308~386未更新到总览表,总览表仍为v9.9版本(P2-386)",第一百零九章,第一百三十六章,待更新(状态管理不规范),新增债务（P2-308~P2-386...）没有更新到总览表；总览表是v9.9版本，已严重过时,未动,技术债务台账仍持续登记至D150；总览表v9.9未更新,中,刷新总览表
-D128,P2,指标函数复用,"指标函数复用已回填结论(P2-389,T6发现)",第一百二十五章,第一百二十五章,待确认,**P2-389 · 指标函数复用已回填结论（P2，T6 发现）**,待核,P2-389 指标函数复用已回填结论，无代码点核,低,核对回填报告
-D129,P2,双腿搜索冷却,"双腿搜索冷却(P2-402,随渠道优化缓解)",第一百三十章,第一百三十章,观察中,| P2-402 | 双腿搜索冷却 | 🟡 观察中 | 随渠道优化缓解 |,已修复,config.py:2043 deep_search_cooldown=30 秒；渠道优化已缓解,高,维持冷却
-D130,P2,五维共振名不副实,五维共振'仅1.5维'系字段名误判(第三方grep复数space_paths/intent_labels，实现用单数),第一百七十五章,第一百七十五章,待补实现/修正叙事,五维共振名不副实：宣称五维，实际只实现1.5维（space_paths/intent_labels全库零消费）,叙事/文档名实不符,ResonanceEngine.py:705/722-727 _calculate_dimensions五维齐备；_calc_space_dim:1016用单数node.space_path(_space_index:51/302-306真实维护)；_calc_logic_dim:1053用event_type/trigger_reason/keywords；_calc_time_dim:1080激活新鲜度+source_timestamp(Cython),高,文档/叙事对齐；可选增强logic维输入丰富度；无需补五维实现
-D131,P2,增量保存名不副实,宣称增量保存实际整读479MB→内存合并→整体重写(第三方P2),第一百七十五章,第一百七十五章,待改造,增量保存名不副实：宣称增量，实际是整读479MB→内存合并→整体重写,未动,PulseSnapshot.py:545 _incremental_save 仍整读合并重写；:932 _m67_incremental_log_save 为真JSONL增量路径但由 :891 _m67_incremental_log_enabled() 门控，config SNAPSHOT_USE_INCREMENTAL_LOG=False→回退整读（两条路径勿混）,高,开启 SNAPSHOT_USE_INCREMENTAL_LOG 或改造 _incremental_save
-D132,P2,继承关系失实,路线图称InfoField继承OscillonField实际继承SilentLogMixin(第三方P2),第一百七十五章,第一百七十五章,待修正文档/代码,OscillonField继承关系失实：路线图说InfoField继承OscillonField，实际继承SilentLogMixin,已失效/不再适用,nucleus/field/InfoField.py:122 class InfoField(SilentLogMixin)，并未继承 OscillonField(ABC)(OscillonField.py:49)；路线图表述失实，代码已证,高,修正路线图文档继承关系
-D133,P2,genetic占位层,"genetic层是占位层:养育=计数器+日志,羁绊硬编码,同意闸门零发射方(第三方P2)",第一百七十五章,第一百七十五章,待实现(能力夸大),genetic层是占位层：养育=计数器+日志，羁绊硬编码，同意闸门零发射方——与真实器官平列构成能力夸大,部分修复,organs/genetic/PulseNurture.py:57 仍为阶段计数器+日志；PulseBonding.py 记录互动；但 P3-5 已补发射方 PulseHormones.py:320（此前bonding有订阅无发射）；PulseConsent 同意闸门仍零发射,中,nurture/consent 补真实逻辑
-D134,P2,肺隐喻漂移,"肺实为LLM模型选型调度器,不是呼吸器官(第三方P2)",第一百七十五章,第一百七十五章,待修正叙事/实现,肺的隐喻与实现漂移：肺实为「LLM模型选型调度器」，不是呼吸器官,未动,organs/body/PulseLung.py:5「脉冲驱动肺·模型调用器官」/ :11 收 LungEvent.SELECT_MODEL 选模型并调大模型——实为LLM选型调度器，非呼吸器官，隐喻漂移依旧,高,修正叙事或重命名
-D135,P2,README数字过期,"README六项数字全部过期(53批vs实际75批,2471vs3311,宣称0失败vs实际125)(第三方P2)",第一百七十五章,第一百七十六章,待文档更新,README六项数字全部过期：53批vs实际75批、2471例vs3311例、宣称0失败vs实际125失败,未动,README.md:12/46 仍写「已完成53批任务」，实际已施工至第81批,高,刷新README批次/测试数/失败数
-D136,P3,硬件自适应L3,硬件自适应动态资源分配试点(P3-5),第六十二章,第六十三章,远期规划(第34批可试点),| P3-5 | 硬件自适应动态资源分配（5项子任务） | 中 | 第34批可试点 |,未动,P3-5 硬件自适应动态资源分配，远期试点，无落地,低,远期试点
-D137,P3,能力整合路线图,能力整合路线图(P3-6),第四十章,第四十章,待后续,| P3-6 | 能力整合路线图 | 📋 待后续 |,未动,P3-6 能力整合路线图，远期,低,远期
-D138,P3,代码分析流水线,独立进程代码分析服务+完整闭环流水线(P3-7),第四十章,第六十章,设计完成待实施,P3-7/P3-8子任务，设计已完成待实施,未动,P3-7 独立进程代码分析服务，设计完成待实施,低,待实施
-D139,P3,知识质量修复闭环,知识质量自动修复闭环(P3-8),第四十章,第四十三章,待后续,| P3-8 | 知识质量自动修复闭环 | 📋 待后续 |,未动,P3-8 知识质量自动修复闭环，远期,低,远期
-D140,P3,矛盾人工确认,语义类矛盾待人工确认清单导出(P3-9),第四十一章,第四十一章,待后续批次,| P3-9 | 语义类矛盾待人工确认清单导出 | 📋 待后续批次 |,未动,P3-9 语义矛盾人工确认清单导出，远期,低,远期
-D141,P3,智能路由评估,"智能路由评估(P3-185,第34批计划)",第五十八章,第六十三章,远期规划(第34批),第34批：...P3-185（智能路由评估）...,未动,P3-185 智能路由评估，远期第34批计划,低,远期
-D142,P3,ReportConsumers格式,ReportConsumers异常格式ERROR(P3-315),第八十四章,第八十五章,待处理,- P3-315：ReportConsumers异常格式ERROR,部分修复,"nucleus/reporting/consumers.py:46/62 已用 get_module_logger(component=""ReportConsumers"") + :65 print兜底；异常格式ERROR问题未完全复现",中,运行期观察异常格式
-D143,P3,摸底新登记,"第二轮摸底新发现P3,正文未展开(P3-338/339/341)",第九十二章,第九十二章,状态未知(未展开),| 新发现技术债务 | P2-340、P3-338、P3-339、P3-341（共4项） |,待核,第二轮摸底P3-338/339/341，正文未展开,低,展开后再判
-D144,P3,摸底新登记,"第三轮摸底新发现P3,正文未展开(P3-344/345/346)",第九十三章,第九十三章,状态未知(未展开),| 新发现技术债务 | ...P3-344、P3-345、P3-346（共5项） |,待核,第三轮摸底P3-344/345/346，正文未展开,低,展开后再判
-D145,P3,摸底新登记,"第四轮摸底新发现P3,正文未展开(P3-351/352)",第九十四章,第九十四章,状态未知(未展开),| 新发现技术债务 | ...P3-351、P3-352（共6项） |,待核,第四轮摸底P3-351/352，正文未展开,低,展开后再判
-D146,P3,摸底新登记,"第五轮死代码轮新登记P3,正文未展开(P3-354/355/356)",第九十五章,第九十五章,状态未知(未展开),| 新登记技术债务 | P2-353、P3-354、P3-355、P3-356（共4项） |,待核,第五轮死代码P3-354/355/356，正文未展开,低,展开后再判
-D147,P3,摸底新登记,"第五轮nucleus摸底新发现P3,正文未展开(P3-361/362/363)",第九十六章续,第九十六章续,状态未知(未展开),| 新发现技术债务 | ...P3-361、P3-362、P3-363（共7项） |,待核,第五轮nucleus摸底P3-361/362/363，正文未展开,低,展开后再判
-D148,P3,UI远期需求,"图形化界面设计(待启动)(P3,第一百二十九章)",第一百二十九章,第一百二十九章,远期待启动,远期需求记录 - 图形化界面设计（待启动）,未动,图形化界面远期待启动；functions/health_ui.py 仅健康面板雏形,中,远期立项
-D149,P3,错峰调度长期架构,"重操作错峰调度长期架构(用户提出,临时拉平已做)",第一百三十九章,第一百四十二章,远期(临时拉平阶段1已验收),"重操作错峰调度长期架构思路（用户提出）；临时拉平阶段1已验收,长期架构待做",部分修复,config.py:4551 第61批P1临时拉平；PulseHeart.py:679/PulseCodeLearner.py:218 错峰偏移已做；长期架构未建,高,临时拉平已验收，长期架构待做
-D150,P3,quit补丁提示,"quit时补丁确认提示(P3-新,第59批T4)",第一百三十三章,第一百三十六章,待实施,T4: P3-新 quit时补丁确认提示,已修复,main.py:3325 第59批T4 用户主动退出(SIGINT/SIGTERM)时待应用补丁确认提示；:3401 标记主动退出,高,维持
-D151,P0,存储/Parquet,Parquet回读丢evol_level+8字段，层级保真0%，verify只数总行数,178.3,178.3/179,G0-a 启用即毁，第81批修,Parquet回读丢evol_level+8字段..._m68_verify_parquet只校验总行数→损坏不可检出,第81批施工中,PulseSnapshot.py:1170/1188必含7新列；:2382写/:2450读已补；:1281 verify升级列集合+版本+分层FAIL,高,T1完工后按A1-A8三遍保真验收，通过才许重开PARQUET_AS_PRIMARY_STORAGE
-D152,P0,存储/冷热加载,_m70清空L2/L3正文，lazy_ids无读取方,178.3,178.3/179,G0-b 重开即85.9%正文空，第81批修,_m70_apply_hot_cold_load就地清空L2/L3 value/linked_nodes，_m70_lazy_ids无读取方,第81批施工中,PulseSnapshot.py:1655/1720已建物化API；但PulseNodePool.py:648 get()未调:2575、set_cold_recall_source无调用方,高,T2收尾：KAL/get/胃/对话热路径接线materialize，接set_cold_recall_source到pool.recall_cold_nodes_batch
-D153,P0,存储/保存,in-flight卡死、超时判定在finally后、退出只等90s假承诺增量恢复,178.3,178.3,G0-c 大部分缓解可能有残留,in-flight标志永久卡死(:770)...判定写在finally之后...线程不结束永不评估...退出只等90s即放行,大部分修复有残留,PulseSnapshot.py:765入口watchdog卡死重置+snapshot_stalled；:813-823超时仍post-hoc；main.py:2718 join(300s)取代90s删假承诺,中,残留：finally后超时在线程挂起时不评估；watchdog内补超时硬告警即闭环
-D154,P0,存储/增量日志,jsonl把全部存活ID当删除集写action=delete，重放得0节点,178.3,178.3/179,G0-d 启用即毁库，第81批修,"_get_changed_nodes返回(changed,current_ids)，_m67_incremental_log_save按(changed,deleted)解构→全部存活ID当删除集",第81批施工中,PulseSnapshot.py:950删除集改_prev_map-current_ids；:955比例熔断降级全量；:979行checksum；:1037重放按ts定序幂等,高,T3完工后故障注入：1变更4存活验证delete集、比例熔断、checksum不符跳过
-D155,P0,存储/原子写,os.remove造主文件消失窗口、except无条件删tmp、0节点软校验、48MB孤儿,178.3,178.3,G0-e 大部分缓解可能有残留,用os.remove(target)再os.replace，Windows占用时制造主文件已消失窗口；except无条件删tmp；checksum不匹配也允许启动,大部分修复有残留,PulseSnapshot.py:875/1491纯os.replace；:879失败留.failed副本；:396保存<50%保护；:2020 checksum mismatch仅WARNING；磁盘snapshot_0g60at6z.json 46.2MB仍在,中,残留：checksum仍软校验；孤儿快照未纳入cleanup(只清tempdir/snap_t4_)
-D156,P0,Web/控制台,WebChat回复被GBK print崩溃吞掉，/replies恒空,178.3,178.3,J-P0-1 一行修，第80批已修,chat_service.py:217 print中emoji在stdout重定向时GBK UnicodeEncodeError→handler中止→:220 _push_reply永不执行,已修复,"functions/chat/chat_service.py:17-18 reconfigure(utf-8,errors=replace)；:245 _push_reply前置print(:249)；80批重启对话回复正常",高,已闭环；保留回归test_gbk_reply_m80
-D157,P0,Web服务,单线程HTTPServer队头阻塞，慢端点可使5051停服10分钟,178.3,第101批 T-101b,J-P0-3 未动，82批,单线程HTTPServer队头阻塞：/evolution/data与/knowledge-graph.json任一可使5051整体停服约10分钟,已闭环：生产树裸 HTTPServer( 构造=0；health_ui.py:1595 与 web_chat.py:637 均 ThreadingHTTPServer(第97批 T-97f + 第99批 T-99b 已修),functions/health_ui.py:1595 / functions/web_chat.py:637 均 ThreadingHTTPServer；grep 裸 HTTPServer(=0；全仓 0.0.0.0 监听=0,中,★映射：烛微第1期 N9 ↔ 台账 D157；第97批 T-97f + 第99批 T-99b 已闭环
-D158,P1,知识质量,Parquet不持久化quality_flag/quality_reason，非clean46条重启被现算覆盖,178.3,178.3/179,D-x1 80批回退后暂失效，81批重开前必修,Parquet不持久化quality_flag/quality_reason...重启后人工仲裁被现算覆盖，PollutionTagger短路永不命中,第81批施工中,PulseSnapshot.py:2390-2391写出/:2456-2457读回quality_flag/reason；当前PARQUET_AS_PRIMARY_STORAGE=False JSON主存储暂不影响,高,随T1验收：suspect/polluted节点往返验证仲裁标记不被现算覆盖
-D159,P1,知识质量,Parquet丢source_time/acquired_time/source_timestamp，新鲜度衰减失效,178.3,178.3/179,D-x2 80批回退后暂失效，81批重开前必修,Parquet丢source_time/source_timestamp/acquired_time(JSON全有非零)，新鲜度/时效衰减失效,第81批施工中,PulseSnapshot.py:2387-2389写出/:2453-2455读回三时间字段；当前JSON主存储,高,随T1验收：时间字段往返非零校验
-D160,P1,存储/引用完整性,悬空引用133773条，落盘链路无引用完整性过滤,178.3,第102批 T-102a,D-x3 未动，82批,悬空引用133773条(sem60856/2.68%+linked72917/3.28%)，落盘链路无引用完整性过滤，多跳检索静默截断,已闭环,PulseSnapshot.py:1524/1429 落盘前 _m102_filter_dangling_edges(+_m102_sidecar_node_ids L2930，无节点池返回None⇒跳过绝不误删)；存量 tools/m102_data_governance.py --apply a 清理133824条(主快照)+133824条(parquet副本),高,已闭环：落盘链路已加引用完整性检查 + 存量全部清零(第102批)；开关 ENABLE_M102_DANGLING_EDGE_GUARD 默认True
-D161,P1,向量,孤儿向量4646占27.5%，remove生产0调用，reconcile只单向补码,178.3,第102批 T-102b,D-x4 未动，82批,孤儿向量4646个占27.5%...VectorStore.remove()生产0调用、AsyncEncodeQueue.reconcile只单向补码,已闭环,PulseNodePool.remove() 级联 get_vector_store().remove；AsyncEncodeQueue.reconcile 反向 reap_orphans；VectorStore.reap_orphans 新增；存量清理孤儿向量4842条(17800→12958，npz 34.77→25.31MB),高,已闭环：删节点级联remove + reconcile反向回收 + 存量清零(第102批)；开关 ENABLE_M102_VECTOR_CASCADE_REMOVE / ENABLE_M102_ORPHAN_VECTOR_REAP 默认True
-D162,P1,存储/门禁,缺字段级存活率硬门禁，大小/行数门禁无感,178.3,178.3,D-x5 未动，82批,缺字段级存活率硬门禁：内容掏空体积只降0.3%，大小/行数门禁无感；需verify_field_survival.py偏移>2%FAIL,未动,无tools/verify_field_survival.py；_m81校验偏schema列集合而非内容存活率,低,82批补字段存活率门禁工具并入A1-A8验收
-D163,P1,可观测/告警,无快照停滞告警、阈值硬编码；冷存skipped仅DEBUG静默丢节点,178.3,178.3/179,D-x6+G-P1 skipped升ERROR一点在81批,无快照停滞/保存静默告警，阈值全硬编码；冷存compaction skipped_files>0仅DEBUG...需升ERROR+汇总,第81批施工中,PulseNodePool.py:3056-3059 skipped>0且开关开→一条ERROR含数+样本；停滞告警/阈值config化未做,中,T4-⑥做一半；剩余停滞告警+pulse_errors接真实故障源入82批(D169同项)
-D164,P1,存储/冷存,冷存5483碎文件、启动顺序反、逐节点全扫N平方、召回硬编码L1,178.3,178.3/179,G-P1-1 第81批修,5483小parquet碎文件...召回需批量write_to_dataset+node_id→offset索引..._recall_cold_node硬编码evol_level=L1,第81批施工中,PulseNodePool.py:2489 recall_cold_nodes_batch一次整读；:2543侧车索引read_row_group直读；:2530注释确认硬编码L1已修,高,T4收尾：冷召回性能基准1000节点<5-10s，验先compaction后lazy顺序
-D165,P1,存储/结构冗余,sem2267630条占85.4%，linked_nodes是sem近乎纯冗余投影,178.3,第102批 T-102c,G-P1-2 未动，82批；重复挂账→D039,sem2267630条/408.6MiB占85.4%...linked_nodes是sem的近乎纯冗余投影(Jaccard0.8972),已闭环,PulseNode.from_dict L391 按 semantic_relations 派生 linked_nodes；to_dict「可派生才不落盘」_m102_linked_derivable(零丢失判据=set(linked)==set(sem目标))；存量 --apply c 先合并19808条linked独有边入sem再清空linked_nodes,高,已闭环：冗余投影移除，只留sem一份+查询时动态计算；主快照522.39→456.83MB(-65.56MB/-12.55%)(第102批)；开关 ENABLE_M102_LINKED_NODES_DERIVED 默认True
-D166,P1,备份策略,3份.bak 1.4GB、选优len>=4000即break退化取最新、无外介质/无RPO,178.3,178.3,G-P1-4 选优已修，其余未动,_load_from_backup选优len>=4000即break(:1494)退化为取最新；backups与data同盘；多域零备份；无RPO,大部分修复有残留,PulseSnapshot.py:1863-1886综合评分选优(节点数+L2/L3+checksum+时间衰减)，旧>=4000 break已删；外介质/RPO/跨域备份未做,中,选优根因已闭；外介质+RPO+跨域备份入82批
-D167,P1,对话/correlation,搜索终止回退丢correlation_id，注册键与pop键必不等,178.3,178.3,J-P1-1 未动，82批,注册键=question[:80](:1386)，pop键=控制器改写后的search_topic(:3378)，两键必不等→空cid→结构性禁言,未动,PulseInnerWorld.py:1386注册search_query[:80]；:3378 pop(search_topic)；两键不一致根因未改,中,82批：correlation注册表改用稳定id而非改写后topic
-D168,P1,对话/净化,短答案被净化器误杀；打招呼被QICA误路由百科,178.3,178.3,J-P1-2 净化器已有S6短答案保留守卫,_sanitize_internal_content(S6判据)把7字问候清成我还需要再想想；打招呼被QICA误路由百科(置信0.49-0.50),部分修复,PulseInnerWorld.py:17407-17414短答案保留守卫(仅前缀删且本短则保留DEBUG)；QICA问候路由阈值未核改,中,净化误杀半已闭；QICA问候路由与置信门槛需另核
-D169,P1,可观测,9类故障面板/HTTP全盲，ERROR不进error_snapshots，5051停服零感知,178.3,178.3,J-P1-3 未动，82批,9类故障在面板/HTTP全盲；logger-ERROR不进error_snapshots；5051自身停服10分钟框架零感知,未动,未见pulse_errors接真实故障源、ERROR入error_snapshots改造；与D163同项,低,82批：ERROR入error_snapshots+面板暴露真实故障
-D170,P1,Web/安全,/params/apply_preset以GET执行参数变更，本机CSRF面,178.3,第101批 T-101b,J-P1-4 半改不一致,/params/apply_preset以GET执行参数变更(health_ui.py:1522-1539)，本机CSRF面,已闭环：前端改 POST(health_ui.py:767)；服务端 do_POST 接管 apply_preset(:876/:884→_serve_apply_preset:1564)；do_GET(:806-850) 已摘除该分支；并加 _is_same_origin 同源校验(:852),functions/health_ui.py:767 POST; :876 do_POST; :884-885 apply_preset→_serve_apply_preset; :852 _is_same_origin; do_GET 内无 apply_preset 分支,中,★映射：第99批 T-99c ↔ 台账 D170；GET 改参 CSRF 面已闭合
-D171,P1,运行期/稳定性,不具备>4h无人值守：RSS锯齿7GB/h、句柄泄漏、pulse劣化3-17倍,178.3,178.3,L活体 未动，需长运行重验,不具备>4h无人值守：RSS谷底5→15GB锯齿...句柄2080→2683单调泄漏...pulse劣化3-17倍,未动,代码级无泄漏修复/长运行看门狗；80批重启短时内存4.2GB稳定，但框架已停，>4h未重测,低,重启后跑>4h无人值守：RSS/句柄/线程/pulse_avg连续采样再判定
-D172,P1,事件流,21个死订阅、15类发射无消费、3个零发射器官、心脏退化为定时器驱动,178.3,178.3,A事件流 未动，82批专项,21个运行期死订阅；15类发射无消费；3个零发射器官；心脏PulseHeart.py:689-816排程字典24事件退化为定时器,未动,organs/body/PulseHeart.py:473 _check_scheduled_tasks定时器排程、:689 knowledge_purge；死订阅未清,低,82批事件流专项：死订阅清理+发射消费对账+零发射器官处置
-D173,P1,宪法/架构,五维权重双源(一死一活违单一来源)+main.py横切直调+organs模块级可变全局,178.3,178.3,B宪法 未动，82批专项；重复挂账→D130,五维共振权重记忆40/空间30/逻辑15/时间10/状态5四处定义违单一来源，实测仅1.5维有效,未动,config.py:668 RESONANCE_WEIGHTS全代码0引用(死定义) vs ResonanceEngine.py:38 WEIGHTS实际生效；main.py:1302/1344/1392/1501/1507/1998/2669直调器官；organs~40+模块级dict(PulseNeurotransmitters.py:24 NEUROTRANSMITTERS/PulseKnowledgeRetriever.py:20 ORGAN_ALIAS_MAP/:80 CORE_CONCEPT_DEFS为可变容器),中,第82批：权重单一来源收敛(删config死定义或改引用)+main横切直调收敛+模块全局冻结
-D174,P1,复杂度,PulseInnerWorld.py 上帝文件(重复编号)→并入D021,178.3,178.3,C复杂度 未动，82批专项；重复挂账→D021,lizard 5984函数CCN≥20=329...PulseInnerWorld.py 23145行上帝文件(D021续),重复挂账,同D021：organs\brain\PulseInnerWorld.py 23145行；本号为第三方冗余编号,高,重复挂账→D021；第82批以D021为主票拆分
-D175,P1,自进化/审批治理,进化审批治理残留组：非核心文件存在完整「免签→落盘→自动重启」通路,烛微第1期,烛微第1期,烛微第1期 N1【P1-1】（烛微第1期报告，已附复现脚本）,① _m85_local_low_risk_auto_apply 显式设计为总开关False时仍生效，local_auto_apply_enabled 键不在config→缺省即开；② PulseCodeLearner apply_now 路只查 status==approved，dynamic_test.passed 缺省 True（异常=假通过）；③ 落地侧三条触发口均不读 auto_apply_enabled；④ _check_patch_safety 对 approved 直接 return safe(人工批准),本批 T-96b 已处置①②④；③改为「区分机器/人工」口径（星轨裁决）,nucleus/reasoning/PatchManager.py:_m85_local_low_risk_auto_apply/:423 _check_patch_safety/:2399 apply_all_pending 入口收口；organs/brain/PulseCodeLearner.py:3493 dynamic_test 缺省改 False,高,★映射：烛微第1期 N1 ↔ 台账 D175；已显式登记 config(local_auto_apply_enabled/allow_core_auto_apply 默认 False)；④ 机器自动批准(auto_approved=True)须走完三关，人工批准维持放行语义
-D176,P1,自进化/账本,进化闭环有效性不达标 + 账本假成功（applied 但磁盘无对应内容）,烛微第1期,烛微第1期,烛微第1期 N2【P1-2】（烛微第1期报告，已附复现脚本）,67 条 applied 中 2 条磁盘无对应内容——含今日 18:13:11 的 LLM 补丁：当前 PulseInnerWorld.py 与其落地前备份逐字节相同、目标符号 _calc_math_question 全文件 0 次，账本仍记 applied=True/effect_verified=True/effectiveness=1.0,本批 T-96c 已处置：已加落地后磁盘复核 + 护栏函数禁补丁清单 + 历史 2 条更正留痕（双写）,nucleus/reasoning/PatchManager.py:applied 后回读磁盘复核 + _M96_NO_AUTO_PATCH_METHODS；data/patches/patch_history.json:patch_llm_1789886168_789a / patch_llm_1789890006_e8ba,高,★映射：烛微第1期 N2 ↔ 台账 D176；★烛微给出的两条证据路径【均未复现】（目标符号0次=0条、与备份逐字节相同=0条）；可靠判据应为「original_code 在盘 & modified_code 不在盘」⇒ 复现 2 条，已更正；见留痕报告
-D177,P1,自进化/LLM通道,进化链 LLM 调用绕过肺熔断与并发控制（09-18 EXP-2 未修）,烛微第1期,烛微第1期,烛微第1期 N3【P1-3】（烛微第1期报告，已附复现脚本）,"今日 3,443 次进化链调用（占答案类请求 63%）走 ssrf_guard.safe_http_json 自有出口，不接 ChannelHealthTracker 熔断、不接并发闸；86% 失败率下仍无退避持续发起",本批 T-96a 已处置（灰度：ENABLE_EVOLUTION_USE_CHANNEL_POOL 默认 False）,nucleus/reasoning/SafeEvolutionExecutor.py:_m96_channel_pool_on / _m96_select_channel / _m96_record_channel_result；config.py:ENABLE_EVOLUTION_USE_CHANNEL_POOL,高,★映射：烛微第1期 N3 ↔ 台账 D177；已具备渠道池选择 + 熔断跳过 + 健康度回写（与对话链路同账本）；开关默认关，需重启后灰度验证再打开；★子进程 run_in_subprocess 路径仍直连 REMOTE_API_CONFIG（未覆盖）
-D178,P1,数据/层级,"冷层 5,504 行错误层级固化（09-18 层级塌缩的化石层）",烛微第1期,烛微第1期,烛微第1期 N4【P1-4】（烛微第1期报告，已附复现脚本）,"data/knowledge/cold/ 全部 5,504 行 evol_level 固化为 L1，与热层真值对照：3,089 应为 L2、2,395 应为 L3，零个真 L1；其中 20 个仅存于冷层的节点层级不可恢复",未动（本批未覆盖，待立项）,data/knowledge/cold/ 冷 flat 文件,高,★映射：烛微第1期 N4 ↔ 台账 D178；离线一次性用热层真值重写冷层；20 个 cold-only 节点需人工裁决；等待冷驱逐首次发生做阳性验证
-D179,P1,可观测性/日志,F3 日志聚合器是死机制，且注释宣称其在工作,烛微第1期,烛微第1期,烛微第1期 N5【P1-5】（烛微第1期报告，已附复现脚本）,"logger.py:483-488 注释称 filter 挂在 root 上同时作用于两个 handler——Python 语义相反：logger 级 filter 不作用于子 logger 传播的记录；今日 20,186 行 DEBUG 中「(聚合」标记 0 次",未动（本批未覆盖，待立项）,nucleus/logger.py:483-488,高,★映射：烛微第1期 N5 ↔ 台账 D179；file_handler.addFilter(...) + console handler 同步挂载；加「聚合器实生效」断言测试（0.5 人时）
-D180,P1,数据/一致性,"悬空边 133,767 条持平未消 + 孤儿向量 27.3% 单调上升（删除只删一半）",烛微第1期,烛微第1期,烛微第1期 N6【P1-6】（烛微第1期报告，已附复现脚本）,"孤儿向量 4,747/17,404=27.3%（一周 +101），VectorStore.remove() 生产调用点仍为 0，检索 TopK 约 1/4 命中不存在的知识；另有 18 个热节点缺向量",未动（本批未覆盖，待立项）,nucleus/mnemosyne/VectorStore.remove() 零生产调用点,高,★映射：烛微第1期 N6 ↔ 台账 D180；删除路径三写一致（快照/向量/边）收口 + 启动期 reconcile 补向量删孤儿；悬空边一次性清洗
-D181,P1,健壮性/静默except,静默 except 存量：84.2% 不上报，安全关键路径 TIER1 = 192 处,烛微第1期,烛微第1期,烛微第1期 N7【P1-7】（烛微第1期报告，已附复现脚本）,"608 文件 3,430 个 except 中 2,889 个吞异常不上报；TIER1（宽捕获+完全静默+含磁盘或网络IO）192 处，最危险：ParamPatchManager._save_history:507 补丁历史写盘失败零留痕",未动（本批未覆盖，待立项）,nucleus/reasoning/ParamPatchManager.py:507；PatchAutoApprover.py:160,高,★映射：烛微第1期 N7 ↔ 台账 D181；不要求清零；TIER1 192 处优先补留痕（每处 1 行），做成门禁防增量
-D182,P1,器官/胃,胃 _balanced_json_extract 必然 TypeError，策略自 26 批起从未生效,烛微第1期,第101批 T-101b,烛微第1期 N8【P1-8】（烛微第1期报告，已附复现脚本）,PulseStomach.py:2233 定义缺 self 缺 @staticmethod，而 :732 以 self._balanced_json_extract(x) 调用 → 每次调用必抛 takes 1 positional argument but 2 were given，被外层 except 吞成 DEBUG（今日 ×10）,已闭环：PulseStomach.py:2233 已加 @staticmethod(原缺 self/@staticmethod 致每次调用必 TypeError 被外层 except 吞成 DEBUG)；:732 以 self._balanced_json_extract(x) 调用现已生效,organs/body/PulseStomach.py:2233 @staticmethod; :732 调用形态一致(staticmethod 可静态调用),高,★映射：烛微第1期 N8 ↔ 台账 D182；第97批顺手并入已闭环
-D183,P1,可观测性/服务,观测面仍是裸单线程 HTTPServer（09-18 J-P0-3 未修）,烛微第1期,第101批 T-101b,烛微第1期 N9【P1-9】（烛微第1期报告，已附复现脚本）,health_ui.py:1558、web_chat.py 均仍为 HTTPServer——09-18 实测一次 /evolution/data 探测拖停 5051 约 10 分钟,已闭环：health_ui.py:1595 ThreadingHTTPServer(与 D157 同修；第97批 T-97f + 第99批 T-99b),"functions/health_ui.py:1595 ThreadingHTTPServer(('127.0.0.1', self.port), HealthHandler)",高,★映射：烛微第1期 N9 ↔ 台账 D183；与 D157 合并自灭，已闭环
-D184,P1,流程/台账,烛微 09-18 六项 P0 无一进台账（流程性盲区）,烛微第1期,烛微第1期,烛微第1期 N10【P1-10】（烛微第1期报告，已附复现脚本）,台账 grep WinError/停摆/首报/限流/聚合 = 0 行。三方审计最重要的输入没有进入两方工作流的事实清单。这是机制问题不是态度问题：建议把「外部审计报告 → 台账条目」做成固定投递工序（含 P级映射）,本批 T-96e 已处置：N1~N11 全部入台账 + P级映射表 + 固定投递工序 SOP 已建立,docs/分析报告/外部审计_台账投递工序.md（SOP + N↔D 映射表）；本 CSV D175~D185,高,★映射：烛微第1期 N10 ↔ 台账 D184；★新增 SOP：每份外部审计报告出稿后 24h 内完成投递；验收判据为「台账出现该报告期号」，并由批次交付报告回引
-D185,P1,数据/FAISS,FAISS 持久化链路死代码,烛微第1期,烛微第1期,烛微第1期 N11【P1-11】（烛微第1期报告，已附复现脚本）,faiss_store.save()（faiss_store.py:298）无任何生产调用点，data/knowledge/faiss/ 目录不存在——68-75 批宣称的 FAISS 能力实际持久态只有 vectors.npz（对齐良好）,未动（本批未覆盖，待裁决：接盘 or 除名）,nucleus/mnemosyne/faiss_store.py:298 save() 零调用点,高,★映射：烛微第1期 N11 ↔ 台账 D185；接盘或除名二选一，禁止「实现了但从不保存」的中间态
+﻿id,优先级,模块,问题简述,首次章节,最近章节,文档声称状态,原文关键句,实查状态,代码证据,置信度,处置建议,轨道,最后对账
+D001,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-1),第八十章,第一百三十六章,状态未知,| P0-1 | （早期运行时阻塞问题） | ❓ 状态未知 |,已修复,总账:119『P0-1~P0-7运行时阻塞第1-15批逐步修复已闭环』；main.py:3364 PulseFramework构造+回退+崩溃钩子已加固,高,关闭，标记已闭环,,
+D002,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-2),第八十章,第一百三十六章,观察中,| P0-2 | （早期运行时阻塞问题） | 🟡 观察中 |,已修复,总账:119 P0-1~P0-7已闭环；启动路径含SafeEvolutionExecutor回退(main.py:3376 rollback_last),高,关闭,,
+D003,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-3),第八十章,第一百三十六章,状态未知,| P0-3 | （早期运行时阻塞问题） | ❓ 状态未知 |,已修复,main.py:3385注释『P0-3统一自重启入口』→_spawn_self_restart():3226+PID登记已实现,高,关闭,,
+D004,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-4),第八十章,第一百三十六章,观察中,| P0-4 | （早期运行时阻塞问题） | 🟡 观察中 |,已修复,总账:119 P0-1~P0-7已闭环；main.py:3415全局crash hook写pulse_crash.log,高,关闭,,
+D005,P0,交互/任务两端,交互与任务两端系统性断链(P0-9),第四十一章,第一百三十六章,部分修复(剩余),| P0-9 | 交互与任务两端系统性断链 | ✅ 部分修复（第24-27批） |,大部分修复有残留,PulseCortex.py:3241 P0-9长答腰斩已修；PulseExpression.py:293 DIALOG_REPLY_TRUNCATE_CHARS默认1000可0不截断；多模态vision consts存在(const.py:47-382),中,复核多模态入模与TaskPipeline真实闭环,,
+D006,P0,工程纪律,工程纪律崩坏(P0-10),第四十一章,第一百三十六章,待处理(部分子问题已修复),| P0-10 | 工程纪律崩坏 | ⏳ 待处理（部分子问题已修复） |,部分修复,根目录无CI workflow(仅pulses/pulse_config.yaml)；PIW仍23145行；但logger.py:317 SafeRotatingFileHandler轮转已上,中,补CI/覆盖率门禁；God文件拆分仍挂,,
+D007,P0,智能外包/知识体系,智能100%外包+知识体系名实不符(P0-11),第四十一章,第一百三十六章,待处理,P0-11（智能外包）等大项分解为可执行的子任务,部分修复,self_inspector.py:2186 L1→L2(30条)→L3(20条)→L4本能(30天冷却)管道已建；但自主修复率仅~1.8%仍高度外包,中,L4本能真实产出待运行期验证,,
+D008,P0,自学习闭环,自学习闭环未有效降低大模型依赖(依赖度反升97%)(P0-250),第七十四章,第一百三十六章,状态未知,| P0-250 | 自学习闭环未有效降低大模型依赖（依赖度反升97%） | ❓ 状态未知 |,叙事/文档名实不符,闭环代码已建(trace_evolution_call/semantic_cache)，但自主修复率~1.8%、适合本地自动修~5.8%，依赖度未实质下降,中,按真实修复率重定义闭环成效，勿称已降依赖,,
+D009,P0,内在模型,框架孕育内在模型(自有灵魂)(P0-251),第七十四章,第一百三十六章,状态未知,| P0-251 | 框架孕育内在模型（自有灵魂） | ❓ 状态未知 |,叙事/文档名实不符,总账:201仅有战略方向+设计文档，无实际训练流程/自有模型产出代码；models/无训练产物,高,维持设计文档，勿宣称已孕育,,
+D010,P0,内在模型,内在模型孕育计划分析文档已生成(P0-252),第一百三十六章,第一百三十六章,待第三方反馈,| P0-252 | 内在模型孕育计划分析文档已生成 | ⏳ 待第三方反馈 |,已失效/不再适用,为里程碑事项(分析文档已交付)，非代码缺陷；后续已转入P0-251实施讨论,高,关闭为历史里程碑,,
+D011,P0,内在模型,内在模型方案数据前提失实(留存率仅2.4%)(P0-253),第七十四章,第一百三十六章,状态未知,| P0-253 | 内在模型方案数据前提失实（留存率仅2.4%） | ❓ 状态未知 |,大部分修复有残留,call_recorder.py已建(530行默认开)，data/llm_traces/calls_*.jsonl落盘149文件/9.84MB；历史2.4%为既成事实,中,数据管道已补，历史低留存不可逆,,
+D012,P0,LLM留存管道,LLM调用全程留存管道(零号工程)(P0-254),第七十四章,第一百三十六章,状态未知,| P0-254 | LLM调用全程留存管道（零号工程） | ❓ 状态未知 |,大部分修复有残留,nucleus/llm/call_recorder.py 530行存在，ENABLE_LLM_CALL_RECORDER默认True(:180)；LLMEvolutionEngine.py:16/SelfReflectionEngine.py:16导入；残留calls_20260913.jsonl 231条中146条为测试桩(write_guard.py:5),高,管道已建，需清洗测试桩污染,,
+D013,P0,依赖度指标,大模型依赖度指标口径错位(P0-262),第七十四章,第一百三十六章,状态未知,| P0-262 | 大模型依赖度指标口径错位 | ❓ 状态未知 |,大部分修复有残留,SCENE_LUNG=肺回答定义(LLMDependencyMetrics.py:32)；PulseLung.py:1742 record_llm_call(SCENE_LUNG)已补原『对话=0』埋点,中,埋点已补，待真实运行数据重算基线,,
+D014,P0,进化验证空转,进化验证空转是依赖度97%的根因(P0-263),第七十四章,第101批 T-101b,状态未知,| P0-263 | 进化验证空转是依赖度97%的根因 | ❓ 状态未知 |,已闭环(部分)：baseline 恒 0 根因已确证修复(patch_verification_split.py)；第101批 T-101a 打通 批准→落盘 闭环，补丁可走完全链(提交→批准→落盘→复验),patch_verification_split.py 确证 baseline 恒 0；SafeEvolutionExecutor.py:1454/1471/1778/1975；PatchManager._m101_low_risk_release_path(第101批 T-101a),中,判据已修，真实修复率待长期观测；★第101批 T-101b 回写：D014 记已闭环(部分)，进化闭环末环由 T-101a 打通,,
+D015,P0,埋点验证,T2埋点需重启框架才能采集真实数据(P0-271),第七十五章,第七十五章,待重启验证,P0-271 ...本批仅代码+单测，未重启 → 重启后应见 calls_*.jsonl 增长,大部分修复有残留,实测data/llm_traces/calls_*.jsonl已149文件/9.84MB，证明重启后真实数据已增长(非空转),中,关闭待重启项，数据已采集,,
+D016,P0,未展开新登记,(新增P0级债务，正文未展开)(P0-272),第七十六章,第一百三十六章,状态未知,| P0-272 | （新增P0级债务） | ❓ 状态未知 |,已修复,总账:4069 第42批T1完成『根因定性外部原地截断；新增超期清理+被截断必留痕』；logger.py:175 LOG_RETENTION_PROTECT永不删,中,关闭,,
+D017,P0,日志治理,日志清空执行者仍未定位(P0-278→P1),第七十七章,第七十七章,待下次发生时定位,P0-278→P1 | 日志清空执行者仍未定位，但已具备留痕能力 | 下次发生时定位,大部分修复有残留,总账:4069根因=外部原地截断已定性并加留痕；logger.py:175保护pulse_crash.log；但具体外部进程/执行者未点名,中,已具留痕，待下次发生抓现行,,
+D018,P0,InfluxDB真实落库,InfluxDB时序库真实落库仍被凭证401硬阻(第79批T3),第一百七十六章,第一百七十六章,待token注入真实环境复跑(移交第80批),数据真实落库且可查询仍被凭证硬阻，移交第80批「外部存储收口」待 token 注入真实环境复跑,已失效/不再适用,ENABLE_INFLUXDB_TIMESERIES=False(已核实)；PulseNodePool.py:1111与PulseStomach.py:302双门禁；influxdb_store.py:46 _influx_enabled()恒False→生产0次真实connect，非运行时阻塞,高,降档为休眠特性，移出P0阻塞,,
+D019,P1,测试真实回归,全量125例失败中B类真实回归16例+C类Cython未编译4例(第79批T4揭示),第一百七十五章,第一百七十六章,"待修复(分诊已完成,真实回归未清)",B 真实回归...16 / C 构建依赖（Cython 未编译）4；其余 B 类候选隔离单跑仍失败→确属真实回归,待核,按指令未跑测试套件；cython_status.py:34 use_cython_extensions默认False(4例C类为环境性),低,缺：真实跑一次测试套件确认16例是否仍失败,,
+D020,P1,推理相似度,历史推理相似度占位0.5(P1-5),第六十一章,第一百三十六章,状态未知,| P1-5 | 历史推理相似度占位0.5 | ❓ 状态未知 |,大部分修复有残留,SelfCalibrator.py:338 history_similarity()已实现真实命中，:348仅无命中回退0.5(:360)，不再恒0.5占位,高,占位已破，回退仍0.5可接受,,
+D021,P1,PulseInnerWorld上帝类,PulseInnerWorld.py 上帝文件(实测23145行未拆分)【主票】,第四十五章,第一百七十五章,未启动(战略高风险专项),P1-60 PulseInnerWorld上帝类拆分：高风险专项，需要单独批次；23145行=brain系统55%，13个>200行方法,未动,organs\brain\PulseInnerWorld.py 实测23145行未拆分；D174并入本票,高,第82批上帝文件拆分专项(主票，D174并此),,
+D022,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-173),第五十章,第一百三十六章,待处理,| P1-173 | （新增P1级债务） | ⏳ 待处理 |,大部分修复有残留,总账:1170 zhipu/doubao曾明文泄露需用户控制台轮换；main.py:3446凭证改从config/env读取不再硬编码,中,代码已改env，轮换为用户操作,,
+D023,P1,渠道,ark火山方舟渠道成功率低(P1-247),第七十三章,第一百三十六章,部分修复(状态未更新),P1-247（ark渠道成功率低）虽已部分修复（第58批T2），但状态未更新,已修复,PulseLung.py:1053 第25批T2/P2-162按渠道裁剪字段修doubao/ark v3 400，:1058实测HTTP200,中,关闭，状态更新为已修复,,
+D024,P1,自检视,self_inspector方法体定位精确命中率0%(P1-248),第七十三章,第一百三十六章,待处理,| P1-248 | self_inspector方法体定位精确命中率0% | ⏳ 待处理 |,部分修复,self_inspector现175处命中，exploration_audit.py:230 run_parallel_audit接入12检测器；但SafeEvolutionExecutor.py:1499注『仍缺method无法方法级修复』,中,框架已接，方法级定位精度仍待提,,
+D025,P1,置信度校准,置信度校准机制缺失(P1-255),第七十四章,第一百三十六章,状态未知,| P1-255 | 置信度校准机制缺失 | ❓ 状态未知 |,大部分修复有残留,SelfCalibrator.calibrate被CausalVerifier.py:314与semantic_cache.py:122阈值校准接入，机制已建,中,机制已上，校准曲线待数据验证,,
+D026,P1,自我蒸馏,自我蒸馏退化螺旋风险(P1-256),第七十四章,第一百三十六章,状态未知,| P1-256 | 自我蒸馏退化螺旋风险 | ❓ 状态未知 |,待核,蒸馏基础设施存在(TaskPipeline DISTILL；self_inspector.py:275 P3-12自增强)，但无针对退化螺旋的主动缓解/监控代码；自主修复率~1.8%佐证风险,低,缺：退化螺旋监控指标与刹车机制,,
+D027,P1,数据规模,数据规模严重低估(110MB→4.8GB)(P1-264),第七十四章,第一百三十六章,状态未知,| P1-264 | 数据规模严重低估（110MB→4.8GB） | ❓ 状态未知 |,待核,规模为运行期量；代码侧未见规模自适应治理，与D038/D041快照增长同源,低,缺：实测data目录当前体量,,
+D028,P1,ONNX资产,已有3套ONNX嵌入模型未被当作资产(P1-265),第七十四章,第一百三十六章,状态未知,| P1-265 | 已有3套ONNX嵌入模型未被当作资产 | ❓ 状态未知 |,部分修复,semantic_cache.py:7已复用90MB ONNX/512维；但models/目录实测未找到.onnx实物(疑运行时下载)，3套资产未集中管理,中,1套已接入，资产台账待建,,
+D029,P1,隔离区可见性,T2隔离区1274文件沙箱执行后不可见(manifest完整)(P1-273),第七十六章,第七十六章,待真实终端复核,P1-273 | T2隔离区1274文件沙箱执行后不可见（manifest完整） | 真实终端复核,待重启验证,总账:3964标记『真实终端复核data/_quarantine/』；沙箱可见性为运行期现象，代码层未见修复,低,缺：真实终端ls data/_quarantine/复核,,
+D030,P1,重启生效,T2/T3生成侧改动需重启框架生效(P1-274),第七十六章,第七十六章,待重启验证,P1-274 | T2/T3生成侧改动需重启框架生效 | 重启窗口,待重启验证,总账:3965标记重启窗口；生成侧改动运行期生效项，代码层无法静态判定,低,缺：重启后确认.bak不再增/cache非空,,
+D031,P1,补丁验证口径,baseline_errors为文件级口径无法反映方法级修复效果(P1-281),第七十七章,第七十七章,待日志格式改善,P1-281 | baseline_errors是文件级口径，无法反映方法级修复效果 | 改善日志格式后提升,大部分修复有残留,patch_verification_split.py:10/110引入post_apply_errors区分Problem-Fixed；patch_quality_evaluator.py:188记录；但SafeEvolutionExecutor.py:1499仍缺方法级,中,口径已加post_apply，方法级定位待补,,
+D032,P1,进化循环埋点,进化循环埋点仅部分闭环(P1-292),第七十九章,第八十章,文档称已完成(待代码复核),⇒ 进化循环埋点已生效（P1-292 部分闭环）,大部分修复有残留,LLMEvolutionEngine.py:16与SelfReflectionEngine.py:16 import trace_evolution_call；总账:4752『已生效部分闭环』,中,部分闭环属实，待全链路验证,,
+D033,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-313),第八十四章,第一百三十六章,状态未知,| P1-313 | （新增P1级债务） | ❓ 状态未知 |,未动,总账:5502队列深度318次WARNING(1006→1240)需背压/细分统计；未见背压代码,低,补队列背压与积压类型细分,,
+D034,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-327),第八十七章,第一百三十六章,状态未知,| P1-327 | （新增P1级债务） | ❓ 状态未知 |,已修复,总账:6830 第49批T1对话路由修复(肺调用LLM来源=background_learning→无罪推定),中,关闭,,
+D035,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-347),第九十四章,第一百三十六章,状态未知,| P1-347 | （新增P1级债务） | ❓ 状态未知 |,部分修复,总账:8300第52批器官单测框架搭建+核心器官覆盖；tests/实测208个py存在,中,框架已搭，覆盖率持续补,,
+D036,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-357),第九十六章续,第一百三十六章,状态未知,| P1-357 | （新增P1级债务） | ❓ 状态未知 |,部分修复,与D041同源(快照482MB过大)；第52批评估；PulseSnapshot.py:201 INCREMENTAL_MAX_NODES=50增量保存已上,低,并入D041快照治理,,
+D037,P1,未展开新登记,(新增P1级债务，正文未展开)(P1-370),第一百章,第一百三十六章,状态未知,| P1-370 | （新增P1级债务） | ❓ 状态未知 |,部分修复,总账:8863 T2选3-5核心器官(PulseStomach/Lung/Heart)写pytest；分批覆盖中,低,器官单测分批推进,,
+D038,P1,冷热分离空转,"第69批冷热分离0%生效,_m70_lazy_ids只写不读(第三方P1)",第一百七十五章,第一百七十六章,待重新评估(第80批),冷热分离功能空转：第69批做的冷热分离0%生效！_m70_lazy_ids只写不读,重复挂账,"PulseSnapshot.py:1606 _m70_lazy_ids现被:1679/1698 discard、:1710 list()真实消费,:1648新增冷存召回；但SNAPSHOT_HOT_COLD_LOAD=False(已核实)生产未触发；与D152同项",中,并表D152；代码已改读待重启开关验证,,
+D039,P1,关系图谱膨胀,"semantic_relations占428MB/501MB,同一邻接关系存两遍(第三方P1)",第一百七十五章,第一百七十六章,待治理(第81-85批),关系图谱膨胀89.6%：semantic_relations占428MB，linked_nodes与target集合Jaccard 0.932——同一邻接关系存两遍,未动,"semantic_relations仍与linked_nodes并存：PulseNode.py:320-321 to_dict双写,:2241 parquet双列；去重治理未做，计划第81-85批(路灯施工中)",中,并入第81-85批去重治理,,
+D040,P1,L3降级通道形同虚设,"conflict_count不进to_dict,should_downgrade_l3需≥3永不满足(第三方P1宪法)",第一百七十五章,第一百七十五章,待实现,L3降级通道形同虚设：conflict_count不进to_dict，should_downgrade_l3需≥3永不满足——宪法条款写了但没实现,部分修复,PulseLiver.py:2933-2936已回写conflict_count(原恒0)；但PulseNode.py:300-336 to_dict仍不含conflict_count→重启归零；should_downgrade_l3(:285阈值3)会话内可达,高,补to_dict持久化conflict_count,,
+D041,P1,快照数据增长治理,"478MB快照数据持续增长,需冷热分离落地/快照轮转裁剪/节点池上限",第一百七十六章,第一百七十六章,待治理,478MB快照数据增长治理：冷热分离落地/快照轮转裁剪/节点池上限,部分修复,增量保存INCREMENTAL_MAX_NODES=50(PulseSnapshot.py:201)已上；冷热分离gated off；节点池硬上限/轮转裁剪未见；与D036同源,低,并入第81批存储核心治理,,
+D042,P1,静默except剩余,全库仍剩490处静默except(第78批仅清理核心4文件105处),第一百七十六章,第一百七十六章,待清理,全库剩余490处静默except：本批仅清理核心4文件105处,部分修复,活代码308py实测：except Exception全量2829处/228文件(未绑定1128/189+as绑定1701/188)，裸except:0处；真正静默吞噬(后跟pass/...)240处/103文件,高,台账490口径过时，按240处静默吞噬续清,,
+D043,P2,并发模型,并发模型混合治理(P2-64),第六十章,第六十三章,排期(第35批后),| P2-64 | 并发模型混合治理 | 中 | 第35批后 |,未动,threading.Lock命中207处/128文件;Thread70/42;Queue16/10;asyncio.Lock=0;三套scheduler(parallel/hybrid/structured)并存,中,"按器官收敛为单一调度原语,淘汰asyncio混用",,
+D044,P2,配置中心,配置中心拆分config.py 3547行(P2-65),第六十章,第六十三章,排期(第35批后),| P2-65 | 配置中心拆分（config.py 3547行） | 中 | 第35批后 |,未动,"实测config.py=4854行,较3547反增;注:config.py为第81批动态文件,行数不稳定",高,"第81批存储核心改完后再排拆分,按域切分",,
+D045,P2,except_pass,except_pass剩余约294处(P2-79),第六十章,第六十章,分批治理(未完成),| P2-79 | except_pass剩余约294处 | 分批治理（按器官推进） |,部分修复,"实测静默except:pass=286处/119文件(旧294处基本未降);已引入nucleus/_silent_except.py的silent_exc(e,where)可见化基建(灰度enable_silent_except_logging默认True),但286处仍为裸pass未迁移",高,"批量机械替换except:pass->silent_exc(e,where)",,
+D046,P2,LLM依赖度,LLM依赖度系统性降低(P2-102),第六十三章,第六十三章,长期/持续,| P2-102 | LLM依赖度系统性降低 | 长期 | 持续 |,部分修复,nucleus/LLMDependencyMetrics.py存在;channel_speed_profiler/call_pattern_analyzer存在,中,设季度下降目标并在LLMDependencyMetrics出报表,,
+D047,P2,EventTap,EventTap时间窗口过滤(P2-115),第四十章,第四十章,待后续,| P2-115 | EventTap 时间窗口过滤 | 📋 待后续 |,未动,"nucleus/events/EventTap.py=379行;全文无window/stale/cooldown/min_interval/expire,仅timestamp透传(L164/204/281)",高,在EventTap入口加最小时间间隔/过期丢弃,,
+D048,P2,call_graph落盘,call_graph落盘体积控制(P2-117),第四十章,第一百三十章,待评估后实施,P2-117 call_graph落盘体积控制：需评估影响后实施,大部分修复有残留,"CallGraphAnalyzer.py=1072行;已有_MAX_SCC_NODES=20000(L119)/_MAX_DEPTH_LIMIT=200(L120)节点上限,但落盘体积无prune/容量告警",中,在落盘前按节点数/字节数截断并记录体积,,
+D049,P2,硬件自适应,硬件自适应动态资源分配5项子任务(P2-124~128/P3-5),第六十章,第六十章,设计已完成待实施,P2-124~128 | 硬件自适应动态资源分配（5项子任务） | P3-5子任务，设计已完成待实施,大部分修复有残留,utils/safe_hw_probe.py=128;nucleus/hardware_probe.py=255;device_router.py=384;GPUCore.py=243 均落地,中,在真机上跑动态降载回归验证,,
+D050,P2,能力整合路线图,能力整合路线图8项子任务(P2-135~142),第六十章,第六十三章,排期(第35批后),| P2-135~142 | 能力整合路线图（8项子任务） | 中 | 第35批后 |,待核,"路线图项,无单一代码落点;需对照总账逐项核",低,开批次时按总账子任务清单逐项销项,,
+D051,P2,代码分析服务,独立进程代码分析服务11项子任务(P2-144~154/P3-7),第六十章,第六十三章,设计已完成待实施,P2-144~154 | 独立进程代码分析服务（11项子任务） | 设计已完成待实施,未动,无独立分析服务进程;仅exploration_audit.py:149 run_in_subprocess零星调用;write_guard的is_framework_process是进程判别非分析服务,中,单开批次落地独立子进程分析服务与IPC,,
+D052,P2,对话超时,对话处理超时优化(P2-155),第四十二章,第四十八章,部分完成(超时降级/队列可视化待后续),| P2-155 | 对话处理超时优化 | 🔄 部分完成（T6进度提示已闭环，超时降级/队列可视化待后续） |,部分修复,T6进度提示闭环:config.py:1365 DIALOG_PROGRESS_HINT_CONFIG;PulseLung.py:1492读取;但timeout_degrade/队列可视化grep=0命中,高,补超时降级(回退短句)与队列进度可见,,
+D053,P2,知识质量打分,KnowledgeQualityScorer抽样验证整合(P2-156),第四十一章,第四十一章,待后续批次,| P2-156 | KnowledgeQualityScorer抽样验证整合 | 📋 待后续批次 |,大部分修复有残留,self_awareness/KnowledgeQualityAnalyzer.py=863;quality_score_v2.py=474存在,中,补抽样验证回路并接回写,,
+D054,P2,渠道路由,用户对话优先付费渠道(P2-158),第四十二章,第四十四章,待验证(可能已落地),| P2-158 | 用户对话优先付费渠道 | ⏳ 待验证（日志显示...可能已落地） |,已修复,"llm/ChannelConcurrency.py:152 paid_channel_names=[deepseek,advanced];:191-192 if name in _paid 判付费优先",高,日志取证一次确认用户对话实际走付费渠道,,
+D055,P2,渠道监控,大模型渠道并发状态监控(P2-159),第四十二章,第四十四章,待验证,| P2-159 | 大模型渠道并发状态监控 | ⏳ 待验证 |,已修复,ChannelConcurrency.py=459;ChannelQuotaMonitor.py=536;channel_speed_profiler.py=88 三模块均落地,高,运行期导出一次并发快照确认,,
+D056,P2,RESULT双发,上游RESULT双发路径治理(P2-164),第四十五章,第五十章,"取证完成,重构待单开批次",| P2-164 | 上游RESULT双发路径治理 | 🔄 取证完成（第26批T4），重构待单开批次 |,部分修复,PulseInnerWorld.py 仍在631/691/811/834/870/896/968/986/1086/1179/1235/1261等12+处_emit(InferenceEvent.RESULT),高,单开批次收口RESULT发射点到单一出口,,
+D057,P2,深度思考超时,深度思考超时致长问题无实质回答(P2-170),第四十九章,第五十二章,文档称已完成(待代码复核),✅ 已完成...端到端待重启验证,待重启验证,代码改动存在但需重启后端到端验证;无运行期证据,中,重启后注入长问题取证,,
+D058,P2,输出长度,大模型输出长度远低于要求(P2-171),第四十九章,第五十四章,文档称已完成(待代码复核),✅ 已完成...端到端待重启验证,待重启验证,"同上,长度约束改动待运行期确认",中,重启后统计实际输出token长度分布,,
+D059,P2,检索重复执行,"on_inference_request检索流程同秒重复2~3次(P2-172,与P2-164同源)",第四十九章,第五十章,待处理,| P2-172 | 内在世界_on_inference_request检索流程重复执行 | ⏳ 待处理...建议合并处理 |,大部分修复有残留,PulseInnerWorld.py=23145行;L245-246 _dedup_cleanup_interval/max_size;L4543 _cleanup_dedup_marks;nucleus/field/RequestDeduplicator.py=375,中,运行期统计同秒检索命中数确认去重生效,,
+D060,P2,自动化优化,"自动化优化(P2-173,第35批计划项)",第五十一章,第六十三章,排期(第35批),第35批：...P2-173（自动化优化）...,部分修复,evolution/AutoParamApplier.py=196;PeriodicTestScheduler.py=400 自动化调度存在,中,出自动化闭环报表,,
+D061,P2,模型自动配置,框架自动管理模型配置阶段2/3(P2-183星轨版),第五十七章,第六十三章,排期(第33-34批),| P2-183（星轨版） | 框架自动管理模型配置（阶段2/3） | 高 | 第33-34批 |,大部分修复有残留,llm/model_self_updater.py=444存在,中,补阶段3切换与回滚取证,,
+D062,P2,操作指令准入,操作指令准入默认关闭后仍存残余风险与判据缺口(P2-191),第六十一章,第六十六章,待完善(残余风险),T5 操作指令准入评估（P2-191）...实测关闭后仍存残余风险与判据缺口,待核,"按OPERATION_GATE/command_gate/shell_gate/操作指令准入等现名grep活代码=0命中;机制可能已重构或移除,需对照总账确认落点",低,对照总账定位现机制后再判残余风险,,
+D063,P2,测试断言形态,源码文本计数型断言需统一改为调用形态计数(P2-195),第六十四章,第一百二十章,待改造,P2-195：源码文本计数型断言（需统一改为调用形态计数）,大部分修复有残留,"与D064同源;RUF100 noqa由210->1,表明文本计数型断言已大批迁移",中,抽查剩余文本计数断言改为调用形态,,
+D064,P2,RUF100承重项,"RUF100门禁承重项210处(P2-201,单开专项批次)",第六十七章,第一百二十三章,待单开专项批次,P2-201：RUF100门禁承重项专项（210处，单开专项批次）,已修复,实测活代码noqa RUF100仅1处/1文件(原210处),高,收尾最后1处,,
+D065,P2,v2探针延迟,T1探针每次进v2前多一次本地检索固定延迟(P2-205),第六十九章,第七十章,待P95评估是否加缓存,P2-205...按P95延迟评估是否加缓存,未动,无专项缓存落点;待P95实测,低,先测P95再决定是否加缓存,,
+D066,P2,扫描极限标定,PROBE_SCAN_LIMIT=800为经验值未在1万+节点真实库标定(P2-206),第六十九章,第七十章,待真实快照反标定,P2-206...未在1万+节点真实库标定 | 用真实快照按延迟反标定,未动,"config.py:1618 MULTI_STEP_ENTRY_PROBE_SCAN_LIMIT=800 仍为经验值,未反标定",高,用真实快照按延迟重标定该值,,
+D067,P2,端到端验证,T1端到端需重启框架验证0次v2模型调用(P2-209),第六十九章,第七十章,待重启验证,P2-209 | T1端到端需重启框架验证...重启后注入问题取证,待重启验证,"需重启运行期取证,无静态证据",中,重启后统计v2模型调用次数=0,,
+D068,P2,自认知接线,evolution_health维度接SafeEvolutionExecutor(P2-214),第七十一章,第七十二章,待实施(T-list),T2（P1）：P2-214 evolution_health维度接SafeEvolutionExecutor,已修复,SafeEvolutionExecutor.py:952 def _log_evolution_health;:1606调用;:4430-4432 integrate_evolution_health收口为公共接口,高,运行期确认health维度非空,,
+D069,P2,阶段二接入设计,"阶段二结果接入(设计文档354行,未改生产代码)(P2-216)",第七十一章,第七十四章,"文档称已完成(待代码复核,仅设计)",T5（P2）P2-216 阶段二结果接入设计 ✅ 设计文档354行...未改生产代码,叙事/文档名实不符,文档自述仅设计354行未改生产代码;生产无对应接入点,中,"设计已完成,落地时再排批次",,
+D070,P2,自我观察噪声,自我观察噪声排除(引擎自身产物目录)(P2-217),第七十一章,第七十二章,待实施(T-list),T4（P2）：P2-217 自我观察噪声排除（引擎自身产物目录）,待核,未grep到引擎自身产物目录排除的明确落点,低,在SelfAwarenessEngine排除引擎产物目录,,
+D071,P2,器官print,"6个器官未实现print改log(P2-230,自P2-212拆出)",第七十一章v9.10,第七十一章v9.10,待处理,P2-212（6个器官未实现）→ P2-230,未动,"实测organs下print(=519处/54文件,远大于文档所述6个;含Lung/Cortex/Stomach/VisualCortex等",高,"按器官print->logger批量迁移,先五脏五感",,
+D072,P2,配对器信噪比,配对器信噪比优化(目录/扩展名排除白名单)(P2-231),第七十二章,第七十二章,待实施(T-list),T5（P2）：P2-231 配对器信噪比优化...,部分修复,self_awareness/ProductionConsumptionMatcher.py=1212;FakeLoopDetector.py=730 存在,中,确认目录/扩展名白名单已进matcher,,
+D073,P2,磁盘枚举标定,磁盘枚举未在10万级文件规模标定(P2-234),第七十二章,第七十二章,后续批次,| P2-234 | 磁盘枚举未在10万级文件规模标定 | 后续批次 |,未动,"性能标定项,无代码缺陷落点",低,在10万级目录实测枚举耗时,,
+D074,P2,调度耗时,"每日调度全流程5.4s(P2-235,当前可接受)",第七十二章,第七十二章,后续优化,| P2-235 | 每日调度全流程5.4s（当前每日1次可接受） | 后续优化 |,未动,"性能观测项,当前每日1次可接受",低,随调度量级增长再优化,,
+D075,P2,返回值区分,返回值未区分两类no_consumer(P2-236),第七十二章,第七十二章,后续批次,| P2-236 | 返回值未区分两类no_consumer | 后续批次 |,待核,"无明确落点,需对照report_bus/publishers确认",低,在consumer返回处细分no_consumer原因码,,
+D076,P2,测试写生产目录,"测试隐式写入生产目录,根因未100%锁定(P2-237)",第七十二章,第七十二章,持续观察,| P2-237 | 测试隐式写入生产目录（已加防御，根因未100%锁定） | 持续观察 |,大部分修复有残留,data/write_guard.py=315行(第44批T4):pytest环境拒写data/前缀;L1-22判据;根因观察中,中,"持续观察守卫告警,定位剩余写点",,
+D077,P2,白名单目录,T2目标依赖任务书外11个目录(P2-244),第七十三章,第七十四章,待第40批确认,| P2-244 | T2 目标依赖任务书之外的11个目录... | 🔄 第40批确认 |,待核,"需对照任务书白名单清单,静态无法判定11目录现状",低,对照任务书逐项确认11目录去留,,
+D078,P2,运行态文件白名单,data根下仍有13个运行态文件未纳入白名单(P2-245),第七十三章,第七十四章,后续批次,| P2-245 | data 根下仍有 13 个运行态文件...未纳入白名单 | 后续批次 |,未动,"白名单项,需运行期扫data根确认",低,运行期扫data根补白名单,,
+D079,P2,路线图排期,PHASE18阶段二3批路线图待排期(P2-248),第七十四章,第七十四章,排期,| P2-248 | 阶段二 3 批路线图（第40/41/42）待排期 | 排期 |,待核,路线图排期项,低,按40/41/42批排期,,
+D080,P2,胃活跃度异常,胃器官活跃度异常高32.6%(P2-249),第七十三章,第七十四章,待排查,- P2-249：胃器官活跃度异常高（32.6%）,待核,"PulseStomach.py=2536行,已有噪声过滤(L525-609);活跃度偏高无回归点",低,运行期采胃活跃度基线排查触发源,,
+D081,P2,内在模型优先级,内在模型优先级与收益倒挂(P2-266),第七十四章,第七十四章,待处理,- P2-266：内在模型优先级与收益倒挂,未动,无明确代码落点,低,按收益重排内在模型优先级,,
+D082,P2,反馈回填,CallRecorder.record_feedback接口已提供但回填逻辑未接入(P2-267),第七十五章,第七十五章,后续批次,P2-267 ...回填逻辑未接入（消费者：cortex路由/补丁验证器） | 后续批次,部分修复,call_recorder.py:366 def record_feedback存在;:378-385写feedback_YYYYMMDD.jsonl;但cortex路由/补丁验证器消费接入未见,中,把feedback jsonl接到cortex路由与补丁验证器,,
+D083,P2,tmp非规范脚本,tmp/28+个并发会话遗留非规范前缀脚本未动(P2-269),第七十五章,第七十五章,待裁决,P2-269 ...未动，待裁决 | 待裁决,未动,"实测tmp/*.py=40个(文档28+,反增)",高,裁决:归档/删除/迁入tools,,
+D084,P2,语义缓存阈值,"语义缓存阈值0.92偏保守,两条验收互斥(P2-275)",第七十六章,第七十六章,"文档称已完成(待代码复核,L2启用前重标定)",P2-275 | 语义缓存阈值0.92偏保守... | L2启用前重标定,已修复,llm/semantic_cache.py:43 DEFAULT_THRESHOLD=0.85(第45批实测校准0.92->0.85);:119-135 threshold()带灰度回退到BEFORE_CALIBRATION=0.92,高,"保持灰度开关,L2启用前再标定",,
+D085,P2,unverifiable补丁,62条unverifiable需等baseline判据改善(P2-282),第七十七章,第七十七章,后续批次复查,P2-282 | 62条unverifiable需等baseline判据改善后才能给出有效结论 | 后续批次复查,部分修复,evolution/patch_quality_evaluator.py=395;EvolutionEffectVerifier.py=365;ParamPatchEffectVerifier.py=212 判据模块在,中,改善baseline判据后复查62条,,
+D086,P2,经验库污染清洗,经验库污染率79.5%(1193条)只做过滤未清洗(P2-283/298),第七十七章,第七十九章,待专项批次清洗,P2-283 ...只做过滤未清洗 | 专项批次清洗；P2-298 ...数据清洗专项,部分修复,evolution/ExperiencePollutionGuard.py=205;data/experience_cleanup.py=294;knowledge/PollutionTagger.py=391 清洗/打标模块均在,中,"单开清洗专项,对1193条跑experience_cleanup",,
+D087,P2,调度接线重启,T2/T3 DailyScheduler接线需重启框架才生效(P2-284),第七十七章,第七十七章,待重启验证,P2-284 ...DailyScheduler接线需重启框架才生效 | 重启窗口,待重启验证,self_awareness/DailyScheduler.py=548行存在;接线需运行期确认,中,重启后确认T2/T3调度注册,,
+D088,P2,数据多样性,"真实数据diversity仅5.7/15,origin仅2类(P2-287)",第七十八章,第七十八章,待扩大采集场景,P2-287 | 真实数据 diversity 仅 5.7/15（origin 仅 2 类） | 扩大采集场景,未动,"数据采集项,非代码缺陷;需扩origin来源",低,扩大采集场景增origin类别,,
+D089,P2,语义缓存升L2,语义缓存升L2仅设计文档274行不实施(P2-288),第七十八章,第七十九章,"文档称已完成(待代码复核,仅设计)",T2 语义缓存升L2设计...完成 设计文档274行...仅设计不实施,叙事/文档名实不符,"semantic_cache.py已用ONNX512维(L7);L2为更高阶设计文档,生产未实施",中,"设计已完成,需要时再实施",,
+D090,P2,调度接线重启,T1/T3 DailyScheduler接线需重启框架(P2-289),第七十八章,第七十八章,待重启验证,P2-289 ...DailyScheduler 接线需重启框架生效 | 重启窗口,待重启验证,与D087同源(DailyScheduler重启);T1/T3接线待运行期,中,与D087同一重启窗口合并验证,,
+D091,P2,历史回填,测试污染防护推广中历史回填延后(P2-290),第七十八章,第七十九章,部分完成(历史回填延后),T4 测试污染防护推广...完成（1项延后）...历史回填延后,部分修复,write_guard.py=315行已推广到写盘型组件;历史污染数据回填延后,中,补历史污染数据回填清理,,
+D092,P2,重启窗口,第44批全部改动需重启框架生效(P2-295),第七十九章,第七十九章,待重启验证,P2-295 | 本批全部改动需重启框架生效 | 重启窗口,待重启验证,第44批写盘守卫等改动需运行期验证,中,与D087/D090同一重启窗口统一取证,,
+D093,P2,写盘守卫,"写盘守卫为黑名单式,新增写盘组件仍会漏(P2-297)",第七十九章,第七十九章,待评估白名单式,P2-297 | 写盘守卫为黑名单式；新增写盘组件仍会漏 | 评估「白名单式」,大部分修复有残留,"write_guard.py实为data/路径前缀守卫(L16只判data/前缀,L11-14三条件),非组件黑名单,新增写data/组件自动覆盖;但仅pytest环境且非全白名单",中,升级为全白名单并覆盖非pytest误写,,
+D094,P2,pulse.log增长,pulse.log实测4.2MB/33094行且持续增长(P2-299),第七十九章,第七十九章,继续观察留痕,P2-299 | logs/pulse.log 实测 4.2MB...持续增长 | 继续观察留痕,已修复,"nucleus/logger.py:317 SafeRotatingFileHandler + :471 挂载；实测 logs/pulse.log=2,073,509B/17,617行（原4.2MB/33,094行，已轮转下降）",高,维持轮转，定期观察,,
+D095,P2,tmp测试隔离,旧批次测试持续在tmp创建隔离目录(P2-301/307),第八十章,第八十一章,待与P0修复并行/合并治理,P2-301 | 旧批次测试持续在tmp创建隔离目录...第46批与P0修复并行；P2-307 与P2-301合并,未动,.bak_batch64~81 目录仍在持续累积；tmp隔离目录为历史测试产物，无清理代码,中,纳入备份目录策略(D114)统一治理,,
+D096,P2,探针集扩充,探针集扩充至≥100对(P2-303),第八十章,第八十一章,待实施,探针集扩充至≥100对（P2-303）+ 硬下限复核（P2-302）,待核,nucleus/probe_strategy.py 存在 ProbeStrategyMemory，但未实跑无法核对探针对数是否≥100,中,需运行探针计数脚本核实,,
+D097,P2,重启验证阈值,重启框架窗口验证T1阈值0.85生效+守卫/埋点复验(P2-304),第八十章,第八十一章,待重启验证,重启框架窗口（P2-304）：验证T1阈值0.85生效 + 第44批守卫/埋点复验,待重启验证,T1阈值0.85配置存在；框架已停止，运行期守卫/埋点未验,中,框架重启后复验,,
+D098,P2,误删脚本覆盖,"抢救误删重建3个tmp脚本(非原文),待找到原脚本覆盖(P2-305)",第八十章,第八十一章,待后续覆盖,P2-305 ...后续找到原脚本可覆盖,未动,tmp下重建脚本为非原文，原脚本未找到；无覆盖动作,低,找到原脚本再覆盖，否则归档,,
+D099,P2,执行产物契约,清理执行产物被当作长期硬契约(P2-312),第八十二章,第八十三章,后续批次,P2-312 ...清理执行产物被当作长期硬契约 | 后续批次,未动,描述性条目，无对应代码改造点,低,并入架构治理,,
+D100,P2,后台线程未关,框架停止后后台线程未关闭(P2-314),第八十四章,第八十五章,待处理,- P2-314：框架停止后后台线程未关闭,大部分修复有残留,main.py:2826 停机时枚举存活非daemon线程并告警；IntentGenerator/蒸馏/health/evolution 均 daemon=True(:593/2105/2151/2337)，随进程退出,高,daemon设计可接受，保留诊断日志,,
+D101,P2,经验库覆盖率0,"生产经验库raw_summary覆盖率0%,历史未回填且1179条原文永久丢失(P2-315/321)",第八十四章,第八十五章,待回填(原文已永久丢失),P2-315/P2-321 | 生产经验库raw_summary覆盖率0%...1179条污染记录原文已永久丢失,大部分修复有残留,M47止血：experience_pool.py:323-327 raw_summary永久保留不覆盖；tests/test_experience_summary_hemostasis_m47.py:68 验证；历史1179条原文永久丢失不可回填,高,新增已止血，历史不可恢复，归档,,
+D102,P2,生命周期统一,架构级生命周期管理不统一(P2-316),第八十四章,第八十五章,待治理,- P2-316：架构级-生命周期管理不统一,未动,架构级生命周期统一，无对应改造提交,低,远期架构专项,,
+D103,P2,baseline调参无效,"EVOLUTION_BASELINE_WINDOW_DAYS调参无效,需主动复现探针(P2-322)",第八十五章,第八十五章,待探针复现,P2-322 ...调参无效——需主动复现探针才能真正提升可判定率,未动,EVOLUTION_BASELINE_WINDOW_DAYS 仍为配置项，无主动复现探针代码,中,需补探针复现,,
+D104,P2,能力分散整合,架构级能力分散没有整合(P2-317),第八十四章,第八十五章,待治理,- P2-317：架构级-能力分散没有整合,未动,架构级能力分散整合，无对应改造,低,远期专项,,
+D105,P2,实施生效缺口,代码实施与生效之间的人工操作缺口(P2-323),第八十六章,第一百零四章,待根治,根因：P2-323（代码实施与生效之间的人工操作缺口）的具体实例,未动,描述性根因条目，无具体代码点,低,随批次治理,,
+D106,P2,自净机制文档,"经验库自净机制存在但未文档化,数字易被误读(P2-325)",第八十六章,第八十六章,待文档完善,P2-325 ...自净机制...但未文档化 | 文档完善,部分修复,experience_pool.py:114 _auto_clean_thread / :477 run_pollution_cleanup(第65批) / :393 _auto_clean_enabled 机制已存在；文档化程度未核,中,补自净机制文档,,
+D107,P2,架构级路由治理,架构级路由治理:请求/数据/信号路由与来源治理不足(P2-330),第八十七章,第九十章,待P0后专项,P2-330 | 架构级路由治理 | ... | P0后专项,未动,架构级路由治理，无对应改造,低,P0后专项,,
+D108,P2,待修复项,第49批验收列出的待修复债务(P2-336),第九十一章,第九十一章,待修复,待修复债务：P0×4、P2-329、P2-334、P2-335、P2-336、P2-337（共9项）,待核,第49批验收待修复项 P2-336，正文未展开,低,需验收报告核对,,
+D109,P2,待修复项,第49批验收列出的待修复债务(P2-337),第九十一章,第九十一章,待修复,待修复债务：...P2-336、P2-337（共9项）,待核,第49批验收待修复项 P2-337，正文未展开,低,需验收报告核对,,
+D110,P2,摸底新登记,"第二轮摸底新发现债务,正文未展开(P2-340)",第九十二章,第九十二章,状态未知(未展开),| 新发现技术债务 | P2-340、P3-338、P3-339、P3-341（共4项） |,待核,第二轮摸底新登记 P2-340，正文未展开,低,展开后再判,,
+D111,P2,摸底新登记,"第三轮摸底新发现债务,正文未展开(P2-342/343)",第九十三章,第九十三章,状态未知(未展开),| 新发现技术债务 | P2-342、P2-343、P3-344...（共5项） |,待核,第三轮摸底新登记 P2-342/343，正文未展开,低,展开后再判,,
+D112,P2,静默异常治理,静默异常捕获治理(第53批计划项)(P2-348),第九十四章,第九十六章续,待治理(第53批),第53批：静默异常捕获治理...（P2-348、P2-349、P2-358）,部分修复,silent_exc 在 chat_service.py 等广泛使用；静默异常仍需全量梳理,中,继续清理 silent_exc 覆盖,,
+D113,P2,print清理,print调试输出清理(第53批计划项)(P2-349),第九十四章,第九十六章续,待清理(第53批),第53批：...print调试输出清理...（P2-348、P2-349、P2-358）,未动,仍存在裸 print：PatchAutoApprover.py:307/330、consumers.py:65,高,替换为 get_module_logger,,
+D114,P2,备份目录策略,备份目录清理策略(第53批计划项)(P2-350),第九十四章,第九十四章,待治理(第53批),| 新发现技术债务 | P1-347、P2-348、P2-349、P2-350...（共6项） |,未动,.bak_batch64~81 共18个批次备份目录仍在堆积，无清理策略代码,高,制定备份目录清理策略,,
+D115,P2,全局变量并发,全局变量并发访问检查(第54批计划项)(P2-353),第九十五章,第九十六章续,待检查(第54批),第54批：死代码价值挖掘（一）+ 全局变量并发访问检查（P2-353、P2-359）,未动,第54批计划项，无并发检查代码落地,低,远期专项,,
+D116,P2,直连写生产库,PulseHormones.py:390与main.py:510直连get_experience_pool()写生产库未经注入隔离(P2-356),第九十六章,第九十九章,待修复,P2-356 ...直连 get_experience_pool() 写生产库（未经注入隔离）,未动,organs/endocrine/PulseHormones.py:389-391 仍直调 get_experience_pool().record_experience()；main.py:540/571 同样直连写生产库，无注入隔离,高,改走注入隔离通道,,
+D117,P2,摸底新登记,"第五轮摸底新发现债务,正文未展开(P2-358/359/360)",第九十六章续,第九十六章续,状态未知(未展开),| 新发现技术债务 | P1-357、P2-358、P2-359、P2-360...（共7项） |,待核,第五轮摸底新登记 P2-358/359/360，正文未展开,低,展开后再判,,
+D118,P2,chat_service接线,"chat_service架构缺口需详细设计(P2-364,第50批单独立项)",第九十七章,第九十九章,待详细设计,P2-364 chat_service 接线启动 ...架构缺口，需详细设计（第50批单独立项）,部分修复,main.py:3585-3587 WebChatServer(port=5052) 已启动并注入 info_field/pulse_core；chat_service.py 存在,高,架构缺口已接线，补详细设计文档,,
+D119,P2,face_welcome优化,face_welcome流程改chat_service.py+灰度开关需重启(P2-365),第九十八章,第一百零三章,待重启验证,face_welcome 优化实施（方案A）...改 chat_service.py + 灰度开关（需重启）,待重启验证,functions/chat/chat_service.py:169 face_welcome_{ts} 相关id；灰度开关需重启验,中,重启后验灰度,,
+D120,P2,partial_fix范围,partial_fix剩余项未按diff区域限定(P2-367),第九十九章,第一百章,待评估,P2-367 | partial_fix的剩余项未按diff区域限定... | 🟡待评估,部分修复,config.py:4522 改动区域统计开关；patch_active_reprobe.py:468-472 说明 diff 区域口径,中,开启开关收敛 partial_fix,,
+D121,P2,补丁静态检测,code_optimization无静态检测器致2条补丁不可判定(P2-368),第九十九章,第一百章,待评估,P2-368 | code_optimization无静态检测器 → 该2条补丁不可判定 | 🟡待评估,部分修复,patch_active_reprobe.py:255-256 code_optimization 无可靠静态判据→恒返回空→not_applicable，已从不可判定改为显式不可用,高,补静态判据或保持 not_applicable,,
+D122,P2,旧体系评分,旧体系evolution_health有模块就给分(v2替代后消除)(P2-369),第九十九章,第一百章,待观察(v2替代后消除),P2-369 | 旧体系evolution_health有模块就给分 | v2替代后消除 | 🟡待观察,已修复,main.py:1038-1045 evolution_health 接线 SafeEvolutionExecutor；SelfAwarenessEngine.py:1016 integrate_evolution_health / :1817 _score_evolution_health；tests/test_evolution_health_m58.py,高,旧体系已被v2替代,,
+D123,P2,框架改写守卫,P2-370族测试框架改写守卫扩展,第一百零三章,第一百二十五章,待扩展,属 P2-370 族的扩展（第54批已为 test_serp_cleanup_m50 加过框架改写守卫）,待核,P2-370族框架改写守卫扩展，无对应代码点核到,低,逐测试核对,,
+D124,P2,测试依赖停机,test_serp_cleanup_m50生产现状断言依赖框架停机(P2-372),第一百零三章,第一百零三章,待解(3例skip),P2-372 ...生产现状断言依赖框架停机...3例 skip,未动,tests/test_serp_cleanup_m50.py 生产现状断言仍依赖框架停机，3例skip,中,解耦停机依赖,,
+D125,P2,flaky分片测试,分片测试与框架审计并发干扰(复发第2次)(P2-373),第一百零三章,第一百零三章,待根治(重跑即过但复发),P2-373 ...分片测试与框架审计并发干扰（复发第2次）...原样重跑 445 passed,未动,flaky分片测试与框架审计并发，无根治代码,中,隔离分片审计,,
+D126,P2,过期检测并存,"两套过期检测并存,启用PatchAutoApprover时处理(P2-382)",第一百零九章,第一百一十章,待后续批次,P2-382 两套过期检测并存 → 后续批次（启用PatchAutoApprover时）,部分修复,PatchManager.py:531-532 注释明指 PatchAutoApprover.is_stale 已deprecated且零生产调用；:552 第54批T3.2 在 PatchManager 内新建 stale 标记,高,旧检测随AutoApprover退役清理,,
+D127,P2,债务总览表过时,"新增债务P2-308~386未更新到总览表,总览表仍为v9.9版本(P2-386)",第一百零九章,第一百三十六章,待更新(状态管理不规范),新增债务（P2-308~P2-386...）没有更新到总览表；总览表是v9.9版本，已严重过时,未动,技术债务台账仍持续登记至D150；总览表v9.9未更新,中,刷新总览表,,
+D128,P2,指标函数复用,"指标函数复用已回填结论(P2-389,T6发现)",第一百二十五章,第一百二十五章,待确认,**P2-389 · 指标函数复用已回填结论（P2，T6 发现）**,待核,P2-389 指标函数复用已回填结论，无代码点核,低,核对回填报告,,
+D129,P2,双腿搜索冷却,"双腿搜索冷却(P2-402,随渠道优化缓解)",第一百三十章,第一百三十章,观察中,| P2-402 | 双腿搜索冷却 | 🟡 观察中 | 随渠道优化缓解 |,已修复,config.py:2043 deep_search_cooldown=30 秒；渠道优化已缓解,高,维持冷却,,
+D130,P2,五维共振名不副实,五维共振'仅1.5维'系字段名误判(第三方grep复数space_paths/intent_labels，实现用单数),第一百七十五章,第一百七十五章,待补实现/修正叙事,五维共振名不副实：宣称五维，实际只实现1.5维（space_paths/intent_labels全库零消费）,叙事/文档名实不符,ResonanceEngine.py:705/722-727 _calculate_dimensions五维齐备；_calc_space_dim:1016用单数node.space_path(_space_index:51/302-306真实维护)；_calc_logic_dim:1053用event_type/trigger_reason/keywords；_calc_time_dim:1080激活新鲜度+source_timestamp(Cython),高,文档/叙事对齐；可选增强logic维输入丰富度；无需补五维实现,,
+D131,P2,增量保存名不副实,宣称增量保存实际整读479MB→内存合并→整体重写(第三方P2),第一百七十五章,第一百七十五章,待改造,增量保存名不副实：宣称增量，实际是整读479MB→内存合并→整体重写,未动,PulseSnapshot.py:545 _incremental_save 仍整读合并重写；:932 _m67_incremental_log_save 为真JSONL增量路径但由 :891 _m67_incremental_log_enabled() 门控，config SNAPSHOT_USE_INCREMENTAL_LOG=False→回退整读（两条路径勿混）,高,开启 SNAPSHOT_USE_INCREMENTAL_LOG 或改造 _incremental_save,,
+D132,P2,继承关系失实,路线图称InfoField继承OscillonField实际继承SilentLogMixin(第三方P2),第一百七十五章,第一百七十五章,待修正文档/代码,OscillonField继承关系失实：路线图说InfoField继承OscillonField，实际继承SilentLogMixin,已失效/不再适用,nucleus/field/InfoField.py:122 class InfoField(SilentLogMixin)，并未继承 OscillonField(ABC)(OscillonField.py:49)；路线图表述失实，代码已证,高,修正路线图文档继承关系,,
+D133,P2,genetic占位层,"genetic层是占位层:养育=计数器+日志,羁绊硬编码,同意闸门零发射方(第三方P2)",第一百七十五章,第一百七十五章,待实现(能力夸大),genetic层是占位层：养育=计数器+日志，羁绊硬编码，同意闸门零发射方——与真实器官平列构成能力夸大,部分修复,organs/genetic/PulseNurture.py:57 仍为阶段计数器+日志；PulseBonding.py 记录互动；但 P3-5 已补发射方 PulseHormones.py:320（此前bonding有订阅无发射）；PulseConsent 同意闸门仍零发射,中,nurture/consent 补真实逻辑,,
+D134,P2,肺隐喻漂移,"肺实为LLM模型选型调度器,不是呼吸器官(第三方P2)",第一百七十五章,第一百七十五章,待修正叙事/实现,肺的隐喻与实现漂移：肺实为「LLM模型选型调度器」，不是呼吸器官,未动,organs/body/PulseLung.py:5「脉冲驱动肺·模型调用器官」/ :11 收 LungEvent.SELECT_MODEL 选模型并调大模型——实为LLM选型调度器，非呼吸器官，隐喻漂移依旧,高,修正叙事或重命名,,
+D135,P2,README数字过期,"README六项数字全部过期(53批vs实际75批,2471vs3311,宣称0失败vs实际125)(第三方P2)",第一百七十五章,第一百七十六章,待文档更新,README六项数字全部过期：53批vs实际75批、2471例vs3311例、宣称0失败vs实际125失败,未动,README.md:12/46 仍写「已完成53批任务」，实际已施工至第81批,高,刷新README批次/测试数/失败数,,
+D136,P3,硬件自适应L3,硬件自适应动态资源分配试点(P3-5),第六十二章,第六十三章,远期规划(第34批可试点),| P3-5 | 硬件自适应动态资源分配（5项子任务） | 中 | 第34批可试点 |,未动,P3-5 硬件自适应动态资源分配，远期试点，无落地,低,远期试点,,
+D137,P3,能力整合路线图,能力整合路线图(P3-6),第四十章,第四十章,待后续,| P3-6 | 能力整合路线图 | 📋 待后续 |,未动,P3-6 能力整合路线图，远期,低,远期,,
+D138,P3,代码分析流水线,独立进程代码分析服务+完整闭环流水线(P3-7),第四十章,第六十章,设计完成待实施,P3-7/P3-8子任务，设计已完成待实施,未动,P3-7 独立进程代码分析服务，设计完成待实施,低,待实施,,
+D139,P3,知识质量修复闭环,知识质量自动修复闭环(P3-8),第四十章,第四十三章,待后续,| P3-8 | 知识质量自动修复闭环 | 📋 待后续 |,未动,P3-8 知识质量自动修复闭环，远期,低,远期,,
+D140,P3,矛盾人工确认,语义类矛盾待人工确认清单导出(P3-9),第四十一章,第四十一章,待后续批次,| P3-9 | 语义类矛盾待人工确认清单导出 | 📋 待后续批次 |,未动,P3-9 语义矛盾人工确认清单导出，远期,低,远期,,
+D141,P3,智能路由评估,"智能路由评估(P3-185,第34批计划)",第五十八章,第六十三章,远期规划(第34批),第34批：...P3-185（智能路由评估）...,未动,P3-185 智能路由评估，远期第34批计划,低,远期,,
+D142,P3,ReportConsumers格式,ReportConsumers异常格式ERROR(P3-315),第八十四章,第八十五章,待处理,- P3-315：ReportConsumers异常格式ERROR,部分修复,"nucleus/reporting/consumers.py:46/62 已用 get_module_logger(component=""ReportConsumers"") + :65 print兜底；异常格式ERROR问题未完全复现",中,运行期观察异常格式,,
+D143,P3,摸底新登记,"第二轮摸底新发现P3,正文未展开(P3-338/339/341)",第九十二章,第九十二章,状态未知(未展开),| 新发现技术债务 | P2-340、P3-338、P3-339、P3-341（共4项） |,待核,第二轮摸底P3-338/339/341，正文未展开,低,展开后再判,,
+D144,P3,摸底新登记,"第三轮摸底新发现P3,正文未展开(P3-344/345/346)",第九十三章,第九十三章,状态未知(未展开),| 新发现技术债务 | ...P3-344、P3-345、P3-346（共5项） |,待核,第三轮摸底P3-344/345/346，正文未展开,低,展开后再判,,
+D145,P3,摸底新登记,"第四轮摸底新发现P3,正文未展开(P3-351/352)",第九十四章,第九十四章,状态未知(未展开),| 新发现技术债务 | ...P3-351、P3-352（共6项） |,待核,第四轮摸底P3-351/352，正文未展开,低,展开后再判,,
+D146,P3,摸底新登记,"第五轮死代码轮新登记P3,正文未展开(P3-354/355/356)",第九十五章,第九十五章,状态未知(未展开),| 新登记技术债务 | P2-353、P3-354、P3-355、P3-356（共4项） |,待核,第五轮死代码P3-354/355/356，正文未展开,低,展开后再判,,
+D147,P3,摸底新登记,"第五轮nucleus摸底新发现P3,正文未展开(P3-361/362/363)",第九十六章续,第九十六章续,状态未知(未展开),| 新发现技术债务 | ...P3-361、P3-362、P3-363（共7项） |,待核,第五轮nucleus摸底P3-361/362/363，正文未展开,低,展开后再判,,
+D148,P3,UI远期需求,"图形化界面设计(待启动)(P3,第一百二十九章)",第一百二十九章,第一百二十九章,远期待启动,远期需求记录 - 图形化界面设计（待启动）,未动,图形化界面远期待启动；functions/health_ui.py 仅健康面板雏形,中,远期立项,,
+D149,P3,错峰调度长期架构,"重操作错峰调度长期架构(用户提出,临时拉平已做)",第一百三十九章,第一百四十二章,远期(临时拉平阶段1已验收),"重操作错峰调度长期架构思路（用户提出）；临时拉平阶段1已验收,长期架构待做",部分修复,config.py:4551 第61批P1临时拉平；PulseHeart.py:679/PulseCodeLearner.py:218 错峰偏移已做；长期架构未建,高,临时拉平已验收，长期架构待做,,
+D150,P3,quit补丁提示,"quit时补丁确认提示(P3-新,第59批T4)",第一百三十三章,第一百三十六章,待实施,T4: P3-新 quit时补丁确认提示,已修复,main.py:3325 第59批T4 用户主动退出(SIGINT/SIGTERM)时待应用补丁确认提示；:3401 标记主动退出,高,维持,,
+D151,P0,存储/Parquet,Parquet回读丢evol_level+8字段，层级保真0%，verify只数总行数,178.3,178.3/179,G0-a 启用即毁，第81批修,Parquet回读丢evol_level+8字段..._m68_verify_parquet只校验总行数→损坏不可检出,第81批施工中,PulseSnapshot.py:1170/1188必含7新列；:2382写/:2450读已补；:1281 verify升级列集合+版本+分层FAIL,高,T1完工后按A1-A8三遍保真验收，通过才许重开PARQUET_AS_PRIMARY_STORAGE,,
+D152,P0,存储/冷热加载,_m70清空L2/L3正文，lazy_ids无读取方,178.3,178.3/179,G0-b 重开即85.9%正文空，第81批修,_m70_apply_hot_cold_load就地清空L2/L3 value/linked_nodes，_m70_lazy_ids无读取方,码级已清——待 R4 后销（第118批 T-118c 据烛微§3.1）,PulseSnapshot.py:1655/1720已建物化API；但PulseNodePool.py:648 get()未调:2575、set_cold_recall_source无调用方,高,T2收尾后 R4 销号；materialize 接线已完成，待运行期验证,,118批更新·据烛微§3.1
+D153,P0,存储/保存,in-flight卡死、超时判定在finally后、退出只等90s假承诺增量恢复,178.3,178.3,G0-c 大部分缓解可能有残留,in-flight标志永久卡死(:770)...判定写在finally之后...线程不结束永不评估...退出只等90s即放行,大部分修复有残留,PulseSnapshot.py:765入口watchdog卡死重置+snapshot_stalled；:813-823超时仍post-hoc；main.py:2718 join(300s)取代90s删假承诺,中,残留：finally后超时在线程挂起时不评估；watchdog内补超时硬告警即闭环,,
+D154,P0,存储/增量日志,jsonl把全部存活ID当删除集写action=delete，重放得0节点,178.3,178.3/179,G0-d 启用即毁库，第81批修,"_get_changed_nodes返回(changed,current_ids)，_m67_incremental_log_save按(changed,deleted)解构→全部存活ID当删除集",码级已清——待 R4 后销（第118批 T-118c 据烛微§3.1）,PulseSnapshot.py:950删除集改_prev_map-current_ids；:955比例熔断降级全量；:979行checksum；:1037重放按ts定序幂等,高,T3故障注入待 R4 验证；delete 集逻辑已修，待运行期确认,,118批更新·据烛微§3.1
+D155,P0,存储/原子写,os.remove造主文件消失窗口、except无条件删tmp、0节点软校验、48MB孤儿,178.3,178.3,G0-e 大部分缓解可能有残留,用os.remove(target)再os.replace，Windows占用时制造主文件已消失窗口；except无条件删tmp；checksum不匹配也允许启动,大部分修复有残留,PulseSnapshot.py:875/1491纯os.replace；:879失败留.failed副本；:396保存<50%保护；:2020 checksum mismatch仅WARNING；磁盘snapshot_0g60at6z.json 46.2MB仍在,中,残留：checksum仍软校验；孤儿快照未纳入cleanup(只清tempdir/snap_t4_),,
+D156,P0,Web/控制台,WebChat回复被GBK print崩溃吞掉，/replies恒空,178.3,178.3,J-P0-1 一行修，第80批已修,chat_service.py:217 print中emoji在stdout重定向时GBK UnicodeEncodeError→handler中止→:220 _push_reply永不执行,已修复,"functions/chat/chat_service.py:17-18 reconfigure(utf-8,errors=replace)；:245 _push_reply前置print(:249)；80批重启对话回复正常",高,已闭环；保留回归test_gbk_reply_m80,,
+D157,P0,Web服务,单线程HTTPServer队头阻塞，慢端点可使5051停服10分钟,178.3,第101批 T-101b,J-P0-3 未动，82批,单线程HTTPServer队头阻塞：/evolution/data与/knowledge-graph.json任一可使5051整体停服约10分钟,已闭环：生产树裸 HTTPServer( 构造=0；health_ui.py:1595 与 web_chat.py:637 均 ThreadingHTTPServer(第97批 T-97f + 第99批 T-99b 已修),functions/health_ui.py:1595 / functions/web_chat.py:637 均 ThreadingHTTPServer；grep 裸 HTTPServer(=0；全仓 0.0.0.0 监听=0,中,★映射：烛微第1期 N9 ↔ 台账 D157；第97批 T-97f + 第99批 T-99b 已闭环,,
+D158,P1,知识质量,Parquet不持久化quality_flag/quality_reason，非clean46条重启被现算覆盖,178.3,178.3/179,D-x1 80批回退后暂失效，81批重开前必修,Parquet不持久化quality_flag/quality_reason...重启后人工仲裁被现算覆盖，PollutionTagger短路永不命中,第81批施工中,PulseSnapshot.py:2390-2391写出/:2456-2457读回quality_flag/reason；当前PARQUET_AS_PRIMARY_STORAGE=False JSON主存储暂不影响,高,随T1验收：suspect/polluted节点往返验证仲裁标记不被现算覆盖,,
+D159,P1,知识质量,Parquet丢source_time/acquired_time/source_timestamp，新鲜度衰减失效,178.3,178.3/179,D-x2 80批回退后暂失效，81批重开前必修,Parquet丢source_time/source_timestamp/acquired_time(JSON全有非零)，新鲜度/时效衰减失效,第81批施工中,PulseSnapshot.py:2387-2389写出/:2453-2455读回三时间字段；当前JSON主存储,高,随T1验收：时间字段往返非零校验,,
+D160,P1,存储/引用完整性,悬空引用133773条，落盘链路无引用完整性过滤,178.3,第102批 T-102a,D-x3 未动，82批,悬空引用133773条(sem60856/2.68%+linked72917/3.28%)，落盘链路无引用完整性过滤，多跳检索静默截断,已闭环,PulseSnapshot.py:1524/1429 落盘前 _m102_filter_dangling_edges(+_m102_sidecar_node_ids L2930，无节点池返回None⇒跳过绝不误删)；存量 tools/m102_data_governance.py --apply a 清理133824条(主快照)+133824条(parquet副本),高,已闭环：落盘链路已加引用完整性检查 + 存量全部清零(第102批)；开关 ENABLE_M102_DANGLING_EDGE_GUARD 默认True,,
+D161,P1,向量,孤儿向量4646占27.5%，remove生产0调用，reconcile只单向补码,178.3,第102批 T-102b,D-x4 未动，82批,孤儿向量4646个占27.5%...VectorStore.remove()生产0调用、AsyncEncodeQueue.reconcile只单向补码,已闭环,PulseNodePool.remove() 级联 get_vector_store().remove；AsyncEncodeQueue.reconcile 反向 reap_orphans；VectorStore.reap_orphans 新增；存量清理孤儿向量4842条(17800→12958，npz 34.77→25.31MB),高,已闭环：删节点级联remove + reconcile反向回收 + 存量清零(第102批)；开关 ENABLE_M102_VECTOR_CASCADE_REMOVE / ENABLE_M102_ORPHAN_VECTOR_REAP 默认True,,
+D162,P1,存储/门禁,缺字段级存活率硬门禁，大小/行数门禁无感,178.3,178.3,D-x5 未动，82批,缺字段级存活率硬门禁：内容掏空体积只降0.3%，大小/行数门禁无感；需verify_field_survival.py偏移>2%FAIL,未动,无tools/verify_field_survival.py；_m81校验偏schema列集合而非内容存活率,低,82批补字段存活率门禁工具并入A1-A8验收,,
+D163,P1,可观测/告警,无快照停滞告警、阈值硬编码；冷存skipped仅DEBUG静默丢节点,178.3,178.3/179,D-x6+G-P1 skipped升ERROR一点在81批,无快照停滞/保存静默告警，阈值全硬编码；冷存compaction skipped_files>0仅DEBUG...需升ERROR+汇总,第81批施工中,PulseNodePool.py:3056-3059 skipped>0且开关开→一条ERROR含数+样本；停滞告警/阈值config化未做,中,T4-⑥做一半；剩余停滞告警+pulse_errors接真实故障源入82批(D169同项),,
+D164,P1,存储/冷存,冷存5483碎文件、启动顺序反、逐节点全扫N平方、召回硬编码L1,178.3,178.3/179,G-P1-1 第81批修,5483小parquet碎文件...召回需批量write_to_dataset+node_id→offset索引..._recall_cold_node硬编码evol_level=L1,第81批施工中,PulseNodePool.py:2489 recall_cold_nodes_batch一次整读；:2543侧车索引read_row_group直读；:2530注释确认硬编码L1已修,高,T4收尾：冷召回性能基准1000节点<5-10s，验先compaction后lazy顺序,,
+D165,P1,存储/结构冗余,sem2267630条占85.4%，linked_nodes是sem近乎纯冗余投影,178.3,第102批 T-102c,G-P1-2 未动，82批；重复挂账→D039,sem2267630条/408.6MiB占85.4%...linked_nodes是sem的近乎纯冗余投影(Jaccard0.8972),已闭环,PulseNode.from_dict L391 按 semantic_relations 派生 linked_nodes；to_dict「可派生才不落盘」_m102_linked_derivable(零丢失判据=set(linked)==set(sem目标))；存量 --apply c 先合并19808条linked独有边入sem再清空linked_nodes,高,已闭环：冗余投影移除，只留sem一份+查询时动态计算；主快照522.39→456.83MB(-65.56MB/-12.55%)(第102批)；开关 ENABLE_M102_LINKED_NODES_DERIVED 默认True,,
+D166,P1,备份策略,3份.bak 1.4GB、选优len>=4000即break退化取最新、无外介质/无RPO,178.3,178.3,G-P1-4 选优已修，其余未动,_load_from_backup选优len>=4000即break(:1494)退化为取最新；backups与data同盘；多域零备份；无RPO,大部分修复有残留,PulseSnapshot.py:1863-1886综合评分选优(节点数+L2/L3+checksum+时间衰减)，旧>=4000 break已删；外介质/RPO/跨域备份未做,中,选优根因已闭；外介质+RPO+跨域备份入82批,,
+D167,P1,对话/correlation,搜索终止回退丢correlation_id，注册键与pop键必不等,178.3,178.3,J-P1-1 未动，82批,注册键=question[:80](:1386)，pop键=控制器改写后的search_topic(:3378)，两键必不等→空cid→结构性禁言,未动,PulseInnerWorld.py:1386注册search_query[:80]；:3378 pop(search_topic)；两键不一致根因未改,中,82批：correlation注册表改用稳定id而非改写后topic,,
+D168,P1,对话/净化,短答案被净化器误杀；打招呼被QICA误路由百科,178.3,178.3,J-P1-2 净化器已有S6短答案保留守卫,_sanitize_internal_content(S6判据)把7字问候清成我还需要再想想；打招呼被QICA误路由百科(置信0.49-0.50),部分修复,PulseInnerWorld.py:17407-17414短答案保留守卫(仅前缀删且本短则保留DEBUG)；QICA问候路由阈值未核改,中,净化误杀半已闭；QICA问候路由与置信门槛需另核,,
+D169,P1,可观测,9类故障面板/HTTP全盲，ERROR不进error_snapshots，5051停服零感知,178.3,178.3,J-P1-3 未动，82批,9类故障在面板/HTTP全盲；logger-ERROR不进error_snapshots；5051自身停服10分钟框架零感知,未动,未见pulse_errors接真实故障源、ERROR入error_snapshots改造；与D163同项,低,82批：ERROR入error_snapshots+面板暴露真实故障,,
+D170,P1,Web/安全,/params/apply_preset以GET执行参数变更，本机CSRF面,178.3,第101批 T-101b,J-P1-4 半改不一致,/params/apply_preset以GET执行参数变更(health_ui.py:1522-1539)，本机CSRF面,已闭环：前端改 POST(health_ui.py:767)；服务端 do_POST 接管 apply_preset(:876/:884→_serve_apply_preset:1564)；do_GET(:806-850) 已摘除该分支；并加 _is_same_origin 同源校验(:852),functions/health_ui.py:767 POST; :876 do_POST; :884-885 apply_preset→_serve_apply_preset; :852 _is_same_origin; do_GET 内无 apply_preset 分支,中,★映射：第99批 T-99c ↔ 台账 D170；GET 改参 CSRF 面已闭合,,
+D171,P1,运行期/稳定性,不具备>4h无人值守：RSS锯齿7GB/h、句柄泄漏、pulse劣化3-17倍,178.3,178.3,L活体 未动，需长运行重验,不具备>4h无人值守：RSS谷底5→15GB锯齿...句柄2080→2683单调泄漏...pulse劣化3-17倍,反向恶化（证据方向：RSS改善但 pulse 仍劣化3-17倍，非否认107a码工）（第118批 T-118c 据烛微§3.1）,代码级无泄漏修复/长运行看门狗；80批重启短时内存4.2GB稳定，但框架已停，>4h未重测,低,重启后跑>4h无人值守：RSS/句柄/线程/pulse_avg 连续采样再判定,,118批更新·据烛微§3.1
+D172,P1,事件流,21个死订阅、15类发射无消费、3个零发射器官、心脏退化为定时器驱动,178.3,178.3,A事件流 未动，82批专项,21个运行期死订阅；15类发射无消费；3个零发射器官；心脏PulseHeart.py:689-816排程字典24事件退化为定时器,未动,organs/body/PulseHeart.py:473 _check_scheduled_tasks定时器排程、:689 knowledge_purge；死订阅未清,低,82批事件流专项：死订阅清理+发射消费对账+零发射器官处置,,
+D173,P1,宪法/架构,五维权重双源(一死一活违单一来源)+main.py横切直调+organs模块级可变全局,178.3,178.3,B宪法 未动，82批专项；重复挂账→D130,五维共振权重记忆40/空间30/逻辑15/时间10/状态5四处定义违单一来源，实测仅1.5维有效,未动,config.py:668 RESONANCE_WEIGHTS全代码0引用(死定义) vs ResonanceEngine.py:38 WEIGHTS实际生效；main.py:1302/1344/1392/1501/1507/1998/2669直调器官；organs~40+模块级dict(PulseNeurotransmitters.py:24 NEUROTRANSMITTERS/PulseKnowledgeRetriever.py:20 ORGAN_ALIAS_MAP/:80 CORE_CONCEPT_DEFS为可变容器),中,第82批：权重单一来源收敛(删config死定义或改引用)+main横切直调收敛+模块全局冻结,,
+D174,P1,复杂度,PulseInnerWorld.py 上帝文件(重复编号)→并入D021,178.3,178.3,C复杂度 未动，82批专项；重复挂账→D021,lizard 5984函数CCN≥20=329...PulseInnerWorld.py 23145行上帝文件(D021续),重复挂账,同D021：organs\brain\PulseInnerWorld.py 23145行；本号为第三方冗余编号,高,重复挂账→D021；第82批以D021为主票拆分,,
+D175,P1,自进化/审批治理,进化审批治理残留组：非核心文件存在完整「免签→落盘→自动重启」通路,烛微第1期,烛微第1期,烛微第1期 N1【P1-1】（烛微第1期报告，已附复现脚本）,① _m85_local_low_risk_auto_apply 显式设计为总开关False时仍生效，local_auto_apply_enabled 键不在config→缺省即开；② PulseCodeLearner apply_now 路只查 status==approved，dynamic_test.passed 缺省 True（异常=假通过）；③ 落地侧三条触发口均不读 auto_apply_enabled；④ _check_patch_safety 对 approved 直接 return safe(人工批准),本批 T-96b 已处置①②④；③改为「区分机器/人工」口径（星轨裁决）,nucleus/reasoning/PatchManager.py:_m85_local_low_risk_auto_apply/:423 _check_patch_safety/:2399 apply_all_pending 入口收口；organs/brain/PulseCodeLearner.py:3493 dynamic_test 缺省改 False,高,★映射：烛微第1期 N1 ↔ 台账 D175；已显式登记 config(local_auto_apply_enabled/allow_core_auto_apply 默认 False)；④ 机器自动批准(auto_approved=True)须走完三关，人工批准维持放行语义,,
+D176,P1,自进化/账本,进化闭环有效性不达标 + 账本假成功（applied 但磁盘无对应内容）,烛微第1期,烛微第1期,烛微第1期 N2【P1-2】（烛微第1期报告，已附复现脚本）,67 条 applied 中 2 条磁盘无对应内容——含今日 18:13:11 的 LLM 补丁：当前 PulseInnerWorld.py 与其落地前备份逐字节相同、目标符号 _calc_math_question 全文件 0 次，账本仍记 applied=True/effect_verified=True/effectiveness=1.0,本批 T-96c 已处置：已加落地后磁盘复核 + 护栏函数禁补丁清单 + 历史 2 条更正留痕（双写）,nucleus/reasoning/PatchManager.py:applied 后回读磁盘复核 + _M96_NO_AUTO_PATCH_METHODS；data/patches/patch_history.json:patch_llm_1789886168_789a / patch_llm_1789890006_e8ba,高,★映射：烛微第1期 N2 ↔ 台账 D176；★烛微给出的两条证据路径【均未复现】（目标符号0次=0条、与备份逐字节相同=0条）；可靠判据应为「original_code 在盘 & modified_code 不在盘」⇒ 复现 2 条，已更正；见留痕报告,,
+D177,P1,自进化/LLM通道,进化链 LLM 调用绕过肺熔断与并发控制（09-18 EXP-2 未修）,烛微第1期,烛微第1期,烛微第1期 N3【P1-3】（烛微第1期报告，已附复现脚本）,"今日 3,443 次进化链调用（占答案类请求 63%）走 ssrf_guard.safe_http_json 自有出口，不接 ChannelHealthTracker 熔断、不接并发闸；86% 失败率下仍无退避持续发起",本批 T-96a 已处置（灰度：ENABLE_EVOLUTION_USE_CHANNEL_POOL 默认 False）,nucleus/reasoning/SafeEvolutionExecutor.py:_m96_channel_pool_on / _m96_select_channel / _m96_record_channel_result；config.py:ENABLE_EVOLUTION_USE_CHANNEL_POOL,高,★映射：烛微第1期 N3 ↔ 台账 D177；已具备渠道池选择 + 熔断跳过 + 健康度回写（与对话链路同账本）；开关默认关，需重启后灰度验证再打开；★子进程 run_in_subprocess 路径仍直连 REMOTE_API_CONFIG（未覆盖）,,
+D178,P1,数据/层级,"冷层 5,504 行错误层级固化（09-18 层级塌缩的化石层）",烛微第1期,烛微第1期,烛微第1期 N4【P1-4】（烛微第1期报告，已附复现脚本）,"data/knowledge/cold/ 全部 5,504 行 evol_level 固化为 L1，与热层真值对照：3,089 应为 L2、2,395 应为 L3，零个真 L1；其中 20 个仅存于冷层的节点层级不可恢复",未动（本批未覆盖，待立项）,data/knowledge/cold/ 冷 flat 文件,高,★映射：烛微第1期 N4 ↔ 台账 D178；离线一次性用热层真值重写冷层；20 个 cold-only 节点需人工裁决；等待冷驱逐首次发生做阳性验证,,
+D179,P1,可观测性/日志,F3 日志聚合器是死机制，且注释宣称其在工作,烛微第1期,烛微第1期,烛微第1期 N5【P1-5】（烛微第1期报告，已附复现脚本）,"logger.py:483-488 注释称 filter 挂在 root 上同时作用于两个 handler——Python 语义相反：logger 级 filter 不作用于子 logger 传播的记录；今日 20,186 行 DEBUG 中「(聚合」标记 0 次",未动（本批未覆盖，待立项）,nucleus/logger.py:483-488,高,★映射：烛微第1期 N5 ↔ 台账 D179；file_handler.addFilter(...) + console handler 同步挂载；加「聚合器实生效」断言测试（0.5 人时）,,
+D180,P1,数据/一致性,"悬空边 133,767 条持平未消 + 孤儿向量 27.3% 单调上升（删除只删一半）",烛微第1期,烛微第1期,烛微第1期 N6【P1-6】（烛微第1期报告，已附复现脚本）,"孤儿向量 4,747/17,404=27.3%（一周 +101），VectorStore.remove() 生产调用点仍为 0，检索 TopK 约 1/4 命中不存在的知识；另有 18 个热节点缺向量",未动（本批未覆盖，待立项）,nucleus/mnemosyne/VectorStore.remove() 零生产调用点,高,★映射：烛微第1期 N6 ↔ 台账 D180；删除路径三写一致（快照/向量/边）收口 + 启动期 reconcile 补向量删孤儿；悬空边一次性清洗,,
+D181,P1,健壮性/静默except,静默 except 存量：84.2% 不上报，安全关键路径 TIER1 = 192 处,烛微第1期,烛微第1期,烛微第1期 N7【P1-7】（烛微第1期报告，已附复现脚本）,"608 文件 3,430 个 except 中 2,889 个吞异常不上报；TIER1（宽捕获+完全静默+含磁盘或网络IO）192 处，最危险：ParamPatchManager._save_history:507 补丁历史写盘失败零留痕",未动（本批未覆盖，待立项）,nucleus/reasoning/ParamPatchManager.py:507；PatchAutoApprover.py:160,高,★映射：烛微第1期 N7 ↔ 台账 D181；不要求清零；TIER1 192 处优先补留痕（每处 1 行），做成门禁防增量,,
+D182,P1,器官/胃,胃 _balanced_json_extract 必然 TypeError，策略自 26 批起从未生效,烛微第1期,第101批 T-101b,烛微第1期 N8【P1-8】（烛微第1期报告，已附复现脚本）,PulseStomach.py:2233 定义缺 self 缺 @staticmethod，而 :732 以 self._balanced_json_extract(x) 调用 → 每次调用必抛 takes 1 positional argument but 2 were given，被外层 except 吞成 DEBUG（今日 ×10）,已闭环：PulseStomach.py:2233 已加 @staticmethod(原缺 self/@staticmethod 致每次调用必 TypeError 被外层 except 吞成 DEBUG)；:732 以 self._balanced_json_extract(x) 调用现已生效,organs/body/PulseStomach.py:2233 @staticmethod; :732 调用形态一致(staticmethod 可静态调用),高,★映射：烛微第1期 N8 ↔ 台账 D182；第97批顺手并入已闭环,,
+D183,P1,可观测性/服务,观测面仍是裸单线程 HTTPServer（09-18 J-P0-3 未修）,烛微第1期,第101批 T-101b,烛微第1期 N9【P1-9】（烛微第1期报告，已附复现脚本）,health_ui.py:1558、web_chat.py 均仍为 HTTPServer——09-18 实测一次 /evolution/data 探测拖停 5051 约 10 分钟,已闭环：health_ui.py:1595 ThreadingHTTPServer(与 D157 同修；第97批 T-97f + 第99批 T-99b),"functions/health_ui.py:1595 ThreadingHTTPServer(('127.0.0.1', self.port), HealthHandler)",高,★映射：烛微第1期 N9 ↔ 台账 D183；与 D157 合并自灭，已闭环,,
+D184,P1,流程/台账,烛微 09-18 六项 P0 无一进台账（流程性盲区）,烛微第1期,烛微第1期,烛微第1期 N10【P1-10】（烛微第1期报告，已附复现脚本）,台账 grep WinError/停摆/首报/限流/聚合 = 0 行。三方审计最重要的输入没有进入两方工作流的事实清单。这是机制问题不是态度问题：建议把「外部审计报告 → 台账条目」做成固定投递工序（含 P级映射）,本批 T-96e 已处置：N1~N11 全部入台账 + P级映射表 + 固定投递工序 SOP 已建立,docs/分析报告/外部审计_台账投递工序.md（SOP + N↔D 映射表）；本 CSV D175~D185,高,★映射：烛微第1期 N10 ↔ 台账 D184；★新增 SOP：每份外部审计报告出稿后 24h 内完成投递；验收判据为「台账出现该报告期号」，并由批次交付报告回引,,
+D185,P1,数据/FAISS,FAISS 持久化链路死代码,烛微第1期,烛微第1期,烛微第1期 N11【P1-11】（烛微第1期报告，已附复现脚本）,faiss_store.save()（faiss_store.py:298）无任何生产调用点，data/knowledge/faiss/ 目录不存在——68-75 批宣称的 FAISS 能力实际持久态只有 vectors.npz（对齐良好）,未动（本批未覆盖，待裁决：接盘 or 除名）,nucleus/mnemosyne/faiss_store.py:298 save() 零调用点,高,★映射：烛微第1期 N11 ↔ 台账 D185；接盘或除名二选一，禁止「实现了但从不保存」的中间态,,
+D186,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D187,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D188,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D189,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D190,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,注销(非债)·HOT_COLD_LOAD 保护机制正常工作（第118批 T-118c 据烛微§3.1）,待录入,待录入,注销：保护机制正常，非技术债务,pool,118批注销·据烛微§3.1
+D191,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D192,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D193,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D194,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D195,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D196,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D197,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D198,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D199,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D200,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D201,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D202,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D203,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D204,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D205,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D206,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D207,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
```

## docs/完整进化路线与技术债务清单_v1.0.md  （T-118c 账本三件套）
```diff
--- a/docs/完整进化路线与技术债务清单_v1.0.md
+++ b/docs/完整进化路线与技术债务清单_v1.0.md
@@ -58,18 +58,36 @@
 | L3队列上限 | ✅ 300（已修复生效） | 第33批T4修复硬编码覆盖，重启后动态扩缩日志显示/300 |
 | 多步检索兜底 | ✅ 已根治 | 第34批T1全步失败return None，第35批T1入口预判0次模型调用 |
 
-### 1.2 技术债务统计（v8.0全面核对后）
-
-| 优先级 | 待处理 | 进行中 | 已完成 | 合计 |
-|--------|--------|--------|--------|------|
-| P0（紧急/阻塞） | 3 | 0 | 8 | 11 |
-| P1（高优先） | 2 | 0 | 26 | 28 |
-| P2（中优先） | 35 | 0 | 75 | 110 |
-| P3（远期/PHASE18） | 12 | 0 | 8 | 20 |
-| **合计** | **52** | **0** | **117** | **169** |
-
-> **v9.9核对说明**：第33-35批技术债务清理阶段新增P2-196~P2-209共14项债务（其中9项已修复，5项待处理）；P2-157/158/159/155等第24批任务已完成标记更新；技术债务清理阶段（第33-35批）收官，第36批起进入PHASE18新能力实施。
-
+### 1.2 技术债务统计（第118批双轨制刷新）
+
+> 旧 §1.2 的 169/117/52 = 69.2% 自述口径为 **09-12 第36批时代过期数据，自第118批起废止**（烛微 118 前置分析 §3.2 判定）。下文改为**池票/现场票双轨**口径，完成率分轨永不合母。
+
+| 轨道 | 已清 | 开放 | 销号 | 完成率 |
+|---|---:|---:|---:|---:|
+| 池票（pool） | 29 | 151 | 5 | 16.1% |
+| 现场票（site） | 0 | 21 | 1 | 0% |
+| 合计参考 | - | - | - | ~14.4%（勿作 KPI） |
+
+注脚：
+- 待验证一律计入「开放」（诚实口径），故池票 185 = 29 清 / 151 开 / 5 销。
+- R4 后 D152/D154/193 等转清 → 池票完成率约 17.2%，**勿提前计入**。
+- 旧 69.2% 自第118批废止，禁止对外引用。
+
+## 债务总账刷新（第118批提案）
+
+### 勘误三处（第118批 T-118c 据烛微§3.3）
+1. T-113d「RequestDeduplicator 通电✅」→ **已实施·默认关**（真通电需改 config 默认值一批 + `[请求去重]` INFO ≥1 才销）。
+2. T-115e「face 四一批✅」→ **3.5/4 开放挂 D205**（含 :89 半修复终判 + 装库激活表）。
+3. git 案「114 期 .git/refs 失踪 P0」→ **新根基线恢复（8d9b95f）**：23:59 新根提交，rev-list=1 旧链断；reflog 坏 4 处；unreachable 对象 ~2923 行疑可 lost-found 归档（星轨决策项，非执行项）。
+
+### 双轨制规则（第118批固化）
+- 完成率分轨永不合母；site 票终身 site（防回池刷分）。
+- site 周冻结基线（防单日 +21 砸成永久低位）。
+- 三读同源：脚本（tools/check_debt_ledger.py）→ MD → 交付报告引用同一四数。
+
+### 待办（第118批遗留）
+- CSV 台账 D186-D207 共 22 行已于第118批按任务书形态预建，实质内容待烛微 lz_analysis/dz_analysis 精确表同步后补录。
+- 107/112 批六票精确票号待定位后更新 CSV 实查状态。
 ### 1.3 近期里程碑（按时间顺序）
 
 | 时间 | 里程碑 |
```

## docs/验收/R4验收清单_B1-B23.md  （T-118d② R4 B16重定/B24顺延）
```diff
--- a/docs/验收/R4验收清单_B1-B23.md
+++ b/docs/验收/R4验收清单_B1-B23.md
@@ -49,38 +49,30 @@
 
 ---
 
-## 3. 跨批组（B13–B23）
+## 3. 跨批组（B13–B24）
 
 | # | 项 | 判据命令 | 期望 | 不通过 → 断点与处置 | 状态 |
 |---|---|---|---|---|---|
 | B13 | 假死文案落盘 | `grep -a "假死探测器" logs/pulse.log \| tail -5` | 机制已对（main.py 三处 `getLogger("pulse")`）；**真触发**才算验 | B8 轮重负载=天然触发窗；被动 ≤24h；**不可生产合成**。24h 无触发→挂观察；有 crash 新块却无文案=修漏回归 | ⬜ |
 | B14 | 停搏对账非 0 | `grep -a "心脏停搏，总计" logs/pulse.log \| tail -3` | **下次停机**时对账 >0 | 仍 0 → 本单全部心脏项作废重查 | ⬜ |
 | B15 | 棘轮分叉终判 | 停机 → 读 `restart_count.txt` → R5 启动读值 | 序列 **0→1→1** | 若跳 6 = 内存回写实锤（老进程 `_save` 点），登记新债 + unlock 后重启先于 apply | ⬜ |
-| B16★ | tracer convoy 归零 | crash.log 新块内 flush 等待帧计数（块边界法；**存量基线 530 帧勿误引**） | flush 等待帧 **0**（旧 11 参与者 → 0） | 复现 → T-117a 未生效（占闸/后台刷），插队修 | ⬜ |
+| B16★ | Dummy AttributeError 复发判定 | `grep -a "Dummy' has no attribute"` 新开机段 | **0 命中**（正控：18:38:22 存量旧命中） | 有命中 → 115d 四处防御未覆盖该属性，补防御后重测 | ⬜ |
 | B17 | face 自检真值 | `grep -a "人脸识别=" logs/pulse.log \| tail -3` | **装库前 = `无`**（诚实态） | =`有` 且库缺 → 113c 回归 | ⬜ |
 | B18 | 影子静默正控 | `grep -a "\[face_welcome影子\]" logs/pulse.log \| tail -5` | `ENABLE_FACE_WELCOME_DIRECT=False` 时必须 **0 行** | 有行 = 条件写反（读 chat_service `_should_skip` 判据） | ⬜ |
 | B19★ | R2 缺口复核（**判据已按 09-24 实测修正**） | `grep -n "_current_user_name = \"" functions/chat/chat_service.py`<br>`grep -n "_current_user_name = \"" organs/body/PulseHeart.py` | chat_service 三处（:69/:139/:229）**均应为"访客"** | ⚠️ 烛微原判「chat_service :89 仍'小林'」**不成立**（115e 已改，且改前 :68 亦是"访客"）。<br>**真残留**：`PulseHeart.py:108` 初始默认仍是 **"小林"**（:289 已是"访客"）。<br>红线：DIRECT 真开前须一并改掉 | ⬜ |
 | B20 | CLI 补口行为面 | `python tools/adjudicate_patch.py stale-audit`（只读）<br>`python tools/adjudicate_patch.py fix-c6`（默认 dry-run）<br>`python tools/adjudicate_patch.py unlock-ratchet --require-queue-clean` | stale-audit 出建议单（顺验是否覆盖到期票）；fix-c6 **默认不写盘**；queue-clean 硬门生效 | 写路径异常 → 停手回滚 `.bak_batch116`；**勿在生产试写**，用 `--root` 指 tmp 副本 | ⬜ |
 | B21 | created_at 新票 | R4 后新入队票：`python -c "读 pending 打 created_at"` | 新票 **created_at > 0** | ⚠️ 停机后无新票（15/74/22 **全 0**），**现网数据暂无法验证**。<br>功能面已用隔离根实证通过（真入队 → created_at=真实时间戳，见 117d③）。<br>仍 0 → PM 入队点漏（116f） | ⬜ |
 | B22 | 冒烟隔离规矩 | 停机/测试窗内 `grep -a "a.py\|m" logs/pulse.log` | **0 命中**（合成指纹只许进 `logs/smoke.log`） | 再现 → 冒烟脚本改用 `nucleus.logger.get_smoke_logger()`（117d② 已提供）<br>现成范式见 `tests/test_m117_ratchet_and_smoke.py` | ⬜ |
-| B23 | 残余 dump 续档 | crash.log 新块 flush 等待者计数（块边界法） | 同 B16：**0** | 7→1 复现 → T-117a 优先级↑，勿等自愈 | ⬜ |
+| B23 | 残余 dump 续档 | crash.log 新块 flush 等待者计数（块边界法） | 同 B24（原 B16）：**0** | 7→1 复现 → T-117a 优先级↑，勿等自愈 | ⬜ |
+| B24 | tracer convoy 归零（原 B16，第118批重编号） | crash.log 新块内 flush 等待帧计数（块边界法；**存量基线 530 帧勿误引**） | flush 等待帧 **0**（旧 11 参与者 → 0） | 复现 → T-117a 未生效（占闸/后台刷），插队修 | ⬜ |
 
 ---
 
-## 3.1 ⚠️ B16 编号说明（路灯补号，非烛微原判）
+## 3.1 ⚠️ B16/B24 编号说明（117 补号 + 118 拍板）
 
-烛微 §2 原表按批次分组枚举，实际列出的编号是
-`B1-B4 / B13-B14 / B17-B19 / B5-B12 / B15 / B20-B23` —— **合计 22 条，缺 B16**。
-
-本表为凑齐任务书要求的 **B1–B23**，把 B16 补给**本批 P0 主体**（tracer flush convoy 归零），
-理由是：烛微 §4 把它列为「D186 收口的最后拼图」，且 T-117a 已在本批落地，
-**正是最需要一条 runtime 判据来开奖的项**。B23 与 B16 同源（都读 crash.log 块），
-但 B16 看**本次修的 flush 等待帧**，B23 看**残余 dump 续档**，两者分开记账。
-
-> 若星轨希望 B16 另作他用（或维持 22 条口径），改一行即可，不影响其余 22 条。
-
----
-
+- **第117批**：烛微 §2 原表实列 22 条缺 B16；路灯补 B16 = tracer flush convoy 归零（T-117a 主体，最需 runtime 开奖）。B23 与 B16 同源（都读 crash.log 块），B16 看 flush 等待帧、B23 看残余 dump 续档，分开记账。
+- **第118批（星轨对 E1 拍板）**：B16 重新定义为 **Dummy AttributeError 复发判定**（115d 四处防御恰好缺运行时判据，空位正好；正控 = 18:38:22 存量旧命中 `Dummy' has no attribute`）。原 tracer convoy 顺延为 **B24**（见跨批组末行），内容不变、判据不变。
+- 编号调整后共 B1–B24 计 24 条；若星轨希望 B16 维持 22 条口径，B24 可并入 B16 描述，不影响其余。
 ## 4. 验收判据的「0 命中正控」
 
 凡用「某串 grep 命中数 = 0」作**通过**判据的条目（B10/B18/B22），
@@ -107,7 +99,7 @@
 | 开机 60s 生死组（B1–B4） | | | | |
 | 进化链组（B5–B12） | | | | |
 | 跨批组（B13–B23） | | | | |
-| **合计** | **/23** | | | |
+| **合计** | **/24** | | | |
 
 执行人：______　重启时刻：______　完成时刻：______
 
```
