(function () {
    'use strict';

    let rows = [];
    let summary = null;
    let discoveryJobPollTimer = null;
    const filters = {
        status: 'all',
        search: '',
    };

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        setupThemeToggle();
        bindControls();
        await loadSummary();
        await loadQueue();
        await resumeDiscoveryIfRunning();
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

    function bindControls() {
        document.getElementById('run-discovery').addEventListener('click', runDiscovery);
        document.getElementById('auto-accept').addEventListener('click', runAutoAccept);
        document.getElementById('refresh-queue').addEventListener('click', async () => {
            await loadSummary();
            await loadQueue();
        });

        document.getElementById('pipeline-status').addEventListener('change', async (event) => {
            filters.status = event.target.value;
            await loadQueue();
        });

        document.getElementById('pipeline-search').addEventListener('input', debounce(async (event) => {
            filters.search = event.target.value.trim();
            await loadQueue();
        }, 220));
    }

    async function resumeDiscoveryIfRunning() {
        try {
            const res = await fetch('/api/pipeline/urls/discover-job');
            const data = await res.json();
            if (!res.ok) return;
            const job = data.job;
            if (!job) return;
            if (job.status === 'running' || job.status === 'idle') {
                document.getElementById('run-discovery').disabled = true;
                startDiscoveryPolling(job.job_id);
            }
        } catch (_err) {
            // best effort only
        }
    }

    async function loadSummary() {
        try {
            const res = await fetch('/api/pipeline/urls/summary');
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to load summary');
            summary = data;
            const braveToggle = document.getElementById('discovery-use-brave');
            const deepToggle = document.getElementById('discovery-use-deep-fallback');
            if (braveToggle) {
                braveToggle.disabled = !summary.brave_enabled;
                if (!summary.brave_enabled) braveToggle.checked = false;
            }
            if (deepToggle) {
                deepToggle.disabled = !summary.brave_enabled;
                if (!summary.brave_enabled) deepToggle.checked = false;
            }
            renderSummary();
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    async function loadQueue() {
        try {
            const params = new URLSearchParams();
            params.set('status', filters.status);
            if (filters.search) params.set('search', filters.search);
            const res = await fetch(`/api/pipeline/urls/queue?${params.toString()}`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to load queue');
            rows = data.rows || [];
            renderQueue();
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    function renderSummary() {
        if (!summary) return;
        document.getElementById('pipeline-summary').innerHTML = `
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">Total Companies</div>
                <div class="pipeline-metric-value">${summary.total_companies}</div>
            </div>
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">Has URL</div>
                <div class="pipeline-metric-value">${summary.with_url}</div>
                <div class="pipeline-metric-sub">${summary.coverage_pct}% coverage</div>
            </div>
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">Pending Review</div>
                <div class="pipeline-metric-value">${summary.pending_review}</div>
            </div>
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">No URL</div>
                <div class="pipeline-metric-value">${summary.no_url}</div>
            </div>
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">Sure URLs</div>
                <div class="pipeline-metric-value">${summary.sure_url}</div>
            </div>
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">Dubious URLs</div>
                <div class="pipeline-metric-value">${summary.dubious_url}</div>
            </div>
            <div class="pipeline-metric">
                <div class="pipeline-metric-label">Brave Search</div>
                <div class="pipeline-metric-value">${summary.brave_enabled ? 'ON' : 'OFF'}</div>
            </div>
        `;
    }

    function renderQueue() {
        const tbody = document.getElementById('pipeline-tbody');
        document.getElementById('pipeline-results').textContent = `${rows.length} rows`;

        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="crm-empty">No companies match this filter</td></tr>';
            return;
        }

        tbody.innerHTML = rows.map((row) => {
            const top = row.top_candidate || {};
            const candidateUrl = top.candidate_url || '';
            const confidence = typeof top.confidence === 'number' ? top.confidence : (row.stage_confidence || 65);
            const reasons = (top.reasons || []).join(' · ');

            return `
                <tr data-bp-id="${row.bp_id}" data-candidate-id="${top.id || ''}">
                    <td>
                        <div class="company-name">${esc(row.company_name)}</div>
                        <div class="company-sub">BP ${row.bp_id} · ${esc(row.industry || '—')}</div>
                    </td>
                    <td><span class="pipeline-stage pipeline-stage-${esc(row.stage_status)}">${formatStage(row.stage_status)}</span></td>
                    <td>
                        ${row.accepted_url ? `<a href="${esc(row.accepted_url)}" target="_blank" rel="noopener noreferrer">${esc(row.accepted_url)}</a>` : '<span class="crm-sub-text">—</span>'}
                        ${row.accepted_confidence ? `<div class="crm-sub-text">${row.accepted_confidence}% · ${esc(row.accepted_source || 'accepted')}</div>` : ''}
                    </td>
                    <td>
                        <input class="detail-input pipeline-url-input" value="${esc(candidateUrl)}" placeholder="https://example.com">
                    </td>
                    <td>
                        <input class="detail-input pipeline-confidence-input" type="number" min="0" max="100" step="0.1" value="${confidence}">
                    </td>
                    <td>
                        <div class="crm-sub-text">${top.source ? `source: ${esc(top.source)}` : '—'}</div>
                        <div class="crm-sub-text">${reasons ? esc(reasons) : ''}</div>
                    </td>
                    <td>
                        <div class="pipeline-actions-cell">
                            <button class="btn btn-sm" data-action="queue">Queue</button>
                            <button class="btn btn-sm btn-primary" data-action="accept">Accept</button>
                            <button class="btn btn-sm" data-action="reject" ${top.id ? '' : 'disabled'}>Reject Top</button>
                            <a class="btn btn-sm" href="/companies/${row.bp_id}">CRM</a>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        bindRowActions();
    }

    function bindRowActions() {
        document.querySelectorAll('#pipeline-tbody tr').forEach((tr) => {
            tr.querySelectorAll('[data-action]').forEach((button) => {
                button.addEventListener('click', async () => {
                    const action = button.dataset.action;
                    const bpId = parseInt(tr.dataset.bpId, 10);
                    const candidateId = parseInt(tr.dataset.candidateId, 10);
                    const urlInput = tr.querySelector('.pipeline-url-input').value.trim();
                    const confidenceInput = tr.querySelector('.pipeline-confidence-input').value.trim();
                    const confidence = confidenceInput === '' ? null : Number(confidenceInput);

                    if (action === 'queue') {
                        await setUrl(bpId, urlInput, confidence, 'pending');
                    }
                    if (action === 'accept') {
                        await setUrl(bpId, urlInput, confidence, 'accepted');
                    }
                    if (action === 'reject' && Number.isInteger(candidateId)) {
                        await rejectCandidate(candidateId);
                    }
                });
            });
        });
    }

    async function runDiscovery() {
        try {
            const btn = document.getElementById('run-discovery');
            const modelInput = document.getElementById('discovery-model');
            const limitInput = document.getElementById('discovery-limit');
            const topResultsInput = document.getElementById('discovery-top-results');
            const model = (modelInput && modelInput.value.trim()) || 'gpt-5-nano';
            const maxCompanies = Math.max(0, Number(limitInput && limitInput.value ? limitInput.value : 0));
            const maxResultsToReview = Math.max(1, Math.min(3, Number(topResultsInput && topResultsInput.value ? topResultsInput.value : 3)));
            btn.disabled = true;
            const res = await fetch('/api/pipeline/urls/discover-job/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    only_missing: true,
                    force: false,
                    model,
                    max_companies: Number.isFinite(maxCompanies) ? maxCompanies : 0,
                    max_results_to_review: Number.isFinite(maxResultsToReview) ? maxResultsToReview : 3,
                    clean_legacy_heuristics: true,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                if (res.status === 409 && data.active_job && data.active_job.job_id) {
                    showMessage('A discovery job is already running. Tracking current job.');
                    startDiscoveryPolling(data.active_job.job_id);
                    return;
                }
                throw new Error(data.error || 'Discovery run failed');
            }
            const job = data.job || {};
            if (!job.job_id) throw new Error('Discovery job did not start correctly');
            showMessage(`Discovery started · 0/${job.total || 0}`);
            startDiscoveryPolling(job.job_id);
        } catch (err) {
            showMessage(err.message, true);
            document.getElementById('run-discovery').disabled = false;
        }
    }

    function startDiscoveryPolling(jobId) {
        window.clearTimeout(discoveryJobPollTimer);

        const poll = async () => {
            try {
                const res = await fetch(`/api/pipeline/urls/discover-job?job_id=${encodeURIComponent(jobId)}`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Failed to load discovery job status');
                const job = data.job;
                if (!job) throw new Error('Discovery job not found');

                const inProgress = job.status === 'running' || job.status === 'idle';
                if (inProgress) {
                    const parts = [
                        `Discovery ${job.status}`,
                        `${job.processed || 0}/${job.total || 0}`,
                        `Current: ${job.current_company_name || '—'}`,
                        `Found: ${job.found_url_companies || 0}`,
                        `Probable: ${job.probable_url_companies || 0}`,
                        `NF: ${job.no_url_companies || 0}`,
                        `Tokens: ${job.total_tokens || 0}`,
                        `Cost: $${Number(job.estimated_cost_usd || 0).toFixed(4)}`,
                    ];
                    if (job.llm_errors) parts.push(`Errors: ${job.llm_errors}`);
                    showMessage(parts.join(' · '));
                    discoveryJobPollTimer = window.setTimeout(poll, 1500);
                    return;
                }

                document.getElementById('run-discovery').disabled = false;
                if (data.summary) {
                    summary = data.summary;
                    renderSummary();
                } else {
                    await loadSummary();
                }
                await loadQueue();

                const doneParts = [
                    `Discovery ${job.status}`,
                    `Processed: ${job.processed || 0}/${job.total || 0}`,
                    `Found: ${job.found_url_companies || 0}`,
                    `Probable: ${job.probable_url_companies || 0}`,
                    `NF: ${job.no_url_companies || 0}`,
                    `Tokens: ${job.total_tokens || 0}`,
                    `Cost: $${Number(job.estimated_cost_usd || 0).toFixed(4)}`,
                ];
                if (job.llm_errors) doneParts.push(`Errors: ${job.llm_errors}`);
                if (job.last_error) doneParts.push(`Last error: ${job.last_error}`);
                showMessage(doneParts.join(' · '), job.status === 'failed');
            } catch (err) {
                document.getElementById('run-discovery').disabled = false;
                showMessage(err.message, true);
            }
        };

        poll();
    }

    async function runAutoAccept() {
        try {
            const threshold = Number(document.getElementById('auto-threshold').value || '80');
            const res = await fetch('/api/pipeline/urls/auto-accept', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ min_confidence: threshold }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Auto-accept failed');
            summary = data.summary;
            renderSummary();
            await loadQueue();
            showMessage(`Auto-accepted ${data.accepted_count} URLs`);
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    async function setUrl(bpId, url, confidence, status) {
        try {
            const res = await fetch(`/api/pipeline/urls/company/${bpId}/set-url`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    candidate_url: url,
                    confidence,
                    status,
                    source: 'bulk_review',
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to save URL');
            summary = data.summary;
            renderSummary();
            await loadQueue();
            showMessage(status === 'accepted' ? 'URL accepted' : 'URL queued for review');
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    async function rejectCandidate(candidateId) {
        try {
            const res = await fetch(`/api/pipeline/urls/candidates/${candidateId}/reject`, {
                method: 'POST',
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to reject candidate');
            summary = data.summary;
            renderSummary();
            await loadQueue();
            showMessage('Candidate rejected');
        } catch (err) {
            showMessage(err.message, true);
        }
    }

    function formatStage(status) {
        switch (status) {
            case 'pending_review': return 'Pending review';
            case 'accepted': return 'Accepted';
            case 'no_url': return 'No URL';
            default: return 'Not started';
        }
    }

    function showMessage(message, isError) {
        const el = document.getElementById('pipeline-feedback');
        el.hidden = false;
        el.textContent = message;
        el.className = `crm-feedback ${isError ? 'crm-feedback-error' : 'crm-feedback-success'}`;
        window.clearTimeout(showMessage._timer);
        showMessage._timer = window.setTimeout(() => {
            el.hidden = true;
        }, 3200);
    }

    function debounce(fn, wait) {
        let timer = null;
        return (...args) => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => fn(...args), wait);
        };
    }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
})();
