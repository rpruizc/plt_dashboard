# PLT Dashboard — Task Tracker

## Phase 0: Architectural Refactoring (Blueprint Split)

### Database Layer → `db/` package
- [x] Create `db/` directory and `db/__init__.py` with re-exports
- [x] Extract `db/core.py` — `get_db()`, `init_db()`, serialization helpers, constants, CREATE TABLE statements
- [x] Extract `db/accounts.py` — `get_enrichment()`, `get_all_enrichments()`, `upsert_enrichment()`, `get_all_tags()`, cached account functions
- [x] Extract `db/crm.py` — company_contacts, company_touchpoints, company_next_actions CRUD
- [x] Extract `db/pipeline.py` — pipeline status + URL candidate functions
- [x] Extract `db/scoring.py` — scoring profile CRUD, sharing, defaults, legacy weights
- [x] Extract `db/users.py` — user CRUD, login code functions
- [x] Extract `db/data_dictionary.py` — DD comment functions
- [x] Convert `database.py` to a shim that imports/re-exports from `db/`
- [x] Verify all existing imports still work after shim

### Shared Utilities
- [x] Create `utils.py` — move `sanitize()`, `normalize_text()`, `normalize_optional_int()`, `normalize_optional_date()`, `normalize_optional_confidence()`, `normalize_website_url()`, `validate_weights_payload()`, `validate_industry_scores_payload()`
- [x] Update all callers in `app.py` and `database.py` modules to import from `utils.py`

### State Module
- [x] Create `state.py` with `ACCOUNTS`, `URL_DISCOVERY_JOBS`, `URL_VALIDATION_JOBS`
- [x] Update all references to these globals across app.py

### Route Layer → `routes/` package
- [x] Create `routes/` directory and `routes/__init__.py`
- [x] Extract `routes/auth.py` (`auth_bp`) — login, verify, logout, pending + decorators
- [x] Extract `routes/admin.py` (`admin_bp`) — /admin/users + approve/deny/make-admin
- [x] Extract `routes/accounts.py` (`accounts_bp`) — /api/accounts, /api/stats, /api/tags, /api/industries
- [x] Extract `routes/crm.py` (`crm_bp`) — /companies/<bp_id> page + all /api/companies/ CRUD
- [x] Extract `routes/pipeline.py` (`pipeline_bp`) — /pipeline/urls page + all /api/pipeline/ routes
- [x] Extract `routes/scoring.py` (`scoring_bp`) — /scoring-profiles page + all /api/scoring-profiles/ + weights
- [x] Extract `routes/export.py` (`export_bp`) — /api/export/csv, /api/export/presentation

### App Factory
- [x] Slim `app.py` down to factory (~200 lines): app creation, config, blueprint registration, `build_accounts()`, `_init_app()`
- [x] Register all blueprints in app factory
- [x] Full regression test — every existing page and endpoint works identically

---

## Phase 1: Account Lifecycle Status

### Database
- [ ] Add migration: `account_status` column to `account_enrichments` (default 'cold')
- [ ] Add migration: `whitespace_potential` column to `account_enrichments`
- [ ] Add migration: `compelling_event` column to `account_enrichments`
- [ ] Add migration: `access_difficulty` column to `account_enrichments` (default 'unknown')
- [ ] Add migration: `sap_solution_family` column to `account_enrichments`
- [ ] Create `account_status_history` table in `init_db()`
- [ ] Define `ALLOWED_TRANSITIONS` dict and `VALID_STATUSES` list

### DB Functions — `db/accounts.py`
- [ ] Implement `update_account_status(bp_id, new_status, user_id, reason)` with transition validation
- [ ] Implement `get_account_status_history(bp_id)`
- [ ] Implement `get_accounts_by_status(status)`
- [ ] Implement `get_status_summary()` — returns `{status: count}`

### API Endpoints
- [ ] `PUT /api/companies/<bp_id>/status` — validate transition, update, return result
- [ ] `GET /api/companies/<bp_id>/status-history` — return transition list
- [ ] `GET /api/accounts/status-summary` — return status counts

### Frontend — Company Page
- [ ] Add status badge + dropdown to company hero section
- [ ] Add editable fields: whitespace_potential, compelling_event, access_difficulty, sap_solution_family
- [ ] Add expandable status history section

### Frontend — Index Page
- [ ] Add "Account Status" filter dropdown to filter bar
- [ ] Add status badge column to account table rows
- [ ] Add status counts to stats cards

### Styling
- [ ] Add status color classes to `style.css` (`.status-cold`, `.status-engaged`, `.status-nurtured`, `.status-qualified`, `.status-handover_ready`, `.status-stalled`, `.status-re_engage`)

---

## Phase 2: Opportunity Qualification Framework

### Database
- [ ] Create `opportunities` table in `init_db()`
- [ ] Add migration: `opportunity_id` column to `company_touchpoints`
- [ ] Add migration: `opportunity_id` column to `company_next_actions`

### DB Functions — `db/opportunities.py`
- [ ] Create `db/opportunities.py` file
- [ ] Implement `create_opportunity(bp_id, title, created_by, **fields)`
- [ ] Implement `get_opportunity(opportunity_id)` — with sponsor contact name joined
- [ ] Implement `list_opportunities(bp_id)`
- [ ] Implement `update_opportunity(opportunity_id, fields)`
- [ ] Implement `delete_opportunity(opportunity_id)`
- [ ] Implement `get_opportunity_touchpoints(opportunity_id)`
- [ ] Implement `get_opportunity_next_actions(opportunity_id)`
- [ ] Implement `get_all_opportunities_summary()` — counts by status
- [ ] Implement `link_touchpoint_to_opportunity(touchpoint_id, opportunity_id)`
- [ ] Implement `link_action_to_opportunity(action_id, opportunity_id)`

### API Endpoints
- [ ] `GET /api/companies/<bp_id>/opportunities` — list opps for company
- [ ] `POST /api/companies/<bp_id>/opportunities` — create opp
- [ ] `GET /api/opportunities/<id>` — full opp with linked touchpoints/actions
- [ ] `PUT /api/opportunities/<id>` — update opp fields
- [ ] `DELETE /api/opportunities/<id>` — delete opp
- [ ] `POST /api/opportunities/<id>/link-touchpoint` — link touchpoint
- [ ] `POST /api/opportunities/<id>/link-action` — link action

### Frontend — Company Page
- [ ] Add "Opportunities" section between hero and CRM grid
- [ ] Build opportunity creation form (title, business_problem, compelling_event, budget_signal, timeline, sponsor dropdown, growth_trigger, confidence slider, estimated_value)
- [ ] Build opportunity cards with status badges and confidence meters
- [ ] Add "Link to Opportunity" dropdown to touchpoint form
- [ ] Add "Link to Opportunity" dropdown to next_action form

### Auto-Transition Logic
- [ ] When opp status → `qualified`, auto-transition account_status to `qualified`
- [ ] When opp status → `handed_over`, auto-transition account_status to `handover_ready`

---

## Phase 3: Handover Package Generator

### Database
- [ ] Create `handovers` table in `init_db()`

### DB Functions — `db/handovers.py`
- [ ] Create `db/handovers.py` file
- [ ] Implement `create_handover(opportunity_id, from_user_id, **fields)` — auto-populate bp_id
- [ ] Implement `get_handover(handover_id)` — with joined user emails
- [ ] Implement `update_handover(handover_id, fields)`
- [ ] Implement `list_handovers_for_company(bp_id)`
- [ ] Implement `list_handovers_by_status(status)`
- [ ] Implement `get_pending_sla_handovers()` — where sla_followup_date <= today and status != completed
- [ ] Implement `auto_populate_handover(opportunity_id)` — build pre-filled dict from contacts, touchpoints, next_actions

### API Endpoints
- [ ] `POST /api/opportunities/<id>/handover` — create auto-populated handover
- [ ] `GET /api/handovers/<id>` — full handover
- [ ] `PUT /api/handovers/<id>` — update fields
- [ ] `GET /api/handovers/<id>/export?format=html` — export as HTML
- [ ] `GET /api/companies/<bp_id>/handovers` — list for company

### Frontend — Company Page
- [ ] Add "Generate Handover" button on qualified opportunities
- [ ] Build handover editor with pre-populated fields
- [ ] Add status badge + SLA timer (days remaining, red/yellow/green)

### Export Template
- [ ] Create `templates/handover_export.html` — standalone print-friendly HTML for PDF export

---

## Phase 4: KPI Dashboard

### DB Functions — `db/kpis.py`
- [ ] Create `db/kpis.py` file
- [ ] Implement `get_opportunity_counts_by_period(start, end, group_by='month')`
- [ ] Implement `get_handover_counts_by_period(start, end)`
- [ ] Implement `get_disqualification_breakdown(start?, end?)`
- [ ] Implement `get_avg_time_to_qualified()` — days from first touchpoint to qualified
- [ ] Implement `get_pipeline_value_by_status()` — sum estimated_value by opp status
- [ ] Implement `get_conversion_rates()` — nurture-to-opp, handover-to-next-step
- [ ] Implement `get_re_engagement_stats()` — stalled → re_engage → engaged success count
- [ ] Implement `get_stale_accounts(days_threshold=30)`

### API Endpoints
- [ ] `GET /api/kpis/summary` — overall KPI summary with date range
- [ ] `GET /api/kpis/opportunities-trend` — monthly trend data
- [ ] `GET /api/kpis/disqualification-breakdown` — reasons with counts/pct
- [ ] `GET /api/kpis/pipeline-value` — value by status + total
- [ ] `GET /api/kpis/conversion-funnel` — stage-to-stage conversion rates
- [ ] `GET /api/kpis/stale-accounts` — stale account list

### Frontend — New Page
- [ ] Create `templates/kpis.html` page shell with nav
- [ ] Create `static/kpis.js` with data fetching
- [ ] Build KPI summary cards row (opps created, handovers completed, conversion rate, pipeline value)
- [ ] Build opportunities trend bar chart
- [ ] Build disqualification reasons donut chart
- [ ] Build conversion funnel horizontal bar chart
- [ ] Build stale accounts table
- [ ] Build re-engagement queue table
- [ ] Add "KPIs" link to header nav on index.html

---

## Phase 5: Seller/Pre-sales Alignment

### Database
- [ ] Create `territory_priorities` table in `init_db()`
- [ ] Create `seller_feedback` table in `init_db()`

### DB Functions — `db/alignment.py`
- [ ] Create `db/alignment.py` file
- [ ] Implement `create_territory_priority(quarter, created_by, **fields)`
- [ ] Implement `get_territory_priorities(quarter?)`
- [ ] Implement `update_territory_priority(id, fields)`
- [ ] Implement `delete_territory_priority(id)`
- [ ] Implement `create_seller_feedback(bp_id, feedback_type, submitted_by, opportunity_id?, reason?)`
- [ ] Implement `list_seller_feedback(bp_id?)`
- [ ] Implement `get_feedback_summary()` — counts by type

### API Endpoints
- [ ] `GET /api/territory-priorities` — list (optional quarter filter)
- [ ] `POST /api/territory-priorities` — create
- [ ] `PUT /api/territory-priorities/<id>` — update
- [ ] `DELETE /api/territory-priorities/<id>` — delete
- [ ] `POST /api/companies/<bp_id>/feedback` — submit seller feedback
- [ ] `GET /api/companies/<bp_id>/feedback` — list feedback for company
- [ ] `GET /api/feedback/summary` — feedback counts by type

### Frontend — Company Page
- [ ] Add "Seller Feedback" section at bottom of company page
- [ ] Build feedback form: feedback_type dropdown + reason textarea + submit
- [ ] Build feedback history list

### Frontend — Index Page
- [ ] Add "Priorities" panel/modal showing current quarter focus areas

---

## Phase 6: Operating Cadences Dashboard

### DB Functions — `db/cadences.py`
- [ ] Create `db/cadences.py` file
- [ ] Implement `get_weekly_touch_targets(user_id?)` — accounts touched this week + pacing
- [ ] Implement `get_quarter_pacing(quarter)` — opps/handovers vs targets
- [ ] Implement `get_stuck_accounts(days=14)` — accounts in same status too long
- [ ] Implement `get_upcoming_handovers(days=14)` — handovers with approaching SLA
- [ ] Implement `get_accounts_needing_review()` — accounts with unresolved seller feedback
- [ ] Implement `get_re_engagement_queue()` — accounts with status `re_engage`
- [ ] Implement `get_overdue_actions(user_id?)` — next_actions past due_date

### API Endpoints
- [ ] `GET /api/cadences/weekly-touches` — touch targets + pacing
- [ ] `GET /api/cadences/quarter-pacing` — quarter progress
- [ ] `GET /api/cadences/stuck-accounts` — stuck account list
- [ ] `GET /api/cadences/upcoming-handovers` — handovers approaching SLA
- [ ] `GET /api/cadences/overdue-actions` — overdue action list
- [ ] `GET /api/cadences/re-engagement-queue` — re-engagement list

### Frontend — New Page
- [ ] Create `templates/cadences.html` page shell with nav
- [ ] Create `static/cadences.js` with data fetching
- [ ] Build Weekly Touches panel (progress bar + list)
- [ ] Build Quarter Pacing panel (bar charts)
- [ ] Build Stuck Accounts table
- [ ] Build Upcoming Handovers table with SLA countdown
- [ ] Build Overdue Actions table (red/yellow indicators)
- [ ] Build Re-engagement Queue table with action buttons
- [ ] Add "Cadences" link to header nav

---

## Phase 7: Nurture & Re-engagement Engine

### Database
- [ ] Create `nurture_sequences` table in `init_db()`
- [ ] Create `nurture_enrollments` table in `init_db()`
- [ ] Create `engagement_triggers` table in `init_db()`
- [ ] Create `content_library` table in `init_db()`

### DB Functions — `db/nurture.py`
- [ ] Create `db/nurture.py` file
- [ ] Implement sequence CRUD: `create_nurture_sequence()`, `list_nurture_sequences()`, `update_nurture_sequence()`, `delete_nurture_sequence()`
- [ ] Implement enrollment: `enroll_account()`, `advance_enrollment()`, `get_due_enrollments()`
- [ ] Implement triggers: `create_engagement_trigger()`, `list_engagement_triggers()`, `act_on_trigger()`, `dismiss_trigger()`
- [ ] Implement content: `create_content()`, `list_content()`, `update_content()`, `delete_content()`

### API Endpoints — Sequences
- [ ] `GET /api/nurture/sequences` — list sequences
- [ ] `POST /api/nurture/sequences` — create sequence
- [ ] `PUT /api/nurture/sequences/<id>` — update sequence
- [ ] `DELETE /api/nurture/sequences/<id>` — delete sequence

### API Endpoints — Enrollment
- [ ] `POST /api/companies/<bp_id>/nurture/enroll` — enroll account
- [ ] `GET /api/nurture/due-actions` — list due enrollment steps
- [ ] `POST /api/nurture/enrollments/<id>/advance` — advance enrollment

### API Endpoints — Triggers
- [ ] `GET /api/engagement-triggers` — list triggers
- [ ] `POST /api/engagement-triggers` — create trigger
- [ ] `POST /api/engagement-triggers/<id>/act` — act on trigger
- [ ] `POST /api/engagement-triggers/<id>/dismiss` — dismiss trigger

### API Endpoints — Content Library
- [ ] `GET /api/content-library` — list content
- [ ] `POST /api/content-library` — create content
- [ ] `PUT /api/content-library/<id>` — update content
- [ ] `DELETE /api/content-library/<id>` — delete content

### Frontend — Cadences Page Additions
- [ ] Add "Engagement Triggers" panel (new triggers with act/dismiss)
- [ ] Add "Nurture Due" panel

### Frontend — Company Page Additions
- [ ] Add "Enroll in Nurture" button
- [ ] Show triggers for this company
- [ ] Content suggestions by industry

### Frontend — Admin Section
- [ ] Build sequence management UI (create/edit/delete sequences with step editor)
- [ ] Build content library management UI (table with inline edit)

---

## Phase 8: SAP Collaboration Mode

### Database
- [ ] Create `sap_assist_requests` table in `init_db()`

### DB Functions — `db/sap_assist.py`
- [ ] Create `db/sap_assist.py` file
- [ ] Implement `create_sap_assist(bp_id, reason, created_by, opportunity_id?, prep_brief?)`
- [ ] Implement `get_sap_assist(assist_id)` — with company name join
- [ ] Implement `update_sap_assist(assist_id, fields)`
- [ ] Implement `list_sap_assists(status?)`
- [ ] Implement `list_sap_assists_for_company(bp_id)`
- [ ] Implement `auto_generate_prep_brief(bp_id, opportunity_id?)` — builds from touchpoints, contacts, opportunity

### API Endpoints
- [ ] `POST /api/companies/<bp_id>/sap-assist` — create SAP assist request
- [ ] `GET /api/sap-assist` — list queue (optional status filter)
- [ ] `GET /api/sap-assist/<id>` — full request
- [ ] `PUT /api/sap-assist/<id>` — update request
- [ ] `GET /api/companies/<bp_id>/sap-assist` — list for company
- [ ] `POST /api/sap-assist/<id>/generate-brief` — auto-generate prep brief

### Frontend — Company Page
- [ ] Add "Request SAP Assist" button in hero/opportunities
- [ ] Build request form: reason dropdown, optional opp link, prep brief textarea
- [ ] Add status tracking display
- [ ] Add auto-generate brief button

### Frontend — Cadences Page
- [ ] Add "SAP Assist Queue" panel showing requested/active assists

### Frontend — Index Page
- [ ] Add small icon/flag on accounts with active SAP assists

---

## Phase 9: Listening & Objection Intelligence

### Database
- [ ] Create `pain_point_dictionary` table in `init_db()`
- [ ] Create `objection_library` table in `init_db()`

### DB Functions — `db/intelligence.py`
- [ ] Create `db/intelligence.py` file
- [ ] Implement `list_pain_points(industry?)`
- [ ] Implement `create_pain_point()`
- [ ] Implement `update_pain_point()`
- [ ] Implement `delete_pain_point()`
- [ ] Implement `search_pain_points(query)`
- [ ] Implement `list_objections(category?, industry?)`
- [ ] Implement `create_objection()`
- [ ] Implement `update_objection()`
- [ ] Implement `delete_objection()`
- [ ] Implement `search_objections(query)`
- [ ] Implement `detect_cue_words(text)` — scan text against cue_words_json, return matches

### API Endpoints — Pain Points
- [ ] `GET /api/intelligence/pain-points` — list (industry/search filter)
- [ ] `POST /api/intelligence/pain-points` — create
- [ ] `PUT /api/intelligence/pain-points/<id>` — update
- [ ] `DELETE /api/intelligence/pain-points/<id>` — delete

### API Endpoints — Objections
- [ ] `GET /api/intelligence/objections` — list (category/industry/search filter)
- [ ] `POST /api/intelligence/objections` — create
- [ ] `PUT /api/intelligence/objections/<id>` — update
- [ ] `DELETE /api/intelligence/objections/<id>` — delete

### API Endpoints — Detection
- [ ] `POST /api/intelligence/detect-cues` — scan text for cue word matches
- [ ] `POST /api/intelligence/classify-note` — LLM-classified response

### Frontend — Company Page
- [ ] Add collapsible "Intelligence" panel with industry-relevant pain points and discovery questions
- [ ] Add objection responses with search
- [ ] Add buzzword detection: debounce detect-cues API while typing touchpoint notes, show inline suggestions

### Frontend — Admin Section
- [ ] Build pain points CRUD table with inline edit
- [ ] Build objections CRUD table with inline edit

---

## Phase III, Module 1: Apollo.io Contact Enrichment Pipeline

### Database
- [ ] Create `apollo_enrichment_jobs` table in `init_db()`
- [ ] Add migration: `apollo_id` column to `company_contacts`
- [ ] Add migration: `apollo_enriched_at` column to `company_contacts`
- [ ] Add migration: `email_status` column to `company_contacts`

### DB Functions — `db/apollo.py`
- [ ] Create `db/apollo.py` file
- [ ] Implement `create_apollo_job(job_id, target_bp_ids, filters, created_by)`
- [ ] Implement `get_apollo_job(job_id)`
- [ ] Implement `update_apollo_job(job_id, fields)`
- [ ] Implement `get_active_apollo_job()`
- [ ] Implement `upsert_contact_from_apollo(bp_id, apollo_data)` — create/update contact with source="apollo"

### Apollo API Integration
- [ ] Implement `apollo_search_contacts(company_name, domain, title_filters, max_results)` using urllib.request
- [ ] Implement `llm_validate_apollo_match(account, apollo_org)` — GPT-4.1-nano company name validation

### Worker Thread
- [ ] Implement `run_apollo_enrichment_job(job_id)` — follows URL discovery job pattern
- [ ] Add lock-based job dict access
- [ ] Add cancel support via `cancel_requested` flag
- [ ] Add credit tracking per job

### API Endpoints
- [ ] `POST /api/apollo/enrich-job/start` — start bulk enrichment job
- [ ] `GET /api/apollo/enrich-job` — get job status
- [ ] `POST /api/apollo/enrich-job/<job_id>/cancel` — cancel running job
- [ ] `POST /api/companies/<bp_id>/apollo/enrich` — single-account enrichment
- [ ] `GET /api/apollo/credits` — credit usage info

### Environment Variables
- [ ] Add `APOLLO_API_KEY`, `APOLLO_ENRICHMENT_MODEL`, `APOLLO_MAX_CONTACTS_PER_COMPANY`, `APOLLO_TITLE_FILTERS` to `.env.example`

### Frontend — New Page
- [ ] Create `templates/apollo_pipeline.html` page shell
- [ ] Create `static/apollo_pipeline.js`
- [ ] Build summary section (total accounts, contacts found, credits used)
- [ ] Build job controls (start enrichment with filters: tier, status, max companies, title seniority)
- [ ] Build job progress polling (same pattern as URL discovery)
- [ ] Build queue table (Company | Existing Contacts | Apollo Contacts | Status | Actions)
- [ ] Add per-company "Enrich" button
- [ ] Add "Contact Enrichment" link to header nav

---

## Phase III, Module 2: AI Outreach Draft Generator

### Database
- [ ] Create `outreach_drafts` table in `init_db()`
- [ ] Create index `idx_outreach_drafts_bp` on `outreach_drafts(bp_id)`

### DB Functions — `db/outreach.py`
- [ ] Create `db/outreach.py` file
- [ ] Implement `create_outreach_draft(bp_id, channel, draft_type, body, created_by, **fields)`
- [ ] Implement `list_outreach_drafts(bp_id?, contact_id?, channel?)`
- [ ] Implement `get_outreach_draft(draft_id)`
- [ ] Implement `update_outreach_draft(draft_id, fields)`
- [ ] Implement `delete_outreach_draft(draft_id)`
- [ ] Implement `mark_draft_sent(draft_id)` — update status + auto-create touchpoint

### LLM Integration
- [ ] Implement `generate_outreach_draft(account, contact, channel, draft_type, language, tone)` with context building
- [ ] Add token/cost tracking per draft

### API Endpoints
- [ ] `POST /api/companies/<bp_id>/outreach/generate` — generate draft with LLM
- [ ] `GET /api/companies/<bp_id>/outreach/drafts` — list drafts for company
- [ ] `GET /api/outreach/drafts/<id>` — get single draft
- [ ] `PUT /api/outreach/drafts/<id>` — edit draft
- [ ] `DELETE /api/outreach/drafts/<id>` — delete draft
- [ ] `POST /api/outreach/drafts/<id>/mark-sent` — mark sent + create touchpoint
- [ ] `POST /api/outreach/bulk-generate` — bulk generate for multiple accounts

### Environment Variables
- [ ] Add `OPENAI_OUTREACH_MODEL`, `OPENAI_OUTREACH_MAX_TOKENS` to `.env.example`

### Frontend — Company Page
- [ ] Add "Generate Outreach" button in Contacts and Opportunities sections
- [ ] Build outreach modal: channel selector, draft type, contact dropdown, opportunity dropdown, language toggle, tone selector
- [ ] Build editable draft display with "Copy to Clipboard" and "Mark as Sent" actions
- [ ] Build draft history section with status badges

---

## Phase III, Module 3: Instantly.ai Email Campaign Engine

### Database
- [ ] Create `instantly_campaigns` table in `init_db()`
- [ ] Create `instantly_leads` table with indexes in `init_db()`

### DB Functions — `db/instantly.py`
- [ ] Create `db/instantly.py` file
- [ ] Implement `create_instantly_campaign(name, nurture_sequence_id, created_by)`
- [ ] Implement `get_instantly_campaign(campaign_id)`
- [ ] Implement `update_instantly_campaign(campaign_id, fields)`
- [ ] Implement `list_instantly_campaigns(status?)`
- [ ] Implement `add_leads_to_campaign(campaign_id, leads)` — bulk insert
- [ ] Implement `update_lead_status(lead_id, fields)`
- [ ] Implement `get_campaign_stats(campaign_id)`
- [ ] Implement `sync_campaign_from_instantly(campaign_id)` — pull stats from API

### Instantly API Integration
- [ ] Implement `instantly_api_call(method, path, payload)` using urllib.request

### Webhook Handler
- [ ] Implement `POST /api/instantly/webhook` — handle reply/bounce/open/unsubscribe events
- [ ] Add webhook secret verification
- [ ] Auto-create touchpoints on reply
- [ ] Auto-transition account status on reply (cold → engaged)

### API Endpoints
- [ ] `POST /api/instantly/campaigns` — create campaign
- [ ] `GET /api/instantly/campaigns` — list campaigns
- [ ] `GET /api/instantly/campaigns/<id>` — campaign with stats
- [ ] `POST /api/instantly/campaigns/<id>/push` — push to Instantly API
- [ ] `POST /api/instantly/campaigns/<id>/sync` — pull stats
- [ ] `POST /api/instantly/campaigns/<id>/pause` — pause campaign

### Environment Variables
- [ ] Add `INSTANTLY_API_KEY`, `INSTANTLY_WORKSPACE_ID`, `INSTANTLY_WEBHOOK_SECRET` to `.env.example`

### Frontend — New Page
- [ ] Create `templates/campaigns.html` page shell
- [ ] Create `static/campaigns.js`
- [ ] Build campaign list with status badges and stats
- [ ] Build create campaign form (name, nurture sequence, account selection, email filter)
- [ ] Build campaign detail view with leads table and per-lead status
- [ ] Add "Push to Instantly" and "Sync Stats" buttons
- [ ] Add "Email Campaigns" link to header nav

---

## Phase III, Module 4: Dripify LinkedIn Automation

### Database
- [ ] Create `dripify_campaigns` table in `init_db()`
- [ ] Create `dripify_leads` table with indexes in `init_db()`

### DB Functions — `db/dripify.py`
- [ ] Create `db/dripify.py` file
- [ ] Implement campaign CRUD: `create_dripify_campaign()`, `get_dripify_campaign()`, `update_dripify_campaign()`, `list_dripify_campaigns()`
- [ ] Implement lead management: `add_dripify_leads()`, `update_dripify_lead()`, `get_campaign_leads()`
- [ ] Implement `sync_dripify_campaign(campaign_id)` — pull stats from API
- [ ] Implement `get_dripify_campaign_stats(campaign_id)`

### Dripify API Integration
- [ ] Implement Dripify API caller using urllib.request

### Webhook Handler
- [ ] Implement `POST /api/dripify/webhook` — handle connection_accepted/reply events
- [ ] Add webhook secret verification
- [ ] Auto-create touchpoints with type="linkedin"
- [ ] Auto-transition account status on engagement

### API Endpoints
- [ ] `POST /api/dripify/campaigns` — create campaign
- [ ] `GET /api/dripify/campaigns` — list campaigns
- [ ] `GET /api/dripify/campaigns/<id>` — campaign with stats
- [ ] `POST /api/dripify/campaigns/<id>/push` — push leads to Dripify
- [ ] `POST /api/dripify/campaigns/<id>/sync` — pull stats

### Environment Variables
- [ ] Add `DRIPIFY_API_KEY`, `DRIPIFY_WEBHOOK_SECRET` to `.env.example`

### Frontend
- [ ] Add LinkedIn Campaigns tab/section to campaigns.html
- [ ] Build campaign list, create form, push/sync buttons, lead status table (same patterns as Instantly)

---

## Phase III, Module 5: Make.com Orchestration Layer

### Database
- [ ] Create `webhook_events` table with indexes in `init_db()`
- [ ] Create `webhook_config` table in `init_db()`

### DB Functions — `db/webhooks.py`
- [ ] Create `db/webhooks.py` file
- [ ] Implement `get_webhook_config(event_type)`
- [ ] Implement `upsert_webhook_config(event_type, target_url, secret?, is_active?)`
- [ ] Implement `list_webhook_configs()`
- [ ] Implement `log_webhook_event(event_type, payload, direction, target, status, response_code?, error?)`
- [ ] Implement `list_webhook_events(event_type?, direction?, limit?)`

### Outbound Webhook Dispatcher
- [ ] Implement `fire_webhook(event_type, payload)` — async daemon thread dispatch with HMAC signing
- [ ] Add `fire_webhook()` call after opportunity status → qualified
- [ ] Add `fire_webhook()` call after handover creation
- [ ] Add `fire_webhook()` call after account status transition
- [ ] Add `fire_webhook()` call after new contact created
- [ ] Add `fire_webhook()` call after nurture enrollment
- [ ] Add `fire_webhook()` call after campaign reply received

### Inbound Webhook Receiver
- [ ] Implement `POST /api/webhooks/make` — receive Make.com events with HMAC verification
- [ ] Implement handler routing: `apollo.contact_found`, `instantly.reply`, `dripify.connection_accepted`, `attio.deal_updated`

### API Endpoints
- [ ] `GET /api/webhooks/config` — list webhook configs
- [ ] `PUT /api/webhooks/config/<event_type>` — update webhook config
- [ ] `GET /api/webhooks/events` — recent events log

### Environment Variables
- [ ] Add `MAKE_WEBHOOK_SECRET`, `MAKE_DEFAULT_WEBHOOK_URL` to `.env.example`

### Frontend — Admin Integrations
- [ ] Build webhook configuration table (event type, target URL, active toggle)
- [ ] Build recent webhook events log
- [ ] Add test button per webhook config

---

## Phase III, Module 6: Attio CRM Sync

### Database
- [ ] Create `attio_sync_map` table with indexes in `init_db()`

### DB Functions — `db/attio.py`
- [ ] Create `db/attio.py` file
- [ ] Implement `get_attio_sync(plt_entity_type, plt_entity_id)`
- [ ] Implement `upsert_attio_sync(plt_entity_type, plt_entity_id, attio_object_type, attio_record_id)`
- [ ] Implement `list_unsynced_entities(entity_type?)`
- [ ] Implement `mark_sync_error(plt_entity_type, plt_entity_id, error)`

### Attio API Integration
- [ ] Implement `attio_api_call(method, path, payload)` using urllib.request
- [ ] Implement `sync_account_to_attio(bp_id)` — push account as Company record
- [ ] Implement `sync_opportunity_to_attio(opportunity_id)` — push opp as Deal record

### API Endpoints
- [ ] `POST /api/attio/sync/accounts` — sync accounts (optional bp_ids filter)
- [ ] `POST /api/attio/sync/opportunities` — sync opportunities
- [ ] `POST /api/attio/sync/full` — full sync job
- [ ] `GET /api/attio/sync/status` — sync status summary
- [ ] `POST /api/attio/sync/company/<bp_id>` — sync single account

### Webhook-Driven Auto-Sync
- [ ] Wire `opportunity.qualified` webhook to trigger `sync_opportunity_to_attio`
- [ ] Wire `account.status_changed` webhook to trigger account sync

### Environment Variables
- [ ] Add `ATTIO_API_KEY`, `ATTIO_WORKSPACE_ID` to `.env.example`

### Frontend
- [ ] Add Attio sync status card in admin Integrations section
- [ ] Add manual "Full Sync" button
- [ ] Add per-account "Sync to Attio" button on company page

---

## Phase III, Module 7: LinkedIn Sales Navigator Enrichment

### Database
- [ ] Add migration: `sales_nav_url` column to `company_contacts`
- [ ] Add migration: `sales_nav_imported_at` column to `company_contacts`

### CSV Import
- [ ] Implement `POST /api/sales-navigator/import` — parse SN CSV, fuzzy match companies, create contacts with source="sales_navigator"
- [ ] Implement `GET /api/sales-navigator/import-history` — list past imports

### Frontend
- [ ] Add "Import from Sales Navigator" button on Apollo pipeline page
- [ ] Build file upload form with drag-and-drop
- [ ] Build preview table showing matched/unmatched companies before confirming import
