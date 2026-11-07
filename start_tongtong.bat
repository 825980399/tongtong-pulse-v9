@echo off
REM ===================================================================
REM  曈曈 PulseNet v10 · 启动脚本（D 盘 Python 3.14t free-threading）
REM
REM  解释器：D:\Program Files\Python314t\python3.14t.exe
REM  依赖包：D:\Program Files\Python314t\Lib\site-packages
REM  缓存/临时：D:\PythonEnv  （pip 缓存 / 安装临时文件 / 浏览器内核）
REM
REM  设计要点：
REM    1) PYTHONNOUSERSITE=1 —— 禁止读取 C 盘
REM       C:\Users\<用户>\AppData\Roaming\Python\Python314t\site-packages
REM       否则 pip 一旦回落到 user site，包就又散到 C 盘了。
REM    2) TEMP/TMP 指向 D 盘 —— 运行时产生的临时文件不进 C 盘。
REM    3) PIP_CACHE_DIR 指向 D 盘 —— 已写进 pip.ini，这里再兜一层。
REM    4) 自重启不经过本脚本
REM       曈曈自重启是另开一个 python 窗口（CREATE_NEW_CONSOLE），
REM       不重进本脚本；启动后关掉本窗口不影响运行中的曈曈。
REM    5) 停曈曈请在对话里输入 quit / exit，或直接关窗口。
REM       不要用 Ctrl+C：cmd 会额外追问「终止批处理操作吗(Y/N)」。
REM ===================================================================
setlocal

set "ROOT=%~dp0"
set "PY=D:\Program Files\Python314t\python3.14t.exe"

if not exist "%PY%" (
    echo [错误] 未找到 Python 解释器：
    echo        %PY%
    echo        请确认 D 盘 Python 3.14t 是否还在该路径。
    pause
    exit /b 1
)

REM --- 环境隔离：不读 C 盘 user site-packages ---
set "PYTHONNOUSERSITE=1"

REM --- 缓存 / 临时目录全部落到 D 盘 ---
set "PIP_CACHE_DIR=D:\PythonEnv\pip-cache"
set "PLAYWRIGHT_BROWSERS_PATH=D:\PythonEnv\playwright"
set "TEMP=D:\PythonEnv\tmp"
set "TMP=D:\PythonEnv\tmp"

REM --- 切到项目根目录（data/ logs/ models/ 全是相对路径）---
cd /d "%ROOT%"

echo ===================================================================
echo  曈曈 PulseNet v10 启动中
echo -------------------------------------------------------------------
echo  解释器  : %PY%
echo  工作目录: %CD%
echo  临时目录: %TEMP%
echo ===================================================================
echo.

"%PY%" main.py
set "RC=%ERRORLEVEL%"

echo.
echo ===================================================================
echo  曈曈已退出，返回码 = %RC%
echo ===================================================================
if not "%RC%"=="0" pause

endlocal & exit /b %RC%
