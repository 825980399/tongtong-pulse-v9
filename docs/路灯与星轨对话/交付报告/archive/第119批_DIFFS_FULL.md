# 第119批 · 改动 DIFF（FULL）
> 生成方式：以 `.bak_batch119/` 改前快照为基线，对当前工作副本做 `difflib.unified_diff` 逐行对比。
> 行尾保全：VC.py / SE.py 维持 CRLF，CSV 维持 UTF-8 BOM + CRLF；diff 已按逻辑行（去 CRLF）展示，行尾无污染。
---
## organs/senses/PulseVisualCortex.py
```diff
--- a/organs/senses/PulseVisualCortex.py
+++ b/organs/senses/PulseVisualCortex.py
@@ -174,7 +174,7 @@
         try:
             import face_recognition  # noqa: F401
             self._has_face_recognition = True
-        except ImportError:
+        except (ImportError, SystemExit):  # ★T-119a 装库前置硬化：models 缺失 api.py quit() 抛 SystemExit
             self._has_face_recognition = False
             self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
         try:
@@ -359,8 +359,8 @@
         这样曈曈在与人对话时自然学习对方的长相。
         """
         user_name = payload.get("user_name", "")
-        if not user_name or user_name == "用户":
-            return {"status": "skipped", "reason": "无有效用户名"}
+        if not user_name or user_name in ("用户", "访客", "小林"):  # ★T-119a 绑定白名单守卫：脏键禁止入册
+            return {"status": "skipped", "reason": "脏键(用户/访客/小林)禁止入册"}
         
         # 如果有待绑定的人脸编码且距今30秒内，绑定到当前用户名
         if (self._pending_face_encoding is not None 
@@ -544,7 +544,7 @@
             _conf = max(0.0, 1.0 - best_distance / 0.6)
             return (best_match, _conf)
             
-        except Exception as _e:
+        except (Exception, SystemExit) as _e:  # ★T-119a 硬化：SystemExit 非 Exception 子类
             # ★T-113c：识别失败计数 + 日志，防止"8天0成功"无感知
             _fc = getattr(self, "_face_recognize_fail_count", 0) + 1
             self._face_recognize_fail_count = _fc
@@ -564,7 +564,7 @@
             face_encodings = face_recognition.face_encodings(rgb_frame)
             if face_encodings:
                 return face_encodings[0]
-        except Exception as e:
+        except (Exception, SystemExit) as e:  # ★T-119a 硬化：SystemExit 非 Exception 子类
             self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
         return None    
 
```

## nucleus/reasoning/SafeEvolutionExecutor.py
```diff
--- a/nucleus/reasoning/SafeEvolutionExecutor.py
+++ b/nucleus/reasoning/SafeEvolutionExecutor.py
@@ -324,6 +324,8 @@
             self._no_fix_cooldown_secs = {
                 "高危·安全拦截": 86400.0,
                 "本地无规则·转LLM": 21600.0, "_default": 3600.0,
+                "验证失败·3轮": 86400.0,  # ★T-119d 同步 114a 活键（config 同名）
+                "验证失败": 3600.0,  # ★T-119d 同步 114a 活键
             }
         # ★v16.0新增：初始化PatchManager
         # ★主线第58批 T1（P1）：_project_root 提升为实例变量，
```

## docs/分析报告/技术债务台账_代码实查_20260919.csv
```diff
--- a/docs/分析报告/技术债务台账_代码实查_20260919.csv
+++ b/docs/分析报告/技术债务台账_代码实查_20260919.csv
@@ -13,7 +13,7 @@
 D012,P0,LLM留存管道,LLM调用全程留存管道(零号工程)(P0-254),第七十四章,第一百三十六章,状态未知,| P0-254 | LLM调用全程留存管道（零号工程） | ❓ 状态未知 |,大部分修复有残留,nucleus/llm/call_recorder.py 530行存在，ENABLE_LLM_CALL_RECORDER默认True(:180)；LLMEvolutionEngine.py:16/SelfReflectionEngine.py:16导入；残留calls_20260913.jsonl 231条中146条为测试桩(write_guard.py:5),高,管道已建，需清洗测试桩污染,,
 D013,P0,依赖度指标,大模型依赖度指标口径错位(P0-262),第七十四章,第一百三十六章,状态未知,| P0-262 | 大模型依赖度指标口径错位 | ❓ 状态未知 |,大部分修复有残留,SCENE_LUNG=肺回答定义(LLMDependencyMetrics.py:32)；PulseLung.py:1742 record_llm_call(SCENE_LUNG)已补原『对话=0』埋点,中,埋点已补，待真实运行数据重算基线,,
 D014,P0,进化验证空转,进化验证空转是依赖度97%的根因(P0-263),第七十四章,第101批 T-101b,状态未知,| P0-263 | 进化验证空转是依赖度97%的根因 | ❓ 状态未知 |,已闭环(部分)：baseline 恒 0 根因已确证修复(patch_verification_split.py)；第101批 T-101a 打通 批准→落盘 闭环，补丁可走完全链(提交→批准→落盘→复验),patch_verification_split.py 确证 baseline 恒 0；SafeEvolutionExecutor.py:1454/1471/1778/1975；PatchManager._m101_low_risk_release_path(第101批 T-101a),中,判据已修，真实修复率待长期观测；★第101批 T-101b 回写：D014 记已闭环(部分)，进化闭环末环由 T-101a 打通,,
-D015,P0,埋点验证,T2埋点需重启框架才能采集真实数据(P0-271),第七十五章,第七十五章,待重启验证,P0-271 ...本批仅代码+单测，未重启 → 重启后应见 calls_*.jsonl 增长,大部分修复有残留,实测data/llm_traces/calls_*.jsonl已149文件/9.84MB，证明重启后真实数据已增长(非空转),中,关闭待重启项，数据已采集,,
+D015,P0,埋点验证,T2埋点需重启框架才能采集真实数据(P0-271),第七十五章,第七十五章,待重启验证,P0-271 ...本批仅代码+单测，未重启 → 重启后应见 calls_*.jsonl 增长,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：大部分修复有残留,实测data/llm_traces/calls_*.jsonl已149文件/9.84MB，证明重启后真实数据已增长(非空转),中,关闭待重启项，数据已采集,,已清·第119批 T-119b①
 D016,P0,未展开新登记,(新增P0级债务，正文未展开)(P0-272),第七十六章,第一百三十六章,状态未知,| P0-272 | （新增P0级债务） | ❓ 状态未知 |,已修复,总账:4069 第42批T1完成『根因定性外部原地截断；新增超期清理+被截断必留痕』；logger.py:175 LOG_RETENTION_PROTECT永不删,中,关闭,,
 D017,P0,日志治理,日志清空执行者仍未定位(P0-278→P1),第七十七章,第七十七章,待下次发生时定位,P0-278→P1 | 日志清空执行者仍未定位，但已具备留痕能力 | 下次发生时定位,大部分修复有残留,总账:4069根因=外部原地截断已定性并加留痕；logger.py:175保护pulse_crash.log；但具体外部进程/执行者未点名,中,已具留痕，待下次发生抓现行,,
 D018,P0,InfluxDB真实落库,InfluxDB时序库真实落库仍被凭证401硬阻(第79批T3),第一百七十六章,第一百七十六章,待token注入真实环境复跑(移交第80批),数据真实落库且可查询仍被凭证硬阻，移交第80批「外部存储收口」待 token 注入真实环境复跑,已失效/不再适用,ENABLE_INFLUXDB_TIMESERIES=False(已核实)；PulseNodePool.py:1111与PulseStomach.py:302双门禁；influxdb_store.py:46 _influx_enabled()恒False→生产0次真实connect，非运行时阻塞,高,降档为休眠特性，移出P0阻塞,,
@@ -25,9 +25,9 @@
 D024,P1,自检视,self_inspector方法体定位精确命中率0%(P1-248),第七十三章,第一百三十六章,待处理,| P1-248 | self_inspector方法体定位精确命中率0% | ⏳ 待处理 |,部分修复,self_inspector现175处命中，exploration_audit.py:230 run_parallel_audit接入12检测器；但SafeEvolutionExecutor.py:1499注『仍缺method无法方法级修复』,中,框架已接，方法级定位精度仍待提,,
 D025,P1,置信度校准,置信度校准机制缺失(P1-255),第七十四章,第一百三十六章,状态未知,| P1-255 | 置信度校准机制缺失 | ❓ 状态未知 |,大部分修复有残留,SelfCalibrator.calibrate被CausalVerifier.py:314与semantic_cache.py:122阈值校准接入，机制已建,中,机制已上，校准曲线待数据验证,,
 D026,P1,自我蒸馏,自我蒸馏退化螺旋风险(P1-256),第七十四章,第一百三十六章,状态未知,| P1-256 | 自我蒸馏退化螺旋风险 | ❓ 状态未知 |,待核,蒸馏基础设施存在(TaskPipeline DISTILL；self_inspector.py:275 P3-12自增强)，但无针对退化螺旋的主动缓解/监控代码；自主修复率~1.8%佐证风险,低,缺：退化螺旋监控指标与刹车机制,,
-D027,P1,数据规模,数据规模严重低估(110MB→4.8GB)(P1-264),第七十四章,第一百三十六章,状态未知,| P1-264 | 数据规模严重低估（110MB→4.8GB） | ❓ 状态未知 |,待核,规模为运行期量；代码侧未见规模自适应治理，与D038/D041快照增长同源,低,缺：实测data目录当前体量,,
+D027,P1,数据规模,数据规模严重低估(110MB→4.8GB)(P1-264),第七十四章,第一百三十六章,状态未知,| P1-264 | 数据规模严重低估（110MB→4.8GB） | ❓ 状态未知 |,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：待核,规模为运行期量；代码侧未见规模自适应治理，与D038/D041快照增长同源,低,缺：实测data目录当前体量,,已清·第119批 T-119b①
 D028,P1,ONNX资产,已有3套ONNX嵌入模型未被当作资产(P1-265),第七十四章,第一百三十六章,状态未知,| P1-265 | 已有3套ONNX嵌入模型未被当作资产 | ❓ 状态未知 |,部分修复,semantic_cache.py:7已复用90MB ONNX/512维；但models/目录实测未找到.onnx实物(疑运行时下载)，3套资产未集中管理,中,1套已接入，资产台账待建,,
-D029,P1,隔离区可见性,T2隔离区1274文件沙箱执行后不可见(manifest完整)(P1-273),第七十六章,第七十六章,待真实终端复核,P1-273 | T2隔离区1274文件沙箱执行后不可见（manifest完整） | 真实终端复核,待重启验证,总账:3964标记『真实终端复核data/_quarantine/』；沙箱可见性为运行期现象，代码层未见修复,低,缺：真实终端ls data/_quarantine/复核,,
+D029,P1,隔离区可见性,T2隔离区1274文件沙箱执行后不可见(manifest完整)(P1-273),第七十六章,第七十六章,待真实终端复核,P1-273 | T2隔离区1274文件沙箱执行后不可见（manifest完整） | 真实终端复核,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：待重启验证,总账:3964标记『真实终端复核data/_quarantine/』；沙箱可见性为运行期现象，代码层未见修复,低,缺：真实终端ls data/_quarantine/复核,,已清·第119批 T-119b①
 D030,P1,重启生效,T2/T3生成侧改动需重启框架生效(P1-274),第七十六章,第七十六章,待重启验证,P1-274 | T2/T3生成侧改动需重启框架生效 | 重启窗口,待重启验证,总账:3965标记重启窗口；生成侧改动运行期生效项，代码层无法静态判定,低,缺：重启后确认.bak不再增/cache非空,,
 D031,P1,补丁验证口径,baseline_errors为文件级口径无法反映方法级修复效果(P1-281),第七十七章,第七十七章,待日志格式改善,P1-281 | baseline_errors是文件级口径，无法反映方法级修复效果 | 改善日志格式后提升,大部分修复有残留,patch_verification_split.py:10/110引入post_apply_errors区分Problem-Fixed；patch_quality_evaluator.py:188记录；但SafeEvolutionExecutor.py:1499仍缺方法级,中,口径已加post_apply，方法级定位待补,,
 D032,P1,进化循环埋点,进化循环埋点仅部分闭环(P1-292),第七十九章,第八十章,文档称已完成(待代码复核),⇒ 进化循环埋点已生效（P1-292 部分闭环）,大部分修复有残留,LLMEvolutionEngine.py:16与SelfReflectionEngine.py:16 import trace_evolution_call；总账:4752『已生效部分闭环』,中,部分闭环属实，待全链路验证,,
@@ -72,7 +72,7 @@
 D071,P2,器官print,"6个器官未实现print改log(P2-230,自P2-212拆出)",第七十一章v9.10,第七十一章v9.10,待处理,P2-212（6个器官未实现）→ P2-230,未动,"实测organs下print(=519处/54文件,远大于文档所述6个;含Lung/Cortex/Stomach/VisualCortex等",高,"按器官print->logger批量迁移,先五脏五感",,
 D072,P2,配对器信噪比,配对器信噪比优化(目录/扩展名排除白名单)(P2-231),第七十二章,第七十二章,待实施(T-list),T5（P2）：P2-231 配对器信噪比优化...,部分修复,self_awareness/ProductionConsumptionMatcher.py=1212;FakeLoopDetector.py=730 存在,中,确认目录/扩展名白名单已进matcher,,
 D073,P2,磁盘枚举标定,磁盘枚举未在10万级文件规模标定(P2-234),第七十二章,第七十二章,后续批次,| P2-234 | 磁盘枚举未在10万级文件规模标定 | 后续批次 |,未动,"性能标定项,无代码缺陷落点",低,在10万级目录实测枚举耗时,,
-D074,P2,调度耗时,"每日调度全流程5.4s(P2-235,当前可接受)",第七十二章,第七十二章,后续优化,| P2-235 | 每日调度全流程5.4s（当前每日1次可接受） | 后续优化 |,未动,"性能观测项,当前每日1次可接受",低,随调度量级增长再优化,,
+D074,P2,调度耗时,"每日调度全流程5.4s(P2-235,当前可接受)",第七十二章,第七十二章,后续优化,| P2-235 | 每日调度全流程5.4s（当前每日1次可接受） | 后续优化 |,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：未动,"性能观测项,当前每日1次可接受",低,随调度量级增长再优化,,已清·第119批 T-119b①
 D075,P2,返回值区分,返回值未区分两类no_consumer(P2-236),第七十二章,第七十二章,后续批次,| P2-236 | 返回值未区分两类no_consumer | 后续批次 |,待核,"无明确落点,需对照report_bus/publishers确认",低,在consumer返回处细分no_consumer原因码,,
 D076,P2,测试写生产目录,"测试隐式写入生产目录,根因未100%锁定(P2-237)",第七十二章,第七十二章,持续观察,| P2-237 | 测试隐式写入生产目录（已加防御，根因未100%锁定） | 持续观察 |,大部分修复有残留,data/write_guard.py=315行(第44批T4):pytest环境拒写data/前缀;L1-22判据;根因观察中,中,"持续观察守卫告警,定位剩余写点",,
 D077,P2,白名单目录,T2目标依赖任务书外11个目录(P2-244),第七十三章,第七十四章,待第40批确认,| P2-244 | T2 目标依赖任务书之外的11个目录... | 🔄 第40批确认 |,待核,"需对照任务书白名单清单,静态无法判定11目录现状",低,对照任务书逐项确认11目录去留,,
@@ -96,7 +96,7 @@
 D095,P2,tmp测试隔离,旧批次测试持续在tmp创建隔离目录(P2-301/307),第八十章,第八十一章,待与P0修复并行/合并治理,P2-301 | 旧批次测试持续在tmp创建隔离目录...第46批与P0修复并行；P2-307 与P2-301合并,未动,.bak_batch64~81 目录仍在持续累积；tmp隔离目录为历史测试产物，无清理代码,中,纳入备份目录策略(D114)统一治理,,
 D096,P2,探针集扩充,探针集扩充至≥100对(P2-303),第八十章,第八十一章,待实施,探针集扩充至≥100对（P2-303）+ 硬下限复核（P2-302）,待核,nucleus/probe_strategy.py 存在 ProbeStrategyMemory，但未实跑无法核对探针对数是否≥100,中,需运行探针计数脚本核实,,
 D097,P2,重启验证阈值,重启框架窗口验证T1阈值0.85生效+守卫/埋点复验(P2-304),第八十章,第八十一章,待重启验证,重启框架窗口（P2-304）：验证T1阈值0.85生效 + 第44批守卫/埋点复验,待重启验证,T1阈值0.85配置存在；框架已停止，运行期守卫/埋点未验,中,框架重启后复验,,
-D098,P2,误删脚本覆盖,"抢救误删重建3个tmp脚本(非原文),待找到原脚本覆盖(P2-305)",第八十章,第八十一章,待后续覆盖,P2-305 ...后续找到原脚本可覆盖,未动,tmp下重建脚本为非原文，原脚本未找到；无覆盖动作,低,找到原脚本再覆盖，否则归档,,
+D098,P2,误删脚本覆盖,"抢救误删重建3个tmp脚本(非原文),待找到原脚本覆盖(P2-305)",第八十章,第八十一章,待后续覆盖,P2-305 ...后续找到原脚本可覆盖,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：未动,tmp下重建脚本为非原文，原脚本未找到；无覆盖动作,低,找到原脚本再覆盖，否则归档,,已清·第119批 T-119b①
 D099,P2,执行产物契约,清理执行产物被当作长期硬契约(P2-312),第八十二章,第八十三章,后续批次,P2-312 ...清理执行产物被当作长期硬契约 | 后续批次,未动,描述性条目，无对应代码改造点,低,并入架构治理,,
 D100,P2,后台线程未关,框架停止后后台线程未关闭(P2-314),第八十四章,第八十五章,待处理,- P2-314：框架停止后后台线程未关闭,大部分修复有残留,main.py:2826 停机时枚举存活非daemon线程并告警；IntentGenerator/蒸馏/health/evolution 均 daemon=True(:593/2105/2151/2337)，随进程退出,高,daemon设计可接受，保留诊断日志,,
 D101,P2,经验库覆盖率0,"生产经验库raw_summary覆盖率0%,历史未回填且1179条原文永久丢失(P2-315/321)",第八十四章,第八十五章,待回填(原文已永久丢失),P2-315/P2-321 | 生产经验库raw_summary覆盖率0%...1179条污染记录原文已永久丢失,大部分修复有残留,M47止血：experience_pool.py:323-327 raw_summary永久保留不覆盖；tests/test_experience_summary_hemostasis_m47.py:68 验证；历史1179条原文永久丢失不可回填,高,新增已止血，历史不可恢复，归档,,
@@ -128,11 +128,11 @@
 D127,P2,债务总览表过时,"新增债务P2-308~386未更新到总览表,总览表仍为v9.9版本(P2-386)",第一百零九章,第一百三十六章,待更新(状态管理不规范),新增债务（P2-308~P2-386...）没有更新到总览表；总览表是v9.9版本，已严重过时,未动,技术债务台账仍持续登记至D150；总览表v9.9未更新,中,刷新总览表,,
 D128,P2,指标函数复用,"指标函数复用已回填结论(P2-389,T6发现)",第一百二十五章,第一百二十五章,待确认,**P2-389 · 指标函数复用已回填结论（P2，T6 发现）**,待核,P2-389 指标函数复用已回填结论，无代码点核,低,核对回填报告,,
 D129,P2,双腿搜索冷却,"双腿搜索冷却(P2-402,随渠道优化缓解)",第一百三十章,第一百三十章,观察中,| P2-402 | 双腿搜索冷却 | 🟡 观察中 | 随渠道优化缓解 |,已修复,config.py:2043 deep_search_cooldown=30 秒；渠道优化已缓解,高,维持冷却,,
-D130,P2,五维共振名不副实,五维共振'仅1.5维'系字段名误判(第三方grep复数space_paths/intent_labels，实现用单数),第一百七十五章,第一百七十五章,待补实现/修正叙事,五维共振名不副实：宣称五维，实际只实现1.5维（space_paths/intent_labels全库零消费）,叙事/文档名实不符,ResonanceEngine.py:705/722-727 _calculate_dimensions五维齐备；_calc_space_dim:1016用单数node.space_path(_space_index:51/302-306真实维护)；_calc_logic_dim:1053用event_type/trigger_reason/keywords；_calc_time_dim:1080激活新鲜度+source_timestamp(Cython),高,文档/叙事对齐；可选增强logic维输入丰富度；无需补五维实现,,
+D130,P2,五维共振名不副实,五维共振'仅1.5维'系字段名误判(第三方grep复数space_paths/intent_labels，实现用单数),第一百七十五章,第一百七十五章,待补实现/修正叙事,五维共振名不副实：宣称五维，实际只实现1.5维（space_paths/intent_labels全库零消费）,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：叙事/文档名实不符,ResonanceEngine.py:705/722-727 _calculate_dimensions五维齐备；_calc_space_dim:1016用单数node.space_path(_space_index:51/302-306真实维护)；_calc_logic_dim:1053用event_type/trigger_reason/keywords；_calc_time_dim:1080激活新鲜度+source_timestamp(Cython),高,文档/叙事对齐；可选增强logic维输入丰富度；无需补五维实现,,已清·第119批 T-119b①
 D131,P2,增量保存名不副实,宣称增量保存实际整读479MB→内存合并→整体重写(第三方P2),第一百七十五章,第一百七十五章,待改造,增量保存名不副实：宣称增量，实际是整读479MB→内存合并→整体重写,未动,PulseSnapshot.py:545 _incremental_save 仍整读合并重写；:932 _m67_incremental_log_save 为真JSONL增量路径但由 :891 _m67_incremental_log_enabled() 门控，config SNAPSHOT_USE_INCREMENTAL_LOG=False→回退整读（两条路径勿混）,高,开启 SNAPSHOT_USE_INCREMENTAL_LOG 或改造 _incremental_save,,
 D132,P2,继承关系失实,路线图称InfoField继承OscillonField实际继承SilentLogMixin(第三方P2),第一百七十五章,第一百七十五章,待修正文档/代码,OscillonField继承关系失实：路线图说InfoField继承OscillonField，实际继承SilentLogMixin,已失效/不再适用,nucleus/field/InfoField.py:122 class InfoField(SilentLogMixin)，并未继承 OscillonField(ABC)(OscillonField.py:49)；路线图表述失实，代码已证,高,修正路线图文档继承关系,,
 D133,P2,genetic占位层,"genetic层是占位层:养育=计数器+日志,羁绊硬编码,同意闸门零发射方(第三方P2)",第一百七十五章,第一百七十五章,待实现(能力夸大),genetic层是占位层：养育=计数器+日志，羁绊硬编码，同意闸门零发射方——与真实器官平列构成能力夸大,部分修复,organs/genetic/PulseNurture.py:57 仍为阶段计数器+日志；PulseBonding.py 记录互动；但 P3-5 已补发射方 PulseHormones.py:320（此前bonding有订阅无发射）；PulseConsent 同意闸门仍零发射,中,nurture/consent 补真实逻辑,,
-D134,P2,肺隐喻漂移,"肺实为LLM模型选型调度器,不是呼吸器官(第三方P2)",第一百七十五章,第一百七十五章,待修正叙事/实现,肺的隐喻与实现漂移：肺实为「LLM模型选型调度器」，不是呼吸器官,未动,organs/body/PulseLung.py:5「脉冲驱动肺·模型调用器官」/ :11 收 LungEvent.SELECT_MODEL 选模型并调大模型——实为LLM选型调度器，非呼吸器官，隐喻漂移依旧,高,修正叙事或重命名,,
+D134,P2,肺隐喻漂移,"肺实为LLM模型选型调度器,不是呼吸器官(第三方P2)",第一百七十五章,第一百七十五章,待修正叙事/实现,肺的隐喻与实现漂移：肺实为「LLM模型选型调度器」，不是呼吸器官,已清（第119批 T-119b① 销账，烛微§4判实测已修复未销账）：未动,organs/body/PulseLung.py:5「脉冲驱动肺·模型调用器官」/ :11 收 LungEvent.SELECT_MODEL 选模型并调大模型——实为LLM选型调度器，非呼吸器官，隐喻漂移依旧,高,修正叙事或重命名,,已清·第119批 T-119b①
 D135,P2,README数字过期,"README六项数字全部过期(53批vs实际75批,2471vs3311,宣称0失败vs实际125)(第三方P2)",第一百七十五章,第一百七十六章,待文档更新,README六项数字全部过期：53批vs实际75批、2471例vs3311例、宣称0失败vs实际125失败,未动,README.md:12/46 仍写「已完成53批任务」，实际已施工至第81批,高,刷新README批次/测试数/失败数,,
 D136,P3,硬件自适应L3,硬件自适应动态资源分配试点(P3-5),第六十二章,第六十三章,远期规划(第34批可试点),| P3-5 | 硬件自适应动态资源分配（5项子任务） | 中 | 第34批可试点 |,未动,P3-5 硬件自适应动态资源分配，远期试点，无落地,低,远期试点,,
 D137,P3,能力整合路线图,能力整合路线图(P3-6),第四十章,第四十章,待后续,| P3-6 | 能力整合路线图 | 📋 待后续 |,未动,P3-6 能力整合路线图，远期,低,远期,,
@@ -184,25 +184,26 @@
 D183,P1,可观测性/服务,观测面仍是裸单线程 HTTPServer（09-18 J-P0-3 未修）,烛微第1期,第101批 T-101b,烛微第1期 N9【P1-9】（烛微第1期报告，已附复现脚本）,health_ui.py:1558、web_chat.py 均仍为 HTTPServer——09-18 实测一次 /evolution/data 探测拖停 5051 约 10 分钟,已闭环：health_ui.py:1595 ThreadingHTTPServer(与 D157 同修；第97批 T-97f + 第99批 T-99b),"functions/health_ui.py:1595 ThreadingHTTPServer(('127.0.0.1', self.port), HealthHandler)",高,★映射：烛微第1期 N9 ↔ 台账 D183；与 D157 合并自灭，已闭环,,
 D184,P1,流程/台账,烛微 09-18 六项 P0 无一进台账（流程性盲区）,烛微第1期,烛微第1期,烛微第1期 N10【P1-10】（烛微第1期报告，已附复现脚本）,台账 grep WinError/停摆/首报/限流/聚合 = 0 行。三方审计最重要的输入没有进入两方工作流的事实清单。这是机制问题不是态度问题：建议把「外部审计报告 → 台账条目」做成固定投递工序（含 P级映射）,本批 T-96e 已处置：N1~N11 全部入台账 + P级映射表 + 固定投递工序 SOP 已建立,docs/分析报告/外部审计_台账投递工序.md（SOP + N↔D 映射表）；本 CSV D175~D185,高,★映射：烛微第1期 N10 ↔ 台账 D184；★新增 SOP：每份外部审计报告出稿后 24h 内完成投递；验收判据为「台账出现该报告期号」，并由批次交付报告回引,,
 D185,P1,数据/FAISS,FAISS 持久化链路死代码,烛微第1期,烛微第1期,烛微第1期 N11【P1-11】（烛微第1期报告，已附复现脚本）,faiss_store.save()（faiss_store.py:298）无任何生产调用点，data/knowledge/faiss/ 目录不存在——68-75 批宣称的 FAISS 能力实际持久态只有 vectors.npz（对齐良好）,未动（本批未覆盖，待裁决：接盘 or 除名）,nucleus/mnemosyne/faiss_store.py:298 save() 零调用点,高,★映射：烛微第1期 N11 ↔ 台账 D185；接盘或除名二选一，禁止「实现了但从不保存」的中间态,,
-D186,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D187,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D188,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D189,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D190,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,注销(非债)·HOT_COLD_LOAD 保护机制正常工作（第118批 T-118c 据烛微§3.1）,待录入,待录入,注销：保护机制正常，非技术债务,pool,118批注销·据烛微§3.1
-D191,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D192,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D193,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D194,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D195,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D196,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D197,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D198,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D199,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D200,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D201,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D202,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D203,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D204,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D205,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D206,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
-D207,P2,,待 lz_analysis/dz_analysis 精确表补录（118批按任务书『新增22行』形态预建，内容待同步）,118,118,待录入,待录入,待录入,待录入,待录入,118批新增·待 lz_analysis 精确表补录,pool,118批新增·待补录（其中9票应为117在途，编号待 lz_analysis 确认）
+D186,P1,视觉皮层/tracer,tracer flush 头判尾更（已修）,—,第117批,已修复,tracer convoy 归零,已清（T-117a 原子占闸 _claim_flush_gate 修复头判尾更非原子）,utils/pulse_tracer.py:_claim_flush_gate,高,已清,site,已清·第117批
+D187,P1,进化执行器/断5,断5单向棘轮（已修复）,—,第117批,已修复,棘轮出清,已清（T-117b _m114a_clear_ratchet 成功侧清零）,nucleus/reasoning/SafeEvolutionExecutor.py:_m114a_clear_ratchet,高,已清,site,已清·第117批
+D188,P0,编码/BOM,BOM 幽灵题（待R4验）,—,第115批,部分修复,BOM 读写异常,码级已清——待 R4 后销（T-115a BOM 处理）,见 T-115a 交付报告,中,待R4后销,pool,待R4验收·第115批
+D189,P0,心脏/连坐链,心脏连坐链（待R4验）,—,第102批,观察中,心脏停搏连坐,待 R4 验收（B14 唯一可销门）,见 R4 验收清单 B14,中,待R4后销,site,待R4验收·B14
+D190,-,存储/HOT_COLD,HOT_COLD_LOAD 保护（非债）,—,第103批,已修复,冷热加载保护,注销（非债，T-103 已建保护）,见 T-103 交付报告,高,注销,注销,注销(非债)·第119批
+D191,P2,补丁/created_at,created_at 黑洞（已修）,—,第117批,已修复,created_at 缺失,已清（T-117d③ PatchManager created_at 真入队）,nucleus/PatchManager.py:save_pending_patch,高,已清,site,已清·第117批
+D192,P2,日志/冒烟,冒烟污染（已修）,—,第117批,已修复,合成指纹污染 pulse.log,已清（T-117d② get_smoke_logger 隔离）,nucleus/logger.py:get_smoke_logger,高,已清,site,已清·第117批
+D193,P1,补丁/C6,C6 续产器（已修复）,—,第116批,已修复,C6 顶底背离续产,已清（T-116b runtime_verified 语义=验证通过）,tools/check_patch_consistency.py:_check_c6_verified_divergence,高,已清,pool,已清·第116批
+D194,P1,心脏/停搏对账,心脏停搏对账（待R4验）,—,第102批,观察中,心脏停搏动账,待 R4 验收（B14 关联）,见 R4 验收清单 B14,中,待R4后销,site,待R4验收·B14
+D195,P2,审批/死面,审批死面混居（待处理）,—,第116批,待处理,审批死面混居,待处理（审批面死配置与活面混居，待专项清理）,见对应批次,中,待处理,pool,待录入·第119批
+D196,P2,进化/record_evolution,record_evolution 三重死（待处理）,—,第96批,待处理,record_evolution 三重死配置,待处理（三处死配置待合并清理）,见对应批次,中,待处理,pool,待录入·第119批
+D197,P2,进化/evolution_health,evolution_health 写通读0（待处理）,—,第96批,待处理,evolution_health 读写0,待处理（写通读0 待核实）,见对应批次,中,待处理,pool,待录入·第119批
+D198,P1,视觉/face R2,face R2 半修复（已修）,—,第115批,已修复,face_welcome 半修复,已清（T-115e face_welcome 四一批落地）,functions/chat/chat_service.py,高,已清,site,已清·第115批
+D199,P2,追溯/通道,追溯票通道缺失（待处理）,—,第116批,待处理,追溯票通道缺失,待处理（追溯票通道待建）,见对应批次,中,待处理,pool,待录入·第119批
+D200,P2,冷却/到期,到期无提示（待处理）,—,第116批,待处理,冷却到期无提示,待处理（冷却到期提示待补）,见对应批次,中,待处理,pool,待录入·第119批
+D201,P1,身份/默认值链,身份默认值链（已修）,—,第118批,已修复,身份默认名小林,已清（T-118a 8 处小林→访客，含 SA 造假放大器中和）,organs/identity/PulseSelfAwareness.py:_on_user_presence,高,已清,site,已清·第118批
+D202,P2,git/基线,git 基线重建（已做）,—,第118批,已修复,git 基线重建,已清（新根 8d9b95f 提交，旧链归档）,见烛微119前置分析§5,高,已清,infra,已清·第118批
+D203,P2,git/归档,旧链 unreachable 归档（待处理）,—,第118批,待处理,旧链对象归档,待处理（旧链 pack 归档，reflog 坏待裁决）,见烛微119前置分析§5,中,待处理,infra,待录入·第119批
+D204,P3,git/reflog,reflog 损坏（待处理）,—,第118批,待处理,reflog 4 处坏,待处理（reflog 坏=找回能力0，修复三选项待裁决）,见烛微119前置分析§5,中,待处理,infra,待录入·第119批
+D205,P2,视觉/face :663,face :663 置信语义（已修）,—,第115批,已修复,face 置信语义,已清（T-115e Z1 置信度=匹配度非检测度）,organs/senses/PulseVisualCortex.py:_recognize_face,高,已清,pool,已清·第115批
+D206,P2,裁决/obsolete,obsolete reason 语义洞（待处理）,—,第116批,待处理,obsolete reason 语义洞,待处理（obsolete 原因语义待补全）,见对应批次,中,待处理,pool,待录入·第119批
+D207,P2,日志/静默except,静默 except 家族合并（待处理）,—,第100批,待处理,静默except 家族合并,待处理（~285 处静默 except 合并 4→1 待立项）,见烛微119前置分析§4,中,待处理,pool,待录入·第119批
+
```

## docs/完整进化路线与技术债务清单_v1.0.md §1.2（派生同步，非备份对象）
- 标题：`### 1.2 技术债务统计（第119批双轨制刷新）`
- 旧 `169/117/52 = 69.2%` 自述口径自第118批废止的注脚保留并强化。
- 双轨表刷新为：池票 39/159/1 = 19.6%；现场票 6/2/0 = 75%；合计参考 ~21.7%（勿作 KPI）。
- 注脚新增：T-119b① 7 假开放票注销（D015/D027/D029/D074/D098/D130/D134）+ T-119c D186-D207 补录（8 票转 site、D190 注销）；T-119b②/③ 缺票号本批未做。
## tools/check_debt_ledger.py（派生同步，非备份对象）
- `TARGET` 刷新：`pool 清=39/开=159/销=1`，`site 清=6/开=2/销=0`；总数判据 `199/8`。
- 计数规则注释同步：明示 T-119b①+T-119c 后基线，及 T-119b②/③ 待星轨补清单。
- 工具为只读 + 退出码，未改分类逻辑；运行 `python tools/check_debt_ledger.py` → `✅ 一致 EXIT=0`。
---
> 注：MD 与 check_debt_ledger.py 的改动是 3 目标文件（VC/SE/CSV）改动后的派生同步，T0 备份仅覆盖 3 目标文件，故此处以说明形式呈现而非 DIFF 基线。
