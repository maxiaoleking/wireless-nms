# 无线网管平台 - 迁移部署指南

> 目标系统: Ubuntu 24.04 Server
> 应用: 全自研无线网管平台 (Flask + SNMP + SQLite)

---

## 一、系统要求

| 项目 | 要求 |
|------|------|
| OS | Ubuntu 24.04 LTS (Server) |
| Python | 3.10+ (系统自带 3.12) |
| 内存 | >= 2GB |
| 磁盘 | >= 5GB (含日志/数据库) |
| 网络 | 需能访问 Aruba 控制器 SNMP UDP 161 |

---

## 二、服务器初始化

### 2.1 系统更新 & 安装基础依赖

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx sshpass
```

### 2.2 创建应用目录

```bash
sudo mkdir -p /opt/wireless-nms/{lib,static/{css,js,images,floorplans},data,templates,scripts}
sudo chown -R $USER:$USER /opt/wireless-nms
```

---

## 三、部署应用代码

### 3.1 上传代码到服务器

将本项目 `wireless-nms/` 目录整体上传到服务器 `/opt/wireless-nms/`:

```bash
# 方法一: scp 逐文件上传
scp app.py config.py requirements.txt user@SERVER:/opt/wireless-nms/
scp lib/*.py user@SERVER:/opt/wireless-nms/lib/
scp templates/*.html user@SERVER:/opt/wireless-nms/templates/
scp static/css/*.css user@SERVER:/opt/wireless-nms/static/css/
scp static/js/*.js user@SERVER:/opt/wireless-nms/static/js/
scp scripts/*.py user@SERVER:/opt/wireless-nms/scripts/

# 方法二: 打包上传 (推荐)
tar czf wireless-nms.tar.gz wireless-nms/
scp wireless-nms.tar.gz user@SERVER:/opt/
ssh user@SERVER "cd /opt && tar xzf wireless-nms.tar.gz && rm wireless-nms.tar.gz"
```

### 3.2 上传平面图素材 (可选)

```bash
scp static/images/lab.jpg user@SERVER:/opt/wireless-nms/static/images/
```

---

## 四、安装 Python 依赖

```bash
cd /opt/wireless-nms
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

**requirements.txt 内容:**
```
flask==3.1.1
gunicorn==23.0.0
pysnmp==7.1.16
apscheduler==3.11.0
influxdb-client==1.48.0
```

---

## 五、配置应用

### 5.1 编辑 config.py

```bash
nano /opt/wireless-nms/config.py
```

**关键配置项:**

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `FLASK_PORT` | Flask 监听端口 | `5577` |
| `SNMP_COMMUNITY` | 默认 SNMP 团体名 | `"demo-community"` |
| `SNMP_TIMEOUT` | SNMP 超时(秒) | `10` |
| `POLL_INTERVAL_MINUTES` | 轮询周期(分钟) | `5` |
| `MOCK_MODE` | 模拟模式开关 | `False` (生产必须 False) |
| `REGIONS` | 控制器/区域定义 | 见下方 |

### 5.2 配置 REGIONS (控制器列表)

```python
REGIONS = {
    "TEST-253": {
        "id": "test-253",
        "name": "测试控制器 (198.51.100.2)",
        "controller_ip": "198.51.100.2",
        "snmp_community": "demo-community",
        "is_cluster": False,
    },
    "MM-Cluster": {
        "id": "mm-cluster",
        "name": "MM 集群 (MD01/MD02)",
        "controller_ip": "198.51.100.10",       # MM 管理 VIP
        "snmp_community": "demo-community",
        "is_cluster": True,
        "cluster_nodes": ["198.51.100.10", "198.51.100.11", "198.51.100.12"],
        "active_check": True,                     # 自动检测 Active MM
    },
}
```

**字段说明:**
- `controller_ip`: 采集目标 IP (集群时为 Active MM VIP)
- `snmp_community`: 该区域的 SNMP 团体名 (覆盖全局默认)
- `is_cluster`: 是否为 MM 集群架构
- `cluster_nodes`: 集群所有节点 IP (用于 Active 检测)
- `active_check`: 启用 Active MM 自动检测 (通过 `wlsxSysExtSwitchRole` OID)

---

## 六、初始化数据库

```bash
cd /opt/wireless-nms
source venv/bin/activate
python scripts/init_db.py
```

数据库文件自动创建于 `/opt/wireless-nms/data/nms.sqlite`。

**数据库表结构:**
- `ap_metadata` - AP 元数据 (名称/MAC/型号/序列号/区域)
- `ap_status` - AP 实时状态 (在线/离线/IP/客户端数/吞吐量)
- `ap_radio` - AP 射频详情 (类型/信道/功率/底噪/客户端)
- `ap_coordinates` - AP 坐标 (平面图定位)
- `alerts` - 告警记录 (预留)
- `poll_log` - 轮询日志

---

## 七、配置 systemd 服务

### 7.1 创建服务文件

```bash
sudo cp deploy/wireless-nms.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wireless-nms
```

### 7.2 服务文件内容

```ini
[Unit]
Description=Wireless NMS Platform - 全自研无线网管平台
After=network.target

[Service]
User=root
WorkingDirectory=/opt/wireless-nms
ExecStart=/opt/wireless-nms/venv/bin/gunicorn \
    --workers 2 \
    --bind 127.0.0.1:5577 \
    --timeout 120 \
    --access-logfile /opt/wireless-nms/data/access.log \
    --error-logfile /opt/wireless-nms/data/error.log \
    app:app
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=wireless-nms

[Install]
WantedBy=multi-user.target
```

### 7.3 启动服务

```bash
sudo systemctl start wireless-nms
sudo systemctl status wireless-nms
```

---

## 八、配置 Nginx 反向代理

### 8.1 部署 Nginx 配置

```bash
sudo cp deploy/nginx-wireless-nms.conf /etc/nginx/conf.d/wireless-nms.conf
sudo rm -f /etc/nginx/sites-enabled/default   # 移除默认配置 (如有冲突)
sudo nginx -t
sudo systemctl reload nginx
```

### 8.2 Nginx 配置内容

```nginx
server {
    listen 80 default_server;
    server_name _;

    # Flask 反向代理
    location / {
        proxy_pass http://127.0.0.1:5577;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
    }

    # 静态文件直供 (不走 Flask, 提高性能)
    location /static/ {
        alias /opt/wireless-nms/static/;
        expires 1h;
        access_log off;
    }
}
```

---

## 九、验证部署

```bash
# 1. 健康检查
curl http://127.0.0.1:5577/api/health | python3 -m json.tool

# 2. 检查 SNMP 采集是否正常 (等待第一个轮询周期 5 分钟, 或手动触发)
curl -X POST http://127.0.0.1:5577/api/poll_now

# 3. 查看 AP 数据
curl http://127.0.0.1:5577/api/ap_status | python3 -m json.tool | head -30

# 4. 查看日志
tail -f /opt/wireless-nms/data/nms.log
tail -f /opt/wireless-nms/data/error.log
```

**预期输出:**
```json
{
  "status": "ok",
  "mock_mode": false,
  "total_aps": 5,
  "regions": ["TEST-253", "MM-Cluster"],
  "poller_active": true,
  "timestamp": "2026-07-17T23:22:33"
}
```

---

## 十、SNMP 连通性测试

```bash
cd /opt/wireless-nms
source venv/bin/activate
python scripts/test_snmp.py
```

该脚本会测试:
1. 基础 SNMP 连通性 (sysName/sysUptime)
2. AP 表 walk (wlsxWlanAPTable)
3. Radio 表 walk (wlanAPRadioTable)
4. 集群模式节点角色检测

---

## 十一、页面访问

| 页面 | URL | 说明 |
|------|-----|------|
| 总览 Dashboard | `http://<SERVER_IP>/` | 全网 AP 统计概览 |
| AP 区域状态 | `http://<SERVER_IP>/ap-status` | 网格化 AP 状态展示 |
| Home AP 布局 | `http://<SERVER_IP>/home-ap` | 拖放 AP 到平面图 |

---

## 十二、日常运维

### 12.1 服务管理

```bash
sudo systemctl start wireless-nms      # 启动
sudo systemctl stop wireless-nms       # 停止
sudo systemctl restart wireless-nms    # 重启
sudo systemctl status wireless-nms     # 状态
sudo journalctl -u wireless-nms -f     # 实时日志
```

### 12.2 更新代码

```bash
# 上传新代码后
sudo systemctl restart wireless-nms
```

### 12.3 备份数据库

```bash
cp /opt/wireless-nms/data/nms.sqlite /opt/wireless-nms/data/nms.sqlite.bak.$(date +%Y%m%d)
```

### 12.4 清理日志

```bash
# 日志轮转 (建议配置 logrotate)
sudo truncate -s 0 /opt/wireless-nms/data/nms.log
sudo truncate -s 0 /opt/wireless-nms/data/access.log
sudo truncate -s 0 /opt/wireless-nms/data/error.log
```

---

## 十三、故障排查

| 问题 | 排查方法 |
|------|----------|
| SNMP 采集无数据 | `python scripts/test_snmp.py` 检查连通性 |
| 服务启动失败 | `journalctl -u wireless-nms -n 50` 查看错误 |
| 页面 502 | 检查 gunicorn 是否运行: `ps aux \| grep gunicorn` |
| Nginx 502 | `nginx -t` 检查配置, `systemctl status nginx` |
| AP 名称显示为 MAC | 控制器未命名该 AP, 属正常现象 |
| IP 地址乱码 | 已修复 (v8.6.X OID 文档适配后) |

---

## 十四、项目文件结构

```
wireless-nms/
├── app.py                  # Flask 主应用 (路由/API/启动)
├── config.py               # 配置文件 (区域/SNMP/轮询参数)
├── requirements.txt        # Python 依赖
├── deploy/
│   ├── deploy.sh           # 一键部署脚本
│   ├── nginx-wireless-nms.conf  # Nginx 配置
│   └── wireless-nms.service     # systemd 服务
├── lib/
│   ├── __init__.py
│   ├── ap_merger.py        # AP 数据合并逻辑
│   ├── db_store.py         # SQLite 数据库操作层
│   ├── mock_data.py        # 模拟数据生成器 (测试用)
│   ├── snmp_oids.py        # Aruba OID 定义 (基于官方文档 v8.6.X)
│   └── snmp_poller.py      # SNMP 异步轮询引擎 (pysnmp 6.x)
├── scripts/
│   ├── init_db.py          # 数据库初始化脚本
│   └── test_snmp.py        # SNMP 连通性测试
├── static/
│   ├── css/style.css       # 全局样式 (日落霓虹渐变主题)
│   ├── js/
│   │   ├── ap-status.js    # AP 状态页 (Leaflet 地图)
│   │   ├── detail-panel.js # AP 详情面板
│   │   └── home_ap.js      # Home AP 拖放布局
│   └── images/
│       └── lab.jpg         # 户型平面图
└── templates/
    ├── index.html          # 总览 Dashboard
    ├── ap_status.html      # AP 区域状态页
    └── home_ap.html        # Home AP 布局页
```

---

## 十五、技术架构

```
浏览器 (Nginx:80)
    │
    ▼
Flask/Gunicorn (127.0.0.1:5577)
    │
    ├── SQLite (data/nms.sqlite)     ← 数据存储
    │
    ── SNMP Poller (后台线程)
            │
            ├── TEST-253 (198.51.100.2)     ← 独立控制器
            │       └── SNMP GET/WALK (UDP 161)
            │
            ── MM-Cluster (198.51.100.10/11/12)  ← MM 集群
                    └── SNMP GET/WALK (UDP 161)
```

**核心 OID 路径 (Aruba AOS8 v8.6.X):**
- AP 表: `1.3.6.1.4.1.14823.2.2.1.5.2.1.4.1` (wlsxWlanAPTable)
- Radio 表: `1.3.6.1.4.1.14823.2.2.1.5.2.1.5.1` (wlanAPRadioTable)
- 信道统计: `1.3.6.1.4.1.14823.2.2.1.5.3.1.6.1` (wlanAPChStatsTable)
- 设备角色: `1.3.6.1.4.1.14823.2.2.1.2.1.4` (wlsxSysExtSwitchRole)
