"""
全自研无线网管平台 - Flask 主应用
提供 AP 状态监控 API 和前端页面
"""
import os
import sys
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, jsonify, request

import config
from lib.db_store import DBStore
from lib.snmp_poller import SNMPPoller, PollScheduler

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("nms.app")

# ============================================================
# Flask 应用初始化
# ============================================================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

db = DBStore()


# ============================================================
# 缓存层 (简单内存缓存, 30s TTL)
# ============================================================
_cache = {}
_cache_time = {}
CACHE_TTL = config.CLIENT_REFRESH_SECONDS


def cached(key_func=None, ttl=CACHE_TTL):
    """简单缓存装饰器"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cache_key = key_func(*args, **kwargs) if key_func else f.__name__
            now = datetime.now().timestamp()

            if cache_key in _cache and (now - _cache_time.get(cache_key, 0)) < ttl:
                return _cache[cache_key]

            result = f(*args, **kwargs)
            _cache[cache_key] = result
            _cache_time[cache_key] = now
            return result
        return wrapper
    return decorator


# ============================================================
# 页面路由
# ============================================================

@app.route("/")
def portal():
    """入口门户页面"""
    return render_template("portal.html")


@app.route("/dashboard")
def index():
    """总览 Dashboard"""
    return render_template("index.html", regions=config.REGIONS)


@app.route("/ap-status")
def ap_status_page():
    """AP 区域状态展示页面"""
    return render_template("ap_status.html",
                           refresh_interval=config.CLIENT_REFRESH_SECONDS,
                           regions=config.REGIONS)




@app.route("/campus-ap")
def campus_ap_page():
    """室外 AP 点位分布页 - 校园地图拖放"""
    return render_template("campus_ap.html")


@app.route("/ctrl-perf")
def ctrl_perf_page():
    """MM/MD 硬件性能监控页"""
    return render_template("ctrl_perf.html")


# ============================================================
# API 路由
# ============================================================

@app.route("/api/stats")
def api_stats():
    """全局/区域统计"""
    region = request.args.get("region")
    stats = db.get_stats(region)
    stats["timestamp"] = datetime.now().isoformat()
    return jsonify(stats)


@app.route("/api/regions")
def api_regions():
    """区域列表"""
    regions = db.get_regions()
    for r in regions:
        cfg = config.REGIONS.get(r["region"], {})
        r["name"] = cfg.get("name", r["region"])
        r["controller_ip"] = cfg.get("controller_ip", "")
        r["is_cluster"] = cfg.get("is_cluster", False)
        # 集群模式下附加 MD 统计
        if r.get("is_cluster"):
            r["md_stats"] = db.get_md_stats(r["region"])
    return jsonify(regions)


@app.route("/api/ap_status")
def api_ap_status():
    """AP 状态列表 (按区域/MD 过滤)"""
    region = request.args.get("region")
    active_md = request.args.get("active_md")
    data = db.get_ap_status(region, active_md)
    return jsonify(data)


@app.route("/api/ap_detail/<ap_name>")
def api_ap_detail(ap_name):
    """单 AP 详情"""
    detail = db.get_ap_detail(ap_name)
    if not detail:
        return jsonify({"error": "AP not found"}), 404
    return jsonify(detail)


@app.route("/api/offline_aps")
def api_offline_aps():
    """离线 AP 列表"""
    region = request.args.get("region")
    aps = db.get_offline_aps(region)
    return jsonify({"count": len(aps), "aps": aps})


@app.route("/api/buildings")
def api_buildings():
    """楼宇/区域配置"""
    result = []
    for key, cfg in config.REGIONS.items():
        result.append({
            "id": cfg["id"],
            "name": cfg["name"],
            "key": key,
            "controller_ip": cfg["controller_ip"],
            "is_cluster": cfg.get("is_cluster", False),
            "floors": list(set(cfg.get("floorplans", []))),
        })
    return jsonify(result)


@app.route("/api/save_coords", methods=["POST"])
def api_save_coords():
    """保存 AP 坐标"""
    data = request.get_json()
    if not data or "coords" not in data:
        return jsonify({"error": "missing coords"}), 400
    db.save_coords(data["coords"])
    return jsonify({"ok": True, "saved": len(data["coords"])})


@app.route("/api/campus-coords")
def api_get_campus_coords():
    """获取校园 AP 点位坐标"""
    coords = db.get_campus_coords()
    return jsonify(coords)


@app.route("/api/campus-coords", methods=["POST"])
def api_save_campus_coords():
    """保存校园 AP 点位坐标"""
    data = request.get_json()
    if data is None:
        return jsonify({"error": "missing data"}), 400
    saved = db.save_campus_coords(data)
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/ctrl-metrics")
def api_ctrl_metrics():
    """获取 MM/MD 硬件性能指标"""
    metrics = db.get_controller_metrics()
    return jsonify({"controllers": metrics})


@app.route("/api/ctrl-history/<ip>")
def api_ctrl_history(ip):
    """获取指定控制器历史指标"""
    hours = request.args.get("hours", 24, type=int)
    history = db.get_controller_history(ip, hours)
    return jsonify({"ip": ip, "history": history})


@app.route("/api/health")
def api_health():
    """健康检查"""
    stats = db.get_stats()
    return jsonify({
        "status": "ok",
        "mock_mode": config.MOCK_MODE,
        "total_aps": stats["total"],
        "regions": list(config.REGIONS.keys()),
        "poller_active": _poller is not None,
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/poll_now", methods=["POST"])
def api_poll_now():
    """手动触发 SNMP 轮询"""
    if _poller is None:
        return jsonify({"error": "SNMP poller not active"}), 503
    import asyncio
    region = request.args.get("region")
    results = []
    loop = asyncio.new_event_loop()
    try:
        for rname, rcfg in config.REGIONS.items():
            if region and rname != region:
                continue
            r = loop.run_until_complete(
                _poller.poll_controller(rcfg["controller_ip"], rname, region_cfg=rcfg)
            )
            if r["success"]:
                db.save_poll_result(r)
            results.append({
                "region": rname,
                "success": r["success"],
                "ap_count": len(r.get("aps", {})),
                "error": r.get("error", ""),
            })
    finally:
        loop.close()
    return jsonify({"ok": True, "results": results})


# ============================================================
# 启动入口
# ============================================================
def init_app():
    """应用初始化: 建表 + 可选导入模拟数据"""
    db.init_db()

    if config.MOCK_MODE:
        stats = db.get_stats()
        if stats["total"] == 0:
            logger.info("模拟模式: 首次启动, 导入模拟数据...")
            from lib.mock_data import generate_mock_aps
            aps = generate_mock_aps(
                total_count=getattr(config, "MOCK_AP_COUNT", 100),
                offline_ratio=getattr(config, "MOCK_OFFLINE_RATIO", 0.005),
            )
            db.save_mock_data(aps)
            logger.info(f"模拟数据导入完成: {len(aps)} AP")
    else:
        logger.info("真实采集模式: 跳过模拟数据导入")
        stats = db.get_stats()
        logger.info(f"数据库现有 {stats['total']} 条 AP 记录")


init_app()

# ============================================================
# SNMP 轮询调度 (后台线程, 文件锁确保单实例)
# ============================================================
_poller = None
_scheduler = None

if not config.MOCK_MODE:
    import fcntl
    _lock_path = os.path.join(config.BASE_DIR, "data", "poller.lock")
    try:
        _lock_fd = open(_lock_path, 'w')
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # 获取锁成功, 启动轮询器
        _poller = SNMPPoller(config)
        _scheduler = PollScheduler(_poller, db, config)
        import threading
        _poll_thread = threading.Thread(
            target=_scheduler.start, daemon=True, name="snmp-poll"
        )
        _poll_thread.start()
        logger.info("SNMP 轮询调度器已在后台线程启动 (本 worker 获得锁)")
    except IOError:
        logger.info("SNMP 轮询调度器: 另一个 worker 已持有锁, 跳过启动")
    except Exception as e:
        logger.error(f"SNMP 轮询调度器启动失败: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=config.DEBUG)
