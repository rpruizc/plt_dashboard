# PLT Dashboard: Full Feature Expansion Plan

## Context

After a call with SAP PLT leadership, 9 modules were identified to transform this tool from an account directory into a full operating console for demand generation. The current codebase is a Flask/SQLite/vanilla-JS territory intelligence platform with scoring, CRM, URL discovery, and role-based auth. This plan takes each module from its current state to 100% completion.

---

## Phase 0: Architectural Refactoring (Blueprint Split)

**Why:** `app.py` is 3,946 lines and `database.py` is 1,489 lines. Adding 9 modules without splitting first would make maintenance impossible.

**Complexity: XL | Dependencies: None**

### Database Layer → `db/` package

| New File | Contents (from database.py) |
|---|---|
| `db/__init__.py` | Re-exports everything for backward compat |
| `db/core.py` | `get_db()`, `init_db()`, `_serialize_json()`, `_parse_json()`, constants, all CREATE TABLE statements |
| `db/accounts.py` | `get_enrichment()`, `get_all_enrichments()`, `upsert_enrichment()`, `get_all_tags()`, cached_accounts functions |
| `db/crm.py` | All company_contacts, company_touchpoints, company_next_actions CRUD |
| `db/pipeline.py` | Pipeline status + URL candidate functions |
| `db/scoring.py` | Scoring profile CRUD, sharing, defaults, legacy weights |
| `db/users.py` | User CRUD, login code functions |
| `db/data_dictionary.py` | DD comment functions |

Keep `database.py` as a shim that imports and re-exports everything from `db/` so existing imports don't break.

### Route Layer → `routes/` package with Flask Blueprints

| New File | Blueprint | Routes (from app.py) |
|---|---|---|
| `routes/auth.py` | `auth_bp` | login, verify, logout, pending + decorators |
| `routes/accounts.py` | `accounts_bp` | /api/accounts, /api/stats, /api/tags, /api/industries |
| `routes/crm.py` | `crm_bp` | /companies/<bp_id> page + all /api/companies/ CRUD |
| `routes/pipeline.py` | `pipeline_bp` | /pipeline/urls page + all /api/pipeline/ routes |
| `routes/scoring.py` | `scoring_bp` | /scoring-profiles page + all /api/scoring-profiles/ + weights |
| `routes/admin.py` | `admin_bp` | /admin/users + approve/deny/make-admin |
| `routes/export.py` | `export_bp` | /api/export/csv, /api/export/presentation |

### Shared utilities → `utils.py`

Move: `sanitize()`, `normalize_text()`, `normalize_optional_int()`, `normalize_optional_date()`, `normalize_optional_confidence()`, `normalize_website_url()`, `validate_weights_payload()`, `validate_industry_scores_payload()`

### ACCOUNTS global → `state.py`

```python
# state.py
ACCOUNTS = {}
URL_DISCOVERY_JOBS = {}
URL_VALIDATION_JOBS = {}
```

All routes import from `state` instead of module-level globals.

### app.py becomes factory (~200 lines)

Flask app creation, config, blueprint registration, `build_accounts()`, `_init_app()`.

### Implementation order
1. Create `db/` package, move one module at a time, test after each
2. Create `utils.py` with shared helpers
3. Create `state.py` with globals
4. Create `routes/` package, move one blueprint at a time (start with auth, then admin, then accounts...)
5. Slim down `app.py` to factory

---

## Phase 1: Account Lifecycle Status

**Why:** Foundation for everything. Accounts need a formal state machine, not just scores and tags.

**Complexity: M | Dependencies: Phase 0 (recommended)**

### Database changes

Add columns to `account_enrichments` (migration pattern: try/except ALTER TABLE):
```sql
ALTER TABLE account_enrichments ADD COLUMN account_status TEXT DEFAULT 'cold';
ALTER TABLE account_enrichments ADD COLUMN whitespace_potential TEXT DEFAULT '';
ALTER TABLE account_enrichments ADD COLUMN compelling_event TEXT DEFAULT '';
ALTER TABLE account_enrichments ADD COLUMN access_difficulty TEXT DEFAULT 'unknown';
ALTER TABLE account_enrichments ADD COLUMN sap_solution_family TEXT DEFAULT '';
```

New table:
```sql
CREATE TABLE IF NOT EXISTS account_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    changed_by_user_id INTEGER,
    reason TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(changed_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
```

Valid statuses: `cold`, `engaged`, `nurtured`, `qualified`, `handover_ready`, `stalled`, `re_engage`

Valid access_difficulty: `unknown`, `easy`, `moderate`, `difficult`, `blocked`

### Status transition rules
```python
ALLOWED_TRANSITIONS = {
    'cold': ['engaged', 'stalled'],
    'engaged': ['nurtured', 'qualified', 'stalled'],
    'nurtured': ['qualified', 'stalled', 're_engage'],
    'qualified': ['handover_ready', 'stalled'],
    'handover_ready': ['stalled'],
    'stalled': ['re_engage', 'cold'],
    're_engage': ['engaged', 'cold'],
}
```

### New DB functions → `db/accounts.py`
- `update_account_status(bp_id, new_status, user_id, reason)` — validate transition, insert history, update enrichment
- `get_account_status_history(bp_id)` — list of transitions
- `get_accounts_by_status(status)` — filter by status
- `get_status_summary()` — `{status: count}` dict

### API endpoints
| Method | Path | Body | Response |
|---|---|---|---|
| `PUT` | `/api/companies/<bp_id>/status` | `{status, reason?}` | `{status, previous_status, changed_at}` |
| `GET` | `/api/companies/<bp_id>/status-history` | — | `[{from_status, to_status, reason, changed_by, created_at}]` |
| `GET` | `/api/accounts/status-summary` | — | `{cold: 42, engaged: 15, ...}` |

### Frontend changes
- **company.html/company.js**: Status badge + dropdown in company hero. Editable fields for whitespace_potential, compelling_event, access_difficulty, sap_solution_family. Status history expandable section.
- **index.html/app.js**: "Account Status" filter dropdown in filter bar. Status badge in table rows. Status counts in stats cards.
- **style.css**: Status color classes (`.status-cold`, `.status-engaged`, etc.)

---

## Phase 2: Opportunity Qualification Framework

**Why:** The core missing object. Transforms the tool from "account tracker" to "pipeline builder."

**Complexity: L | Dependencies: Phase 1**

### Database changes

```sql
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    business_problem TEXT DEFAULT '',
    compelling_event TEXT DEFAULT '',
    budget_signal TEXT DEFAULT '',
    timeline TEXT DEFAULT '',
    sponsor_contact_id INTEGER,
    growth_trigger TEXT DEFAULT '',
    confidence_score INTEGER DEFAULT 0,
    estimated_value REAL,
    status TEXT DEFAULT 'exploring',
    disqualification_reason TEXT DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(sponsor_contact_id) REFERENCES company_contacts(id) ON DELETE SET NULL,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Link existing CRM to opportunities
ALTER TABLE company_touchpoints ADD COLUMN opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL;
ALTER TABLE company_next_actions ADD COLUMN opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL;
```

Valid statuses: `exploring`, `validating`, `qualified`, `disqualified`, `handed_over`

### New DB functions → `db/opportunities.py`
- `list_opportunities(bp_id)` — all opportunities for a company
- `get_opportunity(opportunity_id)` — with sponsor contact name joined
- `create_opportunity(bp_id, title, created_by, **fields)`
- `update_opportunity(opportunity_id, fields)`
- `delete_opportunity(opportunity_id)`
- `get_opportunity_touchpoints(opportunity_id)` — linked touchpoints
- `get_opportunity_next_actions(opportunity_id)` — linked actions
- `get_all_opportunities_summary()` — counts by status for KPI
- `link_touchpoint_to_opportunity(touchpoint_id, opportunity_id)`
- `link_action_to_opportunity(action_id, opportunity_id)`

### API endpoints
| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/companies/<bp_id>/opportunities` | — | `[{id, title, status, confidence, ...}]` |
| `POST` | `/api/companies/<bp_id>/opportunities` | `{title, business_problem?, ...}` | 201 |
| `GET` | `/api/opportunities/<id>` | — | full opp with linked touchpoints/actions |
| `PUT` | `/api/opportunities/<id>` | `{fields}` | updated |
| `DELETE` | `/api/opportunities/<id>` | — | 204 |
| `POST` | `/api/opportunities/<id>/link-touchpoint` | `{touchpoint_id}` | 200 |
| `POST` | `/api/opportunities/<id>/link-action` | `{action_id}` | 200 |

### Frontend changes
- **company.html/company.js**: New "Opportunities" section between hero and CRM grid. Creation form (title, business_problem, compelling_event, budget_signal, timeline, sponsor dropdown, growth_trigger, confidence slider, estimated_value). Opportunity cards with status badges and confidence meters. "Link to Opportunity" dropdown added to touchpoint and next_action forms.
- **Auto-transition**: When opp status → `qualified`, account_status auto-transitions to `qualified`. When → `handed_over`, account → `handover_ready`.

---

## Phase 3: Handover Package Generator

**Why:** Handover is a KPI. Preserving context during transitions is critical.

**Complexity: L | Dependencies: Phase 2**

### Database changes

```sql
CREATE TABLE IF NOT EXISTS handovers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    bp_id INTEGER NOT NULL,
    from_user_id INTEGER NOT NULL,
    to_user_id INTEGER,
    handover_date TEXT,
    contacts_json TEXT DEFAULT '[]',
    pains_stated TEXT DEFAULT '',
    solutions_discussed TEXT DEFAULT '',
    objections_raised TEXT DEFAULT '',
    validated_items TEXT DEFAULT '',
    unvalidated_items TEXT DEFAULT '',
    recommended_next_step TEXT DEFAULT '',
    owner_after_handoff TEXT DEFAULT '',
    sla_followup_date TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE,
    FOREIGN KEY(from_user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY(to_user_id) REFERENCES users(id) ON DELETE SET NULL
);
```

Valid statuses: `draft`, `sent`, `accepted`, `completed`

### New DB functions → `db/handovers.py`
- `create_handover(opportunity_id, from_user_id, **fields)` — auto-populates bp_id from opportunity
- `get_handover(handover_id)` — with joined user emails
- `update_handover(handover_id, fields)`
- `list_handovers_for_company(bp_id)`
- `list_handovers_by_status(status)` — for cadence dashboard
- `get_pending_sla_handovers()` — where sla_followup_date <= today and status != completed
- `auto_populate_handover(opportunity_id)` — builds pre-filled dict from contacts, touchpoints, next_actions

### API endpoints
| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/opportunities/<id>/handover` | `{}` (auto-populates) | 201 |
| `GET` | `/api/handovers/<id>` | — | full handover |
| `PUT` | `/api/handovers/<id>` | `{fields}` | updated |
| `GET` | `/api/handovers/<id>/export` | `?format=html` | HTML document |
| `GET` | `/api/companies/<bp_id>/handovers` | — | list |

### Frontend changes
- **company.html/company.js**: "Generate Handover" button on qualified opportunities. Opens handover editor with pre-populated fields. Status badge + SLA timer (days remaining, red/yellow/green).
- **New template**: `templates/handover_export.html` — standalone print-friendly HTML for PDF export via browser print.

---

## Phase 4: KPI Dashboard

**Why:** Replace activity counting with conversion visibility. Can't manage what you can't measure.

**Complexity: M | Dependencies: Phase 2, Phase 3**

### Database changes
None — all KPIs computed from existing tables (opportunities, handovers, account_status_history, touchpoints, next_actions).

### New DB functions → `db/kpis.py`
- `get_opportunity_counts_by_period(start, end, group_by='month')`
- `get_handover_counts_by_period(start, end)`
- `get_disqualification_breakdown(start?, end?)`
- `get_avg_time_to_qualified()` — days from first touchpoint to qualified status
- `get_pipeline_value_by_status()` — sums estimated_value by opp status
- `get_conversion_rates()` — nurture-to-opp, handover-to-next-step rates
- `get_re_engagement_stats()` — stalled → re_engage → engaged success count
- `get_stale_accounts(days_threshold=30)`

### API endpoints
| Method | Path | Params | Response |
|---|---|---|---|
| `GET` | `/api/kpis/summary` | `?start=&end=&period=month` | `{opps_created, handovers_completed, conversion_rate, ...}` |
| `GET` | `/api/kpis/opportunities-trend` | `?months=6` | `[{month, count, by_status}]` |
| `GET` | `/api/kpis/disqualification-breakdown` | — | `{reasons: [{reason, count, pct}]}` |
| `GET` | `/api/kpis/pipeline-value` | — | `{by_status: {...}, total}` |
| `GET` | `/api/kpis/conversion-funnel` | — | `{cold_to_engaged, engaged_to_qualified, ...}` |
| `GET` | `/api/kpis/stale-accounts` | `?days=30` | `[{bp_id, company_name, last_touch, days_stale}]` |

### Frontend — new page
- **`templates/kpis.html`** + **`static/kpis.js`**
- Top row: KPI summary cards (opps created, handovers completed, conversion rate, pipeline value)
- Charts: opportunities trend (bar), disqualification reasons (donut), conversion funnel (horizontal bar)
- Tables: stale accounts, re-engagement queue
- Uses same inline SVG/DOM chart approach as existing `app.js` `renderCharts()`
- Add "KPIs" link to header nav on index.html

---

## Phase 5: Seller/Pre-sales Alignment

**Why:** SDEs fail when misaligned with sales priorities. This creates the feedback loop.

**Complexity: M | Dependencies: Phase 1, Phase 2**

### Database changes

```sql
CREATE TABLE IF NOT EXISTS territory_priorities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region TEXT DEFAULT '',
    quarter TEXT NOT NULL,
    focus_areas_json TEXT DEFAULT '[]',
    target_industries_json TEXT DEFAULT '[]',
    notes TEXT DEFAULT '',
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS seller_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    opportunity_id INTEGER,
    feedback_type TEXT NOT NULL,
    reason TEXT DEFAULT '',
    submitted_by INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE SET NULL,
    FOREIGN KEY(submitted_by) REFERENCES users(id) ON DELETE SET NULL
);
```

Valid feedback_type: `not_opportunity`, `needs_work`, `good_lead`, `wrong_timing`, `other`

### New DB functions → `db/alignment.py`
- `create_territory_priority(quarter, created_by, **fields)`
- `get_territory_priorities(quarter?)`
- `update_territory_priority(id, fields)`
- `delete_territory_priority(id)`
- `create_seller_feedback(bp_id, feedback_type, submitted_by, opportunity_id?, reason?)`
- `list_seller_feedback(bp_id?)`
- `get_feedback_summary()` — counts by type

### API endpoints
| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/territory-priorities` | `?quarter=2026Q2` | list |
| `POST` | `/api/territory-priorities` | `{quarter, focus_areas, ...}` | 201 |
| `PUT` | `/api/territory-priorities/<id>` | `{fields}` | updated |
| `DELETE` | `/api/territory-priorities/<id>` | — | 204 |
| `POST` | `/api/companies/<bp_id>/feedback` | `{feedback_type, reason?, opportunity_id?}` | 201 |
| `GET` | `/api/companies/<bp_id>/feedback` | — | list |
| `GET` | `/api/feedback/summary` | — | `{not_opportunity: 5, good_lead: 12, ...}` |

### Frontend changes
- **company.html/company.js**: "Seller Feedback" section at bottom of company page. Form: feedback_type dropdown + reason textarea + submit. Feedback history list below.
- **index.html**: Optional "Priorities" panel/modal showing current quarter focus areas.

---

## Phase 6: Operating Cadences Dashboard

**Why:** Converts the tool from archive to operating console with weekly/quarterly rhythm.

**Complexity: M | Dependencies: Phase 1, Phase 2, Phase 3**

### Database changes
None — read-only dashboard aggregating existing data.

### New DB functions → `db/cadences.py`
- `get_weekly_touch_targets(user_id?)` — accounts touched this week + pacing
- `get_quarter_pacing(quarter)` — opps/handovers vs targets
- `get_stuck_accounts(days=14)` — accounts in same status too long
- `get_upcoming_handovers(days=14)` — handovers with approaching SLA
- `get_accounts_needing_review()` — accounts with unresolved seller feedback
- `get_re_engagement_queue()` — accounts with status `re_engage`
- `get_overdue_actions(user_id?)` — next_actions past due_date

### API endpoints
| Method | Path | Params | Response |
|---|---|---|---|
| `GET` | `/api/cadences/weekly-touches` | `?week_of=` | `{touched, target, pace_pct, details}` |
| `GET` | `/api/cadences/quarter-pacing` | `?quarter=` | `{opps_target, opps_actual, ...}` |
| `GET` | `/api/cadences/stuck-accounts` | `?days=14` | `[{bp_id, company, status, days_in_status}]` |
| `GET` | `/api/cadences/upcoming-handovers` | `?days=14` | `[{handover_id, company, sla_date, days_left}]` |
| `GET` | `/api/cadences/overdue-actions` | — | `[{action_id, bp_id, company, title, days_overdue}]` |
| `GET` | `/api/cadences/re-engagement-queue` | — | `[{bp_id, company, stalled_since, last_touch}]` |

### Frontend — new page
- **`templates/cadences.html`** + **`static/cadences.js`**
- 6 panels: Weekly touches (progress bar + list), Quarter pacing (bar charts), Stuck accounts (table), Upcoming handovers (table with SLA countdown), Overdue actions (table, red/yellow), Re-engagement queue (table with action button)
- Add "Cadences" link to header nav

---

## Phase 7: Nurture & Re-engagement Engine

**Why:** Structured follow-up sequences and trigger-based outreach for warming cold accounts.

**Complexity: XL | Dependencies: Phase 1, Phase 6**

### Database changes

```sql
CREATE TABLE IF NOT EXISTS nurture_sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    account_status_trigger TEXT NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER DEFAULT 1,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS nurture_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    sequence_id INTEGER NOT NULL,
    current_step INTEGER DEFAULT 0,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    next_step_due TEXT,
    status TEXT DEFAULT 'active',
    completed_at TIMESTAMP,
    FOREIGN KEY(sequence_id) REFERENCES nurture_sequences(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS engagement_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    source TEXT DEFAULT '',
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    suggested_action TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    acted_by INTEGER,
    acted_at TIMESTAMP,
    FOREIGN KEY(acted_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS content_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content_type TEXT NOT NULL,
    industry TEXT DEFAULT '',
    file_url TEXT DEFAULT '',
    description TEXT DEFAULT '',
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);
```

Valid trigger_type: `funding`, `expansion`, `exec_change`, `hiring_spike`, `transformation`
Valid content_type: `business_case`, `roi_story`, `reference`, `video`, `industry_prompt`
steps_json format: `[{"day_offset": 0, "action_type": "email", "template": "..."}, ...]`

### New DB functions → `db/nurture.py`
- Sequence CRUD: `create_nurture_sequence()`, `list_nurture_sequences()`, `update_nurture_sequence()`, `delete_nurture_sequence()`
- Enrollment: `enroll_account()`, `advance_enrollment()`, `get_due_enrollments()`
- Triggers: `create_engagement_trigger()`, `list_engagement_triggers()`, `act_on_trigger()`, `dismiss_trigger()`
- Content: `create_content()`, `list_content()`, `update_content()`, `delete_content()`

### API endpoints (14 total)
- Sequences CRUD: GET/POST/PUT/DELETE `/api/nurture/sequences`
- Enrollment: POST `/api/companies/<bp_id>/nurture/enroll`, GET `/api/nurture/due-actions`, POST `/api/nurture/enrollments/<id>/advance`
- Triggers: GET/POST `/api/engagement-triggers`, POST `/<id>/act`, POST `/<id>/dismiss`
- Content: GET/POST/PUT/DELETE `/api/content-library`

### Frontend changes
- **cadences.html**: Add "Engagement Triggers" panel (new triggers with act/dismiss). Add "Nurture Due" panel.
- **company.html/company.js**: "Enroll in Nurture" button. Show triggers for this company. Content suggestions by industry.
- **New admin section**: Manage sequences and content library (could be tabs on admin page or `/nurture-admin`).

---

## Phase 8: SAP Collaboration Mode

**Why:** Partners bring SAP real accounts for qualification, C-level support, nurturing. Needs a formal workflow.

**Complexity: M | Dependencies: Phase 2**

### Database changes

```sql
CREATE TABLE IF NOT EXISTS sap_assist_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    opportunity_id INTEGER,
    reason TEXT NOT NULL,
    prep_brief TEXT DEFAULT '',
    status TEXT DEFAULT 'requested',
    joint_notes TEXT DEFAULT '',
    post_engagement_handover TEXT DEFAULT '',
    registration_reminder_date TEXT,
    created_by INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE SET NULL,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);
```

Valid reason: `access`, `c_level`, `qualification`, `nurture`, `re_engagement`
Valid status: `requested`, `active`, `completed`

### New DB functions → `db/sap_assist.py`
- `create_sap_assist(bp_id, reason, created_by, opportunity_id?, prep_brief?)`
- `get_sap_assist(assist_id)` — with company name join
- `update_sap_assist(assist_id, fields)`
- `list_sap_assists(status?)` — for queue view
- `list_sap_assists_for_company(bp_id)`
- `auto_generate_prep_brief(bp_id, opportunity_id?)` — builds from touchpoints, contacts, opportunity

### API endpoints
| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/companies/<bp_id>/sap-assist` | `{reason, opportunity_id?, prep_brief?}` | 201 |
| `GET` | `/api/sap-assist` | `?status=requested` | queue |
| `GET` | `/api/sap-assist/<id>` | — | full request |
| `PUT` | `/api/sap-assist/<id>` | `{fields}` | updated |
| `GET` | `/api/companies/<bp_id>/sap-assist` | — | list for company |
| `POST` | `/api/sap-assist/<id>/generate-brief` | — | `{prep_brief}` |

### Frontend changes
- **company.html/company.js**: "Request SAP Assist" button in hero/opportunities. Form: reason dropdown, optional opp link, prep brief textarea. Status tracking. Auto-generate brief button.
- **cadences.html**: "SAP Assist Queue" panel showing requested/active assists.
- **index.html**: Small icon/flag on accounts with active SAP assists.

---

## Phase 9: Listening & Objection Intelligence

**Why:** Makes the tool a qualification coach, not just a CRM. Prepares SDEs to hear business cues.

**Complexity: L | Dependencies: Phase 2 (optional)**

### Database changes

```sql
CREATE TABLE IF NOT EXISTS pain_point_dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry TEXT DEFAULT '',
    pain_point TEXT NOT NULL,
    related_solutions TEXT DEFAULT '',
    discovery_questions_json TEXT DEFAULT '[]',
    cue_words_json TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS objection_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    objection_text TEXT NOT NULL,
    category TEXT DEFAULT '',
    suggested_response TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### New DB functions → `db/intelligence.py`
- Pain points: `list_pain_points(industry?)`, `create_pain_point()`, `update_pain_point()`, `delete_pain_point()`, `search_pain_points(query)`
- Objections: `list_objections(category?, industry?)`, `create_objection()`, `update_objection()`, `delete_objection()`, `search_objections(query)`
- Detection: `detect_cue_words(text)` — scans text against cue_words_json, returns matches

### API endpoints
| Method | Path | Params/Body | Response |
|---|---|---|---|
| `GET` | `/api/intelligence/pain-points` | `?industry=&q=` | list |
| `POST` | `/api/intelligence/pain-points` | `{pain_point, ...}` | 201 |
| `PUT` | `/api/intelligence/pain-points/<id>` | `{fields}` | updated |
| `DELETE` | `/api/intelligence/pain-points/<id>` | — | 204 |
| `GET` | `/api/intelligence/objections` | `?category=&industry=&q=` | list |
| `POST` | `/api/intelligence/objections` | `{objection_text, ...}` | 201 |
| `PUT` | `/api/intelligence/objections/<id>` | `{fields}` | updated |
| `DELETE` | `/api/intelligence/objections/<id>` | — | 204 |
| `POST` | `/api/intelligence/detect-cues` | `{text}` | `{matches: [{pain_point, cue_word, questions}]}` |
| `POST` | `/api/intelligence/classify-note` | `{text}` | LLM-classified response |

### Frontend changes
- **company.html/company.js**: Collapsible "Intelligence" panel showing industry-relevant pain points with discovery questions, objection responses, search. "Buzzword detection": debounce-call detect-cues API while typing touchpoint notes, show inline suggestions.
- **Admin section**: CRUD for pain points and objections (table with inline edit).
- **LLM integration**: `/api/intelligence/classify-note` uses same OpenAI client pattern as URL pipeline.

---

## Summary

| Phase | Module | New Tables | New Endpoints | New Pages | Complexity | Depends On |
|---|---|---|---|---|---|---|
| 0 | Blueprint Refactoring | 0 | 0 | 0 | XL | — |
| 1 | Account Lifecycle | 1 (+5 cols) | 3 | 0 | M | 0 |
| 2 | Opportunity Qualification | 1 (+2 cols) | 7 | 0 | L | 1 |
| 3 | Handover Generator | 1 | 5 | 1 (export) | L | 2 |
| 4 | KPI Dashboard | 0 | 6 | 1 | M | 2, 3 |
| 5 | Seller Alignment | 2 | 7 | 0 | M | 1, 2 |
| 6 | Operating Cadences | 0 | 6 | 1 | M | 1, 2, 3 |
| 7 | Nurture & Re-engagement | 4 | 14 | 0 | XL | 1, 6 |
| 8 | SAP Collaboration | 1 | 6 | 0 | M | 2 |
| 9 | Listening & Objection | 2 | 10 | 0 | L | 2 (optional) |

**Totals:** 12 new tables, ~64 new API endpoints, 3 new standalone pages, ~8 modified existing pages

### Critical files
- `app.py` (3,946 lines) — split into blueprints in Phase 0
- `database.py` (1,489 lines) — split into db/ package in Phase 0
- `templates/company.html` — receives heaviest modifications (Phases 1-3, 5, 7-9)
- `static/company.js` (332 lines) — most new functionality added here
- `static/style.css` (1,690 lines) — all new component styles

### Migration strategy
All migrations use the existing pattern: `CREATE TABLE IF NOT EXISTS` in `init_db()` for new tables, `try/except ALTER TABLE ADD COLUMN` for column additions. No migration framework needed — runs idempotently on every startup.

### Verification
After each phase:
1. Run `python app.py` — verify startup with no errors, all tables created
2. Test new API endpoints with curl/browser dev tools
3. Verify UI renders correctly in both dark/light themes
4. Check that existing features (scoring, URL pipeline, CRM) still work
5. For Phase 0 specifically: run full app and verify every existing page/endpoint works identically
