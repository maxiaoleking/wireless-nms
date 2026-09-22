"""
SNMP 异步轮询引擎 (pysnmp 7.x)
基于 ArubaOS OID汇总文档 (v8.6.X) 的精确表 walk + 列解析

核心策略:
  - 精确 walk AP 表 (.5.2.1.4.1), Radio 表 (.5.2.1.5.1), 信道统计表 (.5.3.1.6.1)
  - 按列号解析, MAC 索引固定 6 字节
  - 每区域独立 SNMP community
  - MM 集群: 通过 wlsxSysExtSwitchRole 检测 Active 节点
"""
import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from lib.snmp_oids import (
    # 系统
    OID_SYS_NAME, OID_SYS_UPTIME,
    # Aruba 基础
    ARUBA_BASE,
    # AP 表
    OID_AP_TABLE, OID_AP_IP, OID_AP_NAME, OID_AP_GROUP,
    OID_AP_MODEL, OID_AP_SERIAL, OID_AP_NUM_RADIOS,
    OID_AP_UPTIME, OID_AP_MODEL_NAME, OID_AP_LOCATION,
    OID_AP_STATUS, OID_AP_SW_VERSION, OID_AP_LONGITUDE, OID_AP_LATITUDE,
    OID_AP_CONNECTED_AS_STANDBY,
    # Radio 表
    OID_RADIO_TABLE, OID_RADIO_TYPE, OID_RADIO_CHANNEL,
    OID_RADIO_POWER, OID_RADIO_MODE, OID_RADIO_UTIL,
    OID_RADIO_CLIENTS, OID_RADIO_MON_CLIENTS, OID_RADIO_AP_NAME,
    # 信道统计
    OID_CH_STATS_TABLE, OID_CH_STATS_NOISE, OID_CH_STATS_INTERF,
    OID_CH_STATS_BUSY, OID_CH_STATS_UTIL, OID_CH_STATS_NUM_APS,
    # 集群/角色
    OID_SYS_EXT_ROLE, OID_SYS_EXT_MASTER_IP, OID_SWITCH_LIST_ROLE,
    # 枚举
    AP_STATUS_UP, AP_STATUS_DOWN, AP_ROLE_ACTIVE, RADIO_TYPE_MAP, SWITCH_ROLE_MAP,
)

logger = logging.getLogger("nms.snmp_poller")

# Aruba wlanAPModel OID -> 可读型号名映射
# wlanAPModel (col 5) 返回 OBJECT IDENTIFIER, 需要映射为 AP-xxx
ARUBA_MODEL_OID_MAP = {
    "1.3.6.1.4.1.14823.1.2.112": "AP-MODEL-1",
    "1.3.6.1.4.1.14823.1.2.114": "AP-MODEL-2",
    "1.3.6.1.4.1.14823.1.2.115": "AP-MODEL-3",
    "1.3.6.1.4.1.14823.1.2.117": "AP-MODEL-4",
    "1.3.6.1.4.1.14823.1.2.137": "AP-MODEL-5",
}

# 抑制 pysnmp MIB 文件缺失警告 (我们使用纯数字 OID, 不需要 MIB 解析)
logging.getLogger("pysnmp.smi.mibBuilder").setLevel(logging.ERROR)
logging.getLogger("pysnmp.smi.compiler").setLevel(logging.ERROR)


class SNMPResult:
    """单次 SNMP 查询结果"""
    def __init__(self, oid: str, value: str, index: str = ""):
        self.oid = oid
        self.value = value
        self.index = index
        self.timestamp = datetime.now()


def mac_from_oid_octets(parts: list, start: int) -> str:
    """从 OID 索引的 6 个连续数字提取 MAC 地址
    例: parts=['32','76','3','216','10','252'], start=0 -> '02:00:00:00:00:01'
    """
    if start + 6 > len(parts):
        return ""
    try:
        vals = [int(parts[start + i]) for i in range(6)]
        if all(0 <= v <= 255 for v in vals) and any(v != 0 for v in vals):
            return ":".join(f"{v:02x}" for v in vals)
    except (ValueError, IndexError):
        pass
    return ""


def ip_from_octets(val: str) -> str:
    """将 SNMP 返回的 IP 字节串转换为点分十进制
    例: '\\xac\\x10\\x02\\x03' -> '198.51.100.3'
    """
    if not val:
        return ""
    try:
        if len(val) == 4:
            return ".".join(str(b) for b in val.encode("latin-1"))
    except Exception:
        pass
    return val


class SNMPPoller:
    """异步 SNMP 轮询引擎 (pysnmp 7.x)"""

    def __init__(self, config):
        self.config = config
        self.default_community = config.SNMP_COMMUNITY
        self.port = config.SNMP_PORT
        self.timeout = config.SNMP_TIMEOUT
        self.concurrency = config.SNMP_CONCURRENCY
        self.retries = config.SNMP_RETRIES
        self.retry_delay = config.SNMP_RETRY_DELAY
        self._semaphore = None
        self._active_mm_cache: Dict[str, str] = {}
        self._active_mm_cache_time: Dict[str, float] = {}

    def _get_community(self, region_cfg: dict) -> str:
        return region_cfg.get("snmp_community", self.default_community)

    # ============================================================
    # pysnmp 7.x 底层操作
    # ============================================================

    async def _make_transport(self, ip: str):
        """创建 SNMP 传输对象 (pysnmp 7.x 需要异步创建)"""
        from pysnmp.hlapi.asyncio import UdpTransportTarget
        return await UdpTransportTarget.create(
            (ip, self.port), timeout=self.timeout, retries=0
        )

    async def _snmp_get(self, ip: str, oid: str, community: str) -> Optional[str]:
        """SNMP GET 单个 OID, 返回值字符串 (每次创建独立 engine 避免并发冲突)"""
        from pysnmp.hlapi.asyncio import (
            SnmpEngine, get_cmd, CommunityData,
            ContextData, ObjectType, ObjectIdentity,
        )
        engine = SnmpEngine()
        transport = await self._make_transport(ip)
        for attempt in range(self.retries):
            try:
                err_ind, err_st, err_idx, vbs = await get_cmd(
                    engine, CommunityData(community),
                    transport,
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if err_ind:
                    logger.debug(f"SNMP GET {ip} {oid}: {err_ind}")
                elif err_st:
                    logger.debug(f"SNMP GET {ip} {oid}: {err_st}")
                elif vbs:
                    vb = vbs[0]
                    return str(vb.val) if hasattr(vb, 'val') else str(vb[1])
            except asyncio.TimeoutError:
                logger.warning(f"SNMP GET {ip} {oid}: timeout ({attempt+1}/{self.retries})")
            except Exception as e:
                logger.error(f"SNMP GET {ip} {oid}: {e}")
            if attempt < self.retries - 1:
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
        return None

    async def _snmp_get_multi(self, ip: str, oids: List[str],
                              community: str) -> Dict[str, Optional[str]]:
        """批量 SNMP GET, 并发获取多个 OID (每次创建独立 engine)"""
        from pysnmp.hlapi.asyncio import (
            SnmpEngine, get_cmd, CommunityData,
            ContextData, ObjectType, ObjectIdentity,
        )

        async def _get_one(oid: str) -> Tuple[str, Optional[str]]:
            engine = SnmpEngine()
            transport = await self._make_transport(ip)
            for attempt in range(self.retries):
                try:
                    err_ind, err_st, err_idx, vbs = await get_cmd(
                        engine, CommunityData(community),
                        transport,
                        ContextData(),
                        ObjectType(ObjectIdentity(oid)),
                    )
                    if err_ind or err_st:
                        break
                    if vbs:
                        vb = vbs[0]
                        val = str(vb.val) if hasattr(vb, 'val') else str(vb[1])
                        return oid, val
                except (asyncio.TimeoutError, Exception):
                    pass
                if attempt < self.retries - 1:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
            return oid, None

        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)
        tasks = []
        for oid in oids:
            tasks.append(self._semaphore_acquire(_get_one(oid)))
        results_list = await asyncio.gather(*tasks)
        return dict(results_list)

    async def _semaphore_acquire(self, coro):
        async with self._semaphore:
            return await coro

    async def _snmp_walk(self, ip: str, oid_prefix: str,
                         community: str,
                         max_entries: int = 15000,
                         walk_timeout: float = 600.0,
                         bulk_count: int = 50) -> List[SNMPResult]:
        """SNMP WALK 指定 OID 前缀 (使用 GETBULK 批量获取, 提速 20-50x)
        
        安全措施:
          - bulk_cmd + maxRepetitions: 每次请求取 bulk_count 行
          - max_entries: 最大条目数限制
          - walk_timeout: 整体超时 (秒)
          - OID 前缀严格匹配 (防止进入兄弟子树)
        """
        from pysnmp.hlapi.asyncio import (
            SnmpEngine, bulk_cmd, CommunityData,
            ContextData, ObjectType, ObjectIdentity,
        )
        engine = SnmpEngine()
        transport = await self._make_transport(ip)
        results = []
        var_binds = [ObjectType(ObjectIdentity(oid_prefix))]
        strict_prefix = oid_prefix + "."

        async def _do_walk():
            nonlocal var_binds, results
            while len(results) < max_entries:
                try:
                    err_ind, err_st, err_idx, vb_table = await bulk_cmd(
                        engine, CommunityData(community),
                        transport,
                        ContextData(),
                        0, bulk_count,  # nonRepeaters=0, maxRepetitions
                        *var_binds,
                        lexicographicMode=False,
                    )
                    if err_ind:
                        logger.warning(f"SNMP WALK {ip} {oid_prefix}: {err_ind}")
                        break
                    if err_st:
                        logger.warning(f"SNMP WALK {ip} {oid_prefix}: {err_st}")
                        break
                    if not vb_table:
                        break

                    # bulk_cmd 返回多行: vb_table 是 list of rows
                    done = False
                    next_vb = []
                    for row in vb_table:
                        # row 是 list of (name, value) tuples
                        if not isinstance(row, (list, tuple)):
                            row = [row]
                        for vb in row:
                            oid_str = str(vb.name) if hasattr(vb, 'name') else str(vb[0])
                            val = str(vb.val) if hasattr(vb, 'val') else str(vb[1])
                            # 严格前缀匹配
                            if not oid_str.startswith(strict_prefix) and oid_str != oid_prefix:
                                done = True
                                break
                            suffix = oid_str[len(oid_prefix):].lstrip(".")
                            results.append(SNMPResult(oid_str, val, suffix))
                            next_vb.append(vb)
                        if done:
                            break

                    if done or not next_vb:
                        break
                    var_binds = next_vb[-1:]  # 只保留最后一个 vb 作为继续点

                except asyncio.TimeoutError:
                    logger.warning(f"SNMP WALK {ip} {oid_prefix}: single-request timeout")
                    break
                except Exception as e:
                    logger.error(f"SNMP WALK {ip} {oid_prefix}: {e}")
                    break

        try:
            await asyncio.wait_for(_do_walk(), timeout=walk_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"SNMP WALK {ip} {oid_prefix}: overall timeout after "
                f"{walk_timeout}s, collected {len(results)} entries"
            )

        if len(results) >= max_entries:
            logger.warning(
                f"SNMP WALK {ip} {oid_prefix}: hit max_entries={max_entries}, "
                f"truncating"
            )

        return results

    # ============================================================
    # 精确表解析 - 基于官方 OID 文档
    # ============================================================

    def _parse_ap_table(self, results: List[SNMPResult]) -> Dict[str, Dict]:
        """
        解析 wlsxWlanAPTable (.5.2.1.4.1)
        表索引: wlanAPMacAddress (6字节MAC)
        行格式: .{col}.{m1}.{m2}.{m3}.{m4}.{m5}.{m6}
        列号映射 (来自 OID 文档):
          1=MAC(index), 2=IP, 3=Name, 4=Group, 5=Model, 6=Serial,
          9=NumRadios, 12=UpTime, 13=ModelName, 14=Location,
          19=Status(up=1,down=2), 28=Longitude, 29=Latitude,
          33=HwVersion, 34=SwVersion
        """
        ap_data: Dict[str, Dict] = {}
        prefix = OID_AP_TABLE + "."  # "1.3.6.1.4.1.14823.2.2.1.5.2.1.4.1."

        for r in results:
            if not r.oid.startswith(prefix):
                continue
            suffix = r.oid[len(prefix):]
            parts = suffix.split(".")
            # 格式: {col}.{m1}.{m2}.{m3}.{m4}.{m5}.{m6}
            # parts[0] = col, parts[1:7] = MAC octets
            if len(parts) < 7:
                continue

            col = parts[0]
            mac = mac_from_oid_octets(parts, 1)
            if not mac:
                continue

            if mac not in ap_data:
                ap_data[mac] = {
                    "mac": mac, "name": "", "ip": "", "group": "",
                    "model": "", "serial": "", "status": AP_STATUS_DOWN,
                    "num_radios": 0, "uptime": "", "model_name": "",
                    "location": "", "sw_version": "",
                    "longitude": "", "latitude": "",
                }

            ap = ap_data[mac]
            val = r.value

            if col == "2":    # wlanAPIpAddress
                ap["ip"] = ip_from_octets(val)
            elif col == "3":  # wlanAPName
                ap["name"] = val
            elif col == "4":  # wlanAPGroupName
                ap["group"] = val
            elif col == "5":  # wlanAPModel
                ap["model"] = val
            elif col == "6":  # wlanAPSerialNumber
                ap["serial"] = val
            elif col == "9":  # wlanAPNumRadios
                ap["num_radios"] = int(val) if val.isdigit() else 0
            elif col == "12": # wlanAPUpTime
                ap["uptime"] = val
            elif col == "13": # wlanAPModelName
                ap["model_name"] = val
            elif col == "14": # wlanAPLocation
                ap["location"] = val
            elif col == "19": # wlanAPStatus
                ap["status"] = int(val) if val.isdigit() else AP_STATUS_DOWN
            elif col == "28": # wlanAPLongitude
                ap["longitude"] = val
            elif col == "29": # wlanAPLatitude
                ap["latitude"] = val
            elif col == "34": # wlanAPSwVersion
                ap["sw_version"] = val

        return ap_data

    def _parse_radio_table(self, results: List[SNMPResult],
                           active_macs: set = None) -> Dict[str, List[Dict]]:
        """
        解析 wlanAPRadioTable (.5.2.1.5.1)
        表索引: wlanAPMacAddress + wlanAPRadioNumber
        行格式: .{col}.{m1}.{m2}.{m3}.{m4}.{m5}.{m6}.{radio_num}
        列号映射:
          1=RadioNumber(index), 2=RadioType, 3=Channel, 4=TransmitPower,
          5=RadioMode, 6=Utilization, 7=NumAssociatedClients,
          8=NumMonitoredClients, 16=RadioAPName, 17=TransmitPower10x
        """
        radio_data: Dict[str, List[Dict]] = {}  # mac -> [radio_dict, ...]
        prefix = OID_RADIO_TABLE + "."  # "1.3.6.1.4.1.14823.2.2.1.5.2.1.5.1."

        for r in results:
            if not r.oid.startswith(prefix):
                continue
            suffix = r.oid[len(prefix):]
            parts = suffix.split(".")
            # 格式: {col}.{m1-m6}.{radio_num}
            # parts[0]=col, parts[1:7]=MAC, parts[7]=radio_num
            if len(parts) < 8:
                continue

            col = parts[0]
            mac = mac_from_oid_octets(parts, 1)
            if not mac:
                continue
            # 只保留 active AP 的 radio 数据
            if active_macs and mac not in active_macs:
                continue

            radio_num = parts[7]
            val = r.value

            # 找到或创建 radio 条目
            if mac not in radio_data:
                radio_data[mac] = []

            # 查找已有 radio
            radio_entry = None
            for rd in radio_data[mac]:
                if rd.get("radio_number") == radio_num:
                    radio_entry = rd
                    break
            if radio_entry is None:
                radio_entry = {"radio_number": radio_num}
                radio_data[mac].append(radio_entry)

            if col == "2":    # wlanAPRadioType
                type_int = int(val) if val.isdigit() else 0
                radio_entry["radio_type"] = RADIO_TYPE_MAP.get(type_int, f"unknown({val})")
            elif col == "3":  # wlanAPRadioChannel
                radio_entry["channel"] = int(val) if val.isdigit() else 0
            elif col == "4":  # wlanAPRadioTransmitPower
                radio_entry["tx_power"] = int(val) if val.isdigit() else 0
            elif col == "5":  # wlanAPRadioMode
                radio_entry["mode"] = int(val) if val.isdigit() else 0
            elif col == "6":  # wlanAPRadioUtilization
                radio_entry["utilization"] = int(val) if val.isdigit() else 0
            elif col == "7":  # wlanAPRadioNumAssociatedClients
                radio_entry["clients"] = int(val) if val.isdigit() else 0
            elif col == "8":  # wlanAPRadioNumMonitoredClients
                radio_entry["monitored_clients"] = int(val) if val.isdigit() else 0
            elif col == "16": # wlanAPRadioAPName
                radio_entry["ap_name"] = val
            elif col == "17": # wlanAPRadioTransmitPower10x
                radio_entry["eirp_10x"] = int(val) if val.isdigit() else 0

        return radio_data

    def _parse_ch_stats_table(self, results: List[SNMPResult],
                              active_macs: set = None) -> Dict[str, Dict[str, Dict]]:
        """
        解析 wlanAPChStatsTable (.5.3.1.6.1)
        表索引: wlanAPMacAddress + wlanAPRadioNumber
        行格式: .{col}.{m1}.{m2}.{m3}.{m4}.{m5}.{m6}.{radio_num}
        关键列:
          9=ChNoise(dBm), 11=ChInterferenceIndex,
          18=ChBusyRate, 19=ChNumAPs, 37=ChUtilization
        """
        ch_data: Dict[str, Dict[str, Dict]] = {}  # mac -> radio_num -> stats
        prefix = OID_CH_STATS_TABLE + "."

        for r in results:
            if not r.oid.startswith(prefix):
                continue
            suffix = r.oid[len(prefix):]
            parts = suffix.split(".")
            if len(parts) < 8:
                continue

            col = parts[0]
            mac = mac_from_oid_octets(parts, 1)
            if not mac:
                continue
            # 只保留 active AP 的 channel 数据
            if active_macs and mac not in active_macs:
                continue

            radio_num = parts[7]
            val = r.value

            if mac not in ch_data:
                ch_data[mac] = {}
            if radio_num not in ch_data[mac]:
                ch_data[mac][radio_num] = {}

            stats = ch_data[mac][radio_num]
            if col == "9":    # wlanAPChNoise (dBm)
                stats["noise_floor"] = int(val) if val.isdigit() else 0
            elif col == "11": # wlanAPChInterferenceIndex
                stats["interference"] = int(val) if val.isdigit() else 0
            elif col == "18": # wlanAPChBusyRate
                stats["busy_rate"] = int(val) if val.isdigit() else 0
            elif col == "19": # wlanAPChNumAPs
                stats["num_aps"] = int(val) if val.isdigit() else 0
            elif col == "37": # wlanAPChUtilization
                stats["utilization"] = int(val) if val.isdigit() else 0

        return ch_data

    # ============================================================
    # MM 集群 Active 检测 (基于 wlsxSysExtSwitchRole)
    # ============================================================

    async def detect_active_mm(self, region_name: str, region_cfg: dict) -> str:
        """
        检测 MM 集群中的 Active (master) 节点
        使用 wlsxSysExtSwitchRole (.2.1.4): master(1), local(2), standby(3)
        """
        now = time.time()
        cache_key = region_name
        cache_ttl = 300

        if cache_key in self._active_mm_cache and \
           now - self._active_mm_cache_time.get(cache_key, 0) < cache_ttl:
            return self._active_mm_cache[cache_key]

        community = self._get_community(region_cfg)
        nodes = region_cfg.get("cluster_nodes", [region_cfg["controller_ip"]])
        fallback_ip = region_cfg["controller_ip"]

        logger.info(f"[{region_name}] 检测 Active MM, 节点: {nodes}")

        for node_ip in nodes:
            try:
                val = await self._snmp_get(node_ip, OID_SYS_EXT_ROLE, community)
                if val:
                    role = int(val)
                    role_name = SWITCH_ROLE_MAP.get(role, f"unknown({val})")
                    logger.info(f"[{region_name}] {node_ip} SwitchRole = {role_name}")
                    if role == 1:  # master
                        self._active_mm_cache[cache_key] = node_ip
                        self._active_mm_cache_time[cache_key] = now
                        return node_ip
                else:
                    name = await self._snmp_get(node_ip, OID_SYS_NAME, community)
                    if name:
                        logger.info(f"[{region_name}] {node_ip} sysName = {name} (role OID 无响应)")
            except Exception as e:
                logger.warning(f"[{region_name}] {node_ip} 检测失败: {e}")

        logger.warning(f"[{region_name}] 未检测到 master, 回退 {fallback_ip}")
        self._active_mm_cache[cache_key] = fallback_ip
        self._active_mm_cache_time[cache_key] = now
        return fallback_ip

    # ============================================================
    # 控制器轮询主入口
    # ============================================================

    async def poll_controller(self, controller_ip: str, region_name: str,
                              region_cfg: dict = None) -> Dict[str, Any]:
        """对单个控制器/集群执行完整轮询"""
        # 每次 poll 必须重建 semaphore (APScheduler 每次创建新事件循环)
        self._semaphore = asyncio.Semaphore(self.concurrency)

        region_cfg = region_cfg or {}
        community = self._get_community(region_cfg)
        is_cluster = region_cfg.get("is_cluster", False)

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
            if is_cluster:
                # 集群模式: 查询每个 MD 节点, 聚合 AP 数据
                nodes = region_cfg.get("cluster_nodes", [controller_ip])
                all_ap_dict = {}
                all_radio_dict = {}
                all_ch_dict = {}
                node_names = []

                tasks = []
                for node_ip in nodes:
                    tasks.append(self._poll_single_node(node_ip, community))
                node_results = await asyncio.gather(*tasks, return_exceptions=True)

                for node_ip, nr in zip(nodes, node_results):
                    if isinstance(nr, Exception):
                        logger.warning(f"[{region_name}] {node_ip} 查询失败: {nr}")
                        continue
                    name_val, uptime_val, ap_dict, radio_walk, ch_walk = nr
                    if name_val:
                        node_names.append(f"{name_val}({node_ip})")

                    # ap_dict 已经是解析后的结果 (两阶段采集)
                    active_macs_set = set(ap_dict.keys())
                    radio_dict = self._parse_radio_table(radio_walk, active_macs_set)
                    ch_dict = self._parse_ch_stats_table(ch_walk, active_macs_set)

                    # 聚合 (去重, 先到的优先)
                    for mac, ap_info in ap_dict.items():
                        ap_info["active_md"] = node_ip
                        if mac not in all_ap_dict:
                            all_ap_dict[mac] = ap_info
                    for mac, radios in radio_dict.items():
                        if mac not in all_radio_dict:
                            all_radio_dict[mac] = radios
                    for mac, ch_stats in ch_dict.items():
                        if mac not in all_ch_dict:
                            all_ch_dict[mac] = ch_stats

                result["controller"]["name"] = " / ".join(node_names) if node_names else controller_ip
                self._build_result(result, all_ap_dict, all_radio_dict, all_ch_dict)
            else:
                # 单控制器模式
                logger.info(
                    f"开始轮询 [{region_name}] -> {controller_ip} "
                    f"(community={community})"
                )
                name_val, uptime_val, ap_dict, radio_walk, ch_walk = \
                    await self._poll_single_node(controller_ip, community)

                if name_val:
                    result["controller"]["name"] = name_val
                if uptime_val:
                    result["controller"]["uptime"] = uptime_val

                # ap_dict 已经是解析后的结果 (两阶段采集)
                active_macs_set = set(ap_dict.keys())
                radio_dict = self._parse_radio_table(radio_walk, active_macs_set)
                ch_dict = self._parse_ch_stats_table(ch_walk, active_macs_set)
                self._build_result(result, ap_dict, radio_dict, ch_dict)

            result["success"] = True
            elapsed = time.time() - start_time
            logger.info(
                f"[{region_name}] 轮询完成: "
                f"{len(result['aps'])} APs, {len(result['radios'])} radios, "
                f"耗时 {elapsed:.1f}s"
            )

        except Exception as e:
            logger.error(f"[{region_name}] 轮询失败: {e}")
            result["error"] = str(e)

        return result

    async def _poll_single_node(self, ip: str, community: str) -> Tuple:
        """
        三阶段采集单个节点 (只采集 active AP):
        Phase 1: Walk AP 状态列 (col 19) -> 获取 active MAC 列表 (~1100 条)
        Phase 2: Walk 整张 AP 表 -> 一次获取所有列, 按 active MAC 过滤 (~55000 条)
        Phase 3: 并发 walk Radio/Channel 表
        返回: (sysName, sysUptime, ap_dict, radio_walk, ch_walk)
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)

        # Phase 1: 并发获取 sysName, sysUptime, AP 状态列 walk + A-AAC 角色列 walk
        sys_name_coro = self._semaphore_acquire(self._snmp_get(ip, OID_SYS_NAME, community))
        sys_uptime_coro = self._semaphore_acquire(self._snmp_get(ip, OID_SYS_UPTIME, community))
        status_walk_coro = self._semaphore_acquire(
            self._snmp_walk(ip, OID_AP_STATUS, community,
                            max_entries=5000, walk_timeout=120.0)
        )
        # col 41: wlanAPConnectedAsStandby - active(0)/standby(1)
        # 用于区分 A-AAC (active) vs S-AAC (standby) AP
        aac_walk_coro = self._semaphore_acquire(
            self._snmp_walk(ip, OID_AP_CONNECTED_AS_STANDBY, community,
                            max_entries=5000, walk_timeout=120.0)
        )
        sys_name, sys_uptime, status_results, aac_results = await asyncio.gather(
            sys_name_coro, sys_uptime_coro, status_walk_coro, aac_walk_coro
        )

        # 过滤: status=1 (up) AND connectedAsStandby=0 (A-AAC)
        up_macs = self._extract_active_macs(status_results)
        aac_macs = self._extract_aac_macs(aac_results)
        active_macs_set = set(up_macs) & set(aac_macs)
        active_macs = list(active_macs_set)
        logger.info(
            f"{ip}: up={len(up_macs)}, A-AAC(col41=0)={len(aac_macs)}, "
            f"交集(active)={len(active_macs)} 个 A-AAC AP"
        )

        if not active_macs:
            return sys_name, sys_uptime, {}, [], []

        # Phase 2: 并发 walk 关键列 (每列 ~2048 条, 10 列并发完成)
        # 关键列: 2=IP, 3=Name, 4=Group, 5=Model, 6=Serial,
        #         9=NumRadios, 12=UpTime, 13=ModelName, 14=Location, 34=SwVersion
        essential_cols = [2, 3, 4, 5, 6, 9, 12, 13, 14, 34]
        walk_tasks = [
            self._semaphore_acquire(
                self._snmp_walk(ip, f"{OID_AP_TABLE}.{col}", community,
                                max_entries=3000, walk_timeout=180.0)
            )
            for col in essential_cols
        ]
        col_walks = await asyncio.gather(*walk_tasks)

        # 合并所有列 walk 结果并解析 (按 active MAC 过滤)
        all_ap_results = []
        for col, walk_results in zip(essential_cols, col_walks):
            all_ap_results.extend(walk_results)
        ap_dict = self._parse_ap_walk(all_ap_results, active_macs_set)
        logger.info(f"{ip}: walk 关键列解析出 {len(ap_dict)} 个 AP ({len(all_ap_results)} 条原始数据)")

        # Phase 3: 并发 walk Radio 和 Channel 表
        radio_walk_coro = self._semaphore_acquire(
            self._snmp_walk(ip, OID_RADIO_TABLE, community,
                            max_entries=50000, walk_timeout=600.0)
        )
        ch_walk_coro = self._semaphore_acquire(
            self._snmp_walk(ip, OID_CH_STATS_TABLE, community,
                            max_entries=50000, walk_timeout=600.0)
        )
        radio_walk, ch_walk = await asyncio.gather(radio_walk_coro, ch_walk_coro)

        return sys_name, sys_uptime, ap_dict, radio_walk, ch_walk

    def _mac_to_oid_suffix(self, mac: str) -> str:
        """MAC 地址转 OID 索引: '02:00:00:00:00:01' -> '2.0.0.0.0.1'"""
        return ".".join(str(int(b, 16)) for b in mac.split(":"))

    def _extract_active_macs(self, status_results: List[SNMPResult]) -> List[str]:
        """从状态列 walk 结果中提取 up (status=1) AP 的 MAC 列表"""
        active = []
        prefix = OID_AP_STATUS + "."
        for r in status_results:
            if not r.oid.startswith(prefix):
                continue
            suffix = r.oid[len(prefix):]
            mac = mac_from_oid_octets(suffix.split("."), 0)
            if mac and r.value == "1":  # status=1 means up
                active.append(mac)
        return active

    def _extract_aac_macs(self, aac_results: List[SNMPResult]) -> List[str]:
        """从 col 41 walk 结果中提取 A-AAC (connectedAsStandby=0) AP 的 MAC 列表"""
        active = []
        prefix = OID_AP_CONNECTED_AS_STANDBY + "."
        for r in aac_results:
            if not r.oid.startswith(prefix):
                continue
            suffix = r.oid[len(prefix):]
            mac = mac_from_oid_octets(suffix.split("."), 0)
            if mac and r.value == "0":  # active(0) = A-AAC
                active.append(mac)
        return active

    def _resolve_model(self, ap_info: Dict) -> str:
        """解析 AP 型号: 优先 col13 (model_name 可读字符串), fallback col5 OID 映射"""
        # col 13: wlanAPModelName - 可读字符串 (如 "503", "575")
        model_name = ap_info.get("model_name", "").strip()
        if model_name and not model_name.startswith("1.3.6"):
            return f"AP-{model_name}"
        # col 5: wlanAPModel - OID 值, 通过映射表转换
        model_oid = ap_info.get("model", "").strip()
        if model_oid in ARUBA_MODEL_OID_MAP:
            return ARUBA_MODEL_OID_MAP[model_oid]
        # 都无值则返回空
        return ""

    def _parse_ap_walk(self, results: List[SNMPResult],
                        active_macs: set) -> Dict[str, Dict]:
        """
        解析关键列 walk 结果, 按 active MAC 过滤
        行格式: .{col}.{m1}.{m2}.{m3}.{m4}.{m5}.{m6}
        """
        ap_data: Dict[str, Dict] = {}
        prefix = OID_AP_TABLE + "."

        col_map = {"2": "ip", "3": "name", "4": "group", "5": "model",
                   "6": "serial", "9": "num_radios", "12": "uptime",
                   "13": "model_name", "14": "location", "34": "sw_version"}

        for r in results:
            if not r.oid.startswith(prefix):
                continue
            suffix = r.oid[len(prefix):]
            parts = suffix.split(".")
            if len(parts) < 7:
                continue

            col = parts[0]
            if col not in col_map:
                continue
            mac = mac_from_oid_octets(parts, 1)
            if not mac or mac not in active_macs:
                continue

            if mac not in ap_data:
                ap_data[mac] = {
                    "mac": mac, "name": "", "ip": "", "group": "",
                    "model": "", "serial": "", "status": AP_STATUS_UP,
                    "num_radios": 0, "uptime": "", "model_name": "",
                    "location": "", "sw_version": "",
                    "longitude": "", "latitude": "",
                }

            field = col_map[col]
            val = r.value
            if field == "ip":
                ap_data[mac][field] = ip_from_octets(val)
            elif field == "num_radios":
                ap_data[mac][field] = int(val) if val.isdigit() else 0
            else:
                ap_data[mac][field] = val

        return ap_data

    def _build_result(self, result: dict,
                      ap_dict: Dict[str, Dict],
                      radio_dict: Dict[str, List[Dict]],
                      ch_dict: Dict[str, Dict[str, Dict]]):
        """将解析后的表数据转换为标准格式存入 result"""
        for mac, ap_info in ap_dict.items():
            ap_name = ap_info.get("name") or mac
            status = ap_info.get("status", AP_STATUS_DOWN)

            result["aps"][ap_name] = {
                "name": ap_name,
                "mac": mac,
                "ip": ap_info.get("ip", ""),
                "model": self._resolve_model(ap_info),
                "serial": ap_info.get("serial", ""),
                "status": status,
                "active_md": ap_info.get("active_md", ""),
                "group": ap_info.get("group", ""),
                "location": ap_info.get("location", ""),
                "sw_version": ap_info.get("sw_version", ""),
                "num_radios": ap_info.get("num_radios", 0),
                "longitude": ap_info.get("longitude", ""),
                "latitude": ap_info.get("latitude", ""),
            }

            # 合并该 AP 的 radio 数据
            radios = radio_dict.get(mac, [])
            ch_stats = ch_dict.get(mac, {})

            for radio in radios:
                radio_num = radio.get("radio_number", "0")
                radio_key = f"{ap_name}_radio{radio_num}"

                # 合并信道统计
                radio_ch = ch_stats.get(radio_num, {})

                result["radios"][radio_key] = {
                    "ap_name": ap_name,
                    "radio_number": int(radio_num) if radio_num.isdigit() else 0,
                    "radio_type": radio.get("radio_type", ""),
                    "channel": radio.get("channel", 0),
                    "tx_power": radio.get("tx_power", 0),
                    "eirp_10x": radio.get("eirp_10x", 0),
                    "mode": radio.get("mode", 0),
                    "utilization": radio.get("utilization", 0),
                    "clients": radio.get("clients", 0),
                    "monitored_clients": radio.get("monitored_clients", 0),
                    "noise_floor": radio_ch.get("noise_floor", 0),
                    "interference": radio_ch.get("interference", 0),
                    "busy_rate": radio_ch.get("busy_rate", 0),
                    "ch_utilization": radio_ch.get("utilization", 0),
                    "num_aps_on_ch": radio_ch.get("num_aps", 0),
                }


class PollScheduler:
    """轮询调度器 - APScheduler 定期执行 SNMP 轮询"""

    def __init__(self, poller: SNMPPoller, db_store, config):
        self.poller = poller
        self.db_store = db_store
        self.config = config
        self._scheduler = None
        self._poll_lock = threading.Lock()  # 防止轮询重叠

    def start(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._sync_poll_cycle, "interval",
            minutes=self.config.POLL_INTERVAL_MINUTES,
            id="snmp_poll", name="SNMP Poll Cycle",
            max_instances=1, coalesce=True,
        )
        # 控制器性能采集 (与 AP 轮询同频, 独立任务)
        self._scheduler.add_job(
            self._sync_ctrl_poll, "interval",
            minutes=self.config.POLL_INTERVAL_MINUTES,
            id="ctrl_poll", name="Controller Perf Poll",
            max_instances=1, coalesce=True,
        )
        self._scheduler.start()
        logger.info(f"轮询调度器已启动, 间隔 {self.config.POLL_INTERVAL_MINUTES} 分钟")

        # 启动时立即执行一次
        self._sync_ctrl_poll()       # 控制器采集快速完成
        self._sync_poll_cycle()      # AP 轮询耗时较长

    def stop(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

    def _sync_poll_cycle(self):
        """同步包装器 - 防重叠 + 独立事件循环"""
        if not self._poll_lock.acquire(blocking=False):
            logger.warning("上一轮轮询尚未完成, 跳过本次")
            return
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run_poll_cycle())
        finally:
            loop.close()
            self._poll_lock.release()

    async def _run_poll_cycle(self):
        start = time.time()
        logger.info("=== 开始轮询周期 ===")
        for region_name, region_cfg in self.config.REGIONS.items():
            try:
                # 厂商路由: H3C 使用独立采集器
                if region_cfg.get("vendor") == "h3c":
                    from lib.h3c_poller import H3CPoller
                    h3c_poller = H3CPoller(self.config)
                    result = await h3c_poller.poll_controller(
                        region_cfg["controller_ip"], region_name,
                        region_cfg=region_cfg,
                    )
                else:
                    result = await self.poller.poll_controller(
                        region_cfg["controller_ip"], region_name,
                        region_cfg=region_cfg,
                    )
                if result["success"]:
                    self.db_store.save_poll_result(result)
            except Exception as e:
                logger.error(f"区域 {region_name} 轮询异常: {e}")
        elapsed = time.time() - start
        logger.info(f"=== 轮询周期完成, 耗时 {elapsed:.1f}s ===")

    def _sync_ctrl_poll(self):
        """控制器性能采集 - 同步包装器"""
        from lib.ctrl_poller import ControllerPoller, get_controller_targets
        loop = asyncio.new_event_loop()
        try:
            poller = ControllerPoller(self.config)
            targets = get_controller_targets(self.config)
            if not targets:
                return
            metrics = loop.run_until_complete(poller.poll_all(targets))
            if metrics:
                saved = self.db_store.save_controller_metrics(metrics)
                logger.info(f"控制器性能采集完成: {saved} 台")
            else:
                logger.warning("控制器性能采集: 无数据返回")
        except Exception as e:
            logger.error(f"控制器性能采集异常: {e}")
        finally:
            loop.close()
