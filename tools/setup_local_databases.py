# -*- coding: utf-8 -*-
"""主线第72批 T1：本地数据库环境搭建脚本（Neo4j + InfluxDB）。

职责
----
- 指导并辅助用户在**本机**准备 Neo4j / InfluxDB 环境，用于真实端到端验证。
- 提供驱动安装（``pip install neo4j influxdb-client``）、健康检查、约束 / bucket 初始化。
- **不自动安装数据库服务本身**（Neo4j / InfluxDB 为独立服务，由用户按官方文档安装启动）；
  本脚本只负责驱动与连接可用性校验，以及建库 / 建约束等可脚本化步骤。

安全约定
--------
- 所有密码 / Token 从环境变量读取（``NEO4J_PASSWORD`` / ``INFLUXDB_TOKEN``），**绝不硬编码**。
- 任一依赖缺失时给出清晰指引并安全退出，不抛未捕获异常。

用法示例
--------
    python tools/setup_local_databases.py install-drivers
    python tools/setup_local_databases.py check-neo4j
    python tools/setup_local_databases.py check-influxdb
    python tools/setup_local_databases.py init-neo4j
    python tools/setup_local_databases.py init-influxdb
    python tools/setup_local_databases.py all
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 默认连接参数（与 config.py 保持一致，可被环境变量覆盖）
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "tongtong")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "tongtong")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "pulse_metrics")


def _ok(msg):
    print("[OK]   %s" % msg)


def _warn(msg):
    print("[WARN] %s" % msg)


def _fail(msg):
    print("[FAIL] %s" % msg)


def install_drivers():
    """安装 neo4j / influxdb-client 驱动。"""
    print("== 安装驱动 neo4j + influxdb-client ==")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "neo4j", "influxdb-client"]
        )
    except subprocess.CalledProcessError as e:
        _fail("驱动安装失败：%s" % e)
        return 2
    # 校验导入
    try:
        import neo4j  # noqa: F401
        import influxdb_client  # noqa: F401
        _ok("neo4j=%s influxdb_client=%s 导入成功" % (
            getattr(neo4j, "__version__", "?"),
            getattr(influxdb_client, "__version__", "?"),
        ))
    except Exception as e:  # noqa: BLE001
        _fail("驱动安装后导入失败：%s: %s" % (type(e).__name__, e))
        return 2
    return 0


def check_neo4j():
    """检查 Neo4j 驱动与本地服务可用性。"""
    print("== 检查 Neo4j (%s) ==" % NEO4J_URI)
    try:
        from neo4j import GraphDatabase
    except Exception as e:  # noqa: BLE001
        _fail("neo4j 驱动未安装：%s。请先运行 install-drivers。" % e)
        return 2
    if not NEO4J_PASSWORD:
        _warn("环境变量 NEO4J_PASSWORD 为空；若数据库已设密码请先导出。")
    try:
        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
            connection_acquisition_timeout=5,
        )
        with _driver.session(database=NEO4J_DATABASE) as _s:
            _rec = _s.run("RETURN 1 AS ok").single()
            _ok("Neo4j 服务可用，探测返回 %s" % (_rec["ok"] if _rec else None))
        _driver.close()
        return 0
    except Exception as e:  # noqa: BLE001
        _fail("Neo4j 连接失败：%s: %s" % (type(e).__name__, e))
        _warn("请确认 Neo4j Community/Enterprise 已启动且 bolt 端口可达。")
        return 2


def check_influxdb():
    """检查 InfluxDB 驱动与本地服务可用性。"""
    print("== 检查 InfluxDB (%s) ==" % INFLUXDB_URL)
    try:
        from influxdb_client import InfluxDBClient
    except Exception as e:  # noqa: BLE001
        _fail("influxdb-client 驱动未安装：%s。请先运行 install-drivers。" % e)
        return 2
    if not INFLUXDB_TOKEN:
        _warn("环境变量 INFLUXDB_TOKEN 为空；请先导出（influxd 启动时打印的 operator token）。")
    try:
        _client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        _health = _client.ping()
        if _health:
            _ok("InfluxDB 服务健康（ping 成功）")
            _client.close()
            return 0
        _fail("InfluxDB ping 返回非健康")
        _client.close()
        return 2
    except Exception as e:  # noqa: BLE001
        _fail("InfluxDB 连接失败：%s: %s" % (type(e).__name__, e))
        _warn("请确认 InfluxDB 2.x 已启动且 http 端口可达。")
        return 2


def init_neo4j():
    """初始化 Neo4j：建库 + 节点 ID 唯一约束 + 关系索引。"""
    print("== 初始化 Neo4j 约束与索引 ==")
    try:
        from neo4j import GraphDatabase
    except Exception as e:  # noqa: BLE001
        _fail("neo4j 驱动未安装：%s" % e)
        return 2
    try:
        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
            connection_acquisition_timeout=5,
        )
        with _driver.session(database=NEO4J_DATABASE) as _s:
            # 节点 ID 唯一约束（MERGE 幂等依赖）
            _s.run("CREATE CONSTRAINT IF NOT EXISTS "
                   "FOR (n:PulseNode) REQUIRE n.node_id IS UNIQUE")
            # 关系轻量索引（查询性能）
            _s.run("CREATE INDEX IF NOT EXISTS "
                   "FOR ()-[r:RELATED]-() ON r.rel_type")
            _ok("约束 PulseNode.node_id 唯一 + 关系索引 RELATED.rel_type 已创建")
        _driver.close()
        return 0
    except Exception as e:  # noqa: BLE001
        _fail("Neo4j 初始化失败：%s: %s" % (type(e).__name__, e))
        return 2


def init_influxdb():
    """初始化 InfluxDB：创建 bucket 与保留策略（raw 7d / 1m 30d / 1h 365d）。"""
    print("== 初始化 InfluxDB bucket 与保留策略 ==")
    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.bucket_api import BucketsApi
    except Exception as e:  # noqa: BLE001
        _fail("influxdb-client 驱动未安装：%s" % e)
        return 2
    try:
        _client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        _buckets: BucketsApi = _client.buckets_api()
        _found = _buckets.find_bucket_by_name(INFLUXDB_BUCKET)
        if _found is None:
            _buckets.create_bucket(bucket_name=INFLUXDB_BUCKET, org=INFLUXDB_ORG)
            _ok("bucket '%s' 已创建" % INFLUXDB_BUCKET)
        else:
            _ok("bucket '%s' 已存在，跳过创建" % INFLUXDB_BUCKET)
        # 备注：降采样保留策略（raw 7d / 1m 30d / 1h 365d）由 InfluxDB 任务(Downsampling)
        # 在服务端配置；脚本仅确保主 bucket 存在。详细策略见 InfluxDB 集成设计文档。
        _client.close()
        return 0
    except Exception as e:  # noqa: BLE001
        _fail("InfluxDB 初始化失败：%s: %s" % (type(e).__name__, e))
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="第72批 T1：本地数据库环境搭建")
    ap.add_argument("command", choices=[
        "install-drivers", "check-neo4j", "check-influxdb",
        "init-neo4j", "init-influxdb", "all",
    ])
    args = ap.parse_args(argv)

    rc = 0
    if args.command == "install-drivers":
        rc = install_drivers()
    elif args.command == "check-neo4j":
        rc = check_neo4j()
    elif args.command == "check-influxdb":
        rc = check_influxdb()
    elif args.command == "init-neo4j":
        rc = init_neo4j()
    elif args.command == "init-influxdb":
        rc = init_influxdb()
    elif args.command == "all":
        rc = install_drivers() or check_neo4j() or check_influxdb() \
            or init_neo4j() or init_influxdb()
    print("退出码: %d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
