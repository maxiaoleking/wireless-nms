"""
控制器性能采集器 - MM + MD 硬件性能监控
通过 SNMP GET 采集 CPU、内存、温度、AP/STA 数量等关键指标
"""
import asyncio
import logging
import time
from typing import Dict, List, Any, Optional
from datetime import datetime

from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity, get_cmd, bulk_cmd,
)

logger = logging.getLogger("nms.ctrl_poller")

# 控制器性能 OID 映射 (标量, 需追加 .0)
CTRL_OIDS = {
    "1.3.6.1.4.1.14823.2.2.1.1.1.1.0":  "hostname",
    "1.3.6.1.4.1.14823.2.2.1.1.1.2.0":  "model",
    "1.3.6.1.4.1.14823.2.2.1.1.1.4.0":  "role",
    "1.3.6.1.4.1.14823.2.2.1.2.1.10.0": "temperature",
    "1.3.6.1.4.1.14823.2.2.1.2.1.30.0": "cpu_pct",
    "1.3.6.1.4.1.14823.2.2.1.2.1.31.0": "mem_pct",
    "1.3.6.1.4.1.14823.2.2.1.2.1.32.0": "pkt_loss",
    "1.3.6.1.4.1.14823.2.2.1.5.2.1.1.0": "ap_count",
    "1.3.6.1.4.1.14823.2.2.1.5.2.1.2.0": "sta_count",
    "1.3.6.1.2.1.1.1.0":                "sys_descr",
    "1.3.6.1.2.1.1.3.0":                "sys_uptime",
}

# 多核 CPU 表 OID (wlsxSysExtProcessorTable)
CPU_CORE_DESCR = "1.3.6.1.4.1.14823.2.2.1.2.1.13.1.2"  # sysXProcessorDescr
CPU_CORE_LOAD  = "1.3.6.1.4.1.14823.2.2.1.2.1.13.1.3"  # sysXProcessorLoad

# 角色值映射
ROLE_MAP = {"1": "master", "2": "local", "3": "standby", "4": "branch", "5": "md"}


class ControllerPoller:
    """控制器性能采集器"""

    def __init__(self, config):
        self.community = getattr(config, "SNMP_COMMUNITY", "demo-community")
        self.timeout = 5
        self.retries = 2

    async def poll_one(self, ip: str, label: str = "") -> Optional[Dict[str, Any]]:
        """采集单台控制器性能指标"""
        result = {"ip": ip, "label": label}
        engine = SnmpEngine()

        for oid, field in CTRL_OIDS.items():
            try:
                ei, es, eidx, vbt = await get_cmd(
                    engine,
                    CommunityData(self.community),
                    await UdpTransportTarget.create(
                        (ip, 161), timeout=self.timeout, retries=self.retries
                    ),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if ei:
                    result[field] = ""
                    continue

                raw = str(vbt[0][1])

                # 类型转换
                if field in ("cpu_pct", "mem_pct", "pkt_loss"):
                    result[field] = float(raw) if raw else 0.0
                elif field == "ap_count" or field == "sta_count":
                    result[field] = int(raw) if raw else 0
                elif field == "sys_uptime":
                    result[field] = int(raw) if raw else 0
                elif field == "role":
                    result[field] = ROLE_MAP.get(raw, raw)
                elif field == "temperature":
                    try:
                        result[field] = str(round(float(raw.split()[0]), 1))
                    except (ValueError, IndexError):
                        result[field] = raw if raw else "N/A"
                else:
                    result[field] = raw

            except Exception as e:
                logger.warning(f"[{label or ip}] OID {field} 采集失败: {e}")
                result[field] = ""

        # 多核 CPU 采集
        result["cpu_cores"] = await self._walk_cpu_cores(ip, engine)

        return result

    async def _walk_cpu_cores(self, ip: str, engine) -> List[Dict]:
        """Walk CPU 核心表, 返回 [{name, load}, ...]"""
        cores = []
        try:
            transport = await UdpTransportTarget.create(
                (ip, 161), timeout=self.timeout, retries=self.retries
            )
            # 采集描述
            descr_map = {}
            curr = CPU_CORE_DESCR
            for _ in range(5):
                ei, es, eidx, vbt = await bulk_cmd(
                    engine, CommunityData(self.community), transport, ContextData(),
                    0, 25, ObjectType(ObjectIdentity(curr))
                )
                if ei or not vbt:
                    break
                for vb in vbt:
                    oid = str(vb[0])
                    if CPU_CORE_DESCR not in oid:
                        break
                    idx = oid.split(".")[-1]
                    descr_map[idx] = str(vb[1])
                    curr = oid
                else:
                    continue
                break

            # 采集负载
            load_map = {}
            curr = CPU_CORE_LOAD
            for _ in range(5):
                ei, es, eidx, vbt = await bulk_cmd(
                    engine, CommunityData(self.community), transport, ContextData(),
                    0, 25, ObjectType(ObjectIdentity(curr))
                )
                if ei or not vbt:
                    break
                for vb in vbt:
                    oid = str(vb[0])
                    if CPU_CORE_LOAD not in oid:
                        break
                    idx = oid.split(".")[-1]
                    try:
                        load_map[idx] = int(str(vb[1]))
                    except ValueError:
                        load_map[idx] = 0
                    curr = oid
                else:
                    continue
                break

            # 合并
            for idx in sorted(descr_map.keys(), key=lambda x: int(x)):
                cores.append({
                    "name": descr_map[idx],
                    "load": load_map.get(idx, 0),
                })
        except Exception as e:
            logger.warning(f"[{ip}] CPU核心表采集失败: {e}")

        return cores

    async def poll_all(self, targets: List[Dict]) -> List[Dict]:
        """并发采集所有控制器 (MM + MDs + H3C)"""
        aruba_targets = []
        h3c_targets = []
        for t in targets:
            if t.get("vendor") == "h3c":
                h3c_targets.append(t)
            else:
                aruba_targets.append(t)

        metrics = []

        # Aruba 控制器
        if aruba_targets:
            tasks = [self.poll_one(t["ip"], t.get("label", t["ip"])) for t in aruba_targets]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"[{aruba_targets[i].get('label', '')}] 采集异常: {r}")
                    continue
                if r:
                    metrics.append(r)

        # H3C 控制器
        if h3c_targets:
            from lib.h3c_poller import H3CControllerPoller
            h3c_cp = H3CControllerPoller(None)
            tasks = [h3c_cp.poll_one(t["ip"], t.get("community", "demo-community-h3c"), t.get("label", t["ip"])) for t in h3c_targets]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"[{h3c_targets[i].get('label', '')}] 采集异常: {r}")
                    continue
                if r:
                    metrics.append(r)

        return metrics


def get_controller_targets(config) -> List[Dict]:
    """从配置中构建控制器采集目标列表"""
    targets = []

    # MM (Mobility Conductor)
    mm_ip = None
    for rname, rcfg in config.REGIONS.items():
        if rcfg.get("is_cluster"):
            mm_ip = rcfg["controller_ip"]
            break

    if mm_ip:
        targets.append({"ip": mm_ip, "label": "MM"})

    # MDs (Managed Devices)
    for rname, rcfg in config.REGIONS.items():
        if rcfg.get("is_cluster") and rcfg.get("cluster_nodes"):
            for i, md_ip in enumerate(rcfg["cluster_nodes"]):
                targets.append({"ip": md_ip, "label": f"MD{i+1}"})

    # H3C 无线控制器
    for rname, rcfg in config.REGIONS.items():
        if rcfg.get("vendor") == "h3c":
            targets.append({
                "ip": rcfg["controller_ip"],
                "label": "H3C-AC",
                "vendor": "h3c",
                "community": rcfg.get("snmp_community", "demo-community-h3c"),
            })

    return targets
