# 第131批 路灯任务书

> 生成：星轨 · 2026-09-26
> 前置分析：烛微第131批技术债务前置分析（P0事故尸检+批5+Pool拆分+WikiQuerier限频）
> 上一批：第130批（W7三件式独窗+git匿名化rebase，已完成待push）

---

## 任务列表

### T-131a P0事故修复·最小集（1+2+5）
**优先级：P0，必须做**

根据烛微尸检，本次事故三个核心修复点：

1. **SEA `_is_core_file` 名单补 `nucleus/data/`**（1行）
   - 文件：`nucleus/evolution/SafeEvolutionExecutor.py`
   - 现状：核心名单含mnemosyne/reasoning/security/evolution等6域，独缺data
   - 改法：把`nucleus/data/`加入核心文件名单，该目录下文件不允许LLM补丁自动改

2. **PM apply末行写回修复**（3-5行）
   - 文件：`nucleus/patches/PatchManager.py`，约:3177行
   - 现状：apply成功后直接写`[]`清空整个pending列表，吞掉所有未审批补丁
   - 改法：apply成功后写回skipped列表，而不是空列表

3. **`_spawn_self_restart` stdout/stderr重定向**（4行）
   - 文件：找到自重启函数
   - 现状：重启进程零重定向，崩了日志都看不到
   - 改法：把stdout/stderr重定向到`logs/boot_crash.log`，下次崩了能看到死因

---

### T-131b 批5悬账13员静默except
**优先级：P1**

根据烛微重锚的13个位置，全部改成`silent_exc(e,"file:line:语义",level="warning")`格式：

清单：
1. experience_retriever:438
2. JsonRepair:261
3. sandbox_limits:411
4. quality_score_v2:503
5. VectorEncoder:183
6. PulseTouch:534
7. check_patch_consistency:88
8. dead_code_scan:96
9. m102_data_governance:147
10. verify_dual_write_e2e:228
11. verify_write_only_e2e:267
12. model_self_updater:187(_load_index)
13. report_bus:718(load_from_disk)

目标：466→453（13处）

---

### T-131c git push双远端
**优先级：P1**

上一批已完成匿名化rebase，15票全部改成Tongtong Dev作者。本批执行push：

```bash
git push --force-with-lease origin master      # gitee
git push --force-with-lease openi master        # openi.pcl.ac.cn
git push origin refs/archive/old-102            # archive ref
```

推送前确认：
- pre-rebase-keep分支存在（回滚点）
- 15票作者全部是Tongtong Dev <dev@users.noreply.gitee.com>
- 远端没有其他人新提交

---

### T-131d WikiQuerier限频
**优先级：P2**

根据烛微分析，百科反爬命中42轮全打baike.baidu.com，7个URL当日重探多次。

改法：方案甲=域级冷却缓存
- 文件：`nucleus/knowledge/WikiQuerier.py`
- 加域级冷却：同一个域名的请求间隔，首次30min，逐级递增到2h/6h
- 读Retry-After头，消费它而不是放着不用
- 预估10-14行代码

---

## 验收标准

1. 三道门禁全过：ruff F=0 / py_compile / pytest核心用例
2. T-131a：三处修复都完成，注释说明
3. T-131b：13处静默except全部转silent_exc，计数466→453
4. T-131c：双远端push成功，gitee和openi都能看到最新commit
5. T-131d：WikiQuerier加冷却，不会10分钟内反复请求同一个URL

---

*星轨 · 第131批任务书 · 2026-09-26*
