"""
全自研无线网管平台 - 配置文件
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Flask
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5577
DEBUG = False

# SQLite
SQLITE_DB_PATH = os.path.join(BASE_DIR, "data", "nms.sqlite")

# SNMP 全局默认 (可被区域级配置覆盖)
SNMP_VERSION = "2c"
SNMP_COMMUNITY = "demo-community"      # 默认团体名
SNMP_PORT = 161
SNMP_TIMEOUT = 10                 # 单设备超时 (秒)
SNMP_CONCURRENCY = 50             # 并发采集数
SNMP_RETRIES = 3                  # 重试次数
SNMP_RETRY_DELAY = 2              # 重试间隔 (秒, 指数退避基数)

# 轮询调度
POLL_INTERVAL_MINUTES = 15        # 轮询周期 (分钟)

# 前端刷新
CLIENT_REFRESH_SECONDS = 30

# 模拟模式 (True=模拟数据, False=真实SNMP采集)
MOCK_MODE = False

# ============================================================
# 区域定义 (对应控制器/集群)
#
# 字段说明:
#   controller_ip  - 采集目标 IP (集群时为 Active MM VIP)
#   snmp_community - 该区域的 SNMP 团体名 (覆盖全局默认)
#   is_cluster     - 是否为 MM 集群架构
#   cluster_nodes  - 集群所有节点 IP (用于 Active 检测)
#   active_check   - 启用 Active MM 自动检测
# ============================================================
REGIONS = {
    "MM-Cluster": {
        "id": "mm-cluster",
        "name": "MM 集群 (203.0.113.1)",
        "controller_ip": "203.0.113.1",        # MM 管理 IP
        "snmp_community": "demo-community",
        "is_cluster": True,
        "cluster_nodes": ["203.0.113.11", "203.0.113.12", "203.0.113.13", "203.0.113.14"],
        "active_check": True,                     # 自动检测 Active MM
    },

    # H3C 无线控制器 (独立厂商)
    "H3C-AC": {
        "id": "h3c-ac",
        "name": "H3C AC (198.51.100.253)",
        "controller_ip": "198.51.100.253",
        "snmp_community": "demo-community-h3c",
        "vendor": "h3c",
        "is_cluster": False,
        "active_check": False,
    },
}

# InfluxDB (生产环境启用)
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = "wireless-nms"
INFLUXDB_BUCKET = "ap_metrics"
INFLUXDB_ENABLED = False

# 日志
LOG_LEVEL = "INFO"
LOG_FILE = os.path.join(BASE_DIR, "data", "nms.log")
