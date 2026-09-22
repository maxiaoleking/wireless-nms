#!/usr/bin/env python3
"""
数据库初始化脚本
创建表结构并导入模拟数据 (测试环境)
"""
import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db_store import DBStore
from lib.mock_data import generate_mock_aps
import config


def main():
    print("=" * 60)
    print("无线网管平台 - 数据库初始化")
    print("=" * 60)

    db = DBStore()

    # 1. 创建表结构
    print("\n[1/3] 创建数据库表结构...")
    db.init_db()
    print("  -> 表结构创建完成")

    # 2. 导入模拟数据
    if config.MOCK_MODE:
        print(f"\n[2/3] 生成模拟数据 ({config.MOCK_AP_COUNT} AP)...")
        aps = generate_mock_aps(
            total_count=config.MOCK_AP_COUNT,
            offline_ratio=config.MOCK_OFFLINE_RATIO,
        )
        print(f"  -> 生成 {len(aps)} 个模拟 AP")

        # 统计各区域
        region_counts = {}
        for ap in aps:
            r = ap["region"]
            region_counts[r] = region_counts.get(r, 0) + 1
        for r, c in sorted(region_counts.items()):
            print(f"     {r}: {c} AP")

        print("\n[3/3] 导入模拟数据到数据库...")
        db.save_mock_data(aps)
        print("  -> 模拟数据导入完成")
    else:
        print("\n[2/3] 非模拟模式, 跳过数据导入")
        print("[3/3] 等待 SNMP 轮询引擎填充数据")

    # 验证
    stats = db.get_stats()
    print(f"\n验证: 总计 {stats['total']} AP, "
          f"在线 {stats['online']}, 离线 {stats['offline']}, "
          f"离线率 {stats['offline_rate']}%")

    print("\n" + "=" * 60)
    print("初始化完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
