#!/usr/bin/env python3
"""
SNMP 连通性测试脚本
基于 ArubaOS OID汇总文档 (v8.6.X)
测试与 Aruba 控制器/MM 的 SNMP 连接
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from lib.snmp_oids import (
    OID_SYS_NAME, OID_SYS_UPTIME, OID_AP_TABLE, OID_AP_STATUS,
    OID_SYS_EXT_ROLE, OID_RADIO_TABLE, OID_CH_STATS_TABLE,
    SWITCH_ROLE_MAP,
)


async def test_snmp_get(target_ip, community, oid, label):
    """测试单个 SNMP GET"""
    print(f"\n  [{label}] SNMP GET {target_ip} -> ...{oid[-20:]}")
    try:
        from lib.snmp_poller import SNMPPoller
        poller = SNMPPoller(config)
        result = await poller._snmp_get(target_ip, oid, community)
        if result:
            print(f"    -> 成功: {result}")
            return True
        else:
            print(f"    -> 失败: 无返回")
            return False
    except Exception as e:
        print(f"    -> 错误: {e}")
        return False


async def test_snmp_walk(target_ip, community, oid_prefix, label):
    """测试单个 SNMP WALK"""
    print(f"\n  [{label}] SNMP WALK {target_ip} -> ...{oid_prefix[-30:]}")
    try:
        from lib.snmp_poller import SNMPPoller
        poller = SNMPPoller(config)
        results = await poller._snmp_walk(target_ip, oid_prefix, community)
        if results:
            print(f"    -> 成功: 返回 {len(results)} 条记录")
            for r in results[:3]:
                print(f"       {r.oid} = {r.value}")
            if len(results) > 3:
                print(f"       ... 共 {len(results)} 条")
            return True
        else:
            print(f"    -> 失败: 无返回")
            return False
    except Exception as e:
        print(f"    -> 错误: {e}")
        return False


async def test_switch_role(target_ip, community, label):
    """测试设备角色 (master/local/standby)"""
    print(f"\n  [{label}] SwitchRole 检测 {target_ip}")
    try:
        from lib.snmp_poller import SNMPPoller
        poller = SNMPPoller(config)
        result = await poller._snmp_get(target_ip, OID_SYS_EXT_ROLE, community)
        if result:
            role = int(result)
            role_name = SWITCH_ROLE_MAP.get(role, f"Unknown({role})")
            print(f"    -> SwitchRole: {role} ({role_name})")
            return role
        else:
            print(f"    -> 失败: 无返回")
            return None
    except Exception as e:
        print(f"    -> 错误: {e}")
        return None


async def test_ap_status(target_ip, community, label):
    """测试 AP 状态表"""
    print(f"\n  [{label}] AP 状态检测 {target_ip}")
    try:
        from lib.snmp_poller import SNMPPoller
        poller = SNMPPoller(config)
        results = await poller._snmp_walk(target_ip, OID_AP_STATUS, community)
        if results:
            up = sum(1 for r in results if r.value == "1")
            down = sum(1 for r in results if r.value == "2")
            other = len(results) - up - down
            print(f"    -> 总计 {len(results)} AP: "
                  f"Up={up}, Down={down}, Other={other}")
            return True
        else:
            print(f"    -> 无数据")
            return False
    except Exception as e:
        print(f"    -> 错误: {e}")
        return False


async def main():
    print("=" * 60)
    print("无线网管平台 - SNMP 连通性测试 (OID v8.6.X)")
    print("=" * 60)
    print(f"\n共 {len(config.REGIONS)} 个区域待测试:\n")
    for name, cfg in config.REGIONS.items():
        cluster_tag = " [集群]" if cfg.get("is_cluster") else ""
        print(f"  - {name}: {cfg['controller_ip']}{cluster_tag} "
              f"(community={cfg.get('snmp_community', config.SNMP_COMMUNITY)})")

    results = {}

    for region_name, region_cfg in config.REGIONS.items():
        ctrl_ip = region_cfg["controller_ip"]
        community = region_cfg.get("snmp_community", config.SNMP_COMMUNITY)
        is_cluster = region_cfg.get("is_cluster", False)

        print(f"\n{'=' * 60}")
        print(f"区域: {region_name} -> {ctrl_ip}")
        print(f"Community: {community} | 集群: {is_cluster}")
        print(f"{'=' * 60}")

        region_result = {"ip": ctrl_ip, "reachable": False}

        # 1. 基础连通性
        ok1 = await test_snmp_get(ctrl_ip, community, OID_SYS_NAME, f"{region_name} sysName")
        ok2 = await test_snmp_get(ctrl_ip, community, OID_SYS_UPTIME, f"{region_name} sysUpTime")
        region_result["reachable"] = ok1 or ok2

        # 2. AP 表 walk
        ok3 = await test_snmp_walk(ctrl_ip, community, OID_AP_TABLE, f"{region_name} AP Table")
        region_result["ap_table"] = ok3

        # 3. Radio 表 walk
        ok4 = await test_snmp_walk(ctrl_ip, community, OID_RADIO_TABLE, f"{region_name} Radio Table")

        # 4. AP 状态检测
        ok5 = await test_ap_status(ctrl_ip, community, region_name)

        # 5. 集群模式: 检测各节点角色
        if is_cluster:
            nodes = region_cfg.get("cluster_nodes", [ctrl_ip])
            print(f"\n  --- 集群节点角色检测 ---")
            for node_ip in nodes:
                role = await test_switch_role(node_ip, community, f"{region_name} {node_ip}")
                region_result.setdefault("roles", {})[node_ip] = role

        results[region_name] = region_result

    # 汇总
    print(f"\n{'=' * 60}")
    print("测试结果汇总")
    print(f"{'=' * 60}")
    for name, r in results.items():
        reachable = "OK" if r["reachable"] else "FAIL"
        ap_table = "OK" if r.get("ap_table") else "FAIL"
        print(f"  {name}: 可达={reachable}, AP表={ap_table}")

    # 集群角色
    for name, r in results.items():
        if r.get("roles"):
            print(f"\n  集群 {name} 节点角色:")
            for ip, role in r["roles"].items():
                role_name = SWITCH_ROLE_MAP.get(role, f"Unknown({role})" if role else "N/A")
                print(f"    {ip}: {role_name}")

    total = len(results)
    reachable = sum(1 for r in results.values() if r["reachable"])
    print(f"\n总计: {reachable}/{total} 设备可达")

    if reachable == 0:
        print("\n提示: 所有设备不可达。请检查:")
        print("  1. 网络连通性 (ping 设备 IP)")
        print("  2. SNMP community 是否正确")
        print("  3. 设备 SNMP 服务是否启用")
        print("  4. 防火墙是否放行 UDP 161")


if __name__ == "__main__":
    asyncio.run(main())
