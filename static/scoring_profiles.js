(function () {
    'use strict';

    const WEIGHT_FIELDS = [
        ['industry_match', 'Industry Match'],
        ['company_size', 'Company Size'],
        ['sap_relationship', 'SAP Relationship'],
        ['data_completeness', 'Data Completeness'],
    ];

    const state = {
        profiles: [],
        activeProfileId: null,
        defaultProfileId: null,
        currentProfile: null,
        isSuperAdmin: false,
    };

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        const role = document.body.dataset.userRole || '';
        state.isSuperAdmin = role === 'super_admin';

        if (!state.isSuperAdmin) {
            document.getElementById('btn-set-default').style.display = 'none';
        }

        document.getElementById('btn-create-profile').addEventListener('click', createProfile);
        document.getElementById('btn-duplicate-profile').addEventListener('click', duplicateProfile);
        document.getElementById('btn-select-profile').addEventListener('click', selectProfileForSession);
        document.getElementById('btn-set-default').addEventListener('click', setGlobalDefault);
        document.getElementById('btn-save-profile').addEventListener('click', saveProfile);
        document.getElementById('btn-delete-profile').addEventListener('click', deleteProfile);
        document.getElementById('btn-share-profile').addEventListener('click', shareProfile);
        document.getElementById('shares-list').addEventListener('click', onShareListClick);

        loadProfiles();
    }

    async function api(url, options = {}) {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.error || `Request failed (${response.status})`);
        }
        return payload;
    }

    async function loadProfiles(preferredProfileId = null) {
        try {
            const data = await api('/api/scoring-profiles');
            state.profiles = data.profiles || [];
            state.activeProfileId = data.active_profile_id;
            state.defaultProfileId = data.default_profile_id;
            renderProfilesList();

            const fallback = state.profiles[0] ? state.profiles[0].id : null;
            const targetId = preferredProfileId || (state.currentProfile && state.currentProfile.id) || state.activeProfileId || fallback;
            if (targetId) {
                await loadProfile(targetId);
            }
        } catch (err) {
            alert(err.message);
        }
    }

    function renderProfilesList() {
        const list = document.getElementById('profiles-list');
        if (state.profiles.length === 0) {
            list.innerHTML = '<div class="profiles-note">No profiles available.</div>';
            return;
        }

        list.innerHTML = state.profiles.map((profile) => {
            const badges = [];
            if (profile.can_edit) badges.push('editable');
            if (profile.id === state.activeProfileId) badges.push('active');
            if (profile.id === state.defaultProfileId) badges.push('global default');
            const isSelected = state.currentProfile && state.currentProfile.id === profile.id;
            return `
                <button class="profile-item ${isSelected ? 'active' : ''}" data-profile-id="${profile.id}">
                    <div class="profile-item-name">${esc(profile.name)}</div>
                    <div class="profile-item-meta">${esc(profile.owner_email)}${badges.length ? ' • ' + badges.join(' • ') : ''}</div>
                </button>
            `;
        }).join('');

        list.querySelectorAll('[data-profile-id]').forEach((el) => {
            el.addEventListener('click', () => {
                loadProfile(parseInt(el.dataset.profileId, 10));
            });
        });
    }

    async function loadProfile(profileId) {
        try {
            const profile = await api(`/api/scoring-profiles/${profileId}`);
            state.currentProfile = profile;
            renderProfilesList();
            renderProfileForm(profile);
        } catch (err) {
            alert(err.message);
        }
    }

    function renderProfileForm(profile) {
        document.getElementById('profile-name').value = profile.name || '';
        document.getElementById('profile-owner').value = profile.owner_email || '';
        document.getElementById('profile-description').value = profile.description || '';

        const canEdit = !!profile.can_edit;
        document.getElementById('profile-name').disabled = !canEdit;
        document.getElementById('profile-description').disabled = !canEdit;
        document.getElementById('share-email').disabled = !canEdit;
        document.getElementById('btn-share-profile').disabled = !canEdit;
        document.getElementById('btn-save-profile').disabled = !canEdit;
        document.getElementById('btn-delete-profile').disabled = !canEdit;

        const weightsForm = document.getElementById('weights-form');
        weightsForm.innerHTML = WEIGHT_FIELDS.map(([key, label]) => `
            <div>
                <label class="profiles-label" for="weight-${key}">${label}</label>
                <input class="profiles-number" id="weight-${key}" type="number" min="0" max="1" step="0.01" value="${profile.weights[key]}" ${canEdit ? '' : 'disabled'}>
            </div>
        `).join('');
        if (canEdit) {
            WEIGHT_FIELDS.forEach(([key]) => {
                const input = document.getElementById(`weight-${key}`);
                input.addEventListener('input', updateWeightValidationState);
                input.addEventListener('change', updateWeightValidationState);
            });
        }
        updateWeightValidationState();

        const industriesBody = document.getElementById('industry-scores-body');
        const sortedIndustries = Object.keys(profile.industry_scores).sort((a, b) => {
            const scoreDiff = (profile.industry_scores[b] || 0) - (profile.industry_scores[a] || 0);
            if (scoreDiff !== 0) return scoreDiff;
            return a.localeCompare(b);
        });
        industriesBody.innerHTML = sortedIndustries.map((industry) => `
            <tr>
                <td>${esc(industry)}</td>
                <td>
                    <input class="profiles-number industry-score-input" data-industry="${esc(industry)}" type="number" min="0" max="100" step="0.1" value="${profile.industry_scores[industry]}" ${canEdit ? '' : 'disabled'}>
                </td>
            </tr>
        `).join('');

        if (canEdit) {
            industriesBody.querySelectorAll('.industry-score-input').forEach((input) => {
                input.dataset.lastCommittedValue = input.value;
                input.addEventListener('blur', onIndustryScoreCommit);
                input.addEventListener('change', onIndustryScoreCommit);
            });
        }

        renderShares(profile);
    }

    function renderShares(profile) {
        const sharesList = document.getElementById('shares-list');
        if (!profile.can_edit) {
            sharesList.innerHTML = '<div class="profiles-note">Read-only profile. Sharing is only available for the owner.</div>';
            return;
        }

        const shares = profile.shares || [];
        if (shares.length === 0) {
            sharesList.innerHTML = '<div class="profiles-note">Not shared with anyone yet.</div>';
            return;
        }

        sharesList.innerHTML = shares.map((share) => `
            <div class="share-row">
                <span>${esc(share.email)}</span>
                <button class="btn btn-sm" data-remove-share="${share.id}">Remove</button>
            </div>
        `).join('');
    }

    function collectProfileData() {
        const profile = state.currentProfile;
        const weights = {};
        for (const [key] of WEIGHT_FIELDS) {
            const value = parseFloat(document.getElementById(`weight-${key}`).value);
            if (!Number.isFinite(value)) return { error: `Invalid weight value for ${key}` };
            weights[key] = value;
        }

        const weightSum = Object.values(weights).reduce((sum, value) => sum + value, 0);
        if (Math.abs(weightSum - 1.0) > 0.001) {
            return { error: 'Weights must sum to 1.0' };
        }

        const industryScores = {};
        document.querySelectorAll('.industry-score-input').forEach((input) => {
            const value = parseFloat(input.value);
            if (!Number.isFinite(value) || value < 0 || value > 100) {
                industryScores.__invalid = true;
                return;
            }
            industryScores[input.dataset.industry] = value;
        });
        if (industryScores.__invalid) {
            return { error: 'Industry scores must be numeric and between 0 and 100' };
        }

        return {
            name: document.getElementById('profile-name').value.trim(),
            description: document.getElementById('profile-description').value.trim(),
            weights,
            industry_scores: industryScores,
        };
    }

    function getWeightValidationState() {
        const weights = {};
        for (const [key] of WEIGHT_FIELDS) {
            const input = document.getElementById(`weight-${key}`);
            const value = parseFloat(input.value);
            if (!Number.isFinite(value)) {
                return { valid: false, sum: NaN };
            }
            weights[key] = value;
        }
        const sum = Object.values(weights).reduce((acc, value) => acc + value, 0);
        return { valid: Math.abs(sum - 1.0) <= 0.001, sum };
    }

    function updateWeightValidationState() {
        const validationEl = document.getElementById('weights-validation');
        if (!validationEl) return;

        const { valid, sum } = getWeightValidationState();
        const readableSum = Number.isFinite(sum) ? sum.toFixed(2) : '—';

        validationEl.textContent = valid
            ? `Weight sum is valid (${readableSum}).`
            : `Weight sum must be 1.00 (current: ${readableSum}).`;
        validationEl.className = `profiles-note ${valid ? 'profiles-note-valid' : 'profiles-note-invalid'}`;

        if (state.currentProfile && state.currentProfile.can_edit) {
            document.getElementById('btn-save-profile').disabled = !valid;
        }
    }

    function onIndustryScoreCommit(event) {
        const input = event.target;
        const currentValue = input.value;
        const previousValue = input.dataset.lastCommittedValue;
        if (currentValue !== previousValue) {
            const row = input.closest('tr');
            if (row) {
                row.classList.remove('industry-row-updated');
                // Restart animation if user edits same row repeatedly.
                void row.offsetWidth;
                row.classList.add('industry-row-updated');
                window.setTimeout(() => row.classList.remove('industry-row-updated'), 2200);
            }
            input.dataset.lastCommittedValue = currentValue;
        }
        reorderIndustryRowsByScore();
    }

    function reorderIndustryRowsByScore() {
        const body = document.getElementById('industry-scores-body');
        const rows = Array.from(body.querySelectorAll('tr'));

        rows.sort((rowA, rowB) => {
            const inputA = rowA.querySelector('.industry-score-input');
            const inputB = rowB.querySelector('.industry-score-input');
            const valueA = parseFloat(inputA.value);
            const valueB = parseFloat(inputB.value);
            const scoreA = Number.isFinite(valueA) ? valueA : -1;
            const scoreB = Number.isFinite(valueB) ? valueB : -1;

            if (scoreB !== scoreA) {
                return scoreB - scoreA;
            }
            return inputA.dataset.industry.localeCompare(inputB.dataset.industry);
        });

        rows.forEach((row) => body.appendChild(row));
    }

    async function saveProfile() {
        const profile = state.currentProfile;
        if (!profile || !profile.can_edit) return;

        const data = collectProfileData();
        if (data.error) {
            alert(data.error);
            return;
        }
        if (!data.name) {
            alert('Profile name is required');
            return;
        }

        try {
            await api(`/api/scoring-profiles/${profile.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            await loadProfiles(profile.id);
        } catch (err) {
            alert(err.message);
        }
    }

    async function createProfile() {
        const name = prompt('New profile name');
        if (!name) return;
        try {
            const created = await api('/api/scoring-profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() }),
            });
            await loadProfiles(created.id);
        } catch (err) {
            alert(err.message);
        }
    }

    async function duplicateProfile() {
        const profile = state.currentProfile;
        if (!profile) return;
        const name = prompt('Duplicate profile name', `${profile.name} Copy`);
        if (!name) return;

        try {
            const created = await api('/api/scoring-profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name.trim(),
                    copy_from_profile_id: profile.id,
                }),
            });
            await loadProfiles(created.id);
        } catch (err) {
            alert(err.message);
        }
    }

    async function deleteProfile() {
        const profile = state.currentProfile;
        if (!profile || !profile.can_edit) return;
        if (!confirm(`Delete profile "${profile.name}"?`)) return;

        try {
            await api(`/api/scoring-profiles/${profile.id}`, { method: 'DELETE' });
            state.currentProfile = null;
            await loadProfiles();
        } catch (err) {
            alert(err.message);
        }
    }

    async function selectProfileForSession() {
        const profile = state.currentProfile;
        if (!profile) return;
        try {
            await api(`/api/scoring-profiles/${profile.id}/select`, { method: 'POST' });
            state.activeProfileId = profile.id;
            renderProfilesList();
            alert(`Profile "${profile.name}" is now active for your current session.`);
        } catch (err) {
            alert(err.message);
        }
    }

    async function setGlobalDefault() {
        if (!state.isSuperAdmin) return;
        const profile = state.currentProfile;
        if (!profile) return;
        if (!confirm(`Set "${profile.name}" as global default for all users on next load?`)) return;

        try {
            await api(`/api/scoring-profiles/${profile.id}/set-default`, { method: 'POST' });
            state.defaultProfileId = profile.id;
            renderProfilesList();
            alert(`"${profile.name}" is now the global default profile.`);
        } catch (err) {
            alert(err.message);
        }
    }

    async function shareProfile() {
        const profile = state.currentProfile;
        if (!profile || !profile.can_edit) return;

        const emailInput = document.getElementById('share-email');
        const email = emailInput.value.trim().toLowerCase();
        if (!email) {
            alert('Enter an email to share with');
            return;
        }

        try {
            await api(`/api/scoring-profiles/${profile.id}/share`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email }),
            });
            emailInput.value = '';
            await loadProfile(profile.id);
        } catch (err) {
            alert(err.message);
        }
    }

    async function onShareListClick(e) {
        const removeBtn = e.target.closest('[data-remove-share]');
        if (!removeBtn || !state.currentProfile || !state.currentProfile.can_edit) return;

        const targetUserId = parseInt(removeBtn.dataset.removeShare, 10);
        if (!targetUserId) return;

        try {
            await api(`/api/scoring-profiles/${state.currentProfile.id}/share/${targetUserId}`, {
                method: 'DELETE',
            });
            await loadProfile(state.currentProfile.id);
        } catch (err) {
            alert(err.message);
        }
    }

    function esc(value) {
        if (value === null || value === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(value);
        return div.innerHTML;
    }
})();
