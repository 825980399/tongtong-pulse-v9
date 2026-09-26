# Git 提交 Hash 映射表（rebase 匿名化）

> 生成时间：2026-09-26 ｜ 操作：第130批 T-130c git push 匿名化
> 方法：`git commit-tree` plumbing 重写 `8d9b95f..HEAD` 共 **15 票** 的 author 为 `Tongtong Dev <dev@users.noreply.gitee.com>`（作者日期保留，committer 不变）。
> 原始历史保留于分支 `pre-rebase-keep`（= 旧 `fda267d`），可随时回滚。

## 15 票 old → new 对照

| # | 旧 hash (短) | 旧 hash (全) | 新 hash (短) | 新 hash (全) | 提交说明 |
|---|---|---|---|---|---|
| 1 | ae390ba | `ae390ba071fb73b4ebf3683945bbb53b6e39a603` | f2dc5ee | `f2dc5ee7eb054b56dd209dda48e34d15f4d82595` | 第116批 交付报告/测试/前置分析 收口（git纪律·T-124c） |
| 2 | d1a39a1 | `d1a39a113ae31cecd54fdf66d0df3244e27344ae` | 526b664 | `526b664948f965985368ce4254f0c8a7548a86e8` | 第117批 tracer flush修复+R4验收+棘轮修复 收口（git纪律·T-124c） |
| 3 | 2b5a499 | `2b5a499be0be512c068e97f3ee84e6c0d9dae332` | 2602d66 | `2602d66c46d182a01cb0900b5aa28e595a41b437` | 第118批 face R2急救+裸logging+账本三件套 收口（git纪律·T-124c） |
| 4 | ca8469e | `ca8469eaf78609dc6adc47b25d674ce965e94f73` | 1860e7c | `1860e7c55db10baadc02c8d5d176fae59670645d` | 第119批 装库前置硬化+池票首批清账 收口（git纪律·T-124c） |
| 5 | a877bee | `a877bee8cf003f95d12116cd3f32c52055e5ced0` | f89bb0d | `f89bb0dea6bc9e3d3d1ab9a37cc8f734b6ba9156` | 第120+121批 SOP成文/票号校准/D040/账面收尾 收口（git纪律·T-124c） |
| 6 | b666843 | `b66684316de5634d532f082fbbbf7d95df04b835` | c6bbf59 | `c6bbf59e7f7d20082c8742e977d49b273ffeba21` | 第122批 活体窗装库+C4断5 Parquet修复 收口（git纪律·T-124c） |
| 7 | 1a2ff00 | `1a2ff00b281ea5b9a4b17c3ae4a8f2de5f0f082a` | e4e2844 | `e4e284419def9da6ddf39197bdf7e6fcf7a92214` | 第123批 DAL止血+补丁冻结+W6准备 收口（git纪律·T-124c） |
| 8 | c422949 | `c422949c7984ecd3056070d2e7563c98a4b4303a` | 1de516b | `1de516bed1a4c829e25e684ce440b2123cbcef04` | 第124批 R5落码+静默except首批7处+git纪律 本批交付（T-124a/b/c） |
| 9 | fb48f8b | `fb48f8bf585f35f654a46a4cb696e3977f684e9b` | 254401f | `254401fdf3e68343acac1c94cf1be705816ed318` | chore: 收尾删除 legacy 残留产物（dz_claim_scan.json / tmp_verify_influx.py）— T-124c 清理 |
| 10 | cf0b810 | `cf0b81014aedde49566db7089d48984cc0e1a571` | 5b7cff5 | `5b7cff56d5559d4f961d8e6f56a731795ca1b1b3` | 第125批 R1保险丝修复+D040写侧A'+静默except批2 交付（T-125a/b/c） |
| 11 | d713f33 | `d713f335bb20e0e1aac00ad3eb57b0f1c7a67938` | cf62d5f | `cf62d5fb6405ac24e45dbf7ff49a2d5ffcc0ac5c` | 第126批 冷却闸双处限流 + 静默except pre-commit hook 交付（T-126a/c） |
| 12 | 6e78d7a | `6e78d7ab3e98e2e019b56aa2865ff1aa615b59c7` | 6225aa1 | `6225aa13ec3253dc91497446508c1f67887e4b34` | 第127批 静默except批3(25员) + D040 W7-B L3降级执行段 交付（T-127a/b） |
| 13 | 237f4cc | `237f4cc3047ff03d53d60591061b2d407971468e` | f892623 | `f892623bc759d87bfc57b175b6f1cc3090fd55fe` | docs: 第125-127批烛微分析报告与路灯任务书收口 |
| 14 | 0ebd706 | `0ebd70645ecb646104a7de735c1e3f3016ef9b2d` | 807c998 | `807c998bab353daf7bd6f0b7815181236845bfdb` | 第128批 静默except批4(16员) + 六票账面收口 + tracer守卫 交付（T-128a/b/c） |
| 15 | fda267d | `fda267d80ca01e0ee381746d8e05376380d87bff` | abc086b | `abc086b5b18607ca5eaa84a2b037a1ba55c30475` | 第129批 治理五口收敛器 + 粘名甲案修复 + hook基线重发 交付（T-129a/b/c） |

## 本次新增（不在 rebase 范围内）

| 新 hash (短) | 新 hash (全) | 提交说明 | 作者 |
|---|---|---|---|
| 059520b | `059520b1f0a0f4a4cc2bd1839d6fbd6413983e29` | 第130批 T-130b W7三件式独窗交付 | Tongtong Dev |

## 校验

- `git log --format='%an %ae' 8d9b95f..master` → 15 票 author 全部 = `Tongtong Dev <dev@users.noreply.gitee.com>`（已验证 0 例外）
- `git diff fda267d anon-rewrite` → 内容/树完全一致（仅 author 改写，committer 仍 = 原 Administrator）
- 推送前请由星轨确认远端状态；推送需 force（历史已改写）。

