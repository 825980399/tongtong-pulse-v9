阶段D立项方案-启动按需加载与缓存换入换出.md
阶段 D 立项方案：启动按需加载 + 缓存换入换出
版本：v1.0
日期：2026年8月31日
设计：路灯、小林
状态：✅ 已通过评审 + D1/D2/D3 全部落地验收（小林确认，2026年8月31日），11/11 验收通过，AST 语法检查通过
上游依赖：docs/百亿级存储引擎磁盘持久化设计.md 阶段 D、docs/阶段B-C立项方案-冷存储分治与外置索引.md（B'/C' 已落地）
关联代码：nucleus/mnemosyne/PulseNodePool.py、PulseSnapshot.py、main.py、消费方（快照/肾脏/陪伴桥/内在世界）
￼
一、背景与目标
背景
存储引擎已完成阶段 A（Parquet 列式快照）、C'（外置索引）、B'（L1 冷存储分治）。但存在一个关键语义断裂：
• 运行期：B' 已实现「冷池超限 → 驱逐到磁盘 → 按需召回」，冷节点全文落在冷存 Parquet，快照只存 evicted_node_ids 清单。
• 启动期：main.py 仍调用 load_batch(restored_nodes)，把所有快照节点（包括本该是冷的 L1-B/C 节点）全量灌入内存。
千亿级时，这一步启动就会 OOM。阶段 D 的本质（设计文档第 251 行）就是「按需加载 + 缓存换入换出」——让系统永远只把「当前需要的知识」放在内存里。
目标
把「启动全量加载」改为「热层常驻、温层按需、冷层归档召回」的按需加载，并补齐运行期缓存换入换出闭环。
非目标
• 不做「分片跨机部署」（分布式）。
• 不做「Milvus/FAISS 向量检索引擎」接入（外部依赖）。
• 这两个仍是阶段 D 的「长期子目标」，本方案不覆盖。
￼
二、关键设计决策
决策1：冷节点判定复用 _determine_pool（不新增独立规则）
事实：_determine_pool（PulseNodePool.py:650）已有成熟规则——L3→热池、L2+S/A→热池、L2+B/C→温池、L1+S/A→温池、L1+B/C→冷池。
结论：启动按需加载时，用 _determine_pool 判定目标池：落热/温池的节点进内存，落冷池的节点直接写冷存 Parquet 并标记 evicted。零新概念，复用现有能力。
决策2：启动按需加载做成 PulseNodePool 的新入口，冷存储未启用时回退 load_batch
结论：新增 load_batch_lazy（或等价入口），逻辑：
• 冷存储未启用（use_cold_storage=False）→ 行为等价 load_batch（零回归）。
• 冷存储启用 → 按 _determine_pool 分流，冷节点落冷存 + 标记 evicted，不进内存。
这样 main.py 只需替换一行调用，且开关默认 False 时完全不变。
决策3：消费方语义对齐（D2）是 D1 的前置必要条件
事实：B' 落地时已明确「快照保存/肾脏全量扫描需逻辑全量，应改 get_all_including_evicted」，但实际代码里这 4 处消费方仍用 get_all()（内存视图）。一旦 D1 让冷节点不进内存，这些消费方就会漏掉冷节点数据。
结论：D2 必须与 D1 同步完成，否则按需加载会破坏快照完整性和肾脏全量统计。
￼
三、三子目标与改动范围
D1：启动按需加载
改动
文件
内容
新入口
PulseNodePool.py
load_batch_lazy(nodes)：按 _determine_pool 分流，冷节点写冷存 + 标记 evicted
启动链路
main.py:1073
load_batch → load_batch_lazy（冷存储启用时）
开关
config.py
复用 use_cold_storage（不新增开关，冷存储启用即启用按需加载）
D2：消费方语义对齐
消费方
文件:行
改动
快照 save
PulseSnapshot.py:122
get_all() → get_all_including_evicted()
快照 save_l1
PulseSnapshot.py:433
get_all() → get_all_including_evicted()
快照 Parquet
PulseSnapshot.py:1092
get_all() → get_all_including_evicted()
肾脏全量扫描
PulseKidney.py:126
get_all() → get_all_including_evicted()
内在世界统计
PulseInnerWorld.py:14864
get_all() → get_all_including_evicted()
陪伴桥
CompanionBridge.py:281
不动（只取前 50 条共享，内存视图够用）
health_ui
health_ui.py:787/1133
不动（展示统计，内存视图够用）
D3：缓存换入换出
改动
文件
内容
温池降级冷存
PulseNodePool.py
_enforce_capacity 里温池超限时，L1-B/C 节点降级走冷驱逐（复用 _evict_cold_node）
￼
四、实施步骤
步
内容
验证
1
D1：load_batch_lazy 新入口 + main.py 接入
AST + 单测（冷存启用/未启用两态）
2
D2：5 处消费方 get_all → get_all_including_evicted
AST + 零回归（冷存未启用时两方法等价）
3
D3：温池超限降级冷存闭环
AST + 单测（超限降级 + 召回）
4
端到端验收（第七节）
全过 + AST
￼
五、风险评估
风险
影响
缓解
冷节点不进内存，热路径 get() 需召回
性能
_recall_cold_node 已有；召回后提升温池，二次访问快
消费方漏数据
快照/统计不完整
D2 同步对齐，get_all_including_evicted 兜底
冷存储未启用时回归
行为变化
load_batch_lazy 回退 load_batch，get_all_including_evicted 等价 get_all
启动时冷存目录脏数据
召回读到旧节点
复用 B' 的 evicted_node_ids 清单一致性；冷存按 node_id 剪裁读
￼
六、实施硬约束
1. 零回归：use_cold_storage=False 时，框架行为与改造前完全一致（load_batch_lazy 回退、get_all_including_evicted 等价）。
2. 复用现有能力：冷节点判定复用 _determine_pool，召回复用 _recall_cold_node，不新造轮子。
3. 不重写宽路由层：改动只在 PulseNodePool 和 5 处消费方调用点，不触碰查询路由。
4. 每步 AST + 最小功能验证。
5. 改完同步文档，保持「已完成/待办」严格分离。
￼
七、验收标准
#
用例
预期
D-1
冷存储未启用，load_batch_lazy 行为
等价 load_batch（零回归）
D-2
冷存储启用，load_batch_lazy 分流
热/温节点进内存，冷节点落冷存 + 标记 evicted
D-3
冷节点启动后 get() 召回
从冷存召回成功
D-4
get_all() 不含冷节点
内存视图正确
D-5
get_all_including_evicted() 含冷节点
逻辑全量正确
D-6
快照 save 含冷节点
不丢冷数据（D2 生效）
D-7
肾脏全量扫描含冷节点
不丢冷数据
D-8
温池超限降级冷存（D3）
超限 L1-B/C 节点驱逐到冷存
D-9
降级后召回
召回成功 + 提升温池
D-10
冷存储未启用，消费方 get_all_including_evicted
等价 get_all（零回归）
D-11
AST 语法检查
全部通过
￼
八、结论
阶段 D（D1+D2+D3）是「千亿级存储」的关键一步——把「启动全量加载」改为「按需加载 + 换入换出」，直击启动 OOM 的本质。改动范围可控（集中在 PulseNodePool + main.py + 5 处消费方），复用 B'/C' 已落地的冷存/召回能力，零新概念。
九、落地记录（2026年8月31日）
实施内容（4 步全部完成）：
步
内容
交付物
验证
1
D1 启动按需加载
PulseNodePool.load_batch_lazy + _write_cold_node_to_disk（抽取共用）+ main.py 接入
AST ✅ + 单测（冷存两态分流 + 召回）✅
2
D2 消费方语义对齐
5 处消费方 get_all → get_all_including_evicted（快照×3 + 肾脏 + 内在世界）
AST ✅ + 零回归 ✅
3
D3 缓存换入换出
_enforce_capacity 温池超限 L1 节点直接落冷存
AST ✅ + 单测（超限降级 + 召回）✅
4
端到端验收
11 条断言
11/11 通过 ✅
验收结果：D-1~D-11 全部通过，含零回归、启动分流、召回、消费方不漏数据、换入换出、AST。
关键实现要点：
• 抽取 _write_cold_node_to_disk：_evict_cold_node（驱逐）与 load_batch_lazy（启动）共用「序列化 + 写盘」逻辑，零重复。
• 冷节点判定复用 _determine_pool：L3→热、L2/L1+S/A→热/温、L1+B/C→冷，零新概念。
• 修复 B' 遗留 bug：get_all_including_evicted 原实现调用 _recall_cold_node 会 discard evicted 标记但节点不放回池，导致冷节点「悬空」（第二次遍历丢失）。已给 _recall_cold_node 加 consume 参数，get_all_including_evicted 用 consume=False 只读遍历，不破坏驱逐登记。
待后续（长期）：阶段 D 的「分片跨机部署」+「Milvus/FAISS 向量检索引擎」仍属长期子目标，本方案不覆盖，另行立项。