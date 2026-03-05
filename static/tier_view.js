/* ============================================
   EPI-USE México — Tier View
   Dedicated view for a single tier's accounts
   ============================================ */

(function () {
    'use strict';

    const TIER = window.TIER_LETTER;
    const PAGE_SIZE = 50;

    let allAccounts = [];      // all accounts for this tier
    let filteredAccounts = [];
    let industries = [];
    let currentPage = 1;
    let currentSort = { field: 'score', dir: 'desc' };
    let activeDetailBpId = null;

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        setupTheme();
        setupFilters();
        setupPanels();
        await loadData();
    }

    // --- Data Loading ---
    async function loadData() {
        document.getElementById('account-tbody').innerHTML =
            '<tr><td colspan="9" class="loading">Loading accounts...</td></tr>';

        try {
            const [accountsRes, industriesRes] = await Promise.all([
                fetch('/api/accounts').then(r => r.json()),
                fetch('/api/industries').then(r => r.json()),
            ]);

            allAccounts = accountsRes.filter(a => a.tier === TIER);
            industries = industriesRes;

            document.getElementById('tier-count').textContent =
                `${allAccounts.length} accounts`;

            document.getElementById('header-stats').innerHTML =
                `<span><span class="header-stat-value">${allAccounts.length}</span> Tier ${TIER}</span>`;

            populateIndustryFilter();
            populateRegionFilter();
            renderIndustryChart();
            renderSapStatusChart();
            applyFilters();
        } catch (err) {
            console.error('Failed to load data:', err);
            document.getElementById('account-tbody').innerHTML =
                '<tr><td colspan="9" class="loading">Failed to load data. Is the server running?</td></tr>';
        }
    }

    // --- Industry Chart ---
    function renderIndustryChart() {
        const counts = {};
        allAccounts.forEach(a => {
            counts[a.industry] = (counts[a.industry] || 0) + 1;
        });
        const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 12);
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

    function renderSapStatusChart() {
        const counts = {};
        allAccounts.forEach(a => {
            counts[a.sap_status] = (counts[a.sap_status] || 0) + 1;
        });
        const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
        const maxVal = Math.max(...entries.map(e => e[1]));

        document.getElementById('sap-status-chart').innerHTML = `
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

    // --- Filters ---
    function populateIndustryFilter() {
        const sel = document.getElementById('filter-industry');
        const used = new Set(allAccounts.map(a => a.industry));
        const sorted = [...used].sort();
        sel.innerHTML = '<option value="">All Industries</option>' +
            sorted.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join('');
    }

    function populateRegionFilter() {
        const regions = [...new Set(allAccounts.map(a => a.region).filter(Boolean))].sort();
        const sel = document.getElementById('filter-region');
        regions.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r;
            opt.textContent = r;
            sel.appendChild(opt);
        });
    }

    function setupFilters() {
        document.getElementById('search-input').addEventListener('input', debounce(applyFilters, 200));
        document.getElementById('filter-sap').addEventListener('change', applyFilters);
        document.getElementById('filter-rbc').addEventListener('change', applyFilters);
        document.getElementById('filter-region').addEventListener('change', applyFilters);
        document.getElementById('filter-starred').addEventListener('change', applyFilters);
        document.getElementById('filter-industry').addEventListener('change', applyFilters);

        document.getElementById('clear-filters').addEventListener('click', () => {
            document.getElementById('search-input').value = '';
            document.getElementById('filter-sap').value = '';
            document.getElementById('filter-rbc').value = '';
            document.getElementById('filter-region').value = '';
            document.getElementById('filter-starred').value = '';
            Array.from(document.getElementById('filter-industry').options).forEach(o => o.selected = o.value === '');
            applyFilters();
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
                applyFilters();
            });
        });
    }

    function applyFilters() {
        const search = document.getElementById('search-input').value.toLowerCase().trim();
        const sapFilter = document.getElementById('filter-sap').value;
        const rbcFilter = document.getElementById('filter-rbc').value;
        const regionFilter = document.getElementById('filter-region').value;
        const starFilter = document.getElementById('filter-starred').value;
        const selectedIndustries = Array.from(document.getElementById('filter-industry').selectedOptions)
            .map(o => o.value).filter(v => v !== '');

        filteredAccounts = allAccounts.filter(a => {
            if (search && !a.company_name.toLowerCase().includes(search) &&
                !a.sic_description.toLowerCase().includes(search) &&
                !(a.top_parent_name || '').toLowerCase().includes(search)) return false;
            if (sapFilter && a.sap_status !== sapFilter) return false;
            if (rbcFilter && a.rbc_plan !== rbcFilter) return false;
            if (regionFilter && a.region !== regionFilter) return false;
            if (starFilter === 'starred' && !a.starred) return false;
            if (starFilter === 'tagged' && (!a.tags || a.tags.length === 0)) return false;
            if (selectedIndustries.length > 0 && !selectedIndustries.includes(a.industry)) return false;
            return true;
        });

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

    // --- Detail Panel ---
    function setupPanels() {
        document.getElementById('detail-close').addEventListener('click', closeDetail);
        document.getElementById('detail-overlay').addEventListener('click', closeDetail);
    }

    function openDetail(bpId) {
        activeDetailBpId = bpId;
        const account = allAccounts.find(a => a.bp_id === bpId);
        if (!account) return;

        document.getElementById('detail-name').textContent = account.company_name;
        document.getElementById('detail-body').innerHTML = buildDetailHTML(account);
        document.getElementById('detail-panel').classList.add('open');
        document.getElementById('detail-overlay').classList.add('open');

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
            <div class="detail-section">
                <div class="detail-section-title">Composite Score</div>
                <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
                    <span style="font-size:42px;font-weight:800;font-family:var(--font-mono)" class="${tierClass}">${a.score}</span>
                    <span class="tier-badge tier-${a.tier.toLowerCase()}" style="font-size:20px;width:44px;height:44px">${a.tier}</span>
                </div>
                ${breakdownHTML}
            </div>

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

            ${a.base_instalada || a.bpr_products ? `
            <div class="detail-section">
                <div class="detail-section-title">SAP Products</div>
                ${a.base_instalada ? `<div class="detail-row"><span class="detail-key">Installed Base</span><span class="detail-val">${esc(a.base_instalada)}</span></div>` : ''}
                ${a.bpr_products && a.bpr_products !== 'None' ? `<div class="detail-row"><span class="detail-key">BPR Products</span><span class="detail-val">${esc(a.bpr_products)}</span></div>` : ''}
            </div>` : ''}

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

            <div class="detail-section">
                <div class="detail-section-title">Notes</div>
                <textarea class="detail-textarea" id="detail-notes" placeholder="Add notes about this account...">${esc(a.notes || '')}</textarea>
                <button class="btn btn-sm" id="detail-save-notes" style="margin-top:8px">Save Notes</button>
            </div>

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
        document.getElementById('detail-star').addEventListener('click', async () => {
            await updateAccount(account.bp_id, { starred: !account.starred });
        });

        document.getElementById('detail-target').addEventListener('click', async () => {
            await updateAccount(account.bp_id, { target_list: !account.target_list });
        });

        document.getElementById('detail-industry-override').addEventListener('change', async (e) => {
            if (e.target.value) {
                await updateAccount(account.bp_id, { industry_override: e.target.value });
            }
        });

        document.getElementById('detail-save-notes').addEventListener('click', async () => {
            const notes = document.getElementById('detail-notes').value;
            await updateAccount(account.bp_id, { notes });
        });

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

            const idx = allAccounts.findIndex(a => a.bp_id === bpId);
            if (idx !== -1) allAccounts[idx] = { ...allAccounts[idx], ...updated };

            applyFilters();
            if (activeDetailBpId === bpId) openDetail(bpId);
        } catch (err) {
            console.error('Failed to update account:', err);
        }
    }

    // --- Theme ---
    function setupTheme() {
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

    // --- Helpers ---
    function esc(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    function debounce(fn, ms) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), ms);
        };
    }

})();
