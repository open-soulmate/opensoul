/* ── API ───────────────────────────────────────────────── */
const API = {
    base: '/api',
    async get(path) {
        const res = await fetch(`${this.base}${path}`);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
    async post(path, body) {
        const res = await fetch(`${this.base}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
    async put(path, body) {
        const res = await fetch(`${this.base}${path}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
    async del(path) {
        const res = await fetch(`${this.base}${path}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
};

/* ── Toast ─────────────────────────────────────── */
const Toast = {
    container: null,
    init() {
        this.container = document.createElement('div');
        this.container.className = 'toast-container';
        document.body.appendChild(this.container);
    },
    show(message, type = 'success') {
        if (!this.container) this.init();
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.textContent = message;
        this.container.appendChild(el);
        setTimeout(() => el.remove(), 3000);
    },
};

/* ── Router ────────────────────────────────────── */
const Router = {
    pages: {},
    currentPage: null,

    register(name, loader) {
        this.pages[name] = loader;
    },

    async navigate(page) {
        if (this.currentPage === page) return;
        this.currentPage = page;

        // Update nav active state
        document.querySelectorAll('.nav-item').forEach(el => {
            el.classList.toggle('active', el.dataset.page === page);
        });

        // Update page title
        const titles = {
            dashboard: '仪表盘',
            knowledge: '知识库',
            graph: '知识图谱',
            search: '搜索',
            agents: 'Agent节点',
            marketplace: '技能源管理',
            'agent-sources': 'Agent源管理',
            settings: '系统设置',
            llm: 'LLM配置',
        };
        document.getElementById('page-title').textContent = titles[page] || page;

        // Load page content
        const container = document.getElementById('page-content');
        container.innerHTML = '<div class="loading"><div class="spinner"></div>加载中...</div>';

        try {
            if (this.pages[page]) {
                container.innerHTML = await this.pages[page]();
            } else {
                container.innerHTML = await this.loadPageHTML(page);
            }
            if (window[`init_${page}`]) window[`init_${page}`]();
        } catch (e) {
            container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠</div><div class="empty-state-text">加载失败: ${e.message}</div></div>`;
        }
    },

    async loadPageHTML(page) {
        const res = await fetch(`/static/pages/${page}.html`);
        if (!res.ok) throw new Error('Page not found');
        return res.text();
    },

    init() {
        window.addEventListener('hashchange', () => {
            const page = location.hash.slice(1) || 'dashboard';
            this.navigate(page);
        });
        const page = location.hash.slice(1) || 'dashboard';
        this.navigate(page);
    },
};

/* ── Dashboard Stats ──────────────────────────── */
async function loadDashboardStats() {
    try {
        const [health, version] = await Promise.all([
            API.get('/vital/health').catch(() => null),
            API.get('/version').catch(() => null),
        ]);

        if (health) {
            const statusEl = document.getElementById('header-status');
            statusEl.textContent = health.status === 'healthy' ? '正常' : health.status;
            statusEl.className = `badge ${health.status === 'healthy' ? 'badge-ok' : 'badge-warn'}`;
        }
        if (version) {
            document.getElementById('header-version').textContent = `v${version.version}`;
        }

        try {
            const metricsRes = await fetch('/api/vital/metrics');
            const metricsText = await metricsRes.text();
            const metrics = {};
            metricsText.split('\n').forEach(line => {
                const [name, value] = line.trim().split(/\s+/);
                if (name && value) metrics[name] = parseFloat(value);
            });

            const el = (id) => document.getElementById(id);
            if (el('stat-knowledge')) el('stat-knowledge').textContent = Math.round(metrics.vital_knowledge_entries || 0);
            if (el('stat-agents')) el('stat-agents').textContent = Math.round(metrics.vital_agents_online || 0);
            if (el('stat-cpu')) el('stat-cpu').textContent = `${(metrics.vital_cpu_percent || 0).toFixed(1)}%`;
            if (el('stat-memory')) el('stat-memory').textContent = `${(metrics.vital_memory_percent || 0).toFixed(1)}%`;
        } catch (e) { console.warn('Metrics load failed:', e); }
    } catch (e) { console.warn('Dashboard load failed:', e); }
}

/* ── Uptime ────────────────────────────────────── */
const startTime = Date.now();
function updateUptime() {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(elapsed / 3600);
    const m = Math.floor((elapsed % 3600) / 60);
    const s = elapsed % 60;
    const el = document.getElementById('uptime');
    if (el) el.textContent = `${h}h ${m}m ${s}s`;
}

/* ── Sidebar Toggle ────────────────────────────── */
document.getElementById('sidebar-toggle').addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('collapsed');
});

// Mobile menu
document.getElementById('mobile-menu-btn').addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('mobile-open');
});

// User menu toggle
document.getElementById('sidebar-user-btn').addEventListener('click', () => {
    document.getElementById('user-menu').classList.toggle('open');
});

// Close menu on outside click
document.addEventListener('click', (e) => {
    const menu = document.getElementById('user-menu');
    const btn = document.getElementById('sidebar-user-btn');
    if (menu.classList.contains('open') && !menu.contains(e.target) && !btn.contains(e.target)) {
        menu.classList.remove('open');
    }
});

// Close mobile sidebar on nav click
document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
        document.getElementById('sidebar').classList.remove('mobile-open');
    });
});

// Logout
document.getElementById('btn-logout')?.addEventListener('click', () => {
    localStorage.removeItem('opensoul_token');
    location.reload();
});

/* ── Init ──────────────────────────────────────── */
Router.init();
loadDashboardStats();
setInterval(updateUptime, 1000);
setInterval(loadDashboardStats, 30000);
updateUptime();
