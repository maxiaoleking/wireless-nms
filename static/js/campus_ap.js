/**
 * 室外 AP 点位分布页 - 搜索 + 拖放
 */
(function () {
    'use strict';

    let apList = [];

    // 特定放装型型号前缀在线标记改用黄色区分（示例占位，按实际型号填写）
    const SPECIAL_MODEL_PREFIXES = ['MODEL-A', 'MODEL-B', 'MODEL-C', 'MODEL-D'];
    function isSpecialModel(model) {
        const m = (model || '').toUpperCase();
        return SPECIAL_MODEL_PREFIXES.some(p => m.startsWith(p));
    }
    function statusClassOf(ap) {
        if (ap.status !== 1) return 'offline';
        return isSpecialModel(ap.model) ? 'special' : 'online';
    }

    (function injectSpecialStyle() {
        const style = document.createElement('style');
        style.textContent = `
            @keyframes pulse-special {
                0%, 100% { box-shadow: 0 0 0 0 rgba(234, 179, 8, 0.6); }
                50% { box-shadow: 0 0 0 10px rgba(234, 179, 8, 0); }
            }
            .ap-marker-ring.special {
                background: #eab308;
                animation: pulse-special 2s ease-in-out infinite;
            }
            .ap-status-dot.special { background: #eab308; }
        `;
        document.head.appendChild(style);
    })();

    // 横幅追加 黄色放装AP / 蓝色室外AP 数量统计（仅统计已放置且在线的点位）
    const statSpecial = (() => {
        const bar = document.querySelector('.stats-bar');
        if (!bar || document.getElementById('stat-special')) return null;
        const mk = (id, label, color) => {
            const d = document.createElement('div');
            d.className = 'stat-item';
            const l = document.createElement('span');
            l.className = 'stat-label';
            l.style.whiteSpace = 'nowrap';
            l.textContent = label;
            const v = document.createElement('span');
            v.className = 'stat-value';
            v.style.whiteSpace = 'nowrap';
            v.id = id;
            v.style.color = color;
            v.textContent = '0';
            d.append(l, v);
            return d;
        };
        const yellow = mk('stat-special', '放装·黄', '#eab308');
        const blue = mk('stat-outdoor', '室外·蓝', '#3b82f6');
        bar.append(yellow, blue);
        return { yellow: yellow.querySelector('.stat-value'), blue: blue.querySelector('.stat-value') };
    })();

    let placedAPs = {};
    let searchKeyword = '';
    let zoomLevel = 1;

    const apPool = document.getElementById('ap-pool');
    const apSearch = document.getElementById('ap-search');
    const filterCount = document.getElementById('filter-count');
    const totalCount = document.getElementById('total-count');
    const placedCount = document.getElementById('placed-count');
    const statPlaced = document.getElementById('stat-placed');
    const statUnplaced = document.getElementById('stat-unplaced');
    const container = document.getElementById('map-container');
    const mapImg = document.getElementById('map-img');
    const btnSave = document.getElementById('btn-save');
    const btnClear = document.getElementById('btn-clear');
    const zoomInBtn = document.getElementById('zoom-in');
    const zoomOutBtn = document.getElementById('zoom-out');
    const zoomResetBtn = document.getElementById('zoom-reset');

    // ============================================================
    // 数据加载
    // ============================================================
    async function loadAPs() {
        try {
            const [apResp, coordsResp] = await Promise.all([
                fetch('/api/ap_status'),
                fetch('/api/campus-coords')
            ]);
            const apData = await apResp.json();
            apList = apData.aps || [];
            const coords = await coordsResp.json();
            placedAPs = coords || {};
            render();
        } catch (err) {
            apPool.innerHTML = `<div class="empty-state"><p>加载失败: ${err.message}</p></div>`;
        }
    }

    async function savePositions() {
        try {
            const resp = await fetch('/api/campus-coords', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(placedAPs)
            });
            return resp.ok;
        } catch { return false; }
    }

    // ============================================================
    // 渲染
    // ============================================================
    function render() {
        renderAPPool();
        renderMarkers();
        updateStats();
    }

    function renderAPPool() {
        const kw = searchKeyword.toLowerCase();
        const filtered = kw
            ? apList.filter(ap => ap.name && ap.name.toLowerCase().includes(kw))
            : apList;

        filterCount.textContent = filtered.length;
        totalCount.textContent = apList.length;

        if (filtered.length === 0) {
            apPool.innerHTML = '<div class="empty-state"><p>无匹配 AP</p></div>';
            return;
        }

        const placedNames = new Set(Object.keys(placedAPs));
        // 只显示前200条避免卡顿
        const show = filtered.slice(0, 200);

        apPool.innerHTML = show.map(ap => {
            const isPlaced = placedNames.has(ap.name);
            const statusClass = statusClassOf(ap);
            return `<div class="ap-pool-item ${isPlaced ? 'placed' : ''}"
                         draggable="${!isPlaced}"
                         data-ap-name="${esc(ap.name)}">
                <div class="ap-name">
                    <span class="ap-status-dot ${statusClass}"></span>
                    ${esc(ap.name)}
                </div>
                <div class="ap-meta">
                    IP: ${esc(ap.ip || '--')} | MD: ${esc(ap.active_md || '--')}
                </div>
            </div>`;
        }).join('');

        if (filtered.length > 200) {
            apPool.innerHTML += `<div style="text-align:center;padding:8px;color:var(--text-muted);font-size:12px">
                显示前 200 条，共 ${filtered.length} 条匹配。请缩小搜索范围。
            </div>`;
        }

        apPool.querySelectorAll('.ap-pool-item[draggable="true"]').forEach(item => {
            item.addEventListener('dragstart', onPoolDragStart);
            item.addEventListener('dragend', onDragEnd);
        });
    }

    function renderMarkers() {
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

            const statusClass = statusClassOf(ap);

            marker.innerHTML = `
                <div class="ap-marker-ring ${statusClass}">
                    <div class="ap-marker-dot"></div>
                </div>
                <div class="ap-marker-label">${esc(apName)}</div>
                <div class="ap-marker-tooltip">
                    <div class="tt-name">${esc(apName)}</div>
                    <div class="tt-detail">型号: ${esc(ap.model || '--')}</div>
                    <div class="tt-detail">IP: ${esc(ap.ip || '--')}</div>
                    <div class="tt-detail">MAC: ${esc(ap.mac_address || '--')}</div>
                    <div class="tt-detail">Active MD: ${esc(ap.active_md || '--')}</div>
                    <div class="tt-detail">状态: ${ap.status === 1 ? '在线' : '离线'}</div>
                </div>
            `;

            marker.addEventListener('mousedown', onMarkerDragStart);
            // 右键删除
            marker.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                if (confirm(`移除 ${apName} 的标记？`)) {
                    delete placedAPs[apName];
                    savePositions();
                    render();
                }
            });
            container.appendChild(marker);
        }
    }

    function updateStats() {
        const placed = Object.keys(placedAPs).length;
        statPlaced.textContent = placed;
        placedCount.textContent = placed;
        const onlineUnplaced = apList.filter(ap => ap.status === 1 && !placedAPs[ap.name]).length;
        statUnplaced.textContent = onlineUnplaced;
        if (statSpecial) {
            const apMap = {};
            apList.forEach(ap => { apMap[ap.name] = ap; });
            let yellow = 0, blue = 0;
            for (const name of Object.keys(placedAPs)) {
                const ap = apMap[name];
                if (!ap || ap.status !== 1) continue;
                if (isSpecialModel(ap.model)) yellow++; else blue++;
            }
            statSpecial.yellow.textContent = yellow;
            statSpecial.blue.textContent = blue;
        }
    }

    // ============================================================
    // 搜索
    // ============================================================
    let searchTimer = null;
    apSearch.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            searchKeyword = apSearch.value.trim();
            renderAPPool();
        }, 200);
    });

    // ============================================================
    // 拖拽: AP 列表 → 地图
    // ============================================================
    let dragData = null;

    function onPoolDragStart(e) {
        const item = e.target.closest('.ap-pool-item');
        if (!item) return;
        dragData = { type: 'new', apName: item.dataset.apName };
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', item.dataset.apName);

        const ghost = item.cloneNode(true);
        ghost.style.width = '200px';
        ghost.style.position = 'fixed';
        ghost.style.top = '-1000px';
        document.body.appendChild(ghost);
        e.dataTransfer.setDragImage(ghost, 100, 20);
        setTimeout(() => ghost.remove(), 0);
    }

    function onDragEnd() {
        dragData = null;
        container.classList.remove('drag-over');
    }

    container.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        container.classList.add('drag-over');
    });

    container.addEventListener('dragleave', (e) => {
        if (!container.contains(e.relatedTarget)) container.classList.remove('drag-over');
    });

    container.addEventListener('drop', (e) => {
        e.preventDefault();
        container.classList.remove('drag-over');
        if (!dragData || dragData.type !== 'new') return;

        const rect = mapImg.getBoundingClientRect();
        const x = Math.max(1, Math.min(99, ((e.clientX - rect.left) / rect.width) * 100));
        const y = Math.max(1, Math.min(99, ((e.clientY - rect.top) / rect.height) * 100));

        placedAPs[dragData.apName] = { x, y };
        savePositions();
        render();
        dragData = null;
    });

    // ============================================================
    // 拖拽: 地图上移动 AP
    // ============================================================
    let markerDrag = null;

    function onMarkerDragStart(e) {
        if (e.button !== 0) return;
        e.preventDefault();
        const marker = e.target.closest('.ap-placed-marker');
        if (!marker) return;

        markerDrag = {
            marker,
            apName: marker.dataset.apName,
            startMouseX: e.clientX,
            startMouseY: e.clientY,
            startPercentX: parseFloat(marker.style.left),
            startPercentY: parseFloat(marker.style.top),
        };

        document.addEventListener('mousemove', onMarkerDragMove);
        document.addEventListener('mouseup', onMarkerDragEnd);
    }

    function onMarkerDragMove(e) {
        if (!markerDrag) return;
        const rect = mapImg.getBoundingClientRect();
        const dx = ((e.clientX - markerDrag.startMouseX) / rect.width) * 100;
        const dy = ((e.clientY - markerDrag.startMouseY) / rect.height) * 100;
        markerDrag.marker.style.left = Math.max(1, Math.min(99, markerDrag.startPercentX + dx)) + '%';
        markerDrag.marker.style.top = Math.max(1, Math.min(99, markerDrag.startPercentY + dy)) + '%';
    }

    function onMarkerDragEnd(e) {
        if (!markerDrag) return;
        const rect = mapImg.getBoundingClientRect();
        const dx = ((e.clientX - markerDrag.startMouseX) / rect.width) * 100;
        const dy = ((e.clientY - markerDrag.startMouseY) / rect.height) * 100;
        const x = Math.max(1, Math.min(99, markerDrag.startPercentX + dx));
        const y = Math.max(1, Math.min(99, markerDrag.startPercentY + dy));
        placedAPs[markerDrag.apName] = { x, y };
        savePositions();
        renderMarkers();
        markerDrag = null;
        document.removeEventListener('mousemove', onMarkerDragMove);
        document.removeEventListener('mouseup', onMarkerDragEnd);
    }

    // ============================================================
    // 工具栏
    // ============================================================
    btnSave.addEventListener('click', async () => {
        btnSave.textContent = '⏳ 保存中...';
        const ok = await savePositions();
        btnSave.textContent = ok ? '✓ 已保存' : '✗ 保存失败';
        btnSave.style.color = ok ? 'var(--color-online)' : 'var(--color-offline)';
        setTimeout(() => { btnSave.textContent = '💾 保存'; btnSave.style.color = ''; }, 2000);
    });

    btnClear.addEventListener('click', () => {
        if (Object.keys(placedAPs).length === 0) return;
        if (confirm('确定清除所有已放置的 AP 吗？')) {
            placedAPs = {};
            savePositions();
            render();
        }
    });

    // ============================================================
    // 缩放
    // ============================================================
    function applyZoom() {
        container.style.transform = `scale(${zoomLevel})`;
        container.style.transformOrigin = 'top left';
        container.style.width = (100 / zoomLevel) + '%';
    }

    zoomInBtn.addEventListener('click', () => { zoomLevel = Math.min(4, zoomLevel + 0.25); applyZoom(); });
    zoomOutBtn.addEventListener('click', () => { zoomLevel = Math.max(0.25, zoomLevel - 0.25); applyZoom(); });
    zoomResetBtn.addEventListener('click', () => { zoomLevel = 1; applyZoom(); });

    // ============================================================
    // 工具
    // ============================================================
    function esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ============================================================
    // 初始化
    // ============================================================
    loadAPs();
})();
