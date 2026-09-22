#!/bin/bash
# ============================================================
# 全自研无线网管平台 - 通用迁移部署脚本
# 目标系统: Ubuntu 24.04 Server
# 用法: ./deploy.sh <服务器IP> [用户名] [密码]
# 示例: ./deploy.sh 192.168.1.100 root mypassword
# ============================================================

set -e

# ============================================================
# 参数解析
# ============================================================
SERVER="${1:?用法: $0 <服务器IP> [用户名] [密码]}"
USER="${2:-root}"
PASS="${3:?请提供服务器密码 (第3个参数)}"
REMOTE_DIR="/opt/wireless-nms"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
SSH_CMD="sshpass -p '${PASS}' ssh ${SSH_OPTS} ${USER}@${SERVER}"
SCP_CMD="sshpass -p '${PASS}' scp ${SSH_OPTS}"

echo "============================================================"
echo " 全自研无线网管平台 - 迁移部署"
echo " 目标: ${USER}@${SERVER}"
echo " 源码: ${LOCAL_DIR}"
echo "============================================================"

# ============================================================
# Step 1: 检查前置条件
# ============================================================
echo ""
echo "[0/8] 检查前置条件..."
command -v sshpass >/dev/null 2>&1 || { echo "错误: 需要 sshpass (apt install sshpass)"; exit 1; }
echo "  -> sshpass 已安装"

# ============================================================
# Step 2: 安装系统依赖
# ============================================================
echo ""
echo "[1/8] 安装系统依赖..."
eval ${SSH_CMD} "apt update -qq && apt install -y -qq python3 python3-venv python3-pip nginx > /dev/null 2>&1 && echo OK"
echo "  -> 系统依赖安装完成"

# ============================================================
# Step 3: 创建远程目录
# ============================================================
echo ""
echo "[2/8] 创建远程目录结构..."
eval ${SSH_CMD} "mkdir -p ${REMOTE_DIR}/{lib,static/{css,js,images,floorplans},data,templates,scripts}"
echo "  -> 目录创建完成"

# ============================================================
# Step 4: 上传代码文件
# ============================================================
echo ""
echo "[3/8] 上传代码文件..."

# Python 核心文件
eval ${SCP_CMD} ${LOCAL_DIR}/app.py ${USER}@${SERVER}:${REMOTE_DIR}/
eval ${SCP_CMD} ${LOCAL_DIR}/config.py ${USER}@${SERVER}:${REMOTE_DIR}/
eval ${SCP_CMD} ${LOCAL_DIR}/requirements.txt ${USER}@${SERVER}:${REMOTE_DIR}/

# lib/
eval ${SCP_CMD} ${LOCAL_DIR}/lib/__init__.py ${USER}@${SERVER}:${REMOTE_DIR}/lib/
eval ${SCP_CMD} ${LOCAL_DIR}/lib/ap_merger.py ${USER}@${SERVER}:${REMOTE_DIR}/lib/
eval ${SCP_CMD} ${LOCAL_DIR}/lib/db_store.py ${USER}@${SERVER}:${REMOTE_DIR}/lib/
eval ${SCP_CMD} ${LOCAL_DIR}/lib/mock_data.py ${USER}@${SERVER}:${REMOTE_DIR}/lib/
eval ${SCP_CMD} ${LOCAL_DIR}/lib/snmp_oids.py ${USER}@${SERVER}:${REMOTE_DIR}/lib/
eval ${SCP_CMD} ${LOCAL_DIR}/lib/snmp_poller.py ${USER}@${SERVER}:${REMOTE_DIR}/lib/

# templates/
eval ${SCP_CMD} ${LOCAL_DIR}/templates/index.html ${USER}@${SERVER}:${REMOTE_DIR}/templates/
eval ${SCP_CMD} ${LOCAL_DIR}/templates/ap_status.html ${USER}@${SERVER}:${REMOTE_DIR}/templates/
eval ${SCP_CMD} ${LOCAL_DIR}/templates/home_ap.html ${USER}@${SERVER}:${REMOTE_DIR}/templates/

# static/css/
eval ${SCP_CMD} ${LOCAL_DIR}/static/css/style.css ${USER}@${SERVER}:${REMOTE_DIR}/static/css/

# static/js/
eval ${SCP_CMD} ${LOCAL_DIR}/static/js/ap-status.js ${USER}@${SERVER}:${REMOTE_DIR}/static/js/
eval ${SCP_CMD} ${LOCAL_DIR}/static/js/detail-panel.js ${USER}@${SERVER}:${REMOTE_DIR}/static/js/
eval ${SCP_CMD} ${LOCAL_DIR}/static/js/home_ap.js ${USER}@${SERVER}:${REMOTE_DIR}/static/js/

# scripts/
eval ${SCP_CMD} ${LOCAL_DIR}/scripts/init_db.py ${USER}@${SERVER}:${REMOTE_DIR}/scripts/
eval ${SCP_CMD} ${LOCAL_DIR}/scripts/test_snmp.py ${USER}@${SERVER}:${REMOTE_DIR}/scripts/

# 平面图素材 (如果存在)
if [ -f "${LOCAL_DIR}/static/images/lab.jpg" ]; then
    eval ${SCP_CMD} ${LOCAL_DIR}/static/images/lab.jpg ${USER}@${SERVER}:${REMOTE_DIR}/static/images/
    echo "  -> 平面图素材已上传"
fi

echo "  -> 代码上传完成"

# ============================================================
# Step 5: 安装 Python 环境和依赖
# ============================================================
echo ""
echo "[4/8] 安装 Python 虚拟环境和依赖..."
eval ${SSH_CMD} "cd ${REMOTE_DIR} && \
    python3 -m venv venv && \
    source venv/bin/activate && \
    pip install --upgrade pip -q && \
    pip install -r requirements.txt -q"
echo "  -> 依赖安装完成"

# ============================================================
# Step 6: 初始化数据库
# ============================================================
echo ""
echo "[5/8] 初始化数据库..."
eval ${SSH_CMD} "cd ${REMOTE_DIR} && source venv/bin/activate && python scripts/init_db.py"
echo "  -> 数据库初始化完成"

# ============================================================
# Step 7: 配置 systemd 服务
# ============================================================
echo ""
echo "[6/8] 配置 systemd 服务..."
eval ${SCP_CMD} ${LOCAL_DIR}/deploy/wireless-nms.service ${USER}@${SERVER}:/etc/systemd/system/
eval ${SSH_CMD} "systemctl daemon-reload && \
    systemctl enable wireless-nms && \
    systemctl restart wireless-nms"
echo "  -> 服务启动完成"

# ============================================================
# Step 8: 配置 Nginx
# ============================================================
echo ""
echo "[7/8] 配置 Nginx..."
eval ${SSH_CMD} "rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf 2>/dev/null || true"
eval ${SCP_CMD} ${LOCAL_DIR}/deploy/nginx-wireless-nms.conf ${USER}@${SERVER}:/etc/nginx/conf.d/wireless-nms.conf
eval ${SSH_CMD} "nginx -t && systemctl reload nginx"
echo "  -> Nginx 配置完成"

# ============================================================
# Step 9: 验证
# ============================================================
echo ""
echo "[8/8] 验证部署..."
sleep 3
eval ${SSH_CMD} "curl -s http://127.0.0.1:5577/api/health | python3 -m json.tool"

echo ""
echo "============================================================"
echo " 部署完成!"
echo " 访问地址: http://${SERVER}/"
echo " AP 状态: http://${SERVER}/ap-status"
echo " Home AP: http://${SERVER}/home-ap"
echo ""
echo " 提示: 请编辑 /opt/wireless-nms/config.py 配置控制器信息"
echo "============================================================"
