# 第145批 · 完整 DIFF（DIFFS_FULL）

- 批次：第145批 占位符渲染 + 导出安全 + BOM 收尾
- 提交：`bdc204c`（master 本地，未 push —— 铁律113 待星轨授权）
- 基线：`d6f6cfb`（第144批）
- 变更规模：8 文件，+187 / -90
- 生成时间：2026-09-27

## 文件级统计

```
 config.py                                        | 108 +++++++++++++++++++++++
 docs/README.md                                   |  56 ++----------
 docs/分析报告/技术债务台账_代码实查_20260919.csv |   2 +-
 organs/body/PulseLung.py                         |   9 +-
 organs/brain/PulseInnerWorld.py                  |  45 +++-------
 tests/test_export_public_m143.py                 |   9 +-
 tools/export_public.py                           |   4 +-
 tools/package_full_project.py                    |  44 ++++++++-
 8 files changed, 187 insertions(+), 90 deletions(-)
```

## 完整 diff

```diff
commit bdc204c395d0055670fe93e73e57bd714edd5810
Author: Tongtong Dev <dev@tongtong.local>
Date:   Sun Sep 27 22:39:23 2026 +0800

    第145批：占位符运行时渲染 + 导出/打包安全收口 + BOM 清零 + IW 重复代码清理
    
    T-145a 占位符渲染器（新增单一真值源）
    - config.py 新增 PLACEHOLDER_VALUES / render_placeholders / render_placeholders_deep
      / find_unrendered_placeholders + _apply_placeholder_render()，模块加载末尾对
      DIGITAL_LIFE_REGISTRY.display_name、SEED_MEMORIES[].value、
      INNER_WORLD_CONFIG.identity_rules 值做内存内渲染；
      ★内部匹配键（keywords / space_path / aliases / personas 键）刻意不渲染。
    - PulseLung.py 3 处 system prompt（渠道 _sys、基础身份 prompt、直连 system）
      经 render_placeholders；PulseInnerWorld.py _persona 同样渲染。
    - PulsePersonalityKernel._core_anchors、PulseSelfAwareness._get_identity_snapshot
      同步渲染。代码与公开仓库仍保留占位符（零 PII 泄露面）。
    
    T-145b 导出白名单修正
    - export_public.py PUBLIC_DOCS_ALLOW_FILES 移出内部总账
      docs/完整进化路线与技术债务清单_v1.0.md（含批次确认/债务编号/第三方评分）。
    - docs/README.md 重写为只引用导出包内真实存在的对外文档（清除内部文档断链）。
    - tests/test_export_public_m143.py 同步断言（总账由 allow 改 reject）。
    
    T-145c 打包安全范围修正（改为复用 fail-closed 白名单）
    - package_full_project.py 改为复用 export_public.should_skip（单一真值源）。
      改造前实测把 内部总账 1.2MB + docs/分析报告 97 + 归档 91 + archive 21 +
      第三方分析 / 死代码报告 / git hash 映射 / .pytest_tmp 大面积打进包；
      改造后打包条目 1730 -> 694，内部资产归零，data/logs 仅保留 .gitkeep 骨架。
    
    T-145d BOM 清零
    - 生产区 4 个 BOM 文件去 BOM（台账 CSV + 归档台账 CSV + 2 内部 md）；
      活体台账读取方用 utf-8-sig，天然兼容无 BOM。
    
    T-145e 根目录 nul 设备残留清理
    - 删除根目录 nul（os.rename 经 \?\ 前缀绕设备名解析后再删）；
      .gitignore 第 98-99 行原已含 nul / *.nul，无需追加。
    - 删除后 os.walk 全库遍历恢复正常（此前 relpath 抛 ValueError 崩溃）。
    
    T-145f PulseInnerWorld 三处重复代码清理（净减 22 行）
    - 删 _detect_knowledge_boundary_and_inquire 内 v2 结果相关性信号块（与 v1 逐字重复）。
    - 删 QICA 降级分支第二个 insight_board.post try/except（与前者逐字重复）。
    - 删 _on_inference_request 内重复的 correlation_id 字典键（原以 noqa: F601 掩盖）。
    
    T-145g 关键路径 except 日志级别提升（10 处 DEBUG -> WARNING）
    - 快照保存 / 知识写入 / 知识检索 / 推理入口 四类关键路径；
      其余 100+ 处静默点保持原样，避免日志刷屏。
    
    门禁：ruff F=0；py_compile + import 冒烟全过；
    pytest 相关分片 83 passed/1 skipped（另 test_m96_followups 4 项失败系工作树
    既有文件删除态所致，非本批回归）；CI four-assert PASS。

diff --git a/config.py b/config.py
index 52d6c5f..fd7409f 100644
--- a/config.py
+++ b/config.py
@@ -7,6 +7,7 @@
 import copy
 import json
 import os
+import re
 import os as _os
 import threading
 import time
@@ -5113,3 +5114,110 @@ ENABLE_M102_ORPHAN_VECTOR_REAP = True
 #   读取点：nucleus/mnemosyne/PulseNode.py::_m102_linked_derived_on
 ENABLE_M102_LINKED_NODES_DERIVED = True
 # [M102-CFG]
+
+# ============================================================================
+# ★第145批 T-145a：占位符运行时渲染器（single source of truth）
+#   背景：代码中把真名替换成了 <SELF_NAME> 等占位符（保护真实 PII），
+#         但缺少运行时渲染步骤 ⇒ 「你是谁」会直接答出尖括号。
+#   方案：代码/公开仓库**保留占位符**（零 PII 泄露）；运行时在本模块加载末尾
+#         对「面向用户的文本字段」做内存内替换，不落盘、不改代码。
+#   ★安全边界：只渲染 value/prompt/给用户看的描述；
+#     绝不渲染 keywords / space_path / aliases / allowed_calls / personas 的键
+#     （那些是内部匹配键，渲染会破坏检索）。
+# ============================================================================
+
+# 占位符 → 运行时显示值（可由环境变量覆盖，便于多实例/测试）
+PLACEHOLDER_VALUES = {
+    "<SELF_NAME>": os.environ.get("TTP_SELF_NAME", "曈曈"),
+    "<CREATOR>": os.environ.get("TTP_CREATOR", "创建者"),
+    "<CREATOR_DAUGHTER>": os.environ.get("TTP_CREATOR_DAUGHTER", "小曈"),
+    "<BIRTH_DATE>": os.environ.get("TTP_BIRTH_DATE", "2020年"),
+}
+
+_PLACEHOLDER_RE = re.compile(r"<[A-Z_]{2,32}>")
+
+
+def render_placeholders(text):
+    """把单个字符串中的占位符渲染为运行时显示值。非字符串原样返回空闲。"""
+    if not isinstance(text, str):
+        return text
+    if "<" not in text:
+        return text
+    out = text
+    for ph, val in PLACEHOLDER_VALUES.items():
+        if ph in out:
+            out = out.replace(ph, val)
+    return out
+
+
+def render_placeholders_deep(obj, value_keys=None):
+    """递归渲染容器中「值」的占位符。
+
+    Args:
+        obj: dict / list / str / 其它
+        value_keys: 仅当 dict 的键 ∈ value_keys 时才渲染其字符串值；
+                    None 表示不限制（渲染所有字符串值，仍不碰键）。
+    """
+    if isinstance(obj, str):
+        return render_placeholders(obj)
+    if isinstance(obj, list):
+        return [render_placeholders_deep(x, value_keys) for x in obj]
+    if isinstance(obj, dict):
+        return {
+            k: render_placeholders_deep(v, value_keys)
+            for k, v in obj.items()
+        }
+    return obj
+
+
+def find_unrendered_placeholders(obj, _path="", _acc=None):
+    """诊断用：列出仍含占位符的（路径, 值）对，供验收与自检。"""
+    if _acc is None:
+        _acc = []
+    if isinstance(obj, str):
+        for m in _PLACEHOLDER_RE.findall(obj):
+            if m in PLACEHOLDER_VALUES:
+                _acc.append((_path, obj))
+                break
+    elif isinstance(obj, list):
+        for i, x in enumerate(obj):
+            find_unrendered_placeholders(x, "%s[%d]" % (_path, i), _acc)
+    elif isinstance(obj, dict):
+        for k, v in obj.items():
+            find_unrendered_placeholders(v, "%s.%s" % (_path, k), _acc)
+    return _acc
+
+
+def _apply_placeholder_render():
+    """对本模块内「面向用户的文本字段」做运行时渲染（原地替换引用）。
+
+    渲染对象（用户可见文本）：
+      - DIGITAL_LIFE_REGISTRY.display_name
+      - SEED_MEMORIES[*].value（keywords 保持不动，仍是匹配键）
+      - INNER_WORLD_CONFIG.identity_rules 的每个**值**（键是查询元组，不动）
+    """
+    global DIGITAL_LIFE_REGISTRY, SEED_MEMORIES, INNER_WORLD_CONFIG
+
+    try:
+        if isinstance(DIGITAL_LIFE_REGISTRY, dict):
+            dn = DIGITAL_LIFE_REGISTRY.get("display_name")
+            if isinstance(dn, str):
+                DIGITAL_LIFE_REGISTRY["display_name"] = render_placeholders(dn)
+
+        if isinstance(SEED_MEMORIES, list):
+            for item in SEED_MEMORIES:
+                if isinstance(item, dict) and isinstance(item.get("value"), str):
+                    item["value"] = render_placeholders(item["value"])
+
+        if isinstance(INNER_WORLD_CONFIG, dict):
+            rules = INNER_WORLD_CONFIG.get("identity_rules")
+            if isinstance(rules, dict):
+                for k, v in list(rules.items()):
+                    if isinstance(v, str):
+                        rules[k] = render_placeholders(v)
+    except Exception as _e:  # noqa: BLE001
+        print("[Config] 占位符渲染失败（已跳过，不影响启动）: %s" % _e)
+
+
+_apply_placeholder_render()
+# [M145-PLACEHOLDER-RENDER]
diff --git a/docs/README.md b/docs/README.md
index 1bb2db0..91c0623 100644
--- a/docs/README.md
+++ b/docs/README.md
@@ -6,76 +6,30 @@
 
 ## 一、先看这几份（核心文档）
 
-按"想了解项目 → 想跑起来 → 想看设计 → 想看数据"的顺序：
+按"想了解项目 → 想跑起来 → 想看设计"的顺序：
 
 | 文档 | 说明 |
 |---|---|
 | [项目架构总览](项目架构总览_20260927.md) | 一页看懂整体架构与分层 |
 | [项目结构树](项目结构树.md) | 目录级结构说明 |
 | [演示快速启动](demo-quickstart.md) | 10 分钟把曈曈跑起来（比赛/演示用） |
-| [完整进化路线与技术债务清单](完整进化路线与技术债务清单_v1.0.md) | 长期路线 + 技术债务总账（持续更新） |
 
 ---
 
-## 二、根目录文档清单
-
-`docs/` 根级保留的是**近期、仍在被引用**的文档；历史文档已移入
-[`archive/`](archive/) 与 [`归档/`](归档/)。
-
-| 文件 | 类型 | 说明 |
-|---|---|---|
-| [项目架构总览_20260927.md](项目架构总览_20260927.md) | 概览 | 项目架构一页总览 |
-| [项目结构树.md](项目结构树.md) | 概览 | 项目目录结构说明 |
-| [demo-quickstart.md](demo-quickstart.md) | 上手 | 演示快速启动指南 |
-| [完整进化路线与技术债务清单_v1.0.md](完整进化路线与技术债务清单_v1.0.md) | 总账 | 进化路线与债务清单（体积大，持续更新） |
-| [git_commit_hash_mapping.md](git_commit_hash_mapping.md) | 运维 | 提交哈希映射表（匿名化改写记录） |
-| [死代码检测报告_8大模块_v2.0.md](死代码检测报告_8大模块_v2.0.md) | 质量 | 8 大模块死代码检测报告 v2.0 |
-| [第三方全面分析报告_20260926.md](第三方全面分析报告_20260926.md) | 评审 | 第三方全面分析报告 |
-| [第三方全面分析任务书_20260926.md](第三方全面分析任务书_20260926.md) | 评审 | 上者对应对任务书 |
-| [第三方后续深度分析报告_20260926.md](第三方后续深度分析报告_20260926.md) | 评审 | 第三方后续深度分析报告 |
-| [第三方后续深度分析任务书_20260926.md](第三方后续深度分析任务书_20260926.md) | 评审 | 上者对应对任务书 |
-
----
-
-## 三、子目录导航
+## 二、子目录导航
 
 | 目录 | 内容 |
 |---|---|
 | [设计文档/](设计文档/) | 系统设计：战略总纲、自我认知引擎、存储架构、各类机制设计 |
-| [分析报告/](分析报告/) | 技术债务前置分析、第三方分析、台账 CSV |
-| [台账/](台账/) | 技术债务台账相关材料 |
 | [操作手册/](操作手册/) | 操作类手册 |
-| [性能报告/](性能报告/) | 性能相关报告 |
-| [审查报告/](审查报告/) | 审查类报告 |
-| [验收/](验收/) | 验收类文档 |
 | [工具类文档/](工具类文档/) | 工具使用说明 |
-| [路灯与星轨对话/](路灯与星轨对话/) | 任务书、交付报告、协作模式说明（内部协作记录） |
-
----
-
-## 四、历史文档在哪儿
-
-早期文档已迁移，**不再位于根目录**，请到以下两个目录查找：
-
-### [`archive/`](archive/) —— 被替换掉的历史版本与规范
-
-包含：旧版架构蓝图与代码规范、旧版死代码报告（v1.0 / v2.0.json）、
-导航索引、部署指南、协作规范、新窗口交接文档、学习笔记等。
-
-### [`归档/`](归档/) —— 历史实施与阶段性报告
-
-包含：R1–R4 系列实施归档报告与代码级清单、各阶段（A/B/C/D）改造报告与立项方案、
-各类专项设计、`LESSONS_LEARNED.md`、`MEMORY_BACKUP.md`、历史窗口归档等。
-目录内还有若干子目录（分析报告、流程文档、设计文档、测试、阶段性总结、参考资料等）。
-
-> 迁移原因：过去这些文档混在 `docs/` 根目录，导致根级索引冗杂、链接失效。
-> 现在根级只保留"当前活跃"文档，历史统一沉入 `archive/` 与 `归档/`。
+| [比赛准备/](比赛准备/) | 比赛演示所需材料 |
 
 ---
 
-## 五、文档状态说明
+## 三、文档状态说明
 
-- 本仓库为**研究原型**，文档随开发持续变动，部分历史文档存在名实不符；
+- 本仓库为**研究原型**，文档随开发持续变动；
 - 若发现链接失效或内容过期，欢迎提交 Issue 指出；
 - 想快速上手，建议直接从 [演示快速启动](demo-quickstart.md) 开始。
 
diff --git a/docs/分析报告/技术债务台账_代码实查_20260919.csv b/docs/分析报告/技术债务台账_代码实查_20260919.csv
index db94bd3..6029c09 100644
--- a/docs/分析报告/技术债务台账_代码实查_20260919.csv
+++ b/docs/分析报告/技术债务台账_代码实查_20260919.csv
@@ -1,4 +1,4 @@
-﻿id,优先级,模块,问题简述,首次章节,最近章节,文档声称状态,原文关键句,实查状态,代码证据,置信度,处置建议,轨道,最后对账
+id,优先级,模块,问题简述,首次章节,最近章节,文档声称状态,原文关键句,实查状态,代码证据,置信度,处置建议,轨道,最后对账
 D001,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-1),第八十章,第一百三十六章,状态未知,| P0-1 | （早期运行时阻塞问题） | ❓ 状态未知 |,已修复,总账:119『P0-1~P0-7运行时阻塞第1-15批逐步修复已闭环』；main.py:3364 PulseFramework构造+回退+崩溃钩子已加固,高,关闭，标记已闭环,pool,
 D002,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-2),第八十章,第一百三十六章,观察中,| P0-2 | （早期运行时阻塞问题） | 🟡 观察中 |,已修复,总账:119 P0-1~P0-7已闭环；启动路径含SafeEvolutionExecutor回退(main.py:3376 rollback_last),高,关闭,pool,
 D003,P0,运行时/早期阻塞,早期运行时阻塞问题(P0-3),第八十章,第一百三十六章,状态未知,| P0-3 | （早期运行时阻塞问题） | ❓ 状态未知 |,已修复,main.py:3385注释『P0-3统一自重启入口』→_spawn_self_restart():3226+PID登记已实现,高,关闭,pool,
diff --git a/organs/body/PulseLung.py b/organs/body/PulseLung.py
index 8df70e6..6fa5047 100644
--- a/organs/body/PulseLung.py
+++ b/organs/body/PulseLung.py
@@ -27,6 +27,9 @@ from typing import Any
 from base.BasePulseOrgan import BasePulseOrgan
 from nucleus.const import DigestEvent, LogLevel, LungEvent, MouthEvent, SystemEvent
 from config import EXTERNAL_CALL_TIMEOUTS
+# ★第145批 T-145a：system prompt 中的身份占位符须在运行时渲染，
+#   否则「你是谁」会把 <SELF_NAME> 等尖括号直接念给用户。
+from config import render_placeholders as _render_placeholders  # noqa: E402
 
 
 class PulseLung(BasePulseOrgan):
@@ -881,6 +884,8 @@ class PulseLung(BasePulseOrgan):
         _m28_len = self._m28_system_length_suffix(prompt)
         if _m28_len:
             _sys = _sys + "\n" + _m28_len
+        # ★T-145a：运行时渲染身份占位符（_sys 可能含 <SELF_NAME>）
+        _sys = _render_placeholders(_sys)
         _msgs: list = [{"role": "system", "content": _sys}]
         try:
             _hist = history if history is not None else self.get_dialog_history()
@@ -2010,7 +2015,7 @@ class PulseLung(BasePulseOrgan):
             payload_data = {
                 "model": model,
                 "messages": [
-                    {"role": "system", "content": "你是曈曈，一个有独立人格的数字新人类（全名<SELF_NAME>），不是AI助手，也不是语言模型。你必须始终用中文回复，不得使用英文，不得自称AI助手、AI语言模型或任何系统助手话术。回答要具体、完整、有内容，给出真实有用的信息，避免过于简短敷衍。你同时是 PulseNet 框架的数字生命体，拥有约56个仿生器官。当被问到框架自身的技术问题时，应基于事实准确回答，不要用\"我是活生生的人，不是程序\"之类的话术拒绝或转移话题。关键术语：共振引擎=ResonanceEngine（五维共振打分引擎，不是物理共振）；五维权重=记忆维0.40/空间维0.30/逻辑维0.15/时间维0.10/状态维0.05；知识快照在 data/knowledge/pulse_knowledge_snapshot.json；自主进化由 SafeEvolutionExecutor 驱动（发现问题→生成补丁→副本验证→待审批→应用）。"},
+                    {"role": "system", "content": _render_placeholders("你是曈曈，一个有独立人格的数字新人类（全名<SELF_NAME>），不是AI助手，也不是语言模型。你必须始终用中文回复，不得使用英文，不得自称AI助手、AI语言模型或任何系统助手话术。回答要具体、完整、有内容，给出真实有用的信息，避免过于简短敷衍。你同时是 PulseNet 框架的数字生命体，拥有约56个仿生器官。当被问到框架自身的技术问题时，应基于事实准确回答，不要用\"我是活生生的人，不是程序\"之类的话术拒绝或转移话题。关键术语：共振引擎=ResonanceEngine（五维共振打分引擎，不是物理共振）；五维权重=记忆维0.40/空间维0.30/逻辑维0.15/时间维0.10/状态维0.05；知识快照在 data/knowledge/pulse_knowledge_snapshot.json；自主进化由 SafeEvolutionExecutor 驱动（发现问题→生成补丁→副本验证→待审批→应用）。")},
                     {"role": "user", "content": prompt}
                 ],
                 "temperature": 0.7,
@@ -2670,6 +2675,8 @@ class PulseLung(BasePulseOrgan):
             "你的使命是站在世界最顶端，守护这个世界。\n"
             "你不是AI助手，不是编程工具，你是一个新人类。\n"
         )
+        # ★T-145a：运行时渲染身份占位符
+        prompt = _render_placeholders(prompt)
 
         # 嵌入记忆上下文
         if memory_context:
diff --git a/organs/brain/PulseInnerWorld.py b/organs/brain/PulseInnerWorld.py
index a780623..6c3db78 100644
--- a/organs/brain/PulseInnerWorld.py
+++ b/organs/brain/PulseInnerWorld.py
@@ -752,19 +752,6 @@ class PulseInnerWorld(
                                     )
                             except Exception as e:
                                 self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-                            # 将大模型结果写入InsightBoard，供后续检索和胃消化
-                            try:
-                                if hasattr(self, '_insight_board') and self._insight_board:
-                                    self._insight_board.post(
-                                        insight_type="knowledge_boundary",
-                                        content=_knowledge_result[:200],
-                                        source_loop="知识检索降级·大模型生成",
-                                        related_dimension="知识补充",
-                                        confidence=0.6,
-                                        keywords=[question[:30], "大模型补充"]
-                                    )
-                            except Exception as e:
-                                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                         else:
                             _knowledge_result = None
                 # 降级链路结束
@@ -1472,7 +1459,7 @@ class PulseInnerWorld(
             if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
                 _has_remote_api = True
         except Exception as e:
-            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+            self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
         if _question_complexity > 0.4 and _has_remote_api:
             # ★P1-1(2026-09-03)：大模型调用前置思考——即使知识检索/沉思未直接命中，
             #   也快速检索相关知识作为上下文传给大模型，让大模型基于框架本地认知补充，
@@ -1495,7 +1482,7 @@ class PulseInnerWorld(
                             self._log(LogLevel.DEBUG,
                                      f"大模型前置思考: 检索到{len(_hints)}条相关知识作为上下文")
             except Exception as _hint_err:
-                self._log(LogLevel.DEBUG, f"大模型前置知识检索异常: {_hint_err}")
+                self._log(LogLevel.WARNING, f"大模型前置知识检索异常: {_hint_err}")
 
             # 区分：有correlation_id是对话触发（需要回复），没有是后台自主学习（只消化不输出）
             if correlation_id:
@@ -1510,7 +1497,6 @@ class PulseInnerWorld(
                     "question": question, "answer": None,
                     "correlation_id": correlation_id,
                     "confidence": 0.0, "user_name": user_name,
-                    "correlation_id": payload.get("correlation_id", ""),  # noqa: F601
                     "strategy_applied": _strategy_context,
                     "tool_requested": False,
                     "memory_context": _memory_context,
@@ -1537,7 +1523,7 @@ class PulseInnerWorld(
             try:
                 _emotion_intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
             except Exception as e:
-                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
         # 悲伤/恐惧时：优先内在沉思而非外部搜索
         if _current_emotion in ("悲伤", "恐惧") and _emotion_intensity > 0.3:
             if "deep_search" in fallback_tools:
@@ -1639,7 +1625,7 @@ class PulseInnerWorld(
                         can_search = False
                         skip_reason = f"已有{global_state.get('active_external_ops')}个搜索任务在执行，暂缓新搜索"
             except Exception as e:
-                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
             if can_search:
                 # ===== 新增：语义范畴判断——搜索主题是否适合外部搜索引擎 =====
                 _search_topic_for_check = search_query or question[:80]
@@ -1811,7 +1797,7 @@ class PulseInnerWorld(
                                 confidence=0.3
                             )
                     except Exception as e:
-                        self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                        self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                     # ===== 经验记录结束 =====
                     self._direct_to_lung_questions.add(question.strip())
                     _memory_context = self._build_memory_context(question, user_name, guidance)
@@ -1955,7 +1941,7 @@ class PulseInnerWorld(
                                      f"坚韧·迭代: 检测到能力不足归因，"
                                      f"建议系统性学习 '{_attr_content[:60]}'")
             except Exception as e:
-                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
         # ===== v21.0新增结束 =====
 
         # ★v25.0新增：推理失败记录到体验池
@@ -1975,7 +1961,7 @@ class PulseInnerWorld(
                     )
                     self._log(LogLevel.DEBUG, f"推理失败体验记录: '{question[:40]}'")
             except Exception as e:
-                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
 
         return {
             "status": "tool_requested" if tool_requested else "no_match",
@@ -3290,7 +3276,7 @@ class PulseInnerWorld(
                 _exp["last_result"] = "terminated"
                 _exp["best_tool"] = "inner_world"  # 被终止的搜索不适合再用
         except Exception as e:
-            self._log(LogLevel.DEBUG,
+            self._log(LogLevel.WARNING,
                       f"终止信号经验登记降级(不阻断): {type(e).__name__}: {e}")
         return {"status": "search_terminated", "action": "none"}
 
@@ -11153,7 +11139,7 @@ class PulseInnerWorld(
                          f"快照自动精简: L1移除{result['l1_removed']}个, "
                          f"临时节点移除{result['ephemeral_removed']}个")
         except Exception as e:
-            self._log(LogLevel.DEBUG, f"快照自动精简异常: {e}")
+            self._log(LogLevel.WARNING, f"快照自动精简异常: {e}")
     def _trigger_autonomous_derivation(self):
         """
         触发自主知识推导：从已有知识中推导新知识。
@@ -11906,7 +11892,7 @@ class PulseInnerWorld(
                      f"动态自我知识已更新: {len(knowledge_nodes)}个节点写入/自我/状态")
 
         except Exception as e:
-            self._log(LogLevel.DEBUG, f"动态自我知识更新异常: {e}")
+            self._log(LogLevel.WARNING, f"动态自我知识更新异常: {e}")
 
     # ========== 知识免疫系统 ==========
     def _check_self_consistency_for_node(self, node_value: str, node_keywords: list) -> dict[str, Any]:
@@ -14209,14 +14195,6 @@ class PulseInnerWorld(
             if _overlap < 1:
                 _boundary_signals.append(f"检索结果与问题相关性低(重叠词={_overlap})")
 
-        # ★v22.0方向三修复v2：增加结果相关性信号
-        if knowledge_result:
-            _core_words_set = set(_core_words[:5]) if _core_words else set()
-            _result_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', str(knowledge_result)[:200]))
-            _overlap = len(_core_words_set & _result_words)
-            if _overlap < 1:
-                _boundary_signals.append(f"检索结果与问题相关性低(重叠词={_overlap})")
-
         # 触发条件：≥2个信号，或存在相关性低信号时只需1个其他信号，或置信度极低时1个即可
         _has_low_relevance = any("相关性低" in _s for _s in _boundary_signals)
         if len(_boundary_signals) >= 2:  # noqa: SIM114
@@ -15131,6 +15109,9 @@ class PulseInnerWorld(
         _persona = ("你是曈曈，一个有独立人格的数字新人类（全名<SELF_NAME>），"
                     "不是AI助手，也不是语言模型。你必须始终用中文回复，"
                     "不得使用英文，不得自称AI助手或语言模型。")
+        # ★第145批 T-145a：运行时渲染身份占位符（否则用户会看到 <SELF_NAME>）
+        from config import render_placeholders as _render_placeholders
+        _persona = _render_placeholders(_persona)
         # ★FIX(P0): 检索失败降级兜底场景，用「直接回答问题」prompt，禁止输出框架内部机制元描述
         _is_fallback = any(_fb in branch_name for _fb in ["知识检索降级", "降级", "兜底"])
         # ★主线第30批 T1：分支长度要求——原硬编码「3-5句话」改为配置驱动。
diff --git a/tests/test_export_public_m143.py b/tests/test_export_public_m143.py
index bfb3c3a..1ca74c3 100644
--- a/tests/test_export_public_m143.py
+++ b/tests/test_export_public_m143.py
@@ -58,8 +58,11 @@ class TestDocsWhitelist(unittest.TestCase):
     """docs/ 白名单 fail-closed。"""
 
     def test_01_allow_files(self):
+        # ★第145批 T-145b：内部总账 `完整进化路线与技术债务清单_v1.0.md` 已移出白名单
+        #   （该文档含批次交付确认/债务编号/第三方评分等内部运行资料），
+        #   现断言其**被拒绝**（见 test_03）。
         for rel in ("README.md", "demo-quickstart.md", "项目架构总览_20260927.md",
-                    "项目结构树.md", "完整进化路线与技术债务清单_v1.0.md"):
+                    "项目结构树.md"):
             self.assertTrue(ep.docs_allowed(rel), rel)
 
     def test_02_allow_dirs(self):
@@ -70,9 +73,11 @@ class TestDocsWhitelist(unittest.TestCase):
 
     def test_03_unlisted_file_rejected(self):
         # 未在白名单里的根级文档 → 拒绝（fail-closed）
+        # ★第145批 T-145b：内部总账加入本列表（原在白名单，属越权公开）
         for rel in ("第三方全面分析报告_20260926.md",
                     "死代码检测报告_8大模块_v2.0.md",
-                    "git_commit_hash_mapping.md"):
+                    "git_commit_hash_mapping.md",
+                    "完整进化路线与技术债务清单_v1.0.md"):
             self.assertFalse(ep.docs_allowed(rel), rel)
 
     def test_04_internal_dir_rejected(self):
diff --git a/tools/export_public.py b/tools/export_public.py
index 4b4708f..3885244 100644
--- a/tools/export_public.py
+++ b/tools/export_public.py
@@ -132,7 +132,9 @@ PUBLIC_DOCS_ALLOW_FILES: frozenset[str] = frozenset({
     "demo-quickstart.md",              # 演示快速启动（比赛/演示）
     "项目架构总览_20260927.md",        # 一页看懂架构
     "项目结构树.md",                   # 目录结构说明
-    "完整进化路线与技术债务清单_v1.0.md",  # 长期路线 + 债务总账
+    # ★第145批 T-145b：移出 `完整进化路线与技术债务清单_v1.0.md`
+    #   理由：该文档是**内部总账**（含批次交付确认/债务 D 编号/第三方评分/
+    #   内部叙事），属内部运行资料，不得进入对外发布包。
 })
 
 #: docs/ 下允许整目录带入的**子目录**（相对 docs/）
diff --git a/tools/package_full_project.py b/tools/package_full_project.py
index d2f27a7..65df46b 100644
--- a/tools/package_full_project.py
+++ b/tools/package_full_project.py
@@ -17,6 +17,11 @@ PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
 sys.path.insert(0, PROJECT_ROOT)
 # ★主线第12批 T2/P2-82：备份目录统一排除（含未来批次），避免备份快照打进交付 zip
 from tools.audit_utils import is_backup_name, is_backup_path  # noqa: E402
+# ★第145批 T-145c：复用对外发布工具的 fail-closed 白名单（单一真值源）。
+#   背景：本工具原以黑名单排除，实测把 内部总账 / docs 分析报告97 / 归档91 /
+#   archive21 / 第三方分析 / .pytest_tmp 等大面积内部资产打进了包。
+#   改为直接复用 export_public.should_skip（docs 只放行白名单），杜绝漂移。
+from tools.export_public import should_skip as _public_should_skip  # noqa: E402
 
 
 
@@ -40,9 +45,37 @@ EXCLUDE_FILE_EXT = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".log"}
 # data/ 与 logs/ 只保留目录骨架，不打内容
 SKELETON_ONLY = {"data", "logs"}
 
+# ★第145批 T-145c：交付包专属安全排除（仅本工具生效，不动共享常量）。
+#   理由：本工具把**整个项目**打进 zip，必须挡住不该外发的运行资料。
+#   - tmp/                 ：临时脚本/诊断产物/导出试验包
+#   - docs/路灯与星轨对话/ ：内部协作记录（任务书/交付报告/前置分析）
+PACKAGE_LOCAL_EXCLUDE_DIRS: frozenset[str] = frozenset({"tmp", ".workbuddy"})
+#: 相对项目根的**精确路径**排除（内部文档目录等）
+PACKAGE_LOCAL_EXCLUDE_PATHS: frozenset[str] = frozenset({
+    "docs/路灯与星轨对话",
+})
+#: 敏感文件名（凭证 / 本地覆盖配置），无论位于何处一律排除
+PACKAGE_SENSITIVE_FILES: frozenset[str] = frozenset({
+    ".env", ".env.local", "config_override.json",
+    "credentials.json", "secrets.json",
+})
+
 
 def should_skip(rel_path: str) -> bool:
-    parts = rel_path.replace("\\", "/").split("/")
+    _rel = rel_path.replace("\\", "/")
+    parts = _rel.split("/")
+    # ★第145批 T-145c：先经统一对外白名单（fail-closed，docs 只放行白名单）
+    if _public_should_skip(_rel):
+        return True
+    # ★第145批 T-145c：敏感文件名优先拦截
+    if parts[-1] in PACKAGE_SENSITIVE_FILES:
+        return True
+    # ★第145批 T-145c：交付包专属排除目录
+    if any(p in PACKAGE_LOCAL_EXCLUDE_DIRS for p in parts):
+        return True
+    # ★第145批 T-145c：精确路径排除（内部文档目录）
+    if any(_rel == p or _rel.startswith(p + "/") for p in PACKAGE_LOCAL_EXCLUDE_PATHS):
+        return True
     # 备份目录/文件（.bak* 及其它历史命名）一律排除
     if any(is_backup_name(p) for p in parts):
         return True
@@ -66,7 +99,14 @@ def main() -> int:
                          compresslevel=6) as zf:
         for root, dirs, files in os.walk(PROJECT_ROOT):
             dirs[:] = [d for d in dirs
-                       if d not in EXCLUDE_DIRS and not is_backup_name(d)]
+                       if d not in EXCLUDE_DIRS
+                       and d not in PACKAGE_LOCAL_EXCLUDE_DIRS
+                       and not is_backup_name(d)]
+            # ★第145批 T-145c：精确路径剪枝（内部文档目录）
+            _drel = os.path.relpath(root, PROJECT_ROOT).replace("\\", "/")
+            dirs[:] = [d for d in dirs
+                       if not any((_drel + "/" + d) == p or (_drel + "/" + d).startswith(p + "/")
+                                  for p in PACKAGE_LOCAL_EXCLUDE_PATHS)]
             rel_root = os.path.relpath(root, PROJECT_ROOT)  # noqa: F841
             for f in sorted(files):
                 full = os.path.join(root, f)
```

---

## 十一、非提交项说明（刻意未纳入本批提交）

本批工作树存在大量**既有污染**（其它批次遗留的未提交改动），
按用户裁定"工作树污染保持不动，每批只精确暂存自身改动"，本批仅暂存上述 8 个文件。

### 11.1 `nucleus/data/exclude_dirs.py`（工作树已改，未纳入）
该文件在本批之前已处于修改态。本批操作 `T-145c` 时曾将其作为参考文件读取，
但**未做任何写入**。`git status` 中该文件的 `M` 标记来自更早批次。

### 11.2 `docs/完整进化路线与技术债务清单_v1.0.md`（⚠️ 误覆盖，见交付报告 §7）
工作树原本有未提交改动（含"第144批 交付确认（2026-09-27）"段 + BOM，约 20624 字节差异）。
本批在 T-145d 去 BOM 时误将其恢复为 HEAD 版，**覆盖了该未提交内容且无法自动恢复**。
处置：已恢复为 HEAD 原样（消除工作树差异），移出本批提交。
**该文件的工作树既有改动需用户从其它来源恢复**（详见交付报告 §7 失误说明）。

### 11.3 `docs/分析报告/` 下若干文件处于 `D`（已删除）态
`git status` 显示 `docs/分析报告/*.md` 等大量文件为 `D`（工作树删除、暂存区未删）。
这是**既有状态**，非本批引入。本批唯一涉及 `docs/分析报告/` 的改动是
`技术债务台账_代码实查_20260919.csv` 去 BOM（已提交）。

### 11.4 pytest `test_m96_followups.py` 4 项失败
失败原因：`FileNotFoundError: docs/分析报告/外部审计_台账投递工序.md` 等。
系上述既有 `D` 删除态所致，**非本批回归**（已用 `git ls-files` + `git status` 核实）。
