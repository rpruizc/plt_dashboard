/* ============================================
   EPI-USE México — Territory Intelligence
   Frontend Application
   ============================================ */

(function () {
    'use strict';

    // --- State ---
    let allAccounts = [];
    let filteredAccounts = [];
    let stats = {};
    let industries = [];
    let currentPage = 1;
    const PAGE_SIZE = 50;
    let currentSort = { field: 'score', dir: 'desc' };
    let activeDetailBpId = null;

    // --- Init ---
    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        setupThemeToggle();
        setupTabs();
        setupPanels();
        setupFilters();
        setupExports();
        await loadData();
    }

    // --- Data Loading ---
    async function loadData() {
        showLoading('account-tbody');
        try {
            const [accountsRes, statsRes, industriesRes] = await Promise.all([
                fetch('/api/accounts').then(r => r.json()),
                fetch('/api/stats').then(r => r.json()),
                fetch('/api/industries').then(r => r.json()),
            ]);
            allAccounts = accountsRes;
            stats = statsRes;
            industries = industriesRes;

            populateIndustryFilter();
            renderStats();
            renderCharts();
            applyFilters();
            updateTargetBadge();
        } catch (err) {
            console.error('Failed to load data:', err);
            document.getElementById('account-tbody').innerHTML =
                '<tr><td colspan="9" class="loading">Failed to load data. Is the server running?</td></tr>';
        }
    }

    function showLoading(tbodyId) {
        document.getElementById(tbodyId).innerHTML =
            '<tr><td colspan="9" class="loading">Loading accounts...</td></tr>';
    }

    // --- Theme ---
    function setupThemeToggle() {
        const btn = document.getElementById('theme-toggle');
        const saved = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', saved);
        updateThemeIcons(saved);

        btn.addEventListener('click', () => {
            const current = document.documentElement.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('theme', next);
            updateThemeIcons(next);
        });
    }

    function updateThemeIcons(theme) {
        document.getElementById('icon-moon').style.display = theme === 'dark' ? 'none' : 'block';
        document.getElementById('icon-sun').style.display = theme === 'dark' ? 'block' : 'none';
    }

    // --- Tabs ---
    function setupTabs() {
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                document.getElementById('tab-' + tab.dataset.tab).classList.add('active');

                if (tab.dataset.tab === 'targets') renderTargetList();
            });
        });
    }

    // --- Panels ---
    function setupPanels() {
        // Detail panel
        document.getElementById('detail-close').addEventListener('click', closeDetail);
        document.getElementById('detail-overlay').addEventListener('click', closeDetail);

        // Settings panel
        document.getElementById('settings-toggle').addEventListener('click', openSettings);
        document.getElementById('settings-close').addEventListener('click', closeSettings);
        document.getElementById('settings-overlay').addEventListener('click', closeSettings);
    }

    function openDetail(bpId) {
        activeDetailBpId = bpId;
        const account = allAccounts.find(a => a.bp_id === bpId);
        if (!account) return;

        document.getElementById('detail-name').textContent = account.company_name;
        document.getElementById('detail-body').innerHTML = buildDetailHTML(account);
        document.getElementById('detail-panel').classList.add('open');
        document.getElementById('detail-overlay').classList.add('open');

        // Bind detail interactions
        bindDetailEvents(account);
    }

    function closeDetail() {
        activeDetailBpId = null;
        document.getElementById('detail-panel').classList.remove('open');
        document.getElementById('detail-overlay').classList.remove('open');
    }

    function buildDetailHTML(a) {
        const tierClass = `tier-${a.tier.toLowerCase()}-color`;
        const breakdownHTML = Object.entries(a.score_breakdown).map(([key, b]) => {
            const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            return `
                <div class="breakdown-row">
                    <span class="breakdown-label">${label}</span>
                    <div class="breakdown-bar-bg">
                        <div class="breakdown-bar-fill" style="width:${b.score}%"></div>
                    </div>
                    <span class="breakdown-score">${b.weighted}</span>
                </div>`;
        }).join('');

        const tagsHTML = (a.tags || []).map(t =>
            `<span class="tag-removable">${esc(t)} <span class="tag-remove" data-tag="${esc(t)}">&times;</span></span>`
        ).join('');

        return `
            <!-- Score -->
            <div class="detail-section">
                <div class="detail-section-title">Composite Score</div>
                <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
                    <span style="font-size:42px;font-weight:800;font-family:var(--font-mono)" class="${tierClass}">${a.score}</span>
                    <span class="tier-badge tier-${a.tier.toLowerCase()}" style="font-size:20px;width:44px;height:44px">${a.tier}</span>
                </div>
                ${breakdownHTML}
            </div>

            <!-- Actions -->
            <div class="detail-section">
                <div class="detail-section-title">Quick Actions</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap">
                    <a class="btn" href="/companies/${a.bp_id}">Open CRM Workspace</a>
                    <button class="btn ${a.starred ? 'btn-primary' : ''}" id="detail-star">
                        ${a.starred ? '★ Starred' : '☆ Star Account'}
                    </button>
                    <button class="btn ${a.target_list ? 'btn-primary' : ''}" id="detail-target">
                        ${a.target_list ? '✓ On Target List' : '+ Add to Target List'}
                    </button>
                </div>
            </div>

            <!-- Industry Override -->
            <div class="detail-section">
                <div class="detail-section-title">Industry Classification</div>
                <div class="detail-row">
                    <span class="detail-key">SAP Master Code</span>
                    <span class="detail-val">${esc(a.master_industry)}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-key">Current Tag</span>
                    <span class="detail-val">${esc(a.industry)} <small style="color:var(--text-muted)">(${a.industry_source})</small></span>
                </div>
                <div style="margin-top:8px">
                    <label class="filter-label">Override Industry</label>
                    <select class="detail-select" id="detail-industry-override">
                        <option value="">— Keep current —</option>
                        ${industries.map(i => `<option value="${esc(i)}" ${i === a.industry && a.industry_source === 'manual' ? 'selected' : ''}>${esc(i)}</option>`).join('')}
                    </select>
                </div>
            </div>

            <!-- Account Details -->
            <div class="detail-section">
                <div class="detail-section-title">Account Data</div>
                <div class="detail-row"><span class="detail-key">BP ID</span><span class="detail-val">${a.bp_id}</span></div>
                <div class="detail-row"><span class="detail-key">Company Name</span><span class="detail-val">${esc(a.company_name)}</span></div>
                ${a.top_parent_name ? `<div class="detail-row"><span class="detail-key">Top Parent</span><span class="detail-val">${esc(a.top_parent_name)}</span></div>` : ''}
                <div class="detail-row"><span class="detail-key">Planning Entity</span><span class="detail-val">${esc(a.planning_entity_name || '—')}</span></div>
                ${a.tax_number ? `<div class="detail-row"><span class="detail-key">Tax Number (RFC)</span><span class="detail-val">${esc(a.tax_number)}</span></div>` : ''}
                <div class="detail-row"><span class="detail-key">SAP Status</span><span class="detail-val">${esc(a.sap_status)}</span></div>
                <div class="detail-row"><span class="detail-key">ERP ISP ID</span><span class="detail-val">${a.erp_isp_id || '—'}</span></div>
                ${a.rbc_plan ? `<div class="detail-row"><span class="detail-key">RBC 2026 Plan</span><span class="detail-val">${esc(a.rbc_plan)}</span></div>` : ''}
                ${a.archetype ? `<div class="detail-row"><span class="detail-key">Archetype 2026</span><span class="detail-val">${esc(a.archetype)}</span></div>` : ''}
                ${a.market_segment ? `<div class="detail-row"><span class="detail-key">Market Segment</span><span class="detail-val">${esc(a.market_segment)}</span></div>` : ''}
                <div class="detail-row"><span class="detail-key">SIC Description</span><span class="detail-val">${esc(a.sic_description || '—')}</span></div>
                ${a.master_code ? `<div class="detail-row"><span class="detail-key">Master Code</span><span class="detail-val">${a.master_code}</span></div>` : ''}
                <div class="detail-row"><span class="detail-key">Account Exec</span><span class="detail-val">${esc(a.account_exec || '—')}</span></div>
            </div>

            <!-- SAP Products -->
            ${a.base_instalada || a.bpr_products ? `
            <div class="detail-section">
                <div class="detail-section-title">SAP Products</div>
                ${a.base_instalada ? `<div class="detail-row"><span class="detail-key">Installed Base</span><span class="detail-val">${esc(a.base_instalada)}</span></div>` : ''}
                ${a.bpr_products && a.bpr_products !== 'None' ? `<div class="detail-row"><span class="detail-key">BPR Products</span><span class="detail-val">${esc(a.bpr_products)}</span></div>` : ''}
            </div>` : ''}

            <!-- Location & Size -->
            <div class="detail-section">
                <div class="detail-section-title">Location & Size</div>
                ${a.website ? `<div class="detail-row"><span class="detail-key">Website</span><span class="detail-val"><a href="${esc(a.website.startsWith('http') ? a.website : 'https://' + a.website)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(a.website)}</a></span></div>` : ''}
                ${a.address_street ? `<div class="detail-row"><span class="detail-key">Street</span><span class="detail-val">${esc(a.address_street)}</span></div>` : ''}
                ${a.city ? `<div class="detail-row"><span class="detail-key">City</span><span class="detail-val">${esc(a.city)}</span></div>` : ''}
                <div class="detail-row"><span class="detail-key">Region</span><span class="detail-val">${esc(a.region)}${a.address_region_code ? ` (${esc(a.address_region_code)})` : ''}</span></div>
                ${a.address_postal_code ? `<div class="detail-row"><span class="detail-key">Postal Code</span><span class="detail-val">${esc(a.address_postal_code)}</span></div>` : ''}
                ${a.employee_count ? `<div class="detail-row"><span class="detail-key">Employees</span><span class="detail-val">${Number(a.employee_count).toLocaleString()}</span></div>` : ''}
                ${a.revenue ? `<div class="detail-row"><span class="detail-key">Revenue (USD)</span><span class="detail-val">$${Number(a.revenue).toLocaleString()}</span></div>` : ''}
            </div>

            <!-- Notes -->
            <div class="detail-section">
                <div class="detail-section-title">Notes</div>
                <textarea class="detail-textarea" id="detail-notes" placeholder="Add notes about this account...">${esc(a.notes || '')}</textarea>
                <button class="btn btn-sm" id="detail-save-notes" style="margin-top:8px">Save Notes</button>
            </div>

            <!-- Tags -->
            <div class="detail-section">
                <div class="detail-section-title">Tags</div>
                <div class="detail-tags" id="detail-tags-list">${tagsHTML}</div>
                <div class="tag-input-row">
                    <input class="detail-input" id="detail-tag-input" placeholder='e.g. "Week 1 target"'>
                    <button class="btn btn-sm" id="detail-add-tag">Add</button>
                </div>
            </div>
        `;
    }

    function bindDetailEvents(account) {
        // Star
        document.getElementById('detail-star').addEventListener('click', async () => {
            await updateAccount(account.bp_id, { starred: !account.starred });
        });

        // Target list
        document.getElementById('detail-target').addEventListener('click', async () => {
            await updateAccount(account.bp_id, { target_list: !account.target_list });
        });

        // Industry override
        document.getElementById('detail-industry-override').addEventListener('change', async (e) => {
            if (e.target.value) {
                await updateAccount(account.bp_id, { industry_override: e.target.value });
            }
        });

        // Save notes
        document.getElementById('detail-save-notes').addEventListener('click', async () => {
            const notes = document.getElementById('detail-notes').value;
            await updateAccount(account.bp_id, { notes });
        });

        // Add tag
        const addTag = async () => {
            const input = document.getElementById('detail-tag-input');
            const tag = input.value.trim();
            if (!tag) return;
            const newTags = [...(account.tags || []), tag];
            input.value = '';
            await updateAccount(account.bp_id, { tags: newTags });
        };
        document.getElementById('detail-add-tag').addEventListener('click', addTag);
        document.getElementById('detail-tag-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') addTag();
        });

        // Remove tags
        document.getElementById('detail-tags-list').addEventListener('click', async (e) => {
            if (e.target.classList.contains('tag-remove')) {
                const tagToRemove = e.target.dataset.tag;
                const newTags = (account.tags || []).filter(t => t !== tagToRemove);
                await updateAccount(account.bp_id, { tags: newTags });
            }
        });
    }

    async function updateAccount(bpId, data) {
        try {
            const res = await fetch(`/api/accounts/${bpId}/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            const updated = await res.json();

            // Update local state
            const idx = allAccounts.findIndex(a => a.bp_id === bpId);
            if (idx !== -1) allAccounts[idx] = { ...allAccounts[idx], ...updated };

            // Re-render
            applyFilters();
            updateTargetBadge();
            if (activeDetailBpId === bpId) openDetail(bpId);
        } catch (err) {
            console.error('Failed to update account:', err);
        }
    }

    // --- Settings ---
    async function openSettings() {
        const weights = await fetch('/api/weights').then(r => r.json());
        const industryScores = await fetch('/api/industry-scores').then(r => r.json());

        const weightFields = [
            {
                key: 'industry_match',
                label: 'Industry Match',
                tip: 'Measures fit to our SAP cloud target industries using the account industry classification (manual override > SAP master-code map > keyword fallback).',
            },
            {
                key: 'company_size',
                label: 'Company Size',
                tip: 'Uses employee count bands. Best score is 200-1000 employees. Unknown size returns a neutral score of 50.',
            },
            {
                key: 'sap_relationship',
                label: 'SAP Relationship',
                tip: 'Uses SAP status. Existing SAP scores 90, Net New scores 80, and Has Business One scores 40.',
            },
            {
                key: 'data_completeness',
                label: 'Data Completeness',
                tip: 'Percentage of key fields populated: company name, master industry, SIC, region, ERP ISP ID, employee count, revenue, planning entity name, website, city.',
            },
        ];

        document.getElementById('settings-body').innerHTML = `
            <div style="margin-bottom:24px">
                <h3 style="font-size:14px;margin-bottom:12px">Scoring Weights</h3>
                <p style="font-size:12px;color:var(--text-secondary);margin-bottom:16px">
                    Adjust how much each factor contributes to the composite score. Weights must sum to 100%.
                </p>
                ${weightFields.map(f => `
                    <div class="weight-slider-group">
                        <div class="weight-slider-header">
                            <span class="weight-slider-label">${f.label}</span>
                            <span class="weight-slider-value" id="wv-${f.key}">${Math.round(weights[f.key] * 100)}%</span>
                        </div>
                        <input type="range" class="weight-slider" id="ws-${f.key}" min="0" max="100" value="${Math.round(weights[f.key] * 100)}" data-key="${f.key}">
                        <div class="weight-tip">${f.tip}</div>
                    </div>
                `).join('')}
                <div class="weight-total valid" id="weight-total">Total: 100%</div>
                <button class="btn btn-primary" id="save-weights" style="width:100%">Apply Weights & Rescore</button>
            </div>

            <div>
                <h3 style="font-size:14px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;gap:8px">
                    <span>Industry Score Reference</span>
                    <a href="/scoring-profiles" class="settings-link" title="Open scoring profile manager">✎ Edit</a>
                </h3>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">Points assigned per industry category:</div>
                ${Object.entries(industryScores).sort((a, b) => b[1] - a[1]).map(([ind, score]) => `
                    <div class="detail-row">
                        <span class="detail-key">${ind}</span>
                        <span class="detail-val">${score}</span>
                    </div>
                `).join('')}
            </div>
        `;

        // Bind slider events
        document.querySelectorAll('.weight-slider').forEach(slider => {
            slider.addEventListener('input', () => {
                document.getElementById('wv-' + slider.dataset.key).textContent = slider.value + '%';
                const total = Array.from(document.querySelectorAll('.weight-slider'))
                    .reduce((sum, s) => sum + parseInt(s.value), 0);
                const totalEl = document.getElementById('weight-total');
                totalEl.textContent = `Total: ${total}%`;
                totalEl.className = 'weight-total ' + (total === 100 ? 'valid' : 'invalid');
            });
        });

        // Save weights
        document.getElementById('save-weights').addEventListener('click', async () => {
            const total = Array.from(document.querySelectorAll('.weight-slider'))
                .reduce((sum, s) => sum + parseInt(s.value), 0);
            if (total !== 100) {
                alert('Weights must sum to 100%');
                return;
            }
            const newWeights = {};
            document.querySelectorAll('.weight-slider').forEach(s => {
                newWeights[s.dataset.key] = parseInt(s.value) / 100;
            });
            const saveRes = await fetch('/api/weights', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newWeights),
            });
            if (!saveRes.ok) {
                const payload = await saveRes.json().catch(() => ({}));
                alert(payload.error || 'Failed to save weights');
                return;
            }
            closeSettings();
            await loadData();
        });

        document.getElementById('settings-panel').classList.add('open');
        document.getElementById('settings-overlay').classList.add('open');
    }

    function closeSettings() {
        document.getElementById('settings-panel').classList.remove('open');
        document.getElementById('settings-overlay').classList.remove('open');
    }

    // --- Stats ---
    function renderStats() {
        const tiers = { A: 0, B: 0, C: 0, D: 0, E: 0, ...(stats.tiers || {}) };
        const row = document.getElementById('stats-row');
        row.innerHTML = `
            <div class="stat-card">
                <div class="stat-label">Total Accounts</div>
                <div class="stat-value">${stats.total.toLocaleString()}</div>
                <div class="stat-sub">Jalisco territory</div>
            </div>
            <a href="/tier/A" class="stat-card stat-card-clickable">
                <div class="stat-label">Tier A (100)</div>
                <div class="stat-value tier-a-color">${tiers.A}</div>
                <div class="stat-sub">${pct(tiers.A, stats.total)} of accounts</div>
            </a>
            <a href="/tier/B" class="stat-card stat-card-clickable">
                <div class="stat-label">Tier B (80-99)</div>
                <div class="stat-value tier-b-color">${tiers.B}</div>
                <div class="stat-sub">${pct(tiers.B, stats.total)} of accounts</div>
            </a>
            <a href="/tier/C" class="stat-card stat-card-clickable">
                <div class="stat-label">Tier C (60-79)</div>
                <div class="stat-value tier-c-color">${tiers.C}</div>
                <div class="stat-sub">${pct(tiers.C, stats.total)} of accounts</div>
            </a>
            <a href="/tier/D" class="stat-card stat-card-clickable">
                <div class="stat-label">Tier D (40-59)</div>
                <div class="stat-value tier-d-color">${tiers.D}</div>
                <div class="stat-sub">${pct(tiers.D, stats.total)} of accounts</div>
            </a>
            <a href="/tier/E" class="stat-card stat-card-clickable">
                <div class="stat-label">Tier E (0-39)</div>
                <div class="stat-value tier-e-color">${tiers.E}</div>
                <div class="stat-sub">${pct(tiers.E, stats.total)} of accounts</div>
            </a>
            <div class="stat-card">
                <div class="stat-label">Avg Score</div>
                <div class="stat-value">${stats.avg_score}</div>
                <div class="stat-sub">composite score</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Target List</div>
                <div class="stat-value" style="color:var(--sap-blue)">${stats.target_count}</div>
                <div class="stat-sub">${stats.starred_count} starred</div>
            </div>
        `;

        // Header stats
        document.getElementById('header-stats').innerHTML = `
            <span><span class="header-stat-value">${stats.total}</span> accounts</span>
            <span><span class="header-stat-value tier-a-color">${tiers.A}</span> Tier A</span>
            <span><span class="header-stat-value">${stats.avg_score}</span> avg</span>
        `;
    }

    // --- Charts (pure CSS/HTML) ---
    function renderCharts() {
        renderScoreChart();
        renderIndustryChart();
        renderTierChart();
    }

    function renderScoreChart() {
        const dist = stats.score_distribution;
        const maxVal = Math.max(...Object.values(dist));
        const colors = {
            '0-9': 'var(--tier-e)', '10-19': 'var(--tier-e)',
            '20-29': 'var(--tier-d)', '30-39': 'var(--tier-d)',
            '40-49': 'var(--tier-c)', '50-59': 'var(--tier-c)',
            '60-69': 'var(--tier-c)', '70-79': 'var(--tier-c)',
            '80-89': 'var(--tier-b)', '90-99': 'var(--tier-b)',
            '100': 'var(--tier-a)',
        };

        document.getElementById('score-chart').innerHTML = `
            <div class="bar-chart">
                ${Object.entries(dist).map(([label, val]) => `
                    <div class="bar-row">
                        <span class="bar-label">${label}</span>
                        <div class="bar-track">
                            <div class="bar-fill" style="width:${maxVal ? (val / maxVal * 100) : 0}%;background:${colors[label] || 'var(--sap-blue)'}"></div>
                        </div>
                        <span class="bar-value">${val}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function renderIndustryChart() {
        const ind = stats.industries;
        const entries = Object.entries(ind).slice(0, 12);
        const maxVal = Math.max(...entries.map(e => e[1]));

        document.getElementById('industry-chart').innerHTML = `
            <div class="bar-chart">
                ${entries.map(([label, val]) => `
                    <div class="industry-bar-row">
                        <span class="industry-bar-label" title="${label}">${label}</span>
                        <div class="industry-bar-track">
                            <div class="industry-bar-fill" style="width:${maxVal ? (val / maxVal * 100) : 0}%"></div>
                        </div>
                        <span class="industry-bar-value">${val}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function renderTierChart() {
        const total = stats.total;
        const tiers = { A: 0, B: 0, C: 0, D: 0, E: 0, ...(stats.tiers || {}) };
        document.getElementById('tier-chart').innerHTML = `
            <div class="tier-display">
                <div class="tier-row">
                    <div class="tier-badge tier-a">A</div>
                    <div class="tier-info">
                        <div class="tier-count">${tiers.A}</div>
                        <div class="tier-pct">${pct(tiers.A, total)}</div>
                    </div>
                </div>
                <div class="tier-row">
                    <div class="tier-badge tier-b">B</div>
                    <div class="tier-info">
                        <div class="tier-count">${tiers.B}</div>
                        <div class="tier-pct">${pct(tiers.B, total)}</div>
                    </div>
                </div>
                <div class="tier-row">
                    <div class="tier-badge tier-c">C</div>
                    <div class="tier-info">
                        <div class="tier-count">${tiers.C}</div>
                        <div class="tier-pct">${pct(tiers.C, total)}</div>
                    </div>
                </div>
                <div class="tier-row">
                    <div class="tier-badge tier-d">D</div>
                    <div class="tier-info">
                        <div class="tier-count">${tiers.D}</div>
                        <div class="tier-pct">${pct(tiers.D, total)}</div>
                    </div>
                </div>
                <div class="tier-row">
                    <div class="tier-badge tier-e">E</div>
                    <div class="tier-info">
                        <div class="tier-count">${tiers.E}</div>
                        <div class="tier-pct">${pct(tiers.E, total)}</div>
                    </div>
                </div>
            </div>
        `;
    }

    // --- Filters ---
    function setupFilters() {
        document.getElementById('search-input').addEventListener('input', debounce(applyFilters, 200));
        document.getElementById('filter-tier').addEventListener('change', applyFilters);
        document.getElementById('filter-sap').addEventListener('change', applyFilters);
        document.getElementById('filter-rbc').addEventListener('change', applyFilters);
        document.getElementById('filter-region').addEventListener('change', applyFilters);
        document.getElementById('filter-starred').addEventListener('change', applyFilters);
        document.getElementById('sort-by').addEventListener('change', (e) => {
            currentSort = { field: e.target.value, dir: e.target.value === 'name' ? 'asc' : 'desc' };
            applyFilters();
        });
        document.getElementById('clear-filters').addEventListener('click', () => {
            document.getElementById('search-input').value = '';
            document.getElementById('filter-tier').value = '';
            document.getElementById('filter-sap').value = '';
            document.getElementById('filter-rbc').value = '';
            document.getElementById('filter-region').value = '';
            document.getElementById('filter-starred').value = '';
            document.getElementById('sort-by').value = 'score';
            // Clear industry multi-select
            Array.from(document.getElementById('filter-industry').options).forEach(o => o.selected = o.value === '');
            currentSort = { field: 'score', dir: 'desc' };
            applyFilters();
        });

        // Industry multi-select
        document.getElementById('filter-industry').addEventListener('change', applyFilters);

        // Populate region filter dynamically
        const regions = [...new Set(allAccounts.map(a => a.region).filter(Boolean))].sort();
        const regionSel = document.getElementById('filter-region');
        regions.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r;
            opt.textContent = r;
            regionSel.appendChild(opt);
        });

        // Sortable headers
        document.querySelectorAll('th.sortable').forEach(th => {
            th.addEventListener('click', () => {
                const field = th.dataset.sort;
                if (currentSort.field === field) {
                    currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    currentSort = { field, dir: field === 'name' ? 'asc' : 'desc' };
                }
                document.getElementById('sort-by').value = field;
                applyFilters();
            });
        });
    }

    function populateIndustryFilter() {
        const sel = document.getElementById('filter-industry');
        const used = new Set(allAccounts.map(a => a.industry));
        const sorted = [...used].sort();
        sel.innerHTML = '<option value="">All Industries</option>' +
            sorted.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join('');
    }

    function applyFilters() {
        const search = document.getElementById('search-input').value.toLowerCase().trim();
        const tierFilter = document.getElementById('filter-tier').value;
        const sapFilter = document.getElementById('filter-sap').value;
        const rbcFilter = document.getElementById('filter-rbc').value;
        const regionFilter = document.getElementById('filter-region').value;
        const starFilter = document.getElementById('filter-starred').value;
        const industrySelect = document.getElementById('filter-industry');
        const selectedIndustries = Array.from(industrySelect.selectedOptions)
            .map(o => o.value).filter(v => v !== '');

        filteredAccounts = allAccounts.filter(a => {
            if (search && !a.company_name.toLowerCase().includes(search) &&
                !a.sic_description.toLowerCase().includes(search) &&
                !(a.top_parent_name || '').toLowerCase().includes(search)) return false;
            if (tierFilter && a.tier !== tierFilter) return false;
            if (sapFilter && a.sap_status !== sapFilter) return false;
            if (rbcFilter && a.rbc_plan !== rbcFilter) return false;
            if (regionFilter && a.region !== regionFilter) return false;
            if (starFilter === 'starred' && !a.starred) return false;
            if (starFilter === 'tagged' && (!a.tags || a.tags.length === 0)) return false;
            if (selectedIndustries.length > 0 && !selectedIndustries.includes(a.industry)) return false;
            return true;
        });

        // Sort
        filteredAccounts.sort((a, b) => {
            let va, vb;
            switch (currentSort.field) {
                case 'name': va = a.company_name.toLowerCase(); vb = b.company_name.toLowerCase(); break;
                case 'industry': va = a.industry; vb = b.industry; break;
                default: va = a.score; vb = b.score; break;
            }
            if (va < vb) return currentSort.dir === 'asc' ? -1 : 1;
            if (va > vb) return currentSort.dir === 'asc' ? 1 : -1;
            return 0;
        });

        currentPage = 1;
        renderTable();
        renderPagination();

        document.getElementById('filter-count').textContent =
            `${filteredAccounts.length} of ${allAccounts.length} accounts`;

        // Update sort header indicators
        document.querySelectorAll('th.sortable').forEach(th => {
            th.classList.toggle('sort-active', th.dataset.sort === currentSort.field);
        });
    }

    // --- Table ---
    function renderTable() {
        const tbody = document.getElementById('account-tbody');
        const start = (currentPage - 1) * PAGE_SIZE;
        const page = filteredAccounts.slice(start, start + PAGE_SIZE);

        if (page.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--text-muted)">No accounts match your filters</td></tr>';
            return;
        }

        tbody.innerHTML = page.map((a, i) => {
            const rank = start + i + 1;
            const tierClass = a.tier.toLowerCase();
            const sapClass = a.sap_status === 'Net New' ? 'sap-new' : 'sap-existing';
            const indClass = a.industry === 'Unclassified' || a.industry === 'Other' ? 'unclassified' : '';
            const tags = (a.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('');

            return `
                <tr data-bp="${a.bp_id}">
                    <td class="col-rank"><span class="rank-num">${rank}</span></td>
                    <td class="col-star">
                        <button class="btn-star ${a.starred ? 'active' : ''}" data-bp="${a.bp_id}" onclick="event.stopPropagation()">
                            ${a.starred ? '★' : '☆'}
                        </button>
                    </td>
                    <td class="col-name">
                        <div class="company-name">${esc(a.company_name)}</div>
                        ${a.planning_entity_name && a.planning_entity_name !== a.company_name ?
                            `<div class="company-sub">${esc(a.planning_entity_name)}</div>` : ''}
                    </td>
                    <td class="col-industry"><span class="industry-tag ${indClass}">${esc(a.industry)}</span></td>
                    <td class="col-score">
                        <div class="score-cell">
                            <span class="score-num tier-${tierClass}-color">${a.score}</span>
                            <div class="score-bar-bg">
                                <div class="score-bar-fill score-bar-${tierClass}" style="width:${a.score}%"></div>
                            </div>
                            <span class="score-tier tier-${tierClass}-color">${a.tier}</span>
                        </div>
                    </td>
                    <td class="col-sap"><span class="sap-badge ${sapClass}">${esc(a.sap_status)}</span></td>
                    <td class="col-sic"><span class="sic-text" title="${esc(a.sic_description)}">${esc(a.sic_description || '—')}</span></td>
                    <td class="col-tags">${tags || '<span style="color:var(--text-muted)">—</span>'}</td>
                    <td class="col-actions">
                        <div class="table-action-group">
                            <a class="btn btn-sm" href="/companies/${a.bp_id}" onclick="event.stopPropagation()">CRM</a>
                            <button class="btn btn-sm" onclick="event.stopPropagation()" data-target-btn="${a.bp_id}">
                                ${a.target_list ? '✓ Target' : '+ Target'}
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        // Row click -> detail
        tbody.querySelectorAll('tr').forEach(tr => {
            tr.addEventListener('click', () => openDetail(parseInt(tr.dataset.bp)));
        });

        // Star buttons
        tbody.querySelectorAll('.btn-star').forEach(btn => {
            btn.addEventListener('click', async () => {
                const bpId = parseInt(btn.dataset.bp);
                const account = allAccounts.find(a => a.bp_id === bpId);
                if (account) await updateAccount(bpId, { starred: !account.starred });
            });
        });

        // Target buttons
        tbody.querySelectorAll('[data-target-btn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const bpId = parseInt(btn.dataset.targetBtn);
                const account = allAccounts.find(a => a.bp_id === bpId);
                if (account) await updateAccount(bpId, { target_list: !account.target_list });
            });
        });
    }

    // --- Pagination ---
    function renderPagination() {
        const totalPages = Math.ceil(filteredAccounts.length / PAGE_SIZE);
        const pag = document.getElementById('pagination');

        if (totalPages <= 1) { pag.innerHTML = ''; return; }

        let html = `<button class="page-btn" ${currentPage === 1 ? 'disabled' : ''} data-page="${currentPage - 1}">&laquo;</button>`;

        const range = getPageRange(currentPage, totalPages);
        for (const p of range) {
            if (p === '...') {
                html += `<span class="page-info">...</span>`;
            } else {
                html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
            }
        }

        html += `<button class="page-btn" ${currentPage === totalPages ? 'disabled' : ''} data-page="${currentPage + 1}">&raquo;</button>`;
        html += `<span class="page-info">${currentPage} of ${totalPages}</span>`;

        pag.innerHTML = html;

        pag.querySelectorAll('.page-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const page = parseInt(btn.dataset.page);
                if (page >= 1 && page <= totalPages) {
                    currentPage = page;
                    renderTable();
                    renderPagination();
                    document.querySelector('.table-wrapper').scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        });
    }

    function getPageRange(current, total) {
        if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
        if (current <= 3) return [1, 2, 3, 4, '...', total];
        if (current >= total - 2) return [1, '...', total - 3, total - 2, total - 1, total];
        return [1, '...', current - 1, current, current + 1, '...', total];
    }

    // --- Target List ---
    function renderTargetList() {
        const targets = allAccounts
            .filter(a => a.target_list || a.starred)
            .sort((a, b) => b.score - a.score);

        const tbody = document.getElementById('target-tbody');

        if (targets.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--text-muted)">No accounts on target list yet. Star accounts or click "+ Target" to add them.</td></tr>';
            return;
        }

        tbody.innerHTML = targets.map((a, i) => {
            const tierClass = a.tier.toLowerCase();
            const sapClass = a.sap_status === 'Net New' ? 'sap-new' : 'sap-existing';
            const tags = (a.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('');

            return `
                <tr data-bp="${a.bp_id}" style="cursor:pointer">
                    <td class="col-rank"><span class="rank-num">${i + 1}</span></td>
                    <td class="col-star">
                        <button class="btn-star ${a.starred ? 'active' : ''}" data-bp="${a.bp_id}" onclick="event.stopPropagation()">
                            ${a.starred ? '★' : '☆'}
                        </button>
                    </td>
                    <td class="col-name"><div class="company-name">${esc(a.company_name)}</div></td>
                    <td class="col-industry"><span class="industry-tag">${esc(a.industry)}</span></td>
                    <td class="col-score">
                        <div class="score-cell">
                            <span class="score-num tier-${tierClass}-color">${a.score}</span>
                            <div class="score-bar-bg">
                                <div class="score-bar-fill score-bar-${tierClass}" style="width:${a.score}%"></div>
                            </div>
                            <span class="score-tier tier-${tierClass}-color">${a.tier}</span>
                        </div>
                    </td>
                    <td class="col-sap"><span class="sap-badge ${sapClass}">${esc(a.sap_status)}</span></td>
                    <td class="col-tags">${tags || '—'}</td>
                    <td class="col-notes"><span class="notes-preview">${esc(a.notes || '')}</span></td>
                    <td class="col-actions">
                        <div class="table-action-group">
                            <a class="btn btn-sm" href="/companies/${a.bp_id}" onclick="event.stopPropagation()">CRM</a>
                            <button class="btn btn-sm" onclick="event.stopPropagation()" data-remove-target="${a.bp_id}">Remove</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        // Row click -> detail
        tbody.querySelectorAll('tr').forEach(tr => {
            tr.addEventListener('click', () => openDetail(parseInt(tr.dataset.bp)));
        });

        // Star buttons
        tbody.querySelectorAll('.btn-star').forEach(btn => {
            btn.addEventListener('click', async () => {
                const bpId = parseInt(btn.dataset.bp);
                const account = allAccounts.find(a => a.bp_id === bpId);
                if (account) {
                    await updateAccount(bpId, { starred: !account.starred });
                    renderTargetList();
                }
            });
        });

        // Remove from target list
        tbody.querySelectorAll('[data-remove-target]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const bpId = parseInt(btn.dataset.removeTarget);
                await updateAccount(bpId, { target_list: false, starred: false });
                renderTargetList();
            });
        });
    }

    function updateTargetBadge() {
        const count = allAccounts.filter(a => a.target_list || a.starred).length;
        document.getElementById('target-count-badge').textContent = count;
    }

    // --- Exports ---
    function setupExports() {
        document.getElementById('export-targets-csv').addEventListener('click', () => {
            window.location.href = '/api/export/csv?target_only=true';
        });

        document.getElementById('export-all-csv').addEventListener('click', () => {
            window.location.href = '/api/export/csv';
        });

        document.getElementById('export-presentation').addEventListener('click', async () => {
            const data = await fetch('/api/export/presentation?n=30&target_only=true').then(r => r.json());
            if (data.length === 0) {
                // Fall back to top 30 by score
                const fallback = await fetch('/api/export/presentation?n=30').then(r => r.json());
                showPresentation(fallback);
            } else {
                showPresentation(data);
            }
        });
    }

    function showPresentation(data) {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal">
                <button class="modal-close">&times;</button>
                <h2>Territory Top Accounts — Presentation View</h2>
                <p style="color:var(--text-secondary);margin-bottom:16px;font-size:13px">
                    EPI-USE México | Jalisco PLT | ${new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}
                </p>
                <table class="pres-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Company</th>
                            <th>Industry</th>
                            <th>Score</th>
                            <th>Tier</th>
                            <th>SAP Status</th>
                            <th>Notes</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.map(a => `
                            <tr>
                                <td>${a.rank}</td>
                                <td style="font-weight:600">${esc(a.company)}</td>
                                <td>${esc(a.industry)}</td>
                                <td style="font-family:var(--font-mono);font-weight:700">${a.score}</td>
                                <td><span class="tier-${a.tier.toLowerCase()}-color" style="font-weight:700">${a.tier}</span></td>
                                <td>${esc(a.sap_status)}</td>
                                <td style="font-size:12px;color:var(--text-secondary)">${esc(a.notes || '')}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;

        document.body.appendChild(overlay);
        overlay.querySelector('.modal-close').addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    }

    // --- Helpers ---
    function esc(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    function pct(n, total) {
        if (!total) return '0%';
        return (n / total * 100).toFixed(1) + '%';
    }

    function debounce(fn, ms) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), ms);
        };
    }

})();
