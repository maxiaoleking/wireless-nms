/**
 * 全自研无线网管平台 - AP 区域状态页
 * Leaflet CRS.Simple + 网格自动排列 + 30s 自动刷新
 */

// ============================================================
// 全局状态
// ============================================================
let map = null;
let markersLayer = null;
let allAps = [];
let currentRegion = '';
let currentMd = '';  // 当前选中的 MD IP (空=不按 MD 过滤)
let refreshTimer = null;

// 网格排列参数 (固定坐标空间, 不依赖 getBounds)
const GRID_COLS = 50;
const GRID_SPACING_X = 20;
const GRID_SPACING_Y = 14;
const GRID_START_X = 10;
const GRID_START_Y = 10;
const MAP_WIDTH = 1000;
const MAP_HEIGHT = 700;

// ============================================================
// 初始化
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    bindEvents();
    loadRegions();
    loadApStatus();
    startAutoRefresh();
});

function initMap() {
    map = L.map('map', {
        crs: L.CRS.Simple,
        minZoom: -3,
        maxZoom: 3,
        zoomControl: true,
        attributionControl: false,
    });

    // 设置初始视图
    const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
    map.fitBounds(bounds);

    // 添加浅色网格背景
    const gridCanvas = createGridBackground();
    L.imageOverlay(gridCanvas, bounds).addTo(map);

    // 标记层
    markersLayer = L.layerGroup().addTo(map);

    // 地图点击事件 (关闭详情面板)
    map.on('click', () => {
        hideDetail();
    });
}

function createGridBackground() {
    const canvas = document.createElement('canvas');
    canvas.width = MAP_WIDTH;
    canvas.height = MAP_HEIGHT;
    const ctx = canvas.getContext('2d');

    // 透明背景 (透出底层渐变)
    ctx.clearRect(0, 0, MAP_WIDTH, MAP_HEIGHT);

    // 浅色网格线
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
    ctx.lineWidth = 1;
    for (let x = 0; x <= MAP_WIDTH; x += 50) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, MAP_HEIGHT);
        ctx.stroke();
    }
    for (let y = 0; y <= MAP_HEIGHT; y += 50) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(MAP_WIDTH, y);
        ctx.stroke();
    }

    return canvas.toDataURL();
}

function bindEvents() {
    // 区域切换
    document.getElementById('region-select').addEventListener('change', (e) => {
        currentRegion = e.target.value;
        loadApStatus();
        highlightRegionItem();
    });

    // AP 搜索
    const searchInput = document.getElementById('ap-search');
    let searchDebounce = null;
    searchInput.addEventListener('input', (e) => {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(() => {
            doSearch(e.target.value.trim());
        }, 300);
    });

    // 详情关闭
    document.getElementById('detail-close').addEventListener('click', hideDetail);
}

// ============================================================
// 数据加载
// ============================================================
async function loadApStatus() {
    try {
        const params = new URLSearchParams();
        if (currentRegion) params.set('region', currentRegion);
        if (currentMd) params.set('active_md', currentMd);
        const qs = params.toString();
        const resp = await fetch(`/api/ap_status${qs ? '?' + qs : ''}`);
        const data = await resp.json();

        allAps = data.aps || [];
        updateStats(data.stats);
        renderAPs(allAps);
        updateOfflineList(allAps.filter(ap => ap.status === 0));
        document.getElementById('last-update').textContent = formatTime(data.timestamp);
    } catch (err) {
        console.error('[AP Status] 加载失败:', err);
    }
}

async function loadRegions() {
    try {
        const resp = await fetch('/api/regions');
        const regions = await resp.json();
        renderRegionStats(regions);
    } catch (err) {
        console.error('[Regions] 加载失败:', err);
    }
}

// ============================================================
// 统计更新
// ============================================================
function updateStats(stats) {
    document.getElementById('stat-total').textContent = stats.total || 0;
    document.getElementById('stat-online').textContent = stats.online || 0;
    document.getElementById('stat-offline').textContent = stats.offline || 0;
    document.getElementById('stat-offline-rate').textContent = (stats.offline_rate || 0) + '%';
}

function renderRegionStats(regions) {
    const container = document.getElementById('region-stats');
    let html = '';

    // 全部
    const totalAll = regions.reduce((s, r) => s + r.total, 0);
    const onlineAll = regions.reduce((s, r) => s + r.online, 0);
    const offlineAll = regions.reduce((s, r) => s + r.offline, 0);

    html += `<div class="region-stat-item ${!currentRegion ? 'active' : ''}" data-region="">
        <span class="region-stat-name">全部区域</span>
        <span class="region-stat-count">
            <span class="online">${onlineAll}</span> /
            <span class="offline">${offlineAll}</span> /
            ${totalAll}
        </span>
    </div>`;

    for (const r of regions) {
        const name = r.name || r.region;
        html += `<div class="region-stat-item ${currentRegion === r.region ? 'active' : ''}"
                      data-region="${r.region}">
            <span class="region-stat-name">${name}</span>
            <span class="region-stat-count">
                <span class="online">${r.online}</span> /
                <span class="offline">${r.offline}</span> /
                ${r.total}
            </span>
        </div>`;

        // 集群模式下展示各 MD 节点
        if (r.is_cluster && r.md_stats && r.md_stats.length > 0) {
            for (const md of r.md_stats) {
                if (!md.md_ip) continue;  // 跳过无 MD 信息的过期数据
                const mdIp = md.md_ip;
                const isActive = currentMd === mdIp;
                html += `<div class="region-stat-item md-node ${isActive ? 'active' : ''}" data-region="${r.region}" data-md="${mdIp}">
                    <span class="region-stat-name" style="padding-left:16px;font-size:12px">
                        &#9492; ${mdIp}
                    </span>
                    <span class="region-stat-count" style="font-size:12px">
                        <span class="online">${md.online || 0}</span> /
                        <span class="offline">${md.offline || 0}</span> /
                        ${md.total || 0}
                    </span>
                </div>`;
            }
        }
    }

    container.innerHTML = html;

    // 绑定点击 - 区域节点
    container.querySelectorAll('.region-stat-item:not(.md-node)').forEach(item => {
        item.addEventListener('click', () => {
            const region = item.dataset.region;
            currentRegion = region;
            currentMd = '';  // 切换区域时重置 MD 过滤
            document.getElementById('region-select').value = region;
            loadApStatus();
            highlightRegionItem();
        });
    });

    // 绑定点击 - MD 节点
    container.querySelectorAll('.region-stat-item.md-node').forEach(item => {
        item.addEventListener('click', () => {
            const mdIp = item.dataset.md;
            currentMd = currentMd === mdIp ? '' : mdIp;  // 再次点击取消选中
            currentRegion = item.dataset.region;
            document.getElementById('region-select').value = currentRegion;
            loadApStatus();
            highlightRegionItem();
            highlightMdItem();
        });
    });
}

function highlightRegionItem() {
    document.querySelectorAll('.region-stat-item:not(.md-node)').forEach(item => {
        item.classList.toggle('active', item.dataset.region === currentRegion);
    });
    highlightMdItem();
}

function highlightMdItem() {
    document.querySelectorAll('.region-stat-item.md-node').forEach(item => {
        item.classList.toggle('active', item.dataset.md === currentMd && currentMd !== '');
    });
}

// ============================================================
// AP 渲染 (Leaflet CRS.Simple + 固定坐标网格)
// ============================================================
function renderAPs(aps) {
    markersLayer.clearLayers();

    aps.forEach((ap, idx) => {
        // 计算坐标: 有坐标用坐标, 无坐标用网格自动排列
        let x, y;
        if (ap.x != null && ap.y != null) {
            x = ap.x;
            y = ap.y;
        } else {
            x = GRID_START_X + (idx % GRID_COLS) * GRID_SPACING_X;
            y = GRID_START_Y + Math.floor(idx / GRID_COLS) * GRID_SPACING_Y;
        }

        const statusClass = ap.status === 1 ? 'online' : 'offline';
        const icon = L.divIcon({
            className: 'ap-marker',
            html: `<div class="ap-dot ${statusClass}" title="${ap.name}"></div>`,
            iconSize: [12, 12],
            iconAnchor: [6, 6],
        });

        const marker = L.marker([y, x], { icon, riseOnHover: true });

        // Tooltip (hover)
        marker.bindTooltip(ap.name, {
            direction: 'top',
            offset: [0, -8],
            className: 'ap-tooltip',
        });

        // Popup (click)
        const popupHtml = createPopupHtml(ap);
        marker.bindPopup(popupHtml, {
            maxWidth: 300,
            className: 'ap-popup',
        });

        // 显式绑定 click 阻止冒泡
        marker.on('click', function(e) {
            L.DomEvent.stopPropagation(e);
            marker.openPopup();
            showDetail(ap);
        });

        marker.addTo(markersLayer);
    });

    document.getElementById('map-ap-count').textContent = aps.length;
}

function createPopupHtml(ap) {
    const statusClass = ap.status === 1 ? 'online' : 'offline';
    const statusText = ap.status === 1 ? '在线' : '离线';
    const rxMbps = ((ap.rx_throughput || 0) * 8 / 1000000).toFixed(2);
    const txMbps = ((ap.tx_throughput || 0) * 8 / 1000000).toFixed(2);
    const activeMd = ap.active_md || '--';

    return `
        <div class="popup-title">${ap.name}</div>
        <div class="popup-row">
            <span class="popup-label">状态</span>
            <span class="popup-status ${statusClass}">${statusText}</span>
        </div>
        <div class="popup-row">
            <span class="popup-label">MAC</span>
            <span class="popup-value">${ap.mac_address || '--'}</span>
        </div>
        <div class="popup-row">
            <span class="popup-label">IP</span>
            <span class="popup-value">${ap.ip || '--'}</span>
        </div>
        <div class="popup-row">
            <span class="popup-label">型号</span>
            <span class="popup-value">${ap.model || '--'}</span>
        </div>
        <div class="popup-row">
            <span class="popup-label">Active MD</span>
            <span class="popup-value">${activeMd}</span>
        </div>
        <div class="popup-row">
            <span class="popup-label">终端数</span>
            <span class="popup-value">${ap.total_clients || 0}</span>
        </div>
        <div class="popup-row">
            <span class="popup-label">吞吐</span>
            <span class="popup-value">&darr;${rxMbps} / &uarr;${txMbps} Mbps</span>
        </div>
    `;
}

// ============================================================
// AP 详情面板
// ============================================================
async function showDetail(ap) {
    const panel = document.getElementById('detail-panel');
    const content = document.getElementById('detail-content');
    panel.classList.remove('hidden');

    try {
        const resp = await fetch(`/api/ap_detail/${encodeURIComponent(ap.name)}`);
        const detail = await resp.json();
        renderDetail(detail);
    } catch {
        renderDetail(ap);
    }
}

function renderDetail(ap) {
    const content = document.getElementById('detail-content');
    const statusClass = ap.status === 1 ? 'online' : 'offline';
    const statusText = ap.status === 1 ? '在线' : '离线';
    const rxMbps = ((ap.rx_throughput || 0) * 8 / 1000000).toFixed(2);
    const txMbps = ((ap.tx_throughput || 0) * 8 / 1000000).toFixed(2);
    const activeMD = ap.active_md || '--';

    let radiosHtml = '';
    if (ap.radios && ap.radios.length > 0) {
        for (const r of ap.radios) {
            radiosHtml += `
                <div class="radio-card">
                    <div class="radio-card-header">
                        <span class="radio-type ${r.radio_type}">${r.radio_type === 'dot11a' ? '5GHz' : '2.4GHz'}</span>
                        <span style="font-size:12px;color:var(--text-muted)">Radio ${r.radio_number}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">信道</span>
                        <span class="detail-value">${r.channel || '--'}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">发射功率</span>
                        <span class="detail-value">${r.tx_power || '--'} dBm</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">底噪</span>
                        <span class="detail-value">${r.noise_floor || '--'} dBm</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">客户端</span>
                        <span class="detail-value">${r.clients || 0}</span>
                    </div>
                </div>
            `;
        }
    }

    content.innerHTML = `
        <div class="detail-section">
            <div class="detail-section-title">基本信息</div>
            <div class="detail-row">
                <span class="detail-label">名称</span>
                <span class="detail-value" style="color:var(--text-primary);font-weight:700">${ap.name}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">状态</span>
                <span class="detail-value"><span class="popup-status ${statusClass}">${statusText}</span></span>
            </div>
            <div class="detail-row">
                <span class="detail-label">MAC</span>
                <span class="detail-value">${ap.mac_address || '--'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">IP</span>
                <span class="detail-value">${ap.ip || ap.ip_address || '--'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">型号</span>
                <span class="detail-value">${ap.model || '--'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">序列号</span>
                <span class="detail-value">${ap.serial_number || '--'}</span>
            </div>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">控制器 & 位置</div>
            <div class="detail-row">
                <span class="detail-label">控制器</span>
                <span class="detail-value">${ap.controller || ap.region || '--'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Active MD</span>
                <span class="detail-value" style="color:var(--accent);font-weight:600">${activeMD}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">楼宇</span>
                <span class="detail-value">${ap.building || '--'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">楼层</span>
                <span class="detail-value">${ap.floor || '--'}</span>
            </div>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">流量</div>
            <div class="detail-row">
                <span class="detail-label">终端数</span>
                <span class="detail-value" style="color:var(--text-primary)">${ap.total_clients || 0}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">接收</span>
                <span class="detail-value">&darr; ${rxMbps} Mbps</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">发送</span>
                <span class="detail-value">&uarr; ${txMbps} Mbps</span>
            </div>
        </div>

        ${radiosHtml ? `
        <div class="detail-section">
            <div class="detail-section-title">射频详情</div>
            ${radiosHtml}
        </div>` : ''}

        <div class="detail-section">
            <div class="detail-section-title">时间</div>
            <div class="detail-row">
                <span class="detail-label">最后更新</span>
                <span class="detail-value">${formatTime(ap.updated_at || ap.last_seen)}</span>
            </div>
        </div>
    `;
}

function hideDetail() {
    document.getElementById('detail-panel').classList.add('hidden');
}

// ============================================================
// 搜索
// ============================================================
function doSearch(keyword) {
    const results = document.getElementById('search-results');
    if (!keyword) {
        results.innerHTML = '';
        return;
    }

    const kw = keyword.toLowerCase();
    const matched = allAps.filter(ap =>
        (ap.name && ap.name.toLowerCase().includes(kw)) ||
        (ap.mac_address && ap.mac_address.toLowerCase().includes(kw))
    ).slice(0, 50);

    if (matched.length === 0) {
        results.innerHTML = '<div style="padding:8px;color:var(--text-muted);font-size:12px">无匹配结果</div>';
        return;
    }

    results.innerHTML = matched.map(ap => {
        const dotClass = ap.status === 1 ? 'online' : 'offline';
        return `<div class="search-result-item" data-ap="${ap.name}">
            <span class="search-result-dot ${dotClass}"></span>
            <span>${ap.name}</span>
        </div>`;
    }).join('');

    // 绑定点击
    results.querySelectorAll('.search-result-item').forEach(item => {
        item.addEventListener('click', () => {
            const apName = item.dataset.ap;
            const ap = allAps.find(a => a.name === apName);
            if (ap) showDetail(ap);
        });
    });
}

// ============================================================
// 离线列表
// ============================================================
function updateOfflineList(offlineAps) {
    const container = document.getElementById('offline-list');
    const badge = document.getElementById('offline-count');
    badge.textContent = offlineAps.length;

    if (offlineAps.length === 0) {
        container.innerHTML = '<div style="padding:8px;color:var(--text-muted);font-size:12px;text-align:center">暂无离线 AP</div>';
        return;
    }

    container.innerHTML = offlineAps.slice(0, 100).map(ap => {
        return `<div class="offline-item" data-ap="${ap.name}">
            <span class="offline-item-name">${ap.name}</span>
            <span class="offline-item-region">${ap.controller || ''}</span>
        </div>`;
    }).join('');

    container.querySelectorAll('.offline-item').forEach(item => {
        item.addEventListener('click', () => {
            const apName = item.dataset.ap;
            const ap = allAps.find(a => a.name === apName);
            if (ap) showDetail(ap);
        });
    });
}

// ============================================================
// 自动刷新
// ============================================================
function startAutoRefresh() {
    const interval = (window.REFRESH_INTERVAL || 30) * 1000;
    refreshTimer = setInterval(() => {
        loadApStatus();
        loadRegions();
    }, interval);
}

// ============================================================
// 工具函数
// ============================================================
function formatTime(ts) {
    if (!ts) return '--';
    try {
        const d = new Date(ts);
        return d.toLocaleTimeString('zh-CN', { hour12: false });
    } catch {
        return ts;
    }
}
