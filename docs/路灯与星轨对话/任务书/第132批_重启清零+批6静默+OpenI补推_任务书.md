# 第132批 路灯任务书

> 生成：星轨 · 2026-09-26
> 前置分析：烛微第132批技术债务前置分析
> 上一批：第131批（P0事故修复+批5静默+git push Gitee成功，OpenI待补）

---

## 任务列表

### T-132a 重启前两件欠账清零
**优先级：P0，重启前必须做**

根据烛微尸检，框架重启前必须处理：

1. **清pending_verification残留**
   - 文件：`data/patches/pending_verification.json`
   - 现状：还是`{verified:false, attempts:0}`，首boot会进回滚循环
   - 改法：清空成`{}`，或者删掉这个文件让框架重新生成

2. **清restart_count残留**
   - 文件：找到restart_count.txt
   - 现状：有残留计数，会影响自重启逻辑
   - 改法：清零或者删掉

3. **账本重建**
   - 背景：16:58事故吞了21条pending补丁记录
   - 要求：按照烛微的六步SOP重建：
     - 先确认:3177 apply写回修复已经落地（T-131a已做）
     - 从备份文件里恢复21条补丁记录
     - 6条rejected补丁打上`vote_ref=b124_snapshot`标记，防复活
     - 4条状态存疑的，先标记待确认
   - 注意：重建完先放`.rebuilt_131`旁路核对，确认没问题再正式用

---

### T-132b 批6 17员静默except
**优先级：P1**

根据烛微清单，17处全部改成`silent_exc(e,"file:line:语义",level=...)`格式：

#### A块（continue/return型，优先改）
1. SafeEvolutionExecutor:1130 `_m96_channel_health` RETURN型
2. SafeEvolutionExecutor:3428 `_llm_review_patch` RETURN型（事故同函数域）
3. SafeEvolutionExecutor:4131 `_m85_conservative_fix` CONTINUE型
4. SafeEvolutionExecutor:5179 `_generate_llm_patch` RETURN型
5. PatchManager:284 `_m92_count_class_methods` RETURN型
6. PatchManager:1332 `find_blocking_pending_patch` RETURN型
7. PatchManager:2215 `_behavior_equivalence_probe` CONTINUE型
8. ReasoningWorkerPool:608 `_maybe_resize_pool_locked` RETURN型
9. PulseNodePool:1846 `run_memory_verification` CONTINUE型（闭环第3处静默）
10. PulseNodePool:826 `_m71_neo4j_store` RETURN型
11. PulseNodePool:1191 `_m71_influx_store` RETURN型
12. PulseInnerWorld:4135 `_detect_experience_route` RETURN型

#### B块（高价值pass型）
13. PatchManager:3003 `apply_all_pending` OSError pass
14. ReasoningWorkerPool:448 `_rebuild_pool`
15. ReasoningWorkerPool:319 `get_stats`
16. SafeEvolutionExecutor:3239 `_clean_llm_code` SyntaxError pass
17. PulseInnerWorld:5252 `_run_periodic_reflection` RETURN型

**验收**：1416→1399（17处）

---

### T-132c OpenI补推
**优先级：P1**

用户已找回OpenI密码，执行补推：
```bash
git push openi master
```

推完确认OpenI仓库能看到最新commit。

---

### T-132d 豁免申报8员确认
**优先级：P2**

烛微提出8处静默except是合理的自举兜底，建议豁免：
- PatchManager:3361/3490/3638/3722/3779（ImportError×5，日志模块不可用兜底）
- ReasoningWorkerPool:706/746/794（shutdown期落盘保护×3）

任务：把这8处加进pre-commit hook的白名单，以后不再计数。

---

## 验收标准

1. 三道门禁全过：ruff F=0 / py_compile / pytest核心用例
2. T-132a：重启前两件欠账清零，账本重建完成
3. T-132b：17处静默except全部改完，计数1416→1399
4. T-132c：OpenI push成功，仓库同步
5. T-132d：8处豁免加进hook白名单

---

*星轨 · 第132批任务书 · 2026-09-26*
