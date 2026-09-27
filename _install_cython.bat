@echo off
REM 安装优化版 Cython 扩展（6 个模块，框架停止后运行）
REM 用法：停止曈曈框架 → 运行本脚本 → 重新启动框架
REM ★五期：_frequency_codec_cy 已更新为「MD5 C 化」版本（encode 加速 1.02x → 1.56x，签名与 hashlib 逐字节一致）
set ROOT=%~dp0
copy /y "%ROOT%\build\cython_install\_frequency_codec_cy.cp312-win_amd64.pyd" "%ROOT%\nucleus\pulse\" >nul
copy /y "%ROOT%\build\cython_install\_oscillon_cy.cp312-win_amd64.pyd" "%ROOT%\nucleus\field\" >nul
copy /y "%ROOT%\build\cython_install\_resonance_cy.cp312-win_amd64.pyd" "%ROOT%\nucleus\synapsys\" >nul
copy /y "%ROOT%\build\cython_install\_cosine_cpu_cy.cp312-win_amd64.pyd" "%ROOT%\nucleus\gpu\" >nul
REM ★v27新增：第5个模块 _topk_retrieve_cy（批量余弦+top-k一体化，向量检索热路径）
copy /y "%ROOT%\build\cython_install\_topk_retrieve_cy.cp312-win_amd64.pyd" "%ROOT%\nucleus\reasoning\" >nul
REM ★v28新增：第6个模块 _inner_world_math_cy（PulseInnerWorld纯数值计算热路径：分支权重/信任递减/加权融合/Softmax/余弦）
copy /y "%ROOT%\build\cython_install\_inner_world_math_cy.cp312-win_amd64.pyd" "%ROOT%\nucleus\reasoning\" >nul
echo DONE: 6 个 Cython 模块已替换为优化版
