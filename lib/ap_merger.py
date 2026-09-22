"""
AP 状态合并逻辑
将 SNMP 轮询结果与已有数据库数据合并, 处理冲突和去重
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger("nms.ap_merger")


class APMerger:
    """
    AP 数据合并器

    职责:
    1. 将 SNMP 原始轮询结果转换为统一的 AP 记录格式
    2. 处理同一 AP 的多条 radio 记录合并 (每台 AP = 2 radio)
    3. 推导 AP 在线/离线状态
    4. 检测 AP 状态变化 (上线/下线事件)
    5. 处理控制器状态对 AP 状态的级联影响
    """

    def __init__(self, config):
        self.config = config
        # 上一次轮询的 AP 状态快照 (用于检测变化)
        self._prev_snapshot: Dict[str, int] = {}

    def merge_poll_results(self, poll_results: List[Dict]) -> List[Dict]:
        """
        合并多个控制器的轮询结果为统一 AP 列表

        Args:
            poll_results: 各控制器轮询结果列表

        Returns:
            List[Dict]: 合并后的 AP 记录列表
        """
        merged = {}

        for result in poll_results:
            if not result.get("success"):
                logger.warning(
                    f"控制器 {result.get('controller_ip')} 轮询失败, 跳过"
                )
                continue

            region = result.get("region", "unknown")
            aps = result.get("aps", {})
            radios = result.get("radios", {})

            # 合并 AP 系统信息
            for ap_key, ap_info in aps.items():
                ap_name = ap_info.get("name", ap_key)
                if ap_name in merged:
                    logger.warning(f"AP {ap_name} 重复出现在多个控制器中")
                    continue

                merged[ap_name] = self._build_ap_record(ap_info, region)

            # 合并射频信息到对应 AP
            for radio_key, radio_info in radios.items():
                ap_name = radio_info.get("ap_name", "")
                if ap_name in merged:
                    merged[ap_name]["radios"].append(
                        self._build_radio_record(radio_info)
                    )

        # 计算汇总统计
        ap_list = list(merged.values())
        for ap in ap_list:
            self._finalize_ap(ap)

        return ap_list

    def _build_ap_record(self, ap_info: Dict, region: str) -> Dict:
        """构建单条 AP 记录"""
        return {
            "name": ap_info.get("name", ""),
            "mac_address": ap_info.get("mac", ""),
            "ip_address": ap_info.get("ip", ""),
            "model": ap_info.get("model", ""),
            "serial_number": ap_info.get("serial", ""),
            "region": region,
            "controller": region,
            "status": ap_info.get("status", 1),
            "total_clients": ap_info.get("clients", 0),
            "rx_throughput": ap_info.get("rx_throughput", 0),
            "tx_throughput": ap_info.get("tx_throughput", 0),
            "radios": [],
            "last_seen": datetime.now().isoformat(),
        }

    def _build_radio_record(self, radio_info: Dict) -> Dict:
        """构建单条射频记录"""
        return {
            "radio_number": radio_info.get("radio_number", 0),
            "radio_type": radio_info.get("type", ""),
            "channel": radio_info.get("channel", 0),
            "tx_power": radio_info.get("tx_power", 0),
            "noise_floor": radio_info.get("noise_floor", 0),
            "clients": radio_info.get("clients", 0),
        }

    def _finalize_ap(self, ap: Dict):
        """最终化处理: 汇总 radio 数据"""
        # 总客户端数 = 各 radio 客户端数之和
        total_clients = sum(r.get("clients", 0) for r in ap["radios"])
        if total_clients > 0:
            ap["total_clients"] = total_clients

        # 射频按 radio_number 排序
        ap["radios"].sort(key=lambda r: r.get("radio_number", 0))

    def detect_status_changes(self, current_aps: List[Dict]) -> Dict[str, List]:
        """
        检测 AP 状态变化 (上线/下线事件)

        Args:
            current_aps: 当前轮询的 AP 列表

        Returns:
            Dict: {"went_offline": [...], "came_online": [...], "new_aps": [...]}
        """
        current_snapshot = {ap["name"]: ap["status"] for ap in current_aps}
        changes = {
            "went_offline": [],
            "came_online": [],
            "new_aps": [],
        }

        if not self._prev_snapshot:
            # 首次轮询, 无对比基准
            self._prev_snapshot = current_snapshot
            return changes

        for ap_name, status in current_snapshot.items():
            prev_status = self._prev_snapshot.get(ap_name)
            if prev_status is None:
                # 新发现的 AP
                changes["new_aps"].append(ap_name)
            elif prev_status == 1 and status == 0:
                # 从在线变为离线
                changes["went_offline"].append(ap_name)
            elif prev_status == 0 and status == 1:
                # 从离线恢复为在线
                changes["came_online"].append(ap_name)

        # 检测完全消失的 AP (上次有, 这次没有)
        disappeared = set(self._prev_snapshot.keys()) - set(current_snapshot.keys())
        if disappeared:
            logger.warning(f"{len(disappeared)} 个 AP 在本次轮询中消失")
            changes["went_offline"].extend(disappeared)

        # 更新快照
        self._prev_snapshot = current_snapshot

        # 日志
        if changes["went_offline"]:
            logger.info(
                f"AP 离线: {len(changes['went_offline'])} 个 - "
                f"{', '.join(changes['went_offline'][:5])}{'...' if len(changes['went_offline']) > 5 else ''}"
            )
        if changes["came_online"]:
            logger.info(
                f"AP 恢复: {len(changes['came_online'])} 个 - "
                f"{', '.join(changes['came_online'][:5])}{'...' if len(changes['came_online']) > 5 else ''}"
            )
        if changes["new_aps"]:
            logger.info(f"新发现 AP: {len(changes['new_aps'])} 个")

        return changes

    def apply_controller_cascade(
        self, controller_status: Dict[str, bool], aps: List[Dict]
    ) -> List[Dict]:
        """
        控制器离线时, 其下所有 AP 标记为离线

        Args:
            controller_status: {controller_name: is_reachable}
            aps: AP 列表

        Returns:
            List[Dict]: 更新后的 AP 列表
        """
        for ap in aps:
            ctrl = ap.get("controller", "")
            if ctrl in controller_status and not controller_status[ctrl]:
                if ap["status"] == 1:
                    logger.warning(
                        f"控制器 {ctrl} 不可达, AP {ap['name']} 级联标记为离线"
                    )
                    ap["status"] = 0
                    ap["cascade_offline"] = True
        return aps

    def generate_offline_report(self, aps: List[Dict]) -> Dict:
        """
        生成离线 AP 报告

        Args:
            aps: 全量 AP 列表

        Returns:
            Dict: 离线报告
        """
        offline = [ap for ap in aps if ap.get("status") == 0]
        online = [ap for ap in aps if ap.get("status") == 1]

        # 按区域分组
        by_region: Dict[str, List] = {}
        for ap in offline:
            region = ap.get("region", "unknown")
            if region not in by_region:
                by_region[region] = []
            by_region[region].append(ap)

        # 按模型分组
        by_model: Dict[str, int] = {}
        for ap in offline:
            model = ap.get("model", "unknown")
            by_model[model] = by_model.get(model, 0) + 1

        return {
            "total": len(aps),
            "online": len(online),
            "offline": len(offline),
            "offline_rate": round(len(offline) / len(aps) * 100, 2) if aps else 0,
            "by_region": {
                region: {
                    "count": len(aps_list),
                    "aps": [a["name"] for a in aps_list],
                }
                for region, aps_list in by_region.items()
            },
            "by_model": by_model,
            "cascade_offline": [
                ap["name"] for ap in offline if ap.get("cascade_offline")
            ],
            "timestamp": datetime.now().isoformat(),
        }
