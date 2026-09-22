"""
H3C 无线控制器 SNMP 采集器
针对 H3C Comware 7.x WLAN MIB (hh3cDot11, enterprise 25506.2.75)
独立于 Aruba 采集器，输出格式兼容 db_store.save_poll_result()
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity, get_cmd, bulk_cmd,
)

logger = logging.getLogger("nms.h3c_poller")

H3C_ENTERPRISE = "1.3.6.1.4.1.25506"
HH3C_DOT11 = f"{H3C_ENTERPRISE}.2.75"

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

OID_AC_ONLINE_AP = f"{HH3C_DOT11}.1.1.2.4.0"
OID_AC_STA_COUNT = f"{HH3C_DOT11}.1.1.3.6.0"

# WlanDev 表 (在线 AP 的 IP/MAC/名称, 索引 = MAC 字符串)
WLAN_DEV_TABLE = f"{HH3C_DOT11}.2.1.1.1"
WLAN_DEV_COL_IP = 2       # 4字节 hex -> IP 地址
WLAN_DEV_COL_MAC = 3      # 6字节 hex -> MAC 地址
WLAN_DEV_COL_NAME = 5     # AP 名称 (字符串)

AP_TABLE_BASE = f"{HH3C_DOT11}.4.3.1.1"
AP_COL_SERIAL = 2
AP_COL_MODEL = 3
AP_COL_STATUS = 5
AP_COL_NAME = 7
AP_COL_ID = 15

H3C_ENTITY_EXT = f"{H3C_ENTERPRISE}.2.6.1.1.1.1"
OID_ENTITY_CPU = f"{H3C_ENTITY_EXT}.4"
OID_ENTITY_TEMP = f"{H3C_ENTITY_EXT}.6"
OID_ENTITY_MEM_PCT = f"{H3C_ENTITY_EXT}.8"

OID_ENT_PHYS_CLASS = "1.3.6.1.2.1.47.1.1.1.1.5"

AP_STATUS_RUNNING = 3


def decode_h3c_ap_index(suffix: str) -> str:
    """解码 H3C 长度前缀 OID 索引为 AP 名称"""
    parts = suffix.split(".")
    result = []
    idx = 0
    while idx < len(parts):
        try:
            length = int(parts[idx])
            idx += 1
            if length == 0 or idx + length > len(parts):
                break
            chars = parts[idx:idx + length]
            idx += length
            name = "".join(chr(int(c)) for c in chars)
            result.append(name)
        except (ValueError, IndexError):
            break
    return ".".join(result) if result else suffix


def _bytes_to_ip(raw: bytes) -> str:
    """4字节 hex -> x.x.x.x"""
    if len(raw) == 4:
        return ".".join(str(b) for b in raw)
    return ""


def _bytes_to_mac(raw: bytes) -> str:
    """6字节 hex -> xx:xx:xx:xx:xx:xx"""
    if len(raw) == 6:
        return ":".join(f"{b:02x}" for b in raw)
    return ""


class H3CPoller:
    """H3C 无线控制器 SNMP 采集器 (AP 级别)"""

    def __init__(self, config):
        self.timeout = getattr(config, "SNMP_TIMEOUT", 10)
        self.retries = getattr(config, "SNMP_RETRIES", 3)
        self.retry_delay = getattr(config, "SNMP_RETRY_DELAY", 2)

    async def _snmp_get(self, ip: str, oid: str, community: str) -> Optional[str]:
        engine = SnmpEngine()
        for attempt in range(self.retries):
            try:
                transport = await UdpTransportTarget.create(
                    (ip, 161), timeout=self.timeout, retries=0
                )
                ei, es, eidx, vbt = await get_cmd(
                    engine, CommunityData(community),
                    transport, ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if ei or es:
                    break
                if vbt:
                    return str(vbt[0][1])
            except asyncio.TimeoutError:
                logger.warning(f"SNMP GET {ip} {oid}: timeout ({attempt+1}/{self.retries})")
            except Exception as e:
                logger.error(f"SNMP GET {ip} {oid}: {e}")
            if attempt < self.retries - 1:
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
        return None

    async def _snmp_walk(self, ip: str, oid_prefix: str, community: str,
                         max_entries: int = 5000) -> List[Tuple[str, str]]:
        engine = SnmpEngine()
        transport = await UdpTransportTarget.create(
            (ip, 161), timeout=self.timeout, retries=1
        )
        results = []
        current_oid = oid_prefix
        strict_prefix = oid_prefix + "."

        for _ in range(200):
            if len(results) >= max_entries:
                break
            try:
                ei, es, eidx, vbt = await bulk_cmd(
                    engine, CommunityData(community),
                    transport, ContextData(),
                    0, 50,
                    ObjectType(ObjectIdentity(current_oid)),
                )
                if ei or not vbt:
                    break
                done = False
                last_oid = current_oid
                for vb in vbt:
                    oid_str = str(vb[0])
                    val = str(vb[1])
                    if not oid_str.startswith(strict_prefix):
                        done = True
                        break
                    results.append((oid_str, val))
                    last_oid = oid_str
                    if len(results) >= max_entries:
                        done = True
                        break
                if done:
                    break
                current_oid = last_oid
            except asyncio.TimeoutError:
                logger.warning(f"SNMP WALK {ip} {oid_prefix}: timeout")
                break
            except Exception as e:
                logger.error(f"SNMP WALK {ip} {oid_prefix}: {e}")
                break
        return results

    async def _snmp_walk_raw(self, ip: str, oid_prefix: str, community: str,
                             max_entries: int = 5000) -> List[Tuple[str, bytes]]:
        """Walk SNMP 子树, 返回 raw bytes (用于 IP/MAC 等二进制数据)"""
        engine = SnmpEngine()
        transport = await UdpTransportTarget.create(
            (ip, 161), timeout=self.timeout, retries=1
        )
        results = []
        current_oid = oid_prefix
        strict_prefix = oid_prefix + "."

        for _ in range(200):
            if len(results) >= max_entries:
                break
            try:
                ei, es, eidx, vbt = await bulk_cmd(
                    engine, CommunityData(community),
                    transport, ContextData(),
                    0, 50,
                    ObjectType(ObjectIdentity(current_oid)),
                )
                if ei or not vbt:
                    break
                done = False
                last_oid = current_oid
                for vb in vbt:
                    oid_str = str(vb[0])
                    if not oid_str.startswith(strict_prefix):
                        done = True
                        break
                    raw = bytes(vb[1])
                    results.append((oid_str, raw))
                    last_oid = oid_str
                    if len(results) >= max_entries:
                        done = True
                        break
                if done:
                    break
                current_oid = last_oid
            except asyncio.TimeoutError:
                logger.warning(f"SNMP WALK_RAW {ip} {oid_prefix}: timeout")
                break
            except Exception as e:
                logger.error(f"SNMP WALK_RAW {ip} {oid_prefix}: {e}")
                break
        return results

    async def poll_controller(self, controller_ip: str, region_name: str,
                              region_cfg: dict = None) -> Dict[str, Any]:
        region_cfg = region_cfg or {}
        community = region_cfg.get("snmp_community", "demo-community-h3c")

        result = {
            "region": region_name,
            "controller_ip": controller_ip,
            "timestamp": datetime.now().isoformat(),
            "aps": {},
            "radios": {},
            "controller": {},
            "success": False,
        }

        start_time = time.time()

        try:
            logger.info(f"开始轮询 H3C [{region_name}] -> {controller_ip} (community={community})")

            tasks = [
                self._snmp_get(controller_ip, OID_SYS_NAME, community),
                self._snmp_get(controller_ip, OID_SYS_DESCR, community),
                self._snmp_get(controller_ip, OID_SYS_UPTIME, community),
                self._snmp_get(controller_ip, OID_AC_ONLINE_AP, community),
                self._snmp_get(controller_ip, OID_AC_STA_COUNT, community),
                self._snmp_walk(controller_ip, f"{AP_TABLE_BASE}.{AP_COL_NAME}", community, 3000),
                self._snmp_walk(controller_ip, f"{AP_TABLE_BASE}.{AP_COL_SERIAL}", community, 3000),
                self._snmp_walk(controller_ip, f"{AP_TABLE_BASE}.{AP_COL_MODEL}", community, 3000),
                self._snmp_walk(controller_ip, f"{AP_TABLE_BASE}.{AP_COL_STATUS}", community, 3000),
                # WlanDev 表: IP/MAC (raw bytes), AP 名称 (str)
                self._snmp_walk_raw(controller_ip, f"{WLAN_DEV_TABLE}.{WLAN_DEV_COL_IP}", community, 3000),
                self._snmp_walk_raw(controller_ip, f"{WLAN_DEV_TABLE}.{WLAN_DEV_COL_MAC}", community, 3000),
                self._snmp_walk(controller_ip, f"{WLAN_DEV_TABLE}.{WLAN_DEV_COL_NAME}", community, 3000),
            ]
            (sys_name, sys_descr, sys_uptime, online_ap, sta_count,
             name_walk, serial_walk, model_walk, status_walk,
             ip_walk_raw, mac_walk_raw, dev_name_walk) = await asyncio.gather(*tasks)

            ap_data = self._parse_ap_table(name_walk, serial_walk, model_walk, status_walk)

            # 构建 WlanDev IP/MAC 映射 (AP 名称 -> IP/MAC)
            ip_mac_map = self._parse_wlandev_table(ip_walk_raw, mac_walk_raw, dev_name_walk)

            for ap_name, ap_info in ap_data.items():
                ip_mac = ip_mac_map.get(ap_name, {})
                result["aps"][ap_name] = {
                    "name": ap_name,
                    "mac": ip_mac.get("mac", ""),
                    "ip": ip_mac.get("ip", ""),
                    "model": ap_info.get("model", ""),
                    "serial": ap_info.get("serial", ""),
                    "status": ap_info.get("status", 1),
                    "active_md": controller_ip,
                    "clients": 0,
                    "rx_throughput": 0,
                    "tx_throughput": 0,
                }

            result["controller"]["name"] = sys_name or controller_ip
            result["controller"]["sys_descr"] = sys_descr or ""
            result["controller"]["uptime"] = sys_uptime or ""
            result["controller"]["ap_count"] = int(online_ap) if online_ap and online_ap.isdigit() else len(ap_data)
            result["controller"]["sta_count"] = int(sta_count) if sta_count and sta_count.isdigit() else 0

            result["success"] = True
            elapsed = time.time() - start_time
            logger.info(
                f"[{region_name}] H3C 轮询完成: "
                f"{len(result['aps'])} APs, "
                f"AC online={online_ap}, STA={sta_count}, "
                f"耗时 {elapsed:.1f}s"
            )

        except Exception as e:
            logger.error(f"[{region_name}] H3C 轮询失败: {e}")
            result["error"] = str(e)

        return result

    def _parse_ap_table(self, name_walk, serial_walk, model_walk, status_walk) -> Dict[str, Dict]:
        ap_data: Dict[str, Dict] = {}

        def _extract_index(oid: str, col: int) -> str:
            prefix = f"{AP_TABLE_BASE}.{col}."
            if oid.startswith(prefix):
                return oid[len(prefix):]
            return ""

        def _get_or_create(ap_name: str) -> Dict:
            if ap_name not in ap_data:
                ap_data[ap_name] = {
                    "name": ap_name,
                    "serial": "",
                    "model": "",
                    "status": 1,
                }
            return ap_data[ap_name]

        for oid_str, val in name_walk:
            idx = _extract_index(oid_str, AP_COL_NAME)
            if not idx:
                continue
            ap_name = val
            entry = _get_or_create(ap_name)
            entry["name"] = val

        for oid_str, val in serial_walk:
            idx = _extract_index(oid_str, AP_COL_SERIAL)
            if not idx:
                continue
            ap_name = decode_h3c_ap_index(idx)
            entry = _get_or_create(ap_name)
            entry["serial"] = val

        for oid_str, val in model_walk:
            idx = _extract_index(oid_str, AP_COL_MODEL)
            if not idx:
                continue
            ap_name = decode_h3c_ap_index(idx)
            entry = _get_or_create(ap_name)
            entry["model"] = val

        for oid_str, val in status_walk:
            idx = _extract_index(oid_str, AP_COL_STATUS)
            if not idx:
                continue
            ap_name = decode_h3c_ap_index(idx)
            entry = _get_or_create(ap_name)
            try:
                status_val = int(val)
                entry["status"] = 1 if status_val == AP_STATUS_RUNNING else 0
            except ValueError:
                entry["status"] = 1

        return ap_data

    def _parse_wlandev_table(self, ip_walk_raw, mac_walk_raw, dev_name_walk) -> Dict[str, Dict[str, str]]:
        """解析 WlanDev 表, 构建 AP 名称 -> {ip, mac} 映射。
        三个 walk 共享相同的 MAC 字符串索引。"""
        ip_map: Dict[str, str] = {}
        mac_map: Dict[str, str] = {}
        name_map: Dict[str, str] = {}

        ip_prefix = f"{WLAN_DEV_TABLE}.{WLAN_DEV_COL_IP}."
        for oid_str, raw in ip_walk_raw:
            if oid_str.startswith(ip_prefix):
                mac_idx = decode_h3c_ap_index(oid_str[len(ip_prefix):])
                ip = _bytes_to_ip(raw)
                if ip:
                    ip_map[mac_idx] = ip

        mac_prefix = f"{WLAN_DEV_TABLE}.{WLAN_DEV_COL_MAC}."
        for oid_str, raw in mac_walk_raw:
            if oid_str.startswith(mac_prefix):
                mac_idx = decode_h3c_ap_index(oid_str[len(mac_prefix):])
                mac = _bytes_to_mac(raw)
                if mac:
                    mac_map[mac_idx] = mac

        name_prefix = f"{WLAN_DEV_TABLE}.{WLAN_DEV_COL_NAME}."
        for oid_str, val in dev_name_walk:
            if oid_str.startswith(name_prefix):
                mac_idx = decode_h3c_ap_index(oid_str[len(name_prefix):])
                name_map[mac_idx] = val

        result: Dict[str, Dict[str, str]] = {}
        for mac_idx, ap_name in name_map.items():
            result[ap_name] = {
                "ip": ip_map.get(mac_idx, ""),
                "mac": mac_map.get(mac_idx, ""),
            }

        logger.info(
            f"WlanDev 解析: {len(result)} AP (IP={len(ip_map)}, MAC={len(mac_map)}, Name={len(name_map)})"
        )
        return result


class H3CControllerPoller:
    """H3C 控制器性能采集器 (CPU/内存/温度)"""

    def __init__(self, config):
        self.timeout = 5
        self.retries = 2

    async def poll_one(self, ip: str, community: str, label: str = "") -> Optional[Dict[str, Any]]:
        result = {
            "ip": ip,
            "label": label or ip,
            "hostname": "",
            "model": "",
            "role": "ac",
            "cpu_pct": 0.0,
            "mem_pct": 0.0,
            "temperature": "N/A",
            "ap_count": 0,
            "sta_count": 0,
            "pkt_loss": 0.0,
            "sys_uptime": 0,
            "sys_descr": "",
            "cpu_cores": [],
        }

        engine = SnmpEngine()

        try:
            transport = await UdpTransportTarget.create(
                (ip, 161), timeout=self.timeout, retries=self.retries
            )
            community_data = CommunityData(community)

            for oid, field in [(OID_SYS_NAME, "hostname"), (OID_SYS_DESCR, "sys_descr"), (OID_SYS_UPTIME, "sys_uptime")]:
                ei, es, eidx, vbt = await get_cmd(
                    engine, community_data, transport, ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if not ei and vbt:
                    val = str(vbt[0][1])
                    if field == "sys_uptime":
                        try:
                            result[field] = int(val)
                        except ValueError:
                            pass
                    else:
                        result[field] = val

            sys_descr = result.get("sys_descr", "")
            if "LSUM1WCMX40RT" in sys_descr:
                result["model"] = "LSUM1WCMX40RT"
            elif "LSUM1" in sys_descr:
                result["model"] = "H3C WLAN Module"
            else:
                result["model"] = "H3C AC"

            entity_indices = []
            curr_oid = OID_ENT_PHYS_CLASS
            for _ in range(10):
                ei, es, eidx, vbt = await bulk_cmd(
                    engine, community_data, transport, ContextData(),
                    0, 25, ObjectType(ObjectIdentity(curr_oid)),
                )
                if ei or not vbt:
                    break
                done = False
                for vb in vbt:
                    oid_str = str(vb[0])
                    if OID_ENT_PHYS_CLASS not in oid_str:
                        done = True
                        break
                    try:
                        idx = int(oid_str.split(".")[-1])
                        cls = int(str(vb[1]))
                        if cls == 9:
                            entity_indices.append(idx)
                    except ValueError:
                        pass
                    curr_oid = oid_str
                if done:
                    break

            main_entity = min(entity_indices) if entity_indices else 97

            for oid, field in [
                (f"{OID_ENTITY_CPU}.{main_entity}", "cpu_pct"),
                (f"{OID_ENTITY_MEM_PCT}.{main_entity}", "mem_pct"),
                (f"{OID_ENTITY_TEMP}.{main_entity}", "temperature"),
            ]:
                ei, es, eidx, vbt = await get_cmd(
                    engine, community_data, transport, ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if not ei and vbt:
                    val = str(vbt[0][1])
                    if field in ("cpu_pct", "mem_pct"):
                        try:
                            result[field] = float(val)
                        except ValueError:
                            pass
                    elif field == "temperature":
                        try:
                            temp_val = float(val)
                            result[field] = str(temp_val) if temp_val > 0 else "N/A"
                        except ValueError:
                            result[field] = val if val else "N/A"

            for oid, field in [(OID_AC_ONLINE_AP, "ap_count"), (OID_AC_STA_COUNT, "sta_count")]:
                ei, es, eidx, vbt = await get_cmd(
                    engine, community_data, transport, ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if not ei and vbt:
                    try:
                        result[field] = int(str(vbt[0][1]))
                    except ValueError:
                        pass

            logger.info(
                f"[H3C {ip}] CPU={result['cpu_pct']}%, "
                f"Mem={result['mem_pct']}%, Temp={result['temperature']}C, "
                f"AP={result['ap_count']}, STA={result['sta_count']}"
            )

        except Exception as e:
            logger.error(f"[H3C {ip}] 性能采集失败: {e}")

        return result
