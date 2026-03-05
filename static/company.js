(function () {
    'use strict';

    const bpId = parseInt(document.body.dataset.bpId, 10);
    let company = null;
    let summary = null;
    let contacts = [];
    let touchpoints = [];
    let nextActions = [];

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        setupThemeToggle();
        bindForms();
        await loadCRM();
    }

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

    function bindForms() {
        document.getElementById('contact-form').addEventListener('submit', onContactSubmit);
        document.getElementById('touchpoint-form').addEventListener('submit', onTouchpointSubmit);
        document.getElementById('action-form').addEventListener('submit', onActionSubmit);
    }

    async function loadCRM() {
        try {
            const res = await fetch(`/api/companies/${bpId}/crm`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to load CRM data');

            company = data.company;
            summary = data.summary;
            contacts = data.contacts || [];
            touchpoints = data.touchpoints || [];
            nextActions = data.next_actions || [];

            renderCompanyHero();
            renderSummary();
            renderContacts();
            renderTouchpoints();
            renderNextActions();
            populateContactSelects();
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    function renderCompanyHero() {
        const tierClass = `tier-${company.tier.toLowerCase()}-color`;
        const c = company;
        const metaParts = [`BP ID ${c.bp_id}`, esc(c.industry), esc(c.sap_status)];
        if (c.rbc_plan) metaParts.push(`RBC: ${esc(c.rbc_plan)}`);
        if (c.archetype) metaParts.push(esc(c.archetype));

        const detailParts = [];
        if (c.top_parent_name) detailParts.push(`Parent: ${esc(c.top_parent_name)}`);
        if (c.market_segment) detailParts.push(esc(c.market_segment));
        if (c.employee_count) detailParts.push(`${Number(c.employee_count).toLocaleString()} employees`);
        if (c.revenue) detailParts.push(`$${Number(c.revenue).toLocaleString()} revenue`);
        if (c.city) detailParts.push(esc(c.city));
        if (c.website) detailParts.push(`<a href="${esc(c.website.startsWith('http') ? c.website : 'https://' + c.website)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(c.website)}</a>`);

        document.getElementById('company-hero').innerHTML = `
            <div>
                <h1>${esc(c.company_name)}</h1>
                <div class="company-hero-meta">${metaParts.join(' · ')}</div>
                ${detailParts.length ? `<div class="company-hero-meta" style="margin-top:4px;opacity:0.8">${detailParts.join(' · ')}</div>` : ''}
            </div>
            <div class="company-hero-score">
                <span class="company-score ${tierClass}">${c.score}</span>
                <span class="tier-badge tier-${c.tier.toLowerCase()}">${c.tier}</span>
            </div>
        `;
    }

    function renderSummary() {
        document.getElementById('crm-summary').innerHTML = `
            <div class="crm-metric">
                <div class="crm-metric-label">Contacts</div>
                <div class="crm-metric-value">${summary.contact_count}</div>
            </div>
            <div class="crm-metric">
                <div class="crm-metric-label">Touchpoints</div>
                <div class="crm-metric-value">${summary.touchpoint_count}</div>
            </div>
            <div class="crm-metric">
                <div class="crm-metric-label">Open Actions</div>
                <div class="crm-metric-value">${summary.open_actions_count}</div>
            </div>
            <div class="crm-metric ${summary.overdue_actions_count > 0 ? 'crm-metric-danger' : ''}">
                <div class="crm-metric-label">Overdue Actions</div>
                <div class="crm-metric-value">${summary.overdue_actions_count}</div>
            </div>
        `;
    }

    function renderContacts() {
        const tbody = document.getElementById('contacts-tbody');
        if (!contacts.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="crm-empty">No contacts yet</td></tr>';
            return;
        }

        tbody.innerHTML = contacts.map(contact => `
            <tr>
                <td>
                    <div class="crm-main-text">${esc(contact.full_name)}</div>
                    ${contact.source ? `<div class="crm-sub-text">Source: ${esc(contact.source)}</div>` : ''}
                </td>
                <td>${esc(contact.job_title || '—')}</td>
                <td>${contact.email ? `<a href="mailto:${esc(contact.email)}">${esc(contact.email)}</a>` : '—'}</td>
                <td>${esc(contact.phone || '—')}</td>
                <td>
                    <button class="btn btn-sm" data-delete-contact="${contact.id}">Delete</button>
                </td>
            </tr>
        `).join('');

        tbody.querySelectorAll('[data-delete-contact]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const contactId = parseInt(btn.dataset.deleteContact, 10);
                await deleteRecord(`/api/companies/${bpId}/contacts/${contactId}`);
            });
        });
    }

    function renderTouchpoints() {
        const list = document.getElementById('touchpoints-list');
        if (!touchpoints.length) {
            list.innerHTML = '<div class="crm-empty">No touchpoints recorded yet</div>';
            return;
        }

        list.innerHTML = touchpoints.map(tp => `
            <article class="touchpoint-item">
                <div class="touchpoint-top">
                    <div>
                        <span class="crm-pill">${esc(tp.touchpoint_type)}</span>
                        <span class="touchpoint-date">${formatDate(tp.touchpoint_date)}</span>
                    </div>
                    <button class="btn btn-sm" data-delete-touchpoint="${tp.id}">Delete</button>
                </div>
                <div class="crm-main-text">${esc(tp.summary || 'No summary')}</div>
                <div class="crm-sub-text">${esc(tp.contact_name || 'No linked contact')} · ${esc(tp.outcome || 'No outcome')}</div>
                ${tp.notes ? `<p class="touchpoint-notes">${esc(tp.notes)}</p>` : ''}
            </article>
        `).join('');

        list.querySelectorAll('[data-delete-touchpoint]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const touchpointId = parseInt(btn.dataset.deleteTouchpoint, 10);
                await deleteRecord(`/api/companies/${bpId}/touchpoints/${touchpointId}`);
            });
        });
    }

    function renderNextActions() {
        const tbody = document.getElementById('actions-tbody');
        if (!nextActions.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="crm-empty">No next actions yet</td></tr>';
            return;
        }

        const today = new Date().toISOString().slice(0, 10);
        tbody.innerHTML = nextActions.map(action => {
            const overdue = action.status !== 'done' && action.due_date && action.due_date < today;
            return `
                <tr>
                    <td>
                        <div class="crm-main-text">${esc(action.title)}</div>
                        <div class="crm-sub-text">${esc(action.contact_name || 'No linked contact')} · ${esc(action.owner_email || 'No owner')}</div>
                    </td>
                    <td class="${overdue ? 'crm-overdue' : ''}">${action.due_date ? formatDate(action.due_date) : '—'}</td>
                    <td><span class="crm-pill">${esc(action.priority)}</span></td>
                    <td>
                        <select class="detail-select crm-inline-select" data-action-status="${action.id}">
                            <option value="open" ${action.status === 'open' ? 'selected' : ''}>Open</option>
                            <option value="in_progress" ${action.status === 'in_progress' ? 'selected' : ''}>In Progress</option>
                            <option value="done" ${action.status === 'done' ? 'selected' : ''}>Done</option>
                        </select>
                    </td>
                    <td>
                        <button class="btn btn-sm" data-delete-action="${action.id}">Delete</button>
                    </td>
                </tr>
            `;
        }).join('');

        tbody.querySelectorAll('[data-action-status]').forEach(sel => {
            sel.addEventListener('change', async () => {
                const actionId = parseInt(sel.dataset.actionStatus, 10);
                await updateRecord(`/api/companies/${bpId}/next-actions/${actionId}`, { status: sel.value });
            });
        });

        tbody.querySelectorAll('[data-delete-action]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const actionId = parseInt(btn.dataset.deleteAction, 10);
                await deleteRecord(`/api/companies/${bpId}/next-actions/${actionId}`);
            });
        });
    }

    function populateContactSelects() {
        const options = ['<option value="">No contact linked</option>'].concat(
            contacts.map(contact => `<option value="${contact.id}">${esc(contact.full_name)}</option>`)
        );
        document.getElementById('touchpoint-contact-select').innerHTML = options.join('');
        document.getElementById('action-contact-select').innerHTML = options.join('');
    }

    async function onContactSubmit(event) {
        event.preventDefault();
        const form = event.currentTarget;
        const fd = new FormData(form);
        const payload = Object.fromEntries(fd.entries());
        payload.confidence = payload.confidence ? Number(payload.confidence) : null;
        await createRecord(`/api/companies/${bpId}/contacts`, payload);
        form.reset();
    }

    async function onTouchpointSubmit(event) {
        event.preventDefault();
        const form = event.currentTarget;
        const fd = new FormData(form);
        const payload = Object.fromEntries(fd.entries());
        payload.contact_id = payload.contact_id ? Number(payload.contact_id) : null;
        await createRecord(`/api/companies/${bpId}/touchpoints`, payload);
        form.reset();
    }

    async function onActionSubmit(event) {
        event.preventDefault();
        const form = event.currentTarget;
        const fd = new FormData(form);
        const payload = Object.fromEntries(fd.entries());
        payload.contact_id = payload.contact_id ? Number(payload.contact_id) : null;
        await createRecord(`/api/companies/${bpId}/next-actions`, payload);
        form.reset();
    }

    async function createRecord(url, payload) {
        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Create failed');
            showMessage('Saved');
            await loadCRM();
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    async function updateRecord(url, payload) {
        try {
            const res = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Update failed');
            showMessage('Updated');
            await loadCRM();
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    async function deleteRecord(url) {
        try {
            const res = await fetch(url, { method: 'DELETE' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Delete failed');
            showMessage('Deleted');
            await loadCRM();
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    function showMessage(message, isError) {
        const el = document.getElementById('crm-feedback');
        el.hidden = false;
        el.textContent = message;
        el.className = `crm-feedback ${isError ? 'crm-feedback-error' : 'crm-feedback-success'}`;
        window.clearTimeout(showMessage._timer);
        showMessage._timer = window.setTimeout(() => {
            el.hidden = true;
        }, 2800);
    }

    function formatDate(value) {
        if (!value) return '—';
        const date = new Date(`${value}T00:00:00`);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function esc(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
})();
