"""
Aruba AOS8 / MM SNMP OID 定义
基于官方文档: ArubaOS OID汇总-版本8.6.X (20220720 jihu update)

MIB 结构:
  wlsxSwitchMIB (.1)         - 系统信息/交换机信息
  wlsxSysExtGroup (.2)       - 扩展系统信息 (角色/温度/内存/CPU)
  wlsxWlanStateGroup (.5)    - WLAN 状态 (AP/Radio/STA)
  wlsxUserMIB (.4)           - 用户信息
"""

# ============================================================
# 基础设备信息 (System MIB - RFC1213)
# ============================================================
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"

# ============================================================
# Aruba WLSX MIB 基 (1.3.6.1.4.1.14823.2.2.1)
# ============================================================
ARUBA_BASE = "1.3.6.1.4.1.14823.2.2.1"

# --- wlsxSwitchMIB (.1) ---
OID_WLSX_SWITCH_MIB = f"{ARUBA_BASE}.1"
OID_WLSX_HOSTNAME = f"{ARUBA_BASE}.1.1.1.1"         # 设备名称
OID_WLSX_MODEL_NAME = f"{ARUBA_BASE}.1.1.1.2"       # 设备型号
OID_WLSX_SWITCH_ROLE = f"{ARUBA_BASE}.1.1.1.4"      # master(1),local(2),standbymaster(3),branch(4),md(5)
OID_WLSX_SWITCH_MASTER_IP = f"{ARUBA_BASE}.1.1.1.5" # MM IP (MD返回MM地址, MM返回VRRP地址)

# MD 列表 (MM上查询返回所有MM+MD角色)
OID_SWITCH_LIST_TABLE = f"{ARUBA_BASE}.1.1.1.6.1"
OID_SWITCH_LIST_IP = f"{ARUBA_BASE}.1.1.1.6.1.1"    # index=IP
OID_SWITCH_LIST_ROLE = f"{ARUBA_BASE}.1.1.1.6.1.2"  # master(1),local(2),standbymaster(3),md(5)

# --- wlsxSysExtGroup (.2) - 扩展系统信息 ---
OID_SYS_EXT_SWITCH_IP = f"{ARUBA_BASE}.2.1.1"        # AC IP地址
OID_SYS_EXT_HOSTNAME = f"{ARUBA_BASE}.2.1.2"         # 设备命名
OID_SYS_EXT_MODEL = f"{ARUBA_BASE}.2.1.3"            # 设备型号
OID_SYS_EXT_ROLE = f"{ARUBA_BASE}.2.1.4"             # 设备角色: master(1),local(2),backupmaster(3)
OID_SYS_EXT_MASTER_IP = f"{ARUBA_BASE}.2.1.5"        # Master AC IP
OID_SYS_EXT_TEMP = f"{ARUBA_BASE}.2.1.10"            # AC机箱内温度
OID_SYS_EXT_CPU = f"{ARUBA_BASE}.2.1.30"             # AC CPU利用率
OID_SYS_EXT_MEM = f"{ARUBA_BASE}.2.1.31"             # AC内存利用率

# --- wlsxWlanStateGroup (.5) - WLAN 状态 ---

# 全局统计
OID_WLAN_TOTAL_AP = f"{ARUBA_BASE}.5.2.1.1"          # 连接到AC的AP数
OID_WLAN_TOTAL_STA = f"{ARUBA_BASE}.5.2.1.2"         # 连接到AC的终端数

# wlsxWlanAPTable - AP信息表 (索引: wlanAPMacAddress)
# 表路径: .5.2.1.4.1.{col}.{mac_6octets}
OID_AP_TABLE = f"{ARUBA_BASE}.5.2.1.4.1"
OID_AP_MAC = f"{ARUBA_BASE}.5.2.1.4.1.1"             # wlanAPMacAddress (index)
OID_AP_IP = f"{ARUBA_BASE}.5.2.1.4.1.2"              # wlanAPIpAddress
OID_AP_NAME = f"{ARUBA_BASE}.5.2.1.4.1.3"            # wlanAPName
OID_AP_GROUP = f"{ARUBA_BASE}.5.2.1.4.1.4"           # wlanAPGroupName
OID_AP_MODEL = f"{ARUBA_BASE}.5.2.1.4.1.5"           # wlanAPModel
OID_AP_SERIAL = f"{ARUBA_BASE}.5.2.1.4.1.6"          # wlanAPSerialNumber
OID_AP_NUM_RADIOS = f"{ARUBA_BASE}.5.2.1.4.1.9"      # wlanAPNumRadios
OID_AP_UPTIME = f"{ARUBA_BASE}.5.2.1.4.1.12"         # wlanAPUpTime
OID_AP_MODEL_NAME = f"{ARUBA_BASE}.5.2.1.4.1.13"     # wlanAPModelName
OID_AP_LOCATION = f"{ARUBA_BASE}.5.2.1.4.1.14"       # wlanAPLocation
OID_AP_STATUS = f"{ARUBA_BASE}.5.2.1.4.1.19"         # wlanAPStatus: up(1),down(2)
OID_AP_STANDBY_IP = f"{ARUBA_BASE}.5.2.1.4.1.40"     # wlanAPStandbyIpAddress (S-AAC IP)
OID_AP_CONNECTED_AS_STANDBY = f"{ARUBA_BASE}.5.2.1.4.1.41"  # wlanAPConnectedAsStandby: active(0), standby(1)
OID_AP_HW_VERSION = f"{ARUBA_BASE}.5.2.1.4.1.33"     # wlanAPHwVersion
OID_AP_SW_VERSION = f"{ARUBA_BASE}.5.2.1.4.1.34"     # wlanAPSwVersion
OID_AP_LONGITUDE = f"{ARUBA_BASE}.5.2.1.4.1.28"      # wlanAPLongitude
OID_AP_LATITUDE = f"{ARUBA_BASE}.5.2.1.4.1.29"       # wlanAPLatitude

# wlanAPRadioTable - Radio信息表 (索引: wlanAPMacAddress + wlanAPRadioNumber)
# 表路径: .5.2.1.5.1.{col}.{mac_6octets}.{radio_num}
OID_RADIO_TABLE = f"{ARUBA_BASE}.5.2.1.5.1"
OID_RADIO_NUMBER = f"{ARUBA_BASE}.5.2.1.5.1.1"       # wlanAPRadioNumber (index)
OID_RADIO_TYPE = f"{ARUBA_BASE}.5.2.1.5.1.2"         # wlanAPRadioType: dot11a(1),dot11b(2),dot11g(3),dot11ag(4),wired(5)
OID_RADIO_CHANNEL = f"{ARUBA_BASE}.5.2.1.5.1.3"      # wlanAPRadioChannel
OID_RADIO_POWER = f"{ARUBA_BASE}.5.2.1.5.1.4"        # wlanAPRadioTransmitPower
OID_RADIO_MODE = f"{ARUBA_BASE}.5.2.1.5.1.5"         # wlanAPRadioMode: airMonitor(1),ap(2),apAndMonitor(3)...
OID_RADIO_UTIL = f"{ARUBA_BASE}.5.2.1.5.1.6"         # wlanAPRadioUtilization
OID_RADIO_CLIENTS = f"{ARUBA_BASE}.5.2.1.5.1.7"      # wlanAPRadioNumAssociatedClients
OID_RADIO_MON_CLIENTS = f"{ARUBA_BASE}.5.2.1.5.1.8"  # wlanAPRadioNumMonitoredClients
OID_RADIO_AP_NAME = f"{ARUBA_BASE}.5.2.1.5.1.16"     # wlanAPRadioAPName
OID_RADIO_EIRP_10X = f"{ARUBA_BASE}.5.2.1.5.1.17"    # wlanAPRadioTransmitPower10x (值*10)

# wlanAPChStatsTable - Radio信道统计表 (索引: wlanAPMacAddress + wlanAPRadioNumber)
# 表路径: .5.3.1.6.1.{col}.{mac_6octets}.{radio_num}
OID_CH_STATS_TABLE = f"{ARUBA_BASE}.5.3.1.6.1"
OID_CH_STATS_CHANNEL = f"{ARUBA_BASE}.5.3.1.6.1.1"   # wlanAPChannelNumber
OID_CH_STATS_NOISE = f"{ARUBA_BASE}.5.3.1.6.1.9"     # wlanAPChNoise (dBm)
OID_CH_STATS_INTERF = f"{ARUBA_BASE}.5.3.1.6.1.11"   # wlanAPChInterferenceIndex
OID_CH_STATS_BUSY = f"{ARUBA_BASE}.5.3.1.6.1.18"     # wlanAPChBusyRate
OID_CH_STATS_UTIL = f"{ARUBA_BASE}.5.3.1.6.1.37"     # wlanAPChUtilization (总信道利用率)
OID_CH_STATS_NUM_APS = f"{ARUBA_BASE}.5.3.1.6.1.19"  # wlanAPChNumAPs

# wlanAPRadioStatsTable - Radio流量统计 (索引: wlanAPMacAddress + wlanAPRadioNumber)
# 表路径: .5.3.1.9.1.{col}.{mac_6octets}.{radio_num}
OID_RADIO_STATS_TABLE = f"{ARUBA_BASE}.5.3.1.9.1"
OID_RADIO_RX_PKTS = f"{ARUBA_BASE}.5.3.1.9.1.1"      # wlanAPRadioRxPkts
OID_RADIO_RX_BYTES = f"{ARUBA_BASE}.5.3.1.9.1.2"     # wlanAPRadioRxBytes
OID_RADIO_TX_PKTS = f"{ARUBA_BASE}.5.3.1.9.1.3"      # wlanAPRadioTxPkts
OID_RADIO_TX_BYTES = f"{ARUBA_BASE}.5.3.1.9.1.4"     # wlanAPRadioTxBytes

# ============================================================
# 轮询任务定义 (精确表 walk)
# ============================================================
POLL_TASKS = [
    {
        "name": "ap_table",
        "description": "AP信息表 (MAC/IP/名称/型号/状态/序列号)",
        "oid": OID_AP_TABLE,
        "index_type": "mac",  # 索引 = 6字节MAC
    },
    {
        "name": "radio_table",
        "description": "Radio信息表 (类型/信道/功率/模式/客户端数)",
        "oid": OID_RADIO_TABLE,
        "index_type": "mac_radio",  # 索引 = 6字节MAC + radio号
    },
    {
        "name": "ch_stats_table",
        "description": "信道统计表 (底噪/干扰/利用率)",
        "oid": OID_CH_STATS_TABLE,
        "index_type": "mac_radio",
    },
    {
        "name": "controller_status",
        "description": "控制器状态 (sysName/sysUptime)",
        "oids": [OID_SYS_NAME, OID_SYS_UPTIME],
        "target": "controllers",
    },
]

# ============================================================
# OID 名称映射 (用于日志)
# ============================================================
OID_NAMES = {
    OID_AP_TABLE: "wlsxWlanAPTable",
    OID_RADIO_TABLE: "wlanAPRadioTable",
    OID_CH_STATS_TABLE: "wlanAPChStatsTable",
    OID_SYS_NAME: "sysName",
    OID_SYS_UPTIME: "sysUpTime",
    OID_SYS_EXT_ROLE: "wlsxSysExtSwitchRole",
}

# AP 状态枚举
AP_STATUS_UP = 1
AP_STATUS_DOWN = 2

# AP 连接角色枚举 (col 41: wlanAPConnectedAsStandby)
AP_ROLE_ACTIVE = 0     # A-AAC: active on this MD
AP_ROLE_STANDBY = 1    # S-AAC: standby on this MD

# Radio PHY 类型枚举
RADIO_TYPE_MAP = {
    1: "dot11a",   # 5GHz
    2: "dot11b",   # 2.4GHz
    3: "dot11g",   # 2.4GHz
    4: "dot11ag",  # 2.4GHz
    5: "wired",
}

# 设备角色枚举
SWITCH_ROLE_MAP = {
    1: "master",
    2: "local",
    3: "standbymaster",
    4: "branch",
    5: "md",
}
