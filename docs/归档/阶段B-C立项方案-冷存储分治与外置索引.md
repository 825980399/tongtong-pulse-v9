阶段 B / C 立项方案：L1 冷存储分治 + 外置索引磁盘化
版本：v1.1（立项方案；v1.1 为「优化后」版本，含顺序反转 + 架构收敛）
日期：2026年8月31日
设计：路灯、小林
状态：✅ 已通过立项评审 + 阶段 C'/B' 全部落地验收 + compaction 落地（小林确认，2026年8月31日），含 4 条实施硬约束（见第十节）；硬约束 2（compaction）已闭环，B' 无遗留缺口
依赖：docs/百亿级存储引擎磁盘持久化设计.md v2.0 的阶段 B/C、docs/阶段A施工图-Parquet列式元数据快照.md（已落地）
关联代码：nucleus/mnemosyne/PulseNodePool.py（三层池 + 三索引）、PulseSnapshot.py（阶段 A 的 Parquet 快照）
￼
★ v1.1 优化说明（相对 v1.0 的 4 处关键优化）
v1.0 方案经代码级审查，发现 4 个真实风险点，v1.1 据此优化。核心变化一句话：实施顺序从「先 B' 后 C'」反转为「先 C' 后 B'」，并把「外置索引」与「冷存储驱逐登记」合并为同一套磁盘索引，避免两套 node_id 追踪。
#
v1.0 问题
v1.1 优化
依据
1
顺序「先 B' 后 C'」有语义破坏风险：B' 会改 get_all() 语义（驱逐节点不返回），而 get_all() 被 4 个消费方依赖（PulseSnapshot 快照、CompanionBridge 陪伴桥、PulseKidney 主动遗忘扫描 + 偏见质疑、PulseInnerWorld）
反转为「先 C' 后 B'」：C' 只加「磁盘索引副本」，零语义破坏、零消费方改动；B' 依赖 C' 的磁盘索引作驱逐登记，改动面收窄
get_all() 被 4 处依赖（已逐一核对源码）
2
_cold_evicted（驱逐节点 node_id 集合）与 C' 的「外置索引」两套 node_id 追踪，职责重叠
合并为一套：_cold_evicted 就是 level_index.parquet 中 evol_level=L1且标记 evicted=True 的子集，不再单独维护内存集合
消除双份追踪，降低不一致风险
3
冷池命中「召回回冷缓存」与现有 _promote_to_warm 语义冲突（冷池命中即提升温池）
召回直接回温池：磁盘召回的节点走 _promote_to_warm 路径，而非回冷缓存，复用现有激活→提升逻辑，不新增「冷缓存」这一层
get() 已实现「冷池命中→提升温池」
4
快照与冷存的数据关系留待实施时定
提前定死：冷存是 L1 的权威持久层，快照只存热/温 + 冷存的 node_id 清单（不存冷节点全文），避免实施期返工
消除「实施时二选一」的返工风险
￼
〇、为什么是「阶段 B 部分 + 阶段 C」合并立项
原始设计文档把存储引擎拆成 B/C/D 三段。经对现状代码逐项核对，本轮立项做范围收敛：
原始阶段
原始内容
本轮结论
B
L1 冷存储 + 二值量化
二值量化砍掉——frequency_signature 是标量 float（非向量），当前无向量可量化；只保留「L1 冷存储分治」
C
LSM 引擎 + 分片 + 外置索引
LSM/分片延后（依赖 RocksDB 重依赖 + 分布式场景，当前单机不触发）；外置索引磁盘化保留——已有 _level_index/_path_index/_semantic_index 三内存索引作雏形
D
按需加载 + 缓存换入换出
不在本轮（依赖 B/C 地基，需千亿级真实压力才值得做）
收敛后本轮的单一主题：把「已经在内存里维护的索引」和「L1 长尾数据」磁盘化 + 异步构建 + 按需召回，让内存里只保留「当前需要的知识」。这直接兑现设计文档「最重要的一条认知」——终极手段不是更高压缩比，而是按需加载。
￼
〇、为什么是「阶段 B 部分 + 阶段 C」合并立项
原始设计文档把存储引擎拆成 B/C/D 三段。经对现状代码逐项核对，本轮立项做范围收敛：
原始阶段
原始内容
本轮结论
B
L1 冷存储 + 二值量化
二值量化砍掉——frequency_signature 是标量 float（非向量），当前无向量可量化；只保留「L1 冷存储分治」
C
LSM 引擎 + 分片 + 外置索引
LSM/分片延后（依赖 RocksDB 重依赖 + 分布式场景，当前单机不触发）；外置索引磁盘化保留——已有 _level_index/_path_index/_semantic_index 三内存索引作雏形
D
按需加载 + 缓存换入换出
不在本轮（依赖 B/C 地基，需千亿级真实压力才值得做）
收敛后本轮的单一主题：把「已经在内存里维护的索引」和「L1 长尾数据」磁盘化 + 异步构建 + 按需召回，让内存里只保留「当前需要的知识」。这直接兑现设计文档「最重要的一条认知」——终极手段不是更高压缩比，而是按需加载。
￼
一、现状盘点（写方案前先对齐事实）
1.1 阶段 A 已打下的地基
能力
位置
状态
Parquet 列式快照（按 evol_level 分区）
PulseSnapshot.save_parquet
✅ 已落地
按层分区剪裁读取
PulseSnapshot.load_parquet_by_level
✅ 已落地（冷召回雏形）
快照开关
config.FEATURE['use_parquet_snapshot']
✅ 已存在，默认 False
落盘目录
data/knowledge/parquet/evol_level={L1|L2|L3}/
✅ 已定义
1.2 三个内存索引（阶段 C 的雏形，已存在）
索引
格式
维护方式
查询入口
_level_index
{"L1": set, "L2": set, "L3": set}
_update_index_on_add / _update_index_on_remove 同步维护
query(evol_level=...)
_path_index
{"/技术": set, ...}（一级路径前缀）
同上
query(space_path_prefix=...)
_semantic_index
{target_id: {(source_id, rel_type)}}
懒构建 + 脏标记 _semantic_index_valid
反向横向遍历
1.3 三层池定位（现状）
池
定位
是否落盘
_hot
L3 无条件 + L2(S/A)
随快照
_warm
L2(B/C) + L1(S/A)
随快照
_cold
L1(B/C) 长尾
当前仍全内存 + 随快照，无独立磁盘化
_instinct
L4 本能
独立快照
关键问题：_cold 池当前仍是纯内存 dict，节点全量 get_all() 后随快照写入。L1 长尾数据（百亿~千亿级）会无限挤占内存——这正是阶段 B「冷存储分治」要解决的。
￼
二、立项范围（精确到「做什么 / 不做什么」）
2.1 本轮做（两个可独立交付的子项）
子项 B'：L1 冷存储分治
• _cold 池从「全内存」改为「内存缓存 + 磁盘冷存」双层：
• 冷池只保留「最近激活的 N 个」在内存（N 可配，默认 5000），超出部分驱逐到磁盘冷存，内存只留 node_id 元数据（不存完整节点）。
• 磁盘冷存复用阶段 A 的 Parquet 分区：L1 节点写 evol_level=L1/ 分区，淘汰时增量追加而非全量重写。
• 命中淘汰节点时按需召回（见 2.2 数据流）。
子项 C'：外置索引磁盘化
• 把 _level_index / _path_index / _semantic_index 三个内存索引落盘为 Parquet 索引表，启动时异步加载（不阻塞启动），支持增量更新。
• 解决「上百亿节点时，索引本身也大到内存放不下」的问题——索引磁盘化后，内存只保留「热索引」（近期活跃的 node_id 子集）。
2.2 本轮不做（明确砍掉，防范围蔓延）
• ❌ 向量量化 / 二值量化：frequency_signature 是标量，无向量对象，量化无对象可作用。
• ❌ LSM 引擎（RocksDB）：重依赖，且当前单机内存池够用，LSM 的价值在「亿级→千亿级」才显现。
• ❌ 分片跨机：分布式部署，需独立立项。
• ❌ 按需加载全量改造（阶段 D）：只做「冷池按需召回」，不做「启动不全量加载」。
• ❌ 改器官层调用契约：PulseNodePool.get_node/add_node/query/get_all 签名不变。
￼
三、子项 B'：L1 冷存储分治设计
3.1 数据结构改造
在 PulseNodePool.__init__ 新增：
python
￼
# L1 冷存储：内存缓存 + 磁盘冷存双层（v1.1 优化：去掉独立 _cold_evicted 集合）
self._cold_cache: dict[str, PulseNode] = {}   # 最近激活的冷节点（内存）
self._max_cold_cache = 5000                   # 冷池内存缓存上限（可配）
# 驱逐登记不在此处，而是复用 C' 的 level_index.parquet（evicted 列）
v1.1 优化说明：不再维护独立的 _cold_evicted 内存集合。驱逐节点的 node_id 直接写 level_index.parquet 的 evicted=True 列，由 C' 的索引表统一登记，消除「冷存储 + 外置索引」两套 node_id 追踪的不一致风险。
3.2 驱逐策略（LRU）
• 冷池写入时若 len(_cold_cache) >= _max_cold_cache，把最久未激活的节点（last_activated 最小）驱逐：
1. 该节点写入磁盘冷存（增量追加到 evol_level=L1/ 分区，见 3.3）
2. 从 _cold_cache 删除，在 level_index.parquet 标记 evicted=True
3.3 磁盘冷存格式（复用 Parquet，增量追加）
• 不新建格式，直接复用阶段 A 的 Parquet 分区：
• 驱逐节点 → _nodes_to_parquet_columns([node]) → pq.write_to_dataset(..., root_path=parquet_dir, partition_cols=["evol_level"]) 增量写。
• 因为 Parquet 是「目录 + 多个小文件」，增量写只需追加一个新文件（part-{timestamp}.parquet），无需重写整个分区。
• 读取时 load_parquet_by_level("L1") 会读整个 L1 分区目录，天然合并所有小文件。
易错点（已在阶段 A 踩过）：write_to_dataset 会把 evol_level 编码进目录名；增量追加时注意不要用 existing_data_behavior 覆盖已有文件，应让 pyarrow 自动生成新文件名。
3.4 召回数据流（命中被驱逐节点）
get_node(node_id)
  → 命中 _hot/_warm/_cold_cache → 直接返回
  → 命中 evicted（查 level_index.parquet 的 evicted=True）→ 磁盘召回：
     1. 从 Parquet L1 分区按 node_id 过滤读取（列剪裁：只读该 node_id 的行）
     2. PulseNode.from_dict 反序列化
     3. ★ v1.1：走 _promote_to_warm 提升到温池（复用现有激活→提升逻辑，不回冷缓存）
     4. 清除 evicted 标记，返回节点
  → 全 miss → None
列剪裁优化：Parquet 支持 predicate pushdown，pq.read_table(dir, filters=[("node_id", "=", nid)]) 只扫相关 row group，百亿级下比全量 to_pylist() 快数量级。
3.5 兼容性（v1.1 关键优化）
• get_all() 与 get_all_including_evicted() 显式区分：
• get_all()：返回内存中的全部节点（热 + 温 + 冷缓存），不含被驱逐节点——这是「当前内存视图」。
• get_all_including_evicted()：在 get_all() 基础上，对被驱逐节点做磁盘兜底，返回「逻辑全量」。
• 消费方迁移：PulseSnapshot（快照保存）、PulseKidney（主动遗忘扫描/偏见质疑）等「需要全量遍历」的消费方改调 get_all_including_evicted()；CompanionBridge、PulseInnerWorld 等「只需内存快照」的消费方保持 get_all() 不变。
• 快照语义（v1.1 提前定死）：冷存是 L1 的权威持久层，快照只存「热/温 + 冷存 node_id 清单」，不存冷节点全文。这样快照体积不随冷数据膨胀，冷数据召回靠冷存。
• count() 返回「总节点数 = 内存节点 + evicted 节点数」（查索引表 evicted 计数），而非只看内存。
￼
四、子项 C'：外置索引磁盘化设计
4.1 索引表 Schema（Parquet）
三类索引各一张表，统一放 data/knowledge/index/ 目录：
level_index.parquet：
列
类型
说明
evol_level
string
L1/L2/L3
node_id
string
节点 ID
evicted
bool
★v1.1 新增：是否已被冷存储驱逐（B' 复用此列登记驱逐，替代独立 _cold_evicted 集合）
path_index.parquet：
列
类型
说明
path_prefix
string
一级路径前缀（/技术 等）
node_id
string
节点 ID
semantic_index.parquet：
列
类型
说明
target_id
string
被关联节点
source_id
string
关联来源节点
relation_type
string
关系类型
4.2 异步构建（关键：不阻塞启动）
• 启动时不阻塞等待索引加载，而是：
1. 先恢复内存索引的「热子集」（最近活跃节点，从快照恢复时顺带填 _level_index/_path_index）
2. 后台线程异步从 Parquet 索引表加载完整索引（或用 ThreadPoolExecutor）
• 查询时若索引未加载完，降级为「全池扫描」（现有 query() 的 fallback 路径已支持）。
4.3 增量更新
• _update_index_on_add / _update_index_on_remove 在更新内存索引的同时，追加写一条索引变更记录（WAL 语义），定期批量 flush 到 Parquet 索引表。
• 避免每次 add_node 都重写整个 Parquet 索引表（那会是 O(N²)）。
4.4 对账
• 定期（或手动触发）校验「内存索引」与「磁盘索引表」的 node_id 集合一致性，发现遗漏触发重建。
￼
五、接口新增清单（不改旧签名）
方法
归属
说明
evict_cold_node(node)
PulseNodePool
冷节点驱逐到磁盘（写 Parquet L1 分区 + 索引表标记 evicted）
recall_cold_node(node_id)
PulseNodePool
磁盘召回单节点（召回后提升温池）
get_all_including_evicted()
PulseNodePool
★v1.1 新增：返回逻辑全量（内存节点 + 冷存兜底），供快照/肾脏等全量遍历方
save_index_parquet()
IndexStore
索引表落盘
load_index_parquet_async()
IndexStore
异步加载索引表
get_cold_stats()
PulseNodePool
冷池统计（内存缓存数/evicted 数/召回命中率）
配置开关新增（config.FEATURE）：
• use_cold_storage（默认 False）：是否启用 L1 冷存储分治
• use_external_index（默认 False）：是否启用外置索引磁盘化
• 二者均 False 时行为与现在完全一致，零回归。
￼
六、实施步骤（照此顺序写代码，v1.1 已反转）
阶段一：子项 C'（外置索引磁盘化，先行，零语义破坏）—— ✅ 已落地（2026年8月31日）
先做 C' 的理由：它只新增「磁盘索引副本 + 异步加载」，不改任何现有查询/快照语义，可独立交付、独立回退，风险最低。B' 的驱逐登记直接复用 C' 的索引表。
实际落地记录：
交付物
位置
说明
IndexStore 类
nucleus/mnemosyne/IndexStore.py
独立可插拔索引持久化器，管三张 Parquet 索引表
开关
config.FEATURE['use_external_index']
默认 False，零侵入
PulseNodePool 挂钩
set_index_store / get_index_store / save_index_snapshot / load_index_async
可选引用，未注入时全部零副作用
增量 WAL
_update_index_on_add/remove 追加 record_change
增删时记录变更，定期 flush
框架接入
main.py 构造时按开关注入
开启时创建 IndexStore 注入 node_pool
实现方法清单：
• IndexStore.level_index_to_rows / path_index_to_rows / semantic_index_to_rows：内存索引 → Parquet 行
• IndexStore.save_all / _write_table / _read_table：全量落盘 / 读回
• IndexStore.load_level_index / load_path_index / load_semantic_index：读回三类索引
• IndexStore.load_async：后台线程异步加载，不阻塞启动
• IndexStore.record_change / flush_pending：增量 WAL + 定期 flush
• IndexStore.verify：对账（对比内存 vs 磁盘 node_id 集合）
验收结果：C1~C8 全部通过（13/13 断言），含「异步加载不阻塞（返回耗时 0.000s）」「未加载索引时查询降级正常」「三类索引查询与磁盘一致」「对账一致」「开关关闭零回归」。use_external_index=False 时框架行为与改造前完全一致（AST 语法检查通过 + 默认零副作用验证通过）。
阶段二：子项 B'（L1 冷存储分治，后行，复用 C' 的索引）—— ✅ 已落地（2026年8月31日）
后做 B' 的理由：它要改 get_all()/count() 语义，是唯一会触碰消费方的子项，需在 C' 的索引基础上做，把改动面压到最小。
实际落地记录：
交付物
位置
说明
冷存储配置
PulseNodePool.set_cold_storage / is_cold_storage_enabled
use_cold_storage 开关控制，默认关闭
冷缓存上限
_max_cold_cache（默认 5000）
超过则驱逐最久未激活节点
驱逐逻辑
_evict_cold_node / _enforce_cold_cache
写冷存 Parquet L1 分区 + 登记 _cold_evicted + 索引表标记 evicted
召回逻辑
_recall_cold_node / recall_cold_node
从冷存按 node_id 列剪裁读取 + 提升温池
统计
get_cold_stats
内存缓存数/evicted 数/召回命中率
小文件合并
count_cold_parquet_files / compact_cold_storage / maybe_compact_cold_storage
硬约束2：冷存 Parquet 碎片合并（去重 + 原子替换 + 阈值触发）
语义区分
get_all（内存视图）/ get_all_including_evicted（逻辑全量）
硬约束1：get_all 不隐性兜底
快照闭环
PulseSnapshot._build_full_snapshot 加 evicted_node_ids；_restore_from_data 恢复清单
B8：快照只存清单不存冷节点全文
开关
config.FEATURE['use_cold_storage']
默认 False，零侵入
核心设计决策（落地时确定）：
• _cold 池保持不变（仍叫 _cold，作为冷缓存），在其上叠加驱逐机制——被驱逐节点从 _cold 移除，node_id 记入 _cold_evicted，全文写冷存 Parquet。这样 add/get/remove/load_batch 的现有逻辑改动最小。
• 召回直接提升温池（复用 _promote_to_warm 语义），不回冷缓存。
• 冷存目录独立（_cold_dir，默认 data/knowledge/cold），不与快照的 parquet_dir 耦合。
验收结果：B1~B8 全部通过（16/16 断言），含 B8 一票否决项（快照只存 evicted_node_ids 清单，nodes 不含被驱逐节点全文）。C' + B' 联合回归通过（同时启用两开关协同正常）。use_cold_storage=False 时框架行为与改造前完全一致（零回归验证通过 + AST 语法检查通过）。
遗留说明：
• _cold_evicted 仍是内存集合（存 node_id），启动时从快照 evicted_node_ids 清单恢复。真正把 evicted 清单外置到磁盘索引（消除内存集合）属阶段 C 完整版范畴，本轮用「快照清单」作为最小闭环。
compaction 落地记录（硬约束2 闭环）：
• count_cold_parquet_files()：统计冷存 evol_level=L1 分区的小文件数。
• compact_cold_storage()：读整目录 → 按 node_id 去重（保留最新）→ 全量重写为单文件 → 临时目录 + rename 原子替换。
• maybe_compact_cold_storage(max_files=20)：文件数 ≥ 阈值时自动触发合并。
• 关键修复：pq.read_table(单分区目录) 读不出 evol_level 列（Hive 分区列被编码进目录名），改用 pq.ParquetDataset(整目录) 自动恢复。
• 验收：11/11 通过（小文件合并、去重、召回 7/7、未达阈值不触发、达阈值自动合并），AST 语法检查通过。
￼
七、验收清单
子项 C'（外置索引磁盘化，先行）
#
用例
预期
C1
save_index_parquet 后读回
三类索引 node_id 集合与内存一致
C2
异步加载不阻塞启动
启动时间与关闭索引时无明显差异
C3
索引未加载完时查询
降级全池扫描，结果正确
C4
query(evol_level=...) 走磁盘索引
结果与全池扫描一致
C5
query(space_path_prefix=...) 走磁盘索引
结果一致
C6
反向横向遍历走磁盘 semantic_index
结果一致
C7
对账发现遗漏
触发重建，最终一致
C8
use_external_index=False
行为与改造前完全一致
子项 B'（L1 冷存储分治，后行）
#
用例
预期
B1
冷池超 _max_cold_cache 后驱逐
最久未激活节点被驱逐，内存冷缓存不超上限
B2
驱逐节点写入 Parquet L1 分区 + 标记 evicted
load_parquet_by_level("L1") 能读到该节点，索引表 evicted=True
B3
get_node 命中被驱逐节点
磁盘召回 + 提升温池（非回冷缓存），返回正确节点
B4
get_node 全 miss
返回 None，不报错
B5
count() 语义
= 内存节点数 + evicted 节点数
B6
use_cold_storage=False
行为与改造前完全一致（全内存冷池）
B7
召回命中率统计
get_cold_stats() 返回正确命中率
B8
快照完整性
快照只存热/温 + 冷存 node_id 清单；冷节点全文在冷存，可召回不丢失
￼
八、风险与缓解
风险
缓解
Parquet 增量追加导致小文件碎片
定期合并（按天/周做 compaction，复用 write_to_dataset 全量重写该分区）
磁盘召回延迟（百毫秒级）
只对冷池（低频）召回，热/温池不受影响；召回后提升温池减少二次召回
get_all() 语义变更影响消费方
显式区分 get_all()（内存节点）与 get_all_including_evicted()（含冷存兜底），快照/肾脏等全量遍历方改调后者，逐消费方回归
索引增量 WAL 与 Parquet 索引表不一致
定期对账 + 脏标记触发重建
evicted 标记与冷存实际文件不一致
对账校验「索引表 evicted 集合」与「Parquet L1 分区实际 node_id 集合」一致
￼
九、结论与建议
本轮立项的价值：在不引入重依赖（无 RocksDB、无向量库）的前提下，用已落地的 Parquet 地基 + 已存在的三索引雏形，把「L1 长尾分治」和「索引磁盘化」这两件「千亿级必做」的事用最小侵入方式推进。两个子项都可独立交付、可独立回退（开关默认 False），不破坏器官层契约。
建议实施顺序（v1.1 已反转）：先 C' 后 B'。C' 是「索引磁盘化」（零语义破坏、零消费方改动，可独立交付、风险最低）；B' 是「数据分治」（会触碰 get_all() 语义，需在 C' 的索引基础上做，把改动面压到最小）。详见「★ v1.1 优化说明」。
触发阶段 D（按需加载全量改造）的前置条件：B' + C' 验收通过，且出现「单机节点数逼近内存上限」的真实压力场景。
￼
十、立项评审结论（✅ 2026年8月31日 小林已确认通过）
三条决策（范围收敛 / 顺序反转 / 快照语义）全部确认，方案 v1.1 正式通过立项。以下 4 条为评审补充的实施硬约束，落地时不得违反。
硬约束 1：get_all() 语义边界必须划死
• 默认 get_all()：只返回内存可见节点（热 + 温 + 冷缓存），不自动兜底冷存，保持原有语义不变，不隐性引入磁盘 IO。
• get_all_including_evicted()：显式全量兜底，专供快照保存、肾脏扫描等需要全量遍历的场景调用。
• 禁止：在默认 get_all() 里偷偷加磁盘兜底，否则所有调用方隐性性能下降，难排查。
硬约束 2：Parquet 小文件碎片必须有合并机制
• 触发条件：L1 分区文件数 ≥ 20 个，或每周固定执行一次。
• 操作：对 evol_level=L1 分区做 compaction 全量重写，合并小文件。
硬约束 3：两个开关全部默认 False，灰度上线
• use_external_index 默认关 → 先开索引功能，验证一致性、性能无劣化，再进下一步。
• use_cold_storage 默认关 → 索引稳定后，再开冷池驱逐，逐步验证。
• 两个功能独立开关、独立回退，随时可切回原始全内存模式。
硬约束 4：B8 一票否决
• 验收清单 B8（快照只存冷存 node_id 清单，不存冷节点全文） 为核心验收项，一票否决。
• 只要快照里还存冷节点全文，冷存分治就等于没做——快照体积仍随 L1 长尾无限增长。
￼
实施顺序（已确认）：先 C' 验收通过 → 再做 B'。C 阶段不碰业务语义，B 阶段不扩快照边界。