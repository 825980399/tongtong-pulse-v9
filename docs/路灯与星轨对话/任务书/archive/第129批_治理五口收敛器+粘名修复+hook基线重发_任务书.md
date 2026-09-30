# 第129批 任务书：治理五口收敛器 + 粘名修复 + hook基线重发

> 派发：星轨　｜　执行：路灯　｜　前置分析：烛微第129批
> 模式：⚠️ **需停框架执行**（改PatchManager.py+VC.py）
> 红线：不改config.py运行开关、不碰data/knowledge/、改前备份

---

## 0. 本批定位

**W6验收后第一批治理改造。**
本批三个方向：治理五口收敛器+粘名缺陷甲案修复+hook基线重发。

---

## 1. T-129a（P1）：治理五口收敛器

### 背景
六票终裁后，加固治理门禁，防止"下一个六票"。
烛微普查发现：approved赋值点实为5处（非之前3处），新增W2老化口+W3保存口（"保存即批"是六票批量产生的候选解释）。

### 设计（烛微定稿）
- 新增`_approve_via(patch, source, signer)`唯一写入口
- 新增`_govern_fields_ok`+`_boundary_check`两纯函数
- 五处approved赋值全部改写经它
- 总diff≈33~45行，单文件PatchManager.py
- 五口fail-closed，双层串联（apply层T-123b冻结门不动）
- W4/W5人工口强制`human:<名>`签名
- W5批量口加单次≤10上限（堵"一次点名全放行"）

### 五口清单
1. W1 自动口 `_m105_try_release_low_risk`:550
2. W2 老化口 `_m94_apply_pending_aging`:825
3. W3 保存口 `save_pending_patch`:981
4. W4 人工口 `approve_patch`:1286
5. W5 批量口 `approve_all_patches`:1337

### 验收
- py_compile过
- m95单测39 passed + 4 skipped
- 存量19条补丁全池盲跑一遍，出预检清单
- 不影响现有approved补丁状态

---

## 2. T-129b（P1）：粘名缺陷甲案修复

### 背景
VC粘名通道（sticky-name）=影子误报最大结构性污染源。
测试脸A命中后离场、陌生人B入座→影子行记user=A而镜头前不是A。

### 修复（甲案2行）
- PathA（:786 return前）：插`self._current_user_name = "访客"`
- PathB（:864 elif尾）：插`self._current_user_name = "访客"`
- 红线：一切payload不动（反向验收：离开print仍带真名）

### 验收
- py_compile过
- 不影响现有行为（USER_LEFT payload仍带旧名）
- 杜绝当面错叫

---

## 3. T-129c（P1）：hook基线重发

### 背景
批3清掉的25个指纹仍在基线里，"先删后加"洞已现实存在。

### 操作
- 重跑静默except扫描，生成新基线
- 覆盖`tools/ci/baselines/silent_except_baseline.json`

### 验收
- 新基线=456个静默点
- CI门禁PASS（新增=0）

---

## 4. T-129d（P2）：git commit第15票

### commit message
`第129批 治理五口收敛器 + 粘名甲案修复 + hook基线重发 交付（T-129a/b/c）`

---

## 5. 门禁要求

| 门禁 | 标准 |
|---|---|
| ruff F | =0 |
| py_compile | 改动文件全过 |
| m95单测 | 39 passed + 4 skipped |
| 静默except CI | PASS（新增=0） |
| 行尾保全 | .py CRLF不变 |

---

## 6. 交付要求

1. 治理五口收敛器DIFF
2. 粘名修复DIFF
3. hook基线重发记录
4. 存量补丁盲跑预检清单
5. git commit记录
6. 门禁结果

---

*星轨 · 2026-09-26 · 第129批*
