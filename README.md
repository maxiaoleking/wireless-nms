# Wireless NMS — 轻量级校园无线网管平台

面向高校校园 WLAN 的自研轻量网管系统：通过 SNMP 定时采集 Aruba (AOS8 MM/MD 集群) 与 H3C 无线控制器的 AP 数据，提供全网 AP 实时监控、离线告警、控制器性能看板和校园地图点位分布可视化。

> 本仓库为演示/参考实现，所有 IP、SNMP 团体名均为占位值（RFC 5737 文档地址段），校园底图为程序生成的占位图。落地部署时请替换 `config.py` 中的控制器列表与团体名，并自备校园平面图 `static/images/campus.png`。

## 功能页面

| 路由 | 说明 |
|---|---|
| `/` | 门户导航 |
| `/dashboard` | 全网总览：AP 总数、在线/离线、按区域/型号统计 |
| `/ap-status` | AP 状态列表：搜索、过滤、离线明细、单 AP 详情 |
| `/campus-ap` | 校园地图点位分布：拖放布点、按型号着色、在线状态动画标记、坐标持久化 |
| `/ctrl-perf` | 控制器性能：CPU/内存/温度实时值与历史趋势 |

## 架构

```
┌────────────┐   SNMP (pysnmp, 异步并发)   ┌──────────────────┐
│ Aruba MM/MD │ ◄────────────────────────── │                  │
│ H3C AC      │                             │  wireless-nms    │
└────────────┘                             │  Flask + APScheduler│
                                            │  gunicorn :5577  │
      SQLite (data/nms.sqlite)  ◄──────────►│                  │
      InfluxDB (可选，控制器指标历史) ◄──────│                  │
                                            └──────────────────┘
                                                   ▲
                                       nginx 反向代理 / 静态直出
```

- **采集层** `lib/`：`ctrl_poller`（Aruba AOS8）、`h3c_poller`（H3C）、`snmp_poller`/`snmp_oids`（SNMP 封装与 OID 表）、`db_store`（SQLite 存储）、`ap_merger`（多控制器 AP 去重合并）
- **服务层** `app.py`：Flask 路由 + JSON API（`/api/*`）
- **前端** `templates/` + `static/js/`：无框架原生 JS，运行时注入样式与统计，模板仅做骨架

## 快速开始

```bash
# 1. 安装依赖（建议虚拟环境）
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 修改 config.py：控制器 IP / SNMP 团体名 / 轮询周期

# 3. 初始化数据库并手动触发一次采集
python scripts/init_db.py
python -c "from lib.ctrl_poller import poll_all; poll_all()"

# 4. 启动
gunicorn -w 2 -b 127.0.0.1:5577 app:app
# 浏览器访问 http://127.0.0.1:5577
```

## 生产部署

`deploy/` 提供完整参考：

- `deploy.sh` — 一键打包上传 + 重启服务
- `wireless-nms.service` — systemd 单元（Restart=always）
- `nginx-wireless-nms.conf` — nginx 反代（`/static/` alias 直出、禁缓存）
- `DEPLOY_GUIDE.md` — 部署手册（Ubuntu 24.04 实测）

## 目录结构

```
├── app.py              # Flask 入口 + API
├── config.py           # 全部配置（控制器、SNMP、轮询）
├── lib/                # 采集与存储模块
├── templates/          # 页面模板
├── static/{css,js,images}/
├── scripts/            # 初始化与调试脚本
├── deploy/             # systemd / nginx / 部署脚本
└── data/               # SQLite 与日志（已 gitignore）
```

## License

[MIT](LICENSE)
