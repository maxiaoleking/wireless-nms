/**
 * Home AP 布局页面 - 拖放逻辑
 * 功能:
 *   - 从左侧 AP 列表拖拽 AP 到右侧平面图
 *   - 已放置的 AP 可在平面图上自由拖动
 *   - 位置保存到 localStorage (按区域)
 *   - 支持缩放
 */
(function () {
    'use strict';

    // ============================================================
    // 状态管理
    // ============================================================
    const STORAGE_KEY = 'home_ap_layout';
    let currentRegion = '';
    let apList = [];          // 当前控制器的 AP 列表
    let placedAPs = {};       // { apName: { x, y } } 百分比坐标
    let zoomLevel = 1;

    // ============================================================
    // DOM 引用
    // ============================================================
    const regionSelect = document.getElementById('region-select');
    const apPool = document.getElementById('ap-pool');
    const apCountEl = document.getElementById('ap-count');
    const statPlaced = document.getElementById('stat-placed');
    const statUnplaced = document.getElementById('stat-unplaced');
    const container = document.getElementById('floorplan-container');
    const floorplanArea = document.getElementById('floorplan-area');
    const btnSave = document.getElementById('btn-save');
    const btnClear = document.getElementById('btn-clear');
    const zoomInBtn = document.getElementById('zoom-in');
    const zoomOutBtn = document.getElementById('zoom-out');
    const zoomResetBtn = document.getElementById('zoom-reset');

    // ============================================================
    // 数据加载
    // ============================================================

    async function loadAPs(regionKey) {
        currentRegion = regionKey;
        apPool.innerHTML = '<div class="empty-state"><p>加载中...</p></div>';

        try {
            const resp = await fetch(`/api/ap_status?region=${encodeURIComponent(regionKey)}`);
            const data = await resp.json();
            apList = data.aps || [];
            loadPlacedPositions();
            renderAPPool();
            renderPlacedMarkers();
            updateStats();
        } catch (err) {
            apPool.innerHTML = `<div class="empty-state"><p>加载失败: ${err.message}</p></div>`;
        }
    }

    function loadPlacedPositions() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved) {
                const all = JSON.parse(saved);
                placedAPs = all[currentRegion] || {};
            } else {
                placedAPs = {};
            }
        } catch {
            placedAPs = {};
        }
    }

    function savePlacedPositions() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            const all = saved ? JSON.parse(saved) : {};
            all[currentRegion] = placedAPs;
            localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
        } catch (e) {
            console.error('保存失败:', e);
        }
    }

    // ============================================================
    // 渲染 AP 列表
    // ============================================================

    function renderAPPool() {
        if (apList.length === 0) {
            apPool.innerHTML = '<div class="empty-state"><div class="icon">&#128225;</div><p>该控制器暂无 AP 数据</p></div>';
            apCountEl.textContent = '(0)';
            return;
        }

        apCountEl.textContent = `(${apList.length})`;
        const placedNames = new Set(Object.keys(placedAPs));

        apPool.innerHTML = apList.map(ap => {
            const isPlaced = placedNames.has(ap.name);
            const statusClass = ap.status === 1 ? 'online' : 'offline';
            return `
                <div class="ap-pool-item ${isPlaced ? 'placed' : ''}"
                     draggable="${!isPlaced}"
                     data-ap-name="${escapeHtml(ap.name)}"
                     data-ap-mac="${escapeHtml(ap.mac_address || '')}"
                     data-ap-status="${ap.status}">
                    <div class="ap-name">
                        <span class="ap-status-dot ${statusClass}"></span>
                        ${escapeHtml(ap.name)}
                    </div>
                    <div class="ap-meta">
                        MAC: ${escapeHtml(ap.mac_address || '--')} | IP: ${escapeHtml(ap.ip_address || '--')}
                    </div>
                </div>
            `;
        }).join('');

        // 绑定拖拽事件
        apPool.querySelectorAll('.ap-pool-item[draggable="true"]').forEach(item => {
            item.addEventListener('dragstart', onPoolDragStart);
            item.addEventListener('dragend', onDragEnd);
        });
    }

    // ============================================================
    // 渲染已放置的 AP 标记
    // ============================================================

    function renderPlacedMarkers() {
        // 清除旧标记
        container.querySelectorAll('.ap-placed-marker').forEach(el => el.remove());

        const apMap = {};
        apList.forEach(ap => { apMap[ap.name] = ap; });

        for (const [apName, pos] of Object.entries(placedAPs)) {
            const ap = apMap[apName];
            if (!ap) continue;

            const marker = document.createElement('div');
            marker.className = 'ap-placed-marker';
            marker.style.left = pos.x + '%';
            marker.style.top = pos.y + '%';
            marker.dataset.apName = apName;

            const statusClass = ap.status === 1 ? 'online' : 'offline';

            marker.innerHTML = `
                <div class="ap-marker-ring ${statusClass}">
                    <div class="ap-marker-dot"></div>
                    <span class="ap-marker-star">&#11088;</span>
                </div>
                <div class="ap-marker-tooltip">
                    <div class="tt-name">${escapeHtml(apName)}</div>
                    <div class="tt-detail">MAC: ${escapeHtml(ap.mac_address || '--')}</div>
                    <div class="tt-detail">IP: ${escapeHtml(ap.ip_address || '--')}</div>
                    <div class="tt-detail">位置: (${Math.round(pos.x)}%, ${Math.round(pos.y)}%)</div>
                </div>
            `;

            // 已放置的 AP 可以拖动重新定位
            marker.addEventListener('mousedown', onMarkerDragStart);
            container.appendChild(marker);
        }
    }

    function updateStats() {
        const placedCount = Object.keys(placedAPs).length;
        statPlaced.textContent = placedCount;
        statUnplaced.textContent = apList.length - placedCount;
    }

    // ============================================================
    // 拖拽: 从 AP 列表拖到平面图
    // ============================================================

    let dragData = null;  // { type: 'new'|'move', apName, offsetX, offsetY }

    function onPoolDragStart(e) {
        const item = e.target.closest('.ap-pool-item');
        if (!item) return;

        dragData = {
            type: 'new',
            apName: item.dataset.apName,
        };

        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', item.dataset.apName);

        // 自定义拖拽图像
        const ghost = item.cloneNode(true);
        ghost.style.width = '200px';
        ghost.style.position = 'fixed';
        ghost.style.top = '-1000px';
        document.body.appendChild(ghost);
        e.dataTransfer.setDragImage(ghost, 100, 20);
        setTimeout(() => ghost.remove(), 0);
    }

    function onDragEnd(e) {
        dragData = null;
        container.classList.remove('drag-over');
    }

    // 平面图接受拖放
    container.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        container.classList.add('drag-over');
    });

    container.addEventListener('dragleave', (e) => {
        if (!container.contains(e.relatedTarget)) {
            container.classList.remove('drag-over');
        }
    });

    container.addEventListener('drop', (e) => {
        e.preventDefault();
        container.classList.remove('drag-over');

        if (!dragData || dragData.type !== 'new') return;

        const rect = container.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 100;
        const y = ((e.clientY - rect.top) / rect.height) * 100;

        // 限制在 0-100% 范围内
        const clampedX = Math.max(2, Math.min(98, x));
        const clampedY = Math.max(2, Math.min(98, y));

        placedAPs[dragData.apName] = { x: clampedX, y: clampedY };
        savePlacedPositions();
        renderAPPool();
        renderPlacedMarkers();
        updateStats();
        dragData = null;
    });

    // ============================================================
    // 拖拽: 平面图上移动已放置的 AP
    // ============================================================

    let markerDrag = null;

    function onMarkerDragStart(e) {
        if (e.button !== 0) return;
        e.preventDefault();

        const marker = e.target.closest('.ap-placed-marker');
        if (!marker) return;

        const rect = container.getBoundingClientRect();
        const startX = e.clientX;
        const startY = e.clientY;

        markerDrag = {
            marker,
            apName: marker.dataset.apName,
            startMouseX: startX,
            startMouseY: startY,
            startPercentX: parseFloat(marker.style.left),
            startPercentY: parseFloat(marker.style.top),
        };

        document.addEventListener('mousemove', onMarkerDragMove);
        document.addEventListener('mouseup', onMarkerDragEnd);
    }

    function onMarkerDragMove(e) {
        if (!markerDrag) return;

        const rect = container.getBoundingClientRect();
        const dx = e.clientX - markerDrag.startMouseX;
        const dy = e.clientY - markerDrag.startMouseY;
        const dxPercent = (dx / rect.width) * 100;
        const dyPercent = (dy / rect.height) * 100;

        const newX = Math.max(2, Math.min(98, markerDrag.startPercentX + dxPercent));
        const newY = Math.max(2, Math.min(98, markerDrag.startPercentY + dyPercent));

        markerDrag.marker.style.left = newX + '%';
        markerDrag.marker.style.top = newY + '%';
    }

    function onMarkerDragEnd(e) {
        if (!markerDrag) return;

        const rect = container.getBoundingClientRect();
        const dx = e.clientX - markerDrag.startMouseX;
        const dy = e.clientY - markerDrag.startMouseY;
        const dxPercent = (dx / rect.width) * 100;
        const dyPercent = (dy / rect.height) * 100;

        const newX = Math.max(2, Math.min(98, markerDrag.startPercentX + dxPercent));
        const newY = Math.max(2, Math.min(98, markerDrag.startPercentY + dyPercent));

        placedAPs[markerDrag.apName] = { x: newX, y: newY };
        savePlacedPositions();
        renderPlacedMarkers();

        markerDrag = null;
        document.removeEventListener('mousemove', onMarkerDragMove);
        document.removeEventListener('mouseup', onMarkerDragEnd);
    }

    // ============================================================
    // 工具栏操作
    // ============================================================

    btnSave.addEventListener('click', () => {
        savePlacedPositions();
        // 视觉反馈
        btnSave.textContent = '✓ 已保存';
        btnSave.style.color = 'var(--color-online)';
        setTimeout(() => {
            btnSave.textContent = '💾 保存';
            btnSave.style.color = '';
        }, 1500);
    });

    btnClear.addEventListener('click', () => {
        if (Object.keys(placedAPs).length === 0) return;
        if (confirm('确定清除所有已放置的 AP 吗？')) {
            placedAPs = {};
            savePlacedPositions();
            renderAPPool();
            renderPlacedMarkers();
            updateStats();
        }
    });

    // ============================================================
    // 缩放
    // ============================================================

    function applyZoom() {
        container.style.transform = `scale(${zoomLevel})`;
        container.style.transformOrigin = 'top left';
        container.style.width = (100 / zoomLevel) + '%';
        container.style.height = 'auto';
    }

    zoomInBtn.addEventListener('click', () => {
        zoomLevel = Math.min(3, zoomLevel + 0.25);
        applyZoom();
    });

    zoomOutBtn.addEventListener('click', () => {
        zoomLevel = Math.max(0.5, zoomLevel - 0.25);
        applyZoom();
    });

    zoomResetBtn.addEventListener('click', () => {
        zoomLevel = 1;
        applyZoom();
    });

    // ============================================================
    // 区域切换
    // ============================================================

    regionSelect.addEventListener('change', () => {
        loadAPs(regionSelect.value);
    });

    // ============================================================
    // 工具函数
    // ============================================================

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ============================================================
    // 初始化
    // ============================================================

    // 默认选择第一个区域
    if (regionSelect.options.length > 0) {
        regionSelect.selectedIndex = 0;
        loadAPs(regionSelect.value);
    }

})();
