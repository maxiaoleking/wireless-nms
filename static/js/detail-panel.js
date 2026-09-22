/**
 * AP 详情面板模块
 * 独立管理 AP 详情的加载、渲染和交互
 */

const DetailPanel = (function() {
    let panelEl = null;
    let contentEl = null;
    let currentAp = null;

    function init() {
        panelEl = document.getElementById('detail-panel');
        contentEl = document.getElementById('detail-content');

        if (!panelEl || !contentEl) {
            console.warn('[DetailPanel] 面板元素未找到');
            return;
        }

        // 关闭按钮
        const closeBtn = document.getElementById('detail-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', hide);
        }

        // 点击地图空白区域关闭
        if (typeof map !== 'undefined' && map) {
            map.on('click', hide);
        }
    }

    /**
     * 显示 AP 详情
     * @param {Object} ap - AP 基础信息 (从列表数据)
     */
    async function show(ap) {
        if (!panelEl) init();
        if (!panelEl) return;

        currentAp = ap;
        panelEl.classList.remove('hidden');
        contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">加载中...</div>';

        try {
            const resp = await fetch(`/api/ap_detail/${encodeURIComponent(ap.name)}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const detail = await resp.json();
            render(detail);
        } catch (err) {
            console.error('[DetailPanel] 加载详情失败:', err);
            render(ap); // fallback 到列表数据
        }
    }

    /**
     * 渲染详情内容
     * @param {Object} ap - AP 完整信息
     */
    function render(ap) {
        const statusClass = ap.status === 1 ? 'online' : 'offline';
        const statusText = ap.status === 1 ? '在线' : '离线';
        const rxMbps = ((ap.rx_throughput || 0) * 8 / 1000000).toFixed(2);
        const txMbps = ((ap.tx_throughput || 0) * 8 / 1000000).toFixed(2);

        let radiosHtml = '';
        if (ap.radios && ap.radios.length > 0) {
            for (const r of ap.radios) {
                const freqLabel = r.radio_type === 'dot11a' ? '5GHz' : '2.4GHz';
                radiosHtml += `
                    <div class="radio-card">
                        <div class="radio-card-header">
                            <span class="radio-type ${r.radio_type}">${freqLabel}</span>
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

        contentEl.innerHTML = `
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

    function hide() {
        if (panelEl) {
            panelEl.classList.add('hidden');
        }
        currentAp = null;
    }

    function getCurrent() {
        return currentAp;
    }

    function formatTime(ts) {
        if (!ts) return '--';
        try {
            return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false });
        } catch {
            return ts;
        }
    }

    // 公共 API
    return { init, show, hide, getCurrent };
})();

// DOM 就绪后初始化
document.addEventListener('DOMContentLoaded', () => DetailPanel.init());
