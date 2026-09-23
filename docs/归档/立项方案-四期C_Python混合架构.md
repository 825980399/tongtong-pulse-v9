立项方案：四期 C/Python 混合架构（编译落地 + 性能基准）
日期：2026年8月31日
提出：小林
方案：路灯
状态：✅ 已落地（编译成功 + 正确性 100% + 性能基准实测 + 灰度开关接线）
关联：承接《立项方案-硬件自适应升级与混合架构》中「四期 R6」的独立立项诉求，本期不与前一二三期混同。
￼
一、需求背景
框架已有 C/Python 混合架构雏形（3 个 Cython 扩展），但当前 Linux 沙箱未编译，全部走 Python fallback。四期目标是：把已预埋的 Cython 扩展在目标环境编译落地，实测性能收益，为后续「底层硬件操作下沉 C」铺路。
二、现状盘点（关键事实，已核实）
2.1 已有 Cython 扩展（3 个）
模块
.pyx 源码
现有 .pyd
功能
_oscillon_cy
nucleus/field/_oscillon_cy.pyx
cp312-win_amd64.pyd
共振强度 + 频率锁定（纯数值，nogil）
_frequency_codec_cy
nucleus/pulse/_frequency_codec_cy.pyx
cp312-win_amd64.pyd
频率编解码（MD5 哈希 + 正弦映射）
_resonance_cy
nucleus/synapsys/_resonance_cy.pyx
cp312-win_amd64.pyd
五维共振得分（记忆/时间/状态维）
2.2 关键问题（已核实）
#
问题
证据
1
现有 .pyd 是 Windows Python 3.12 产物
cp312-win_amd64.pyd，当前 Linux Python 3.11.1 无法加载
2
构建脚本 setup_cython.py 有 bug
只 glob *.pyd（Windows），Linux 应 glob *.so；跨目录复制逻辑对 Linux 不完整
3
沙箱缺 Cython
gcc/clang 已就绪，但 import Cython 失败
4
.pyx 源码质量高
含 nogil 优化、纯数值计算，注释严谨，可直接编译
2.3 Python fallback 消费方（正确性对照基准）
模块
fallback 位置
说明
OscillonField.calculate_resonance
nucleus/field/OscillonField.py:219-222
有 _oscillon_cy_available 开关，未编译时走原生
OscillonField.try_lock_frequency
nucleus/field/OscillonField.py:421
同上
FrequencyCodec.encode
nucleus/pulse/FrequencyCodec.py:109-119
encode_cy 失败走原生
FrequencyCodec.encode_batch
nucleus/pulse/FrequencyCodec.py:191-206
encode_batch_cy 失败走 Python 循环
这些 fallback 是四期正确性验证的对照基准：Cython 输出必须与 Python fallback 完全一致。
￼
三、方案设计（第一步：编译落地 3 个已有模块）
3.1 目标
1. 修复构建脚本，在本机（Linux Python 3.11）把 3 个 .pyx 编译成 .so；
2. 验证 Cython 输出与 Python fallback 完全一致（正确性）；
3. 实测性能倍率（timeit），达标值按小林已定：振荡场 ≥3x、编解码 ≥2.5x、共振 ≥2x。
3.2 改动清单
#
改动
文件
说明
1
安装 Cython
环境
pip install cython（构建期依赖，非运行期依赖）
2
修复构建脚本跨平台
nucleus/pulse/setup_cython.py
.pyd/.so 双平台兼容：Windows 编译输出 *.pyd、Linux 输出 *.so，两套平台都能走通
3
编译 3 个模块
nucleus/{field,pulse,synapsys}/
python setup_cython.py build_ext --inplace，生成扩展文件
4
新增灰度开关
config.py
use_cython_extensions（默认 False）：关闭强制走 fallback，开启优先加载 Cython，失败自动降级
5
正确性验证脚本
tools/verify_cython_parity.py
覆盖正常/边界/零值/极值四类用例，断言输出值+返回类型+异常行为三维一致
6
性能基准脚本
tools/benchmark_cython.py
timeit 实测，拆解纯计算 vs 整体加速比，附环境标注 + 瓶颈分析
3.3 验证标准（量化）
验收项
量化标准
编译成功
3 个扩展文件生成，import 成功，_xxx_cy_available = True
正确性
Cython 与 Python fallback 输出完全一致，覆盖正常/边界/零值/极值 + 输出值/类型/异常三维
性能
振荡场 ≥3x、编解码 ≥2.5x、共振 ≥2x（timeit 实测，如实报告，不硬凑）
兼容性
编译失败自动 fallback；删除扩展文件后启动不报错；开关关闭行为与改造前完全一致
内存占用
Cython 扩展加载后进程内存增量 ≤ 5MB
环境标注
性能报告附 CPU 型号/核数/主频、Python 版本、测试数据规模
3.4 风险与回滚
风险
应对
编译失败
保留 Python fallback（现有机制），编译失败自动降级，不影响运行
性能不达标
如实报告真实倍率 + 拆解纯计算/整体加速比 + 瓶颈分析（如 hashlib.md5 持 GIL）
跨平台差异
构建脚本双平台兼容（.pyd/.so）；本期 Linux 编译验证，Windows 留待后续（现有 .pyd 已覆盖）
3.5 关键原则（四期铁律）
「实测倍率不达标就如实报告 + 分析瓶颈，绝不凑数」 是四期铁律。frequency_codec 中 hashlib.md5 持 GIL 属「Python 对象交互无法加速」的客观瓶颈，最终总加速比被拖低是正常现象。验证后须明确输出：
1. 纯计算部分的加速比
2. 整体函数的实际加速比
3. 瓶颈分析（哪部分拖后腿、为什么）
￼
四、后续路线图（本期不实施，仅规划）
阶段
内容
状态
第一阶段
现有 3 个 Cython 模块编译落地 + fallback 验证
📋 本期实施
第二阶段
硬件检测底层 syscall 下沉 C 实现
⏳ 后置
第三阶段
向量检索 faiss C 扩展接入
⏳ 后置
第四阶段
节点遍历、压缩计算等热点路径 C 化
⏳ 后置
￼
五、决策记录（小林已拍板，2026-08-31）
决策点
结论
第一步范围
✅ 先编译落地 3 个已有模块（不做硬件检测下沉 C）
性能基准方式
✅ 实测真实倍率（timeit），达标值振荡场≥3x/编解码≥2.5x/共振≥2x
灰度开关
✅ 新增 use_cython_extensions（默认 False，保守灰度），统一控制所有扩展模块加载
双平台兼容
✅ 构建脚本必须同时支持 Windows（.pyd）与 Linux（.so）
诚实报告
✅ 不凑数，拆解纯计算/整体加速比 + 瓶颈分析
六、实施顺序（按风险从低到高，每步验收通过再进下一步）
1. 安装 Cython 编译环境，验证工具链可用
2. 修复 setup_cython.py 跨平台构建脚本（.pyd/.so 双平台）
3. 编译 3 个 .pyx 生成扩展文件
4. 逐函数正确性断言验证（100% 通过才进下一步）
5. 性能基准测试，输出真实倍率 + 拆解 + 瓶颈分析
6. 配置开关 use_cython_extensions + fallback 降级验证
7. 双平台（Windows/Linux）编译验证（Windows 留待后续，现有 .pyd 已覆盖）
￼
七、观测性埋点
埋点
说明
模块加载日志
Cython加速模块已加载 (_xxx_cy)（已有）/ 未编译降级日志（已有）
开关状态日志
use_cython_extensions 开启/关闭时记录，便于灰度排查
性能耗时统计
benchmark 脚本输出每模块「Python 耗时 / Cython 耗时 / 倍率 / 纯计算倍率 / 瓶颈」
￼
八、落地记录（2026-08-31）
8.1 改动清单
文件
改动
config.py
新增 use_cython_extensions（默认 False，保守灰度）
nucleus/pulse/setup_cython.py
跨平台修复：.pyd（Windows）/ .so（Linux）双平台 glob + 复制
nucleus/field/OscillonField.py
加载逻辑统一受 use_cython_extensions 控制
nucleus/synapsys/ResonanceEngine.py
同上
nucleus/pulse/FrequencyCodec.py
encode/encode_batch 统一受开关控制
tools/verify_cython_parity.py
新增：正确性验证（四类用例 + 三维断言）
tools/benchmark_cython.py
新增：性能基准（环境标注 + 瓶颈拆解）
8.2 编译产物
模块
产物
状态
_oscillon_cy
nucleus/field/_oscillon_cy.cpython-311-x86_64-linux-gnu.so
✅ 编译成功
_frequency_codec_cy
nucleus/pulse/_frequency_codec_cy.cpython-311-x86_64-linux-gnu.so
✅ 编译成功
_resonance_cy
nucleus/synapsys/_resonance_cy.cpython-311-x86_64-linux-gnu.so
✅ 编译成功
8.3 验收结果
验收项
结果
编译成功
✅ 3 个 .so 生成，import 成功
正确性
✅ 35 项全通过（正常/边界/零值/极值 + 值/类型/异常三维）
性能
✅ 振荡场 4.91x、五维共振 7.68x（达标）；❌ 频率编解码 1.19x（未达标，有瓶颈分析）
兼容性
✅ 删除 .so 自动 fallback；开关关闭零回归
内存占用
✅ 增量 3.81MB（≤5MB）
8.4 性能基准实测数据（诚实报告）
测试环境：CPU x86_64（32 核，2250MHz）、Python 3.11.1、测试规模 200000 次调用。
基准项
Python(ms)
Cython(ms)
加速比
目标
达标
振荡场 calculate_resonance（纯数值）
58.416
11.889
4.91x
≥3x
✅
频率编解码 encode（含 MD5 哈希）
467.235
391.995
1.19x
≥2.5x
❌
五维共振 calc_memory_dim（纯数值）
100.174
13.051
7.68x
≥2x
✅
瓶颈分析（encode 未达标原因）：
• encode_cy 中 hashlib.md5 属 Python 内置对象，调用时强制持 GIL，无法放入 nogil 块加速；
• MD5 哈希占总耗时比例极高，故整体加速比被拖低到 1.19x；
• 纯数值模块（振荡场/共振）加速比达 4.91x/7.68x，证明 C 化对纯数值计算显著有效；
• 若要提升 encode 整体加速比，需将 MD5 哈希替换为 C 实现，但会改变频率签名，需谨慎评估全库兼容性（当前哈希算法固定为 MD5，改动会导致全库签名失效）。
结论：四期第一阶段「编译落地 + fallback 验证」达成核心目标——混合架构链路已打通，纯数值计算路径获得 4.9~7.7x 加速，含 Python 对象交互的路径（MD5）如实标注未达标的客观瓶颈，为后续优化指明方向。