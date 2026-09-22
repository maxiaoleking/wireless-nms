"""
SNMP 模拟数据生成器
测试环境无真实设备时, 生成模拟 AP 数据用于前端开发和功能验证
"""
import random
import hashlib
from datetime import datetime
from typing import Dict, List, Any

# AP 型号池
AP_MODELS = ["AP-MODEL-1", "AP-MODEL-2", "AP-MODEL-3", "AP-MODEL-4", "AP-MODEL-5", "AP-MODEL-6"]

# 区域 -> AP 名称前缀 -> 数量映射
REGION_AP_MAP = {
    "MD-1": {
        "prefix": "JX",      # 教学楼
        "count": 1104,
        "buildings": ["JX-A", "JX-B", "JX-C"],
    },
    "MD-2": {
        "prefix": "LIB",     # 图书馆
        "count": 1085,
        "buildings": ["LIB-1", "LIB-2"],
    },
    "MD-3": {
        "prefix": "SY",      # 实验楼
        "count": 890,
        "buildings": ["SY-A", "SY-B"],
    },
    "MD-4": {
        "prefix": "XZ",      # 行政楼
        "count": 650,
        "buildings": ["XZ-A", "XZ-B"],
    },
    "H3C-AC": {
        "prefix": "GY",      # 公寓楼
        "count": 651,
        "buildings": ["GY-1", "GY-2", "GY-3"],
    },
}

# 楼层
FLOORS = ["1F", "2F", "3F", "4F", "5F"]


def _generate_mac(index: int) -> str:
    """生成模拟 MAC 地址"""
    h = hashlib.md5(f"ap-{index}".encode()).hexdigest()[:10]
    return f"1c:30:03:{h[0:2]}:{h[2:4]}:{h[4:6]}"


def _generate_serial(region: str, index: int) -> str:
    """生成模拟序列号"""
    return f"CN{region[:2]}{index:06d}"


def _generate_ip(region: str, index: int) -> str:
    """生成模拟 IP 地址"""
    region_subnets = {
        "MD-1": "10.100.20",
        "MD-2": "10.100.30",
        "MD-3": "10.100.40",
        "MD-4": "10.100.50",
        "H3C-AC": "10.100.60",
    }
    subnet = region_subnets.get(region, "10.100.99")
    return f"{subnet}.{(index % 254) + 1}"


def generate_mock_aps(total_count: int = 4380,
                      offline_ratio: float = 0.005) -> List[Dict[str, Any]]:
    """
    生成模拟 AP 数据

    Args:
        total_count: AP 总数
        offline_ratio: 离线比例

    Returns:
        List[Dict]: AP 数据列表
    """
    aps = []
    ap_index = 0

    for region, cfg in REGION_AP_MAP.items():
        prefix = cfg["prefix"]
        count = cfg["count"]
        buildings = cfg["buildings"]

        for i in range(count):
            ap_index += 1
            building = buildings[i % len(buildings)]
            floor = FLOORS[i % len(FLOORS)]
            seq = f"{i + 1:03d}"

            # 随机决定是否离线
            is_offline = random.random() < offline_ratio

            # 生成射频信息 (每个 AP 2 个 radio: dot11a 5G + dot11g 2.4G)
            radios = []
            for radio_num, radio_type in enumerate(["dot11a", "dot11g"]):
                channel = random.choice([36, 40, 44, 48, 149, 153, 157, 161]) \
                    if radio_type == "dot11a" else random.choice([1, 6, 11])
                radios.append({
                    "radio_number": radio_num,
                    "type": radio_type,
                    "channel": channel,
                    "tx_power": random.randint(10, 23),
                    "noise_floor": random.randint(-100, -65),
                    "clients": random.randint(0, 40) if not is_offline else 0,
                })

            total_clients = sum(r["clients"] for r in radios)
            rx_throughput = round(random.uniform(0.1, 50.0), 2) if not is_offline else 0
            tx_throughput = round(random.uniform(0.1, 30.0), 2) if not is_offline else 0

            ap = {
                "name": f"{prefix}-{building}-{floor}-{seq}",
                "mac_address": _generate_mac(ap_index),
                "ip_address": _generate_ip(region, ap_index),
                "model": random.choice(AP_MODELS),
                "serial_number": _generate_serial(region, ap_index),
                "region": region,
                "building": building,
                "floor": floor,
                "status": 0 if is_offline else 1,
                "controller": region,
                "total_clients": total_clients,
                "rx_throughput": rx_throughput,
                "tx_throughput": tx_throughput,
                "radios": radios,
                "last_seen": datetime.now().isoformat(),
            }
            aps.append(ap)

    return aps


def generate_mock_poll_result(region: str, aps: List[Dict]) -> Dict[str, Any]:
    """
    将模拟 AP 数据转换为轮询结果格式 (与 SNMP poller 输出一致)

    Args:
        region: 区域名称
        aps: 该区域的 AP 列表

    Returns:
        Dict: 轮询结果
    """
    return {
        "region": region,
        "controller_ip": REGION_AP_MAP.get(region, {}).get("controller_ip", "unknown"),
        "timestamp": datetime.now().isoformat(),
        "aps": {
            ap["name"]: {
                "name": ap["name"],
                "mac": ap["mac_address"],
                "ip": ap["ip_address"],
                "model": ap["model"],
                "serial": ap["serial_number"],
                "status": ap["status"],
                "clients": ap["total_clients"],
                "rx_throughput": ap["rx_throughput"],
                "tx_throughput": ap["tx_throughput"],
            }
            for ap in aps
        },
        "radios": {
            f"{ap['name']}_r{r['radio_number']}": {
                "ap_name": ap["name"],
                "radio_number": r["radio_number"],
                "type": r["type"],
                "channel": r["channel"],
                "tx_power": r["tx_power"],
                "clients": r["clients"],
                "noise_floor": r["noise_floor"],
            }
            for ap in aps
            for r in ap.get("radios", [])
        },
        "controller": {
            "name": region,
            "uptime": "30 days, 12:34:56",
            "status": 1,
        },
        "success": True,
    }
