"""
数据库操作层 - SQLite
负责 AP 元数据、状态、射频信息的读写
"""
import os
import json
import sqlite3
import logging
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any

import config

logger = logging.getLogger("nms.db_store")


class DBStore:
    """
    SQLite 数据库操作层

    线程安全: 每个线程使用独立连接
    写入优化: WAL 模式 + 批量事务
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.SQLITE_DB_PATH
        self._local = threading.local()
        # 确保数据库目录存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"创建数据库目录: {db_dir}")

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            # 每次连接前检查目录是否存在 (防止运行时被清理)
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30,
            )
            self._local.conn.row_factory = sqlite3.Row
            # WAL 模式提升并发读写性能
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            # 增大 busy timeout 避免并发锁冲突
            self._local.conn.execute("PRAGMA busy_timeout=30000")
        return self._local.conn

    def init_db(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        conn.executescript("""
            -- AP 元数据
            CREATE TABLE IF NOT EXISTS ap_metadata (
                name TEXT PRIMARY KEY,
                mac_address TEXT,
                model TEXT,
                serial_number TEXT,
                region TEXT,
                building TEXT,
                floor TEXT,
                last_seen TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- AP 实时状态
            CREATE TABLE IF NOT EXISTS ap_status (
                ap_name TEXT PRIMARY KEY,
                status INTEGER DEFAULT 1,
                ip_address TEXT,
                controller TEXT,
                active_md TEXT,
                total_clients INTEGER DEFAULT 0,
                rx_throughput REAL DEFAULT 0,
                tx_throughput REAL DEFAULT 0,
                updated_at TIMESTAMP
            );

            -- AP 射频详情
            CREATE TABLE IF NOT EXISTS ap_radio (
                ap_name TEXT,
                radio_number INTEGER,
                radio_type TEXT,
                channel INTEGER,
                tx_power INTEGER,
                noise_floor INTEGER,
                clients INTEGER DEFAULT 0,
                updated_at TIMESTAMP,
                PRIMARY KEY (ap_name, radio_number)
            );

            -- AP 坐标
            CREATE TABLE IF NOT EXISTS ap_coordinates (
                ap_name TEXT PRIMARY KEY,
                region TEXT,
                x REAL,
                y REAL,
                floor TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- 控制器性能指标 (MM + MD)
            CREATE TABLE IF NOT EXISTS controller_metrics (
                ip TEXT PRIMARY KEY,
                hostname TEXT,
                model TEXT,
                role TEXT,
                cpu_pct REAL,
                mem_pct REAL,
                temperature TEXT,
                ap_count INTEGER,
                sta_count INTEGER,
                pkt_loss REAL,
                sys_uptime INTEGER,
                sys_descr TEXT,
                cpu_cores TEXT DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- 控制器性能历史 (保留最近7天)
            CREATE TABLE IF NOT EXISTS controller_metrics_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT,
                cpu_pct REAL,
                mem_pct REAL,
                ap_count INTEGER,
                sta_count INTEGER,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_ctrl_hist_ip ON controller_metrics_history(ip, recorded_at);

            -- 告警记录 (预留)
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ap_name TEXT,
                alert_type TEXT,
                message TEXT,
                severity TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            );

            -- 轮询日志
            CREATE TABLE IF NOT EXISTS poll_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                ap_count INTEGER,
                success INTEGER,
                error_message TEXT
            );

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_ap_status_region ON ap_status(controller);
            CREATE INDEX IF NOT EXISTS idx_ap_metadata_region ON ap_metadata(region);
            CREATE INDEX IF NOT EXISTS idx_ap_metadata_building ON ap_metadata(building);
            CREATE INDEX IF NOT EXISTS idx_ap_radio_ap ON ap_radio(ap_name);
            CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
        """)
        conn.commit()

        # 兼容旧库: 如果 active_md 列不存在, 动态添加
        try:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(ap_status)").fetchall()]
            if "active_md" not in cols:
                conn.execute("ALTER TABLE ap_status ADD COLUMN active_md TEXT")
                conn.commit()
                logger.info("已为 ap_status 表添加 active_md 列")
            # active_md 索引 (列存在后才创建)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ap_status_active_md ON ap_status(active_md)")
            conn.commit()
        except Exception as e:
            logger.warning(f"检查/添加 active_md 列失败: {e}")

        # 兼容旧库: controller_metrics 表添加 cpu_cores 列
        try:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(controller_metrics)").fetchall()]
            if "cpu_cores" not in cols:
                conn.execute("ALTER TABLE controller_metrics ADD COLUMN cpu_cores TEXT DEFAULT '[]'")
                conn.commit()
                logger.info("已为 controller_metrics 表添加 cpu_cores 列")
        except Exception:
            pass  # 表不存在时忽略

        logger.info(f"数据库初始化完成: {self.db_path}")

    def save_poll_result(self, result: Dict[str, Any]):
        """
        保存一轮轮询结果到数据库

        Args:
            result: poller 返回的结果字典
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()
        region = result.get("region", "unknown")

        try:
            # 批量写入 AP 状态
            ap_data = []
            meta_data = []
            for ap_name, ap_info in result.get("aps", {}).items():
                ap_data.append((
                    ap_name,
                    ap_info.get("status", 1),
                    ap_info.get("ip", ""),
                    region,
                    ap_info.get("active_md", ""),
                    ap_info.get("clients", 0),
                    ap_info.get("rx_throughput", 0),
                    ap_info.get("tx_throughput", 0),
                    now,
                ))
                meta_data.append((
                    ap_name,
                    ap_info.get("mac", ""),
                    ap_info.get("model", ""),
                    ap_info.get("serial", ""),
                    region,
                    ap_info.get("building", ""),
                    ap_info.get("floor", ""),
                    now,
                ))

            # UPSERT AP 状态
            conn.executemany("""
                INSERT INTO ap_status (ap_name, status, ip_address, controller,
                                       active_md, total_clients, rx_throughput, tx_throughput, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ap_name) DO UPDATE SET
                    status=excluded.status,
                    ip_address=excluded.ip_address,
                    active_md=excluded.active_md,
                    total_clients=excluded.total_clients,
                    rx_throughput=excluded.rx_throughput,
                    tx_throughput=excluded.tx_throughput,
                    updated_at=excluded.updated_at
            """, ap_data)

            # UPSERT AP 元数据 (更新 model/serial/last_seen)
            conn.executemany("""
                INSERT INTO ap_metadata (name, mac_address, model, serial_number,
                                         region, building, floor, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    mac_address=CASE WHEN excluded.mac_address != '' THEN excluded.mac_address ELSE ap_metadata.mac_address END,
                    model=CASE WHEN excluded.model != '' THEN excluded.model ELSE ap_metadata.model END,
                    serial_number=CASE WHEN excluded.serial_number != '' THEN excluded.serial_number ELSE ap_metadata.serial_number END
            """, meta_data)

            # 写入射频信息
            radio_data = []
            for radio_key, radio_info in result.get("radios", {}).items():
                radio_data.append((
                    radio_info.get("ap_name", ""),
                    radio_info.get("radio_number", 0),
                    radio_info.get("type", ""),
                    radio_info.get("channel", 0),
                    radio_info.get("tx_power", 0),
                    radio_info.get("noise_floor", 0),
                    radio_info.get("clients", 0),
                    now,
                ))

            if radio_data:
                conn.executemany("""
                    INSERT INTO ap_radio (ap_name, radio_number, radio_type, channel,
                                          tx_power, noise_floor, clients, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ap_name, radio_number) DO UPDATE SET
                        radio_type=excluded.radio_type,
                        channel=excluded.channel,
                        tx_power=excluded.tx_power,
                        noise_floor=excluded.noise_floor,
                        clients=excluded.clients,
                        updated_at=excluded.updated_at
                """, radio_data)

            # 清理过期数据: 将本轮未出现的 AP 标记为 offline
            if ap_data:
                current_names = {row[0] for row in ap_data}
                placeholders = ",".join("?" * len(current_names))
                conn.execute(f"""
                    UPDATE ap_status SET status=0 WHERE controller=?
                    AND ap_name NOT IN ({placeholders})
                """, [region] + list(current_names))

            # 写入轮询日志
            conn.execute("""
                INSERT INTO poll_log (region, start_time, end_time, ap_count, success)
                VALUES (?, ?, ?, ?, ?)
            """, (region, now, now, len(ap_data), 1 if result.get("success") else 0))

            conn.commit()
            logger.debug(f"区域 {region}: 保存 {len(ap_data)} AP 状态, {len(radio_data)} 射频记录")

        except sqlite3.OperationalError as e:
            # 并发写入冲突, 重试最多 3 次
            logger.warning(f"数据库写入冲突 ({region}), 尝试重试: {e}")
            self._retry_save(result, max_retries=3)
        except Exception as e:
            logger.error(f"保存轮询结果失败 ({region}): {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    def _retry_save(self, result: Dict[str, Any], max_retries: int = 3):
        """带重试的数据库写入 (处理 SQLite 锁竞争)"""
        for attempt in range(max_retries):
            time.sleep(0.5 * (2 ** attempt))  # 指数退避: 0.5s, 1s, 2s
            try:
                conn = self._get_conn()
                now = datetime.now().isoformat()
                region = result.get("region", "unknown")

                ap_data = []
                meta_data = []
                for ap_name, ap_info in result.get("aps", {}).items():
                    ap_data.append((
                        ap_name,
                        ap_info.get("status", 1),
                        ap_info.get("ip", ""),
                        region,
                        ap_info.get("clients", 0),
                        ap_info.get("rx_throughput", 0),
                        ap_info.get("tx_throughput", 0),
                        now,
                    ))
                    meta_data.append((
                        ap_name,
                        ap_info.get("mac", ""),
                        ap_info.get("model", ""),
                        ap_info.get("serial", ""),
                        region,
                        ap_info.get("building", ""),
                        ap_info.get("floor", ""),
                        now,
                    ))

                conn.executemany("""
                    INSERT INTO ap_status (ap_name, status, ip_address, controller,
                                           total_clients, rx_throughput, tx_throughput, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ap_name) DO UPDATE SET
                        status=excluded.status,
                        ip_address=excluded.ip_address,
                        total_clients=excluded.total_clients,
                        rx_throughput=excluded.rx_throughput,
                        tx_throughput=excluded.tx_throughput,
                        updated_at=excluded.updated_at
                """, ap_data)

                conn.executemany("""
                    INSERT INTO ap_metadata (name, mac_address, model, serial_number,
                                             region, building, floor, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        last_seen=excluded.last_seen,
                        mac_address=CASE WHEN excluded.mac_address != '' THEN excluded.mac_address ELSE ap_metadata.mac_address END,
                        model=CASE WHEN excluded.model != '' THEN excluded.model ELSE ap_metadata.model END,
                        serial_number=CASE WHEN excluded.serial_number != '' THEN excluded.serial_number ELSE ap_metadata.serial_number END
                """, meta_data)

                radio_data = []
                for radio_key, radio_info in result.get("radios", {}).items():
                    radio_data.append((
                        radio_info.get("ap_name", ""),
                        radio_info.get("radio_number", 0),
                        radio_info.get("type", ""),
                        radio_info.get("channel", 0),
                        radio_info.get("tx_power", 0),
                        radio_info.get("noise_floor", 0),
                        radio_info.get("clients", 0),
                        now,
                    ))

                if radio_data:
                    conn.executemany("""
                        INSERT INTO ap_radio (ap_name, radio_number, radio_type, channel,
                                              tx_power, noise_floor, clients, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ap_name, radio_number) DO UPDATE SET
                            radio_type=excluded.radio_type,
                            channel=excluded.channel,
                            tx_power=excluded.tx_power,
                            noise_floor=excluded.noise_floor,
                            clients=excluded.clients,
                            updated_at=excluded.updated_at
                    """, radio_data)

                conn.execute("""
                    INSERT INTO poll_log (region, start_time, end_time, ap_count, success)
                    VALUES (?, ?, ?, ?, ?)
                """, (region, now, now, len(ap_data), 1 if result.get("success") else 0))

                conn.commit()
                logger.info(f"[重试成功] 区域 {region}: 保存 {len(ap_data)} AP (第{attempt+1}次重试)")
                return

            except sqlite3.OperationalError as e:
                logger.warning(f"重试 {attempt+1}/{max_retries} 仍然失败: {e}")
                # 关闭旧连接, 下次重试时重新建立
                try:
                    conn.close()
                except Exception:
                    pass
                if hasattr(self._local, 'conn'):
                    self._local.conn = None

        logger.error(f"数据库写入最终失败 ({result.get('region', 'unknown')}), 放弃 {max_retries} 次重试")

    def save_mock_data(self, aps: List[Dict]):
        """
        批量保存模拟数据 (初始化用)

        Args:
            aps: AP 数据列表
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()

        try:
            for ap in aps:
                # 元数据
                conn.execute("""
                    INSERT OR REPLACE INTO ap_metadata
                    (name, mac_address, model, serial_number, region, building, floor, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ap["name"], ap["mac_address"], ap["model"],
                    ap["serial_number"], ap["region"],
                    ap.get("building", ""), ap.get("floor", ""),
                    now,
                ))

                # 状态
                conn.execute("""
                    INSERT OR REPLACE INTO ap_status
                    (ap_name, status, ip_address, controller, total_clients,
                     rx_throughput, tx_throughput, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ap["name"], ap["status"], ap["ip_address"],
                    ap["controller"], ap["total_clients"],
                    ap["rx_throughput"], ap["tx_throughput"], now,
                ))

                # 射频
                for radio in ap.get("radios", []):
                    conn.execute("""
                        INSERT OR REPLACE INTO ap_radio
                        (ap_name, radio_number, radio_type, channel,
                         tx_power, noise_floor, clients, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ap["name"], radio["radio_number"], radio["type"],
                        radio["channel"], radio["tx_power"],
                        radio["noise_floor"], radio["clients"], now,
                    ))

            conn.commit()
            logger.info(f"模拟数据导入完成: {len(aps)} AP")

        except Exception as e:
            logger.error(f"模拟数据导入失败: {e}")
            conn.rollback()

    # ============================================================
    # 查询方法 (供 Flask API 调用)
    # ============================================================

    def get_stats(self, region: str = None) -> Dict[str, Any]:
        """获取统计信息"""
        conn = self._get_conn()
        sql = """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) as online,
                SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END) as offline
            FROM ap_status
        """
        params = []
        if region:
            sql += " WHERE controller = ?"
            params.append(region)

        row = conn.execute(sql, params).fetchone()
        total = row["total"] or 0
        online = row["online"] or 0
        offline = row["offline"] or 0

        return {
            "total": total,
            "online": online,
            "offline": offline,
            "offline_rate": round(offline / total * 100, 2) if total > 0 else 0,
        }

    def get_ap_status(self, region: str = None, active_md: str = None) -> Dict[str, Any]:
        """获取 AP 状态列表"""
        conn = self._get_conn()
        sql = """
            SELECT
                s.ap_name as name,
                s.status,
                s.ip_address as ip,
                s.controller,
                s.active_md,
                s.total_clients,
                s.rx_throughput,
                s.tx_throughput,
                s.updated_at as last_seen,
                m.mac_address,
                m.model,
                m.serial_number,
                m.building,
                m.floor,
                c.x,
                c.y
            FROM ap_status s
            LEFT JOIN ap_metadata m ON s.ap_name = m.name
            LEFT JOIN ap_coordinates c ON s.ap_name = c.ap_name
        """
        params = []
        conditions = []
        if region:
            conditions.append("s.controller = ?")
            params.append(region)
        if active_md:
            conditions.append("s.active_md = ?")
            params.append(active_md)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY s.ap_name"

        rows = conn.execute(sql, params).fetchall()
        aps = [dict(r) for r in rows]

        stats = self.get_stats(region)
        return {
            "stats": stats,
            "aps": aps,
            "timestamp": datetime.now().isoformat(),
        }

    def get_ap_detail(self, ap_name: str) -> Optional[Dict[str, Any]]:
        """获取单个 AP 详情 (含射频信息)"""
        conn = self._get_conn()

        row = conn.execute("""
            SELECT
                s.ap_name as name,
                s.status,
                s.ip_address as ip,
                s.controller,
                s.active_md,
                s.total_clients,
                s.rx_throughput,
                s.tx_throughput,
                s.updated_at as last_seen,
                m.mac_address,
                m.model,
                m.serial_number,
                m.region,
                m.building,
                m.floor
            FROM ap_status s
            LEFT JOIN ap_metadata m ON s.ap_name = m.name
            WHERE s.ap_name = ?
        """, (ap_name,)).fetchone()

        if not row:
            return None

        result = dict(row)

        # 获取射频信息
        radios = conn.execute("""
            SELECT radio_number, radio_type, channel, tx_power,
                   noise_floor, clients, updated_at
            FROM ap_radio
            WHERE ap_name = ?
            ORDER BY radio_number
        """, (ap_name,)).fetchall()

        result["radios"] = [dict(r) for r in radios]
        return result

    def get_md_stats(self, region: str = None) -> List[Dict[str, Any]]:
        """按 MD 聚合统计 (active_md 维度)"""
        conn = self._get_conn()
        sql = """
            SELECT
                active_md as md_ip,
                COUNT(*) as total,
                SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) as online,
                SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END) as offline
            FROM ap_status
        """
        params = []
        if region:
            sql += " WHERE controller = ?"
            params.append(region)
        sql += " GROUP BY active_md ORDER BY active_md"

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_regions(self) -> List[Dict[str, Any]]:
        """获取区域列表及统计"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT
                controller as region,
                COUNT(*) as total,
                SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) as online,
                SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END) as offline
            FROM ap_status
            GROUP BY controller
            ORDER BY controller
        """).fetchall()

        return [dict(r) for r in rows]

    def get_offline_aps(self, region: str = None) -> List[Dict]:
        """获取离线 AP 列表"""
        conn = self._get_conn()
        sql = """
            SELECT s.ap_name as name, s.controller, s.updated_at as last_seen,
                   m.mac_address, m.model
            FROM ap_status s
            LEFT JOIN ap_metadata m ON s.ap_name = m.name
            WHERE s.status = 0
        """
        params = []
        if region:
            sql += " AND s.controller = ?"
            params.append(region)
        sql += " ORDER BY s.ap_name"

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def save_coords(self, coords: List[Dict]):
        """保存 AP 坐标"""
        conn = self._get_conn()
        try:
            for c in coords:
                conn.execute("""
                    INSERT OR REPLACE INTO ap_coordinates (ap_name, region, x, y, floor)
                    VALUES (?, ?, ?, ?, ?)
                """, (c["ap_name"], c.get("region", ""), c["x"], c["y"], c.get("floor", "")))
            conn.commit()
        except Exception as e:
            logger.error(f"保存坐标失败: {e}")
            conn.rollback()

    def get_campus_coords(self) -> Dict[str, Any]:
        """获取校园 AP 点位坐标 (用于 campus-ap 页面)"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT ap_name, x, y FROM ap_coordinates
            WHERE x IS NOT NULL AND y IS NOT NULL
        """).fetchall()
        return {r["ap_name"]: {"x": r["x"], "y": r["y"]} for r in rows}

    def save_campus_coords(self, positions: Dict[str, Any]):
        """批量保存校园 AP 点位 (positions: {ap_name: {x, y}})"""
        conn = self._get_conn()
        try:
            for ap_name, pos in positions.items():
                conn.execute("""
                    INSERT INTO ap_coordinates (ap_name, x, y, region, floor, updated_at)
                    VALUES (?, ?, ?, '', '', datetime('now'))
                    ON CONFLICT(ap_name) DO UPDATE SET
                        x=excluded.x, y=excluded.y, updated_at=excluded.updated_at
                """, (ap_name, pos["x"], pos["y"]))
            # 删除服务端有但客户端已移除的点位
            server_names = set(
                r[0] for r in conn.execute("SELECT ap_name FROM ap_coordinates").fetchall()
            )
            client_names = set(positions.keys())
            removed = server_names - client_names
            if removed:
                conn.execute(
                    f"DELETE FROM ap_coordinates WHERE ap_name IN ({','.join('?' * len(removed))})",
                    list(removed)
                )
            conn.commit()
            return len(positions)
        except Exception as e:
            logger.error(f"保存校园点位失败: {e}")
            conn.rollback()
            return 0

    # ============================================================
    # 控制器性能监控
    # ============================================================
    def save_controller_metrics(self, metrics: List[Dict]):
        """保存控制器性能指标 + 历史记录"""
        conn = self._get_conn()
        try:
            for m in metrics:
                cpu_cores_json = json.dumps(m.get("cpu_cores", []), ensure_ascii=False)
                conn.execute("""
                    INSERT INTO controller_metrics
                        (ip, hostname, model, role, cpu_pct, mem_pct, temperature,
                         ap_count, sta_count, pkt_loss, sys_uptime, sys_descr, cpu_cores, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(ip) DO UPDATE SET
                        hostname=excluded.hostname, model=excluded.model, role=excluded.role,
                        cpu_pct=excluded.cpu_pct, mem_pct=excluded.mem_pct, temperature=excluded.temperature,
                        ap_count=excluded.ap_count, sta_count=excluded.sta_count, pkt_loss=excluded.pkt_loss,
                        sys_uptime=excluded.sys_uptime, sys_descr=excluded.sys_descr,
                        cpu_cores=excluded.cpu_cores, updated_at=datetime('now')
                """, (m["ip"], m.get("hostname", ""), m.get("model", ""), m.get("role", ""),
                      m.get("cpu_pct", 0), m.get("mem_pct", 0), m.get("temperature", ""),
                      m.get("ap_count", 0), m.get("sta_count", 0), m.get("pkt_loss", 0),
                      m.get("sys_uptime", 0), m.get("sys_descr", ""), cpu_cores_json))
                # 写入历史
                conn.execute("""
                    INSERT INTO controller_metrics_history (ip, cpu_pct, mem_pct, ap_count, sta_count)
                    VALUES (?, ?, ?, ?, ?)
                """, (m["ip"], m.get("cpu_pct", 0), m.get("mem_pct", 0),
                      m.get("ap_count", 0), m.get("sta_count", 0)))
            conn.commit()
            # 清理7天前的历史数据
            conn.execute("DELETE FROM controller_metrics_history WHERE recorded_at < datetime('now', '-7 days')")
            conn.commit()
            return len(metrics)
        except Exception as e:
            logger.error(f"保存控制器指标失败: {e}")
            conn.rollback()
            return 0

    def get_controller_metrics(self) -> Dict[str, Any]:
        """获取所有控制器最新指标"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM controller_metrics ORDER BY
                CASE role WHEN 'master' THEN 0 WHEN '1' THEN 0 ELSE 1 END, ip
        """).fetchall()
        return [dict(r) for r in rows]

    def get_controller_history(self, ip: str, hours: int = 24) -> List[Dict]:
        """获取指定控制器历史指标"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT cpu_pct, mem_pct, ap_count, sta_count, recorded_at
            FROM controller_metrics_history
            WHERE ip = ? AND recorded_at > datetime('now', ?)
            ORDER BY recorded_at
        """, (ip, f"-{hours} hours")).fetchall()
        return [dict(r) for r in rows]
