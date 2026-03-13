# PLT Dashboard: Phase III — External Execution & Integration Stack

## Context

Phase II builds the internal operating console: opportunities, handovers, KPIs, seller alignment, nurture engine, and intelligence. Phase III connects that console to external execution tools — actually discovering contacts at scale, sending personalized outreach, automating LinkedIn, and syncing data across systems.

**Assumption:** Phase II is complete. The following objects exist: opportunities, handovers, account_status, nurture_sequences, engagement_triggers, content_library, seller_feedback, pain_point_dictionary, objection_library, sap_assist_requests. All Phase 0 blueprint refactoring is done.

**Architecture principle:** PLT Dashboard remains the single source of truth. External tools are execution endpoints. Data flows out via API pushes and Make.com webhooks, and flows back in as touchpoints, contacts, and status updates.

---

## Tech Stack Summary

| Tool | Purpose | Cost | API Docs |
|---|---|---|---|
| OpenAI (GPT-4.1-nano) | Outreach draft generation, enrichment | ~$5–15/mo | Already integrated |
| Apollo.io (Basic) | Contact discovery: emails, phones, 1M credits/yr | $49/mo | REST API v1 |
| LinkedIn Sales Navigator (Core) | Advanced search, lead lists, 50 InMails/mo | $90/mo | No public API — manual + Dripify |
| Dripify (Pro) | LinkedIn automation: connections, drip sequences | $59/mo | REST API + webhooks |
| Instantly.ai (Growth) | 5K emails/mo, warmup, campaign sequences | $38/mo | REST API v1 |
| Make.com (Core) | Integration glue: webhooks, scenarios, 10K ops/mo | $11/mo | Webhook triggers + HTTP modules |
| Attio CRM (Free) | External CRM for sales team, 50K records, API access | $0 | REST API v2 |
| Resend (Free) | Transactional emails (already integrated for auth) | $0 | Already integrated |

**Total monthly cost:** ~$247–257 USD

---

## Module 1: Apollo.io Contact Enrichment Pipeline

**Why:** Replaces manual contact entry for 2,100 accounts. Apollo provides verified emails, phone numbers, job titles, and LinkedIn URLs at scale. Directly populates `company_contacts` table.

**Complexity: L | Dependencies: Phase II CRM (company_contacts exists)**

### Database Changes

```sql
CREATE TABLE IF NOT EXISTS apollo_enrichment_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'idle',
    total_accounts INTEGER DEFAULT 0,
    processed INTEGER DEFAULT 0,
    contacts_found INTEGER DEFAULT 0,
    contacts_created INTEGER DEFAULT 0,
    credits_used INTEGER DEFAULT 0,
    current_bp_id INTEGER,
    current_company TEXT DEFAULT '',
    target_bp_ids_json TEXT DEFAULT '[]',
    filters_json TEXT DEFAULT '{}',
    last_error TEXT DEFAULT '',
    cancel_requested INTEGER DEFAULT 0,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Track Apollo-specific metadata per contact
ALTER TABLE company_contacts ADD COLUMN apollo_id TEXT DEFAULT '';
ALTER TABLE company_contacts ADD COLUMN apollo_enriched_at TIMESTAMP;
ALTER TABLE company_contacts ADD COLUMN email_status TEXT DEFAULT '';
```

### Environment Variables

```env
APOLLO_API_KEY=                          # Apollo.io API key
APOLLO_ENRICHMENT_MODEL=gpt-4.1-nano    # For matching company names
APOLLO_MAX_CONTACTS_PER_COMPANY=5       # Limit contacts per account
APOLLO_TITLE_FILTERS=CEO,CFO,CIO,CTO,VP,Director,Head  # Default title seniority filter
```

### Apollo API Integration Pattern

Follow the existing Brave Search HTTP pattern (urllib.request, no external library):

```python
APOLLO_API_ENDPOINT = "https://api.apollo.io/api/v1"
APOLLO_SEARCH_PEOPLE = f"{APOLLO_API_ENDPOINT}/mixed_people/search"
APOLLO_ENRICH_PERSON = f"{APOLLO_API_ENDPOINT}/people/match"
APOLLO_ORG_SEARCH = f"{APOLLO_API_ENDPOINT}/mixed_companies/search"

def apollo_search_contacts(company_name, domain=None, title_filters=None, max_results=5):
    """Search Apollo for contacts at a company."""
    api_key = os.environ.get("APOLLO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("APOLLO_API_KEY is required for contact enrichment.")

    payload = {
        "api_key": api_key,
        "q_organization_name": company_name,
        "per_page": max_results,
        "person_titles": title_filters or APOLLO_DEFAULT_TITLE_FILTERS,
    }
    if domain:
        payload["q_organization_domains"] = domain

    data = json.dumps(payload).encode("utf-8")
    req = Request(
        APOLLO_SEARCH_PEOPLE,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # ... same try/except pattern as Brave Search
```

### New DB Functions → `db/apollo.py`

- `create_apollo_job(job_id, target_bp_ids, filters, created_by)` — create enrichment job
- `get_apollo_job(job_id)` — get job status
- `update_apollo_job(job_id, fields)` — update progress
- `get_active_apollo_job()` — check for running job
- `upsert_contact_from_apollo(bp_id, apollo_data)` — create or update contact with Apollo data, set source="apollo", set apollo_id and apollo_enriched_at

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/apollo/enrich-job/start` | `{tier?, status?, max_companies?, title_filters?}` | 202 with job |
| `GET` | `/api/apollo/enrich-job` | `?job_id=` | job status |
| `POST` | `/api/apollo/enrich-job/<job_id>/cancel` | — | 200 |
| `POST` | `/api/companies/<bp_id>/apollo/enrich` | `{title_filters?}` | contacts found |
| `GET` | `/api/apollo/credits` | — | `{credits_used, credits_remaining}` |

### Worker Thread → `run_apollo_enrichment_job(job_id)`

Follows the exact pattern of `run_url_discovery_job`:
1. Lock-based access to job dict
2. Iterate target accounts
3. For each account: search Apollo by company_name + domain (from accepted URL)
4. Match results against existing contacts (by email or name) to avoid duplicates
5. Create new contacts with source="apollo"
6. Track credits_used, contacts_found, contacts_created
7. Check cancel_requested each iteration
8. Update job progress in-memory

### Frontend Changes

**URL Pipeline page pattern reused.** New page: `templates/apollo_pipeline.html` + `static/apollo_pipeline.js`

- Summary: Total accounts, contacts found, credits used this month
- Controls: Start enrichment job (filters: tier, status, max companies, title seniority)
- Job progress: same polling pattern as URL discovery
- Queue table: Company | Existing Contacts | Apollo Contacts Found | Status | Actions
- Per-company "Enrich" button for manual single-account enrichment

Add "Contact Enrichment" link to header nav.

### Company Name Matching

Apollo search by company name is fuzzy. Use a lightweight LLM call (same GPT-4.1-nano pattern) to validate matches:

```python
def llm_validate_apollo_match(account, apollo_org):
    """Validate that an Apollo organization matches our account."""
    payload = {
        "our_company": {
            "name": account.get("company_name"),
            "industry": account.get("industry"),
            "city": account.get("city"),
            "region": account.get("region"),
        },
        "apollo_company": {
            "name": apollo_org.get("name"),
            "industry": apollo_org.get("industry"),
            "city": apollo_org.get("city"),
            "website": apollo_org.get("website_url"),
        },
    }
    system_msg = (
        "You are matching two company records. Return JSON with keys: "
        "match (true/false), confidence (0-100), reason."
    )
    # ... standard OpenAI call pattern
```

---

## Module 2: AI Outreach Draft Generator

**Why:** Given account context (industry, SAP status, size, compelling event, pain points), generate personalized email and LinkedIn message drafts. Replaces generic templates with contextual outreach.

**Complexity: M | Dependencies: Phase II opportunities + intelligence (pain_point_dictionary)**

### Database Changes

```sql
CREATE TABLE IF NOT EXISTS outreach_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bp_id INTEGER NOT NULL,
    opportunity_id INTEGER,
    contact_id INTEGER,
    channel TEXT NOT NULL,
    draft_type TEXT NOT NULL,
    subject TEXT DEFAULT '',
    body TEXT NOT NULL,
    tone TEXT DEFAULT 'professional',
    language TEXT DEFAULT 'es',
    model TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    status TEXT DEFAULT 'draft',
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE SET NULL,
    FOREIGN KEY(contact_id) REFERENCES company_contacts(id) ON DELETE SET NULL,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_drafts_bp ON outreach_drafts(bp_id);
```

Valid `channel`: `email`, `linkedin_connection`, `linkedin_inmail`, `whatsapp`
Valid `draft_type`: `cold_intro`, `follow_up`, `re_engagement`, `event_based`, `referral`, `nurture_step`
Valid `tone`: `professional`, `casual`, `executive`, `consultative`
Valid `language`: `es`, `en`
Valid `status`: `draft`, `sent`, `used`

### Environment Variables

```env
OPENAI_OUTREACH_MODEL=gpt-4.1-nano     # Model for draft generation
OPENAI_OUTREACH_MAX_TOKENS=500         # Max output tokens per draft
```

### New DB Functions → `db/outreach.py`

- `create_outreach_draft(bp_id, channel, draft_type, body, created_by, **fields)`
- `list_outreach_drafts(bp_id?, contact_id?, channel?)`
- `get_outreach_draft(draft_id)`
- `update_outreach_draft(draft_id, fields)` — for editing before sending
- `delete_outreach_draft(draft_id)`
- `mark_draft_sent(draft_id)` — update status to sent, create touchpoint automatically

### LLM Draft Generation

```python
def generate_outreach_draft(account, contact, channel, draft_type, language="es", tone="professional"):
    """Generate personalized outreach using account context."""
    # Build rich context from Phase II data
    context = {
        "company": {
            "name": account.get("company_name"),
            "industry": account.get("industry"),
            "sap_status": account.get("sap_status"),
            "employee_count": account.get("employee_count"),
            "compelling_event": account.get("compelling_event"),
            "whitespace_potential": account.get("whitespace_potential"),
        },
        "contact": {
            "name": contact.get("full_name") if contact else None,
            "title": contact.get("job_title") if contact else None,
        },
        "opportunity": None,  # filled if opportunity_id provided
        "pain_points": [],    # from pain_point_dictionary for this industry
        "channel": channel,
        "draft_type": draft_type,
        "language": language,
        "tone": tone,
    }

    system_msg = f"""You are an expert SAP partner sales development representative writing {channel} outreach.
Language: {"Spanish" if language == "es" else "English"}.
Tone: {tone}.
Type: {draft_type}.
Return JSON with keys: subject (for email, empty for LinkedIn), body, call_to_action, personalization_notes.
Keep {channel} messages under {"150 words" if channel.startswith("linkedin") else "250 words"}.
Focus on the business problem, not the product. Reference the compelling event if available.
Never use generic phrases like "I noticed your company" without specific context."""

    # ... standard OpenAI call pattern with cost tracking
```

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/companies/<bp_id>/outreach/generate` | `{contact_id?, channel, draft_type, language?, tone?, opportunity_id?}` | `{draft}` with 201 |
| `GET` | `/api/companies/<bp_id>/outreach/drafts` | `?channel=&status=` | list of drafts |
| `GET` | `/api/outreach/drafts/<id>` | — | full draft |
| `PUT` | `/api/outreach/drafts/<id>` | `{body?, subject?, status?}` | updated |
| `DELETE` | `/api/outreach/drafts/<id>` | — | 204 |
| `POST` | `/api/outreach/drafts/<id>/mark-sent` | `{touchpoint_type?}` | 200 + creates touchpoint |
| `POST` | `/api/outreach/bulk-generate` | `{bp_ids, channel, draft_type, language?}` | 202 job |

### Frontend Changes

**`company.html` / `company.js`**: Add "Generate Outreach" button in the Contacts section and Opportunities section. Opens modal with:
- Channel selector (Email / LinkedIn Connection / LinkedIn InMail / WhatsApp)
- Draft type selector
- Contact dropdown (pre-selected if clicked from a contact row)
- Opportunity dropdown (optional)
- Language toggle (ES/EN)
- Tone selector
- "Generate" button → shows editable draft
- "Copy to Clipboard" / "Mark as Sent" actions

Draft history section showing previous drafts per company with status badges.

---

## Module 3: Instantly.ai Email Campaign Engine

**Why:** Pushes nurture sequences (Phase 7) out as real email campaigns with warmup, deliverability tracking, and reply detection.

**Complexity: L | Dependencies: Phase II nurture_sequences, company_contacts with emails**

### Database Changes

```sql
CREATE TABLE IF NOT EXISTS instantly_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT UNIQUE,
    name TEXT NOT NULL,
    nurture_sequence_id INTEGER,
    status TEXT DEFAULT 'draft',
    leads_count INTEGER DEFAULT 0,
    emails_sent INTEGER DEFAULT 0,
    opens INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    bounces INTEGER DEFAULT 0,
    sync_status TEXT DEFAULT 'pending',
    last_synced_at TIMESTAMP,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(nurture_sequence_id) REFERENCES nurture_sequences(id) ON DELETE SET NULL,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS instantly_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    bp_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    instantly_lead_id TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    emails_sent INTEGER DEFAULT 0,
    last_email_status TEXT DEFAULT '',
    replied INTEGER DEFAULT 0,
    replied_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(contact_id) REFERENCES company_contacts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_instantly_leads_campaign ON instantly_leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_instantly_leads_bp ON instantly_leads(bp_id);
```

Valid campaign status: `draft`, `active`, `paused`, `completed`
Valid lead status: `active`, `paused`, `completed`, `bounced`, `replied`, `unsubscribed`

### Environment Variables

```env
INSTANTLY_API_KEY=                       # Instantly.ai API key
INSTANTLY_WORKSPACE_ID=                  # Workspace identifier
INSTANTLY_WEBHOOK_SECRET=                # For incoming webhook verification
```

### Instantly API Integration

```python
INSTANTLY_API_ENDPOINT = "https://api.instantly.ai/api/v1"

def instantly_api_call(method, path, payload=None):
    """Generic Instantly API caller following Brave pattern."""
    api_key = os.environ.get("INSTANTLY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("INSTANTLY_API_KEY is required.")

    url = f"{INSTANTLY_API_ENDPOINT}{path}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    # ... urllib.request pattern
```

### New DB Functions → `db/instantly.py`

- `create_instantly_campaign(name, nurture_sequence_id, created_by)`
- `get_instantly_campaign(campaign_id)`
- `update_instantly_campaign(campaign_id, fields)`
- `list_instantly_campaigns(status?)`
- `add_leads_to_campaign(campaign_id, leads)` — bulk insert leads
- `update_lead_status(lead_id, fields)` — from webhook callbacks
- `get_campaign_stats(campaign_id)` — aggregate stats
- `sync_campaign_from_instantly(campaign_id)` — pull latest stats from API

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/instantly/campaigns` | `{name, nurture_sequence_id?, bp_ids?, contact_filter?}` | 201 |
| `GET` | `/api/instantly/campaigns` | `?status=` | list |
| `GET` | `/api/instantly/campaigns/<id>` | — | campaign with stats |
| `POST` | `/api/instantly/campaigns/<id>/push` | — | push to Instantly API |
| `POST` | `/api/instantly/campaigns/<id>/sync` | — | pull stats from Instantly |
| `POST` | `/api/instantly/campaigns/<id>/pause` | — | pause campaign |
| `POST` | `/api/instantly/webhook` | (Instantly callback) | 200 |

### Webhook Handler

```python
@app.route("/api/instantly/webhook", methods=["POST"])
def instantly_webhook():
    """Handle Instantly.ai webhook callbacks for replies, bounces, etc."""
    # Verify webhook secret
    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != os.environ.get("INSTANTLY_WEBHOOK_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json()
    event_type = data.get("event_type")  # reply, bounce, open, click, unsubscribe

    if event_type == "reply":
        # Find lead, update status, create touchpoint in PLT
        lead = get_lead_by_email(data.get("email"))
        if lead:
            update_lead_status(lead["id"], {"status": "replied", "replied": 1})
            # Auto-create touchpoint
            create_company_touchpoint(
                bp_id=lead["bp_id"],
                touchpoint_date=utc_today_iso(),
                touchpoint_type="email",
                contact_id=lead["contact_id"],
                summary=f"Reply received via Instantly campaign",
                outcome="replied",
            )
            # Auto-transition account status if cold → engaged
            update_account_status(lead["bp_id"], "engaged", reason="Reply to email campaign")

    return jsonify({"status": "ok"}), 200
```

### Frontend Changes

New page: `templates/campaigns.html` + `static/campaigns.js`

- Campaign list with status badges and stats (sent, opens, replies, bounces)
- Create campaign form: name, select nurture sequence or manual account selection, email filter
- Campaign detail: leads table with per-lead status, reply content preview
- "Push to Instantly" button to create campaign via API
- "Sync Stats" button to pull latest metrics
- Auto-creates touchpoints when replies detected

Add "Email Campaigns" link to header nav.

---

## Module 4: Dripify LinkedIn Automation

**Why:** Automates LinkedIn connection requests and drip sequences. Syncs activity back as touchpoints.

**Complexity: L | Dependencies: Phase II nurture_sequences, company_contacts with linkedin_url**

### Database Changes

```sql
CREATE TABLE IF NOT EXISTS dripify_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dripify_campaign_id TEXT UNIQUE,
    name TEXT NOT NULL,
    nurture_sequence_id INTEGER,
    status TEXT DEFAULT 'draft',
    leads_count INTEGER DEFAULT 0,
    connections_sent INTEGER DEFAULT 0,
    connections_accepted INTEGER DEFAULT 0,
    messages_sent INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    sync_status TEXT DEFAULT 'pending',
    last_synced_at TIMESTAMP,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(nurture_sequence_id) REFERENCES nurture_sequences(id) ON DELETE SET NULL,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS dripify_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    bp_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    linkedin_url TEXT NOT NULL,
    dripify_lead_id TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    connection_status TEXT DEFAULT 'not_sent',
    messages_sent INTEGER DEFAULT 0,
    replied INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(contact_id) REFERENCES company_contacts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dripify_leads_campaign ON dripify_leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_dripify_leads_bp ON dripify_leads(bp_id);
```

### Environment Variables

```env
DRIPIFY_API_KEY=                         # Dripify API key
DRIPIFY_WEBHOOK_SECRET=                  # For incoming webhook verification
```

### New DB Functions → `db/dripify.py`

- Campaign CRUD: `create_dripify_campaign()`, `get_dripify_campaign()`, `update_dripify_campaign()`, `list_dripify_campaigns()`
- Lead management: `add_dripify_leads()`, `update_dripify_lead()`, `get_campaign_leads()`
- Sync: `sync_dripify_campaign(campaign_id)` — pull stats from Dripify API
- Stats: `get_dripify_campaign_stats(campaign_id)`

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/dripify/campaigns` | `{name, nurture_sequence_id?, bp_ids?}` | 201 |
| `GET` | `/api/dripify/campaigns` | `?status=` | list |
| `GET` | `/api/dripify/campaigns/<id>` | — | campaign with stats |
| `POST` | `/api/dripify/campaigns/<id>/push` | — | push leads to Dripify |
| `POST` | `/api/dripify/campaigns/<id>/sync` | — | pull stats |
| `POST` | `/api/dripify/webhook` | (Dripify callback) | 200 |

### Webhook Handler

Same pattern as Instantly: on connection_accepted or reply events, auto-create touchpoints with type="linkedin", auto-transition account status.

### Frontend Changes

Integrated into `campaigns.html` as a second tab (Email Campaigns | LinkedIn Campaigns), or a separate section on the same page. Same patterns as Instantly: campaign list, create form, push/sync buttons, lead status table.

---

## Module 5: Make.com Orchestration Layer

**Why:** The integration glue. Instead of building point-to-point integrations for every tool, PLT fires webhooks to Make.com which routes events to the right destination.

**Complexity: M | Dependencies: Modules 1-4 benefit but not strictly required**

### Architecture

```
PLT Dashboard ──webhook──▶ Make.com ──▶ Apollo.io
                                    ──▶ Instantly.ai
                                    ──▶ Dripify
                                    ──▶ Attio CRM
                                    ──▶ Slack (notifications)

External Tools ──webhook──▶ Make.com ──▶ PLT Dashboard /api/webhooks/make
```

### Database Changes

```sql
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    direction TEXT NOT NULL,
    target TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    response_code INTEGER,
    error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_type ON webhook_events(event_type);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(status);

CREATE TABLE IF NOT EXISTS webhook_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT UNIQUE NOT NULL,
    target_url TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    secret TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Valid `direction`: `outbound` (PLT → Make), `inbound` (Make → PLT)
Valid event_type (outbound):
- `opportunity.qualified` — when opp reaches qualified status
- `opportunity.handed_over` — when handover is created
- `account.status_changed` — on any status transition
- `contact.created` — new contact added (for Attio sync)
- `nurture.enrolled` — account enrolled in nurture sequence
- `campaign.reply_received` — reply detected from any campaign

### Environment Variables

```env
MAKE_WEBHOOK_SECRET=                     # Shared secret for Make → PLT
MAKE_DEFAULT_WEBHOOK_URL=               # Default Make.com webhook URL for outbound events
```

### New DB Functions → `db/webhooks.py`

- `get_webhook_config(event_type)` — get target URL for event
- `upsert_webhook_config(event_type, target_url, secret?, is_active?)`
- `list_webhook_configs()`
- `log_webhook_event(event_type, payload, direction, target, status, response_code?, error?)`
- `list_webhook_events(event_type?, direction?, limit?)` — for debugging

### Outbound Webhook Dispatcher

```python
def fire_webhook(event_type, payload):
    """Fire outbound webhook to Make.com (or any configured target)."""
    config = get_webhook_config(event_type)
    if not config or not config.get("is_active"):
        return  # silently skip if not configured

    target_url = config["target_url"]
    secret = config.get("secret", "")

    body = json.dumps({
        "event": event_type,
        "timestamp": utc_now_iso(),
        "payload": payload,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-PLT-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else "",
    }

    # Fire async (daemon thread) to not block the request
    def _send():
        try:
            req = Request(target_url, data=body, headers=headers, method="POST")
            with urlopen(req, timeout=10) as resp:
                log_webhook_event(event_type, payload, "outbound", target_url, "delivered", resp.status)
        except Exception as exc:
            log_webhook_event(event_type, payload, "outbound", target_url, "failed", error=str(exc)[:500])

    threading.Thread(target=_send, daemon=True).start()
```

### Inbound Webhook Receiver

```python
@app.route("/api/webhooks/make", methods=["POST"])
def make_webhook_receiver():
    """Receive events from Make.com."""
    secret = os.environ.get("MAKE_WEBHOOK_SECRET", "")
    if secret:
        sig = request.headers.get("X-Make-Signature", "")
        expected = hmac.new(secret.encode(), request.data, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_json()
    event_type = data.get("event")
    payload = data.get("payload", {})

    log_webhook_event(event_type, payload, "inbound", "make.com", "received")

    # Route to appropriate handler
    handlers = {
        "apollo.contact_found": handle_apollo_contact_from_make,
        "instantly.reply": handle_instantly_reply_from_make,
        "dripify.connection_accepted": handle_dripify_connection_from_make,
        "attio.deal_updated": handle_attio_update_from_make,
    }
    handler = handlers.get(event_type)
    if handler:
        handler(payload)

    return jsonify({"status": "ok"}), 200
```

### Integration Points in Existing Code

Add `fire_webhook()` calls at key moments in Phase II code:

```python
# In routes/crm.py — after opportunity status change
if new_status == "qualified":
    fire_webhook("opportunity.qualified", {"bp_id": bp_id, "opportunity_id": opp_id, ...})

# In routes/crm.py — after handover creation
fire_webhook("opportunity.handed_over", {"bp_id": bp_id, "handover_id": handover_id, ...})

# In routes/accounts.py — after status transition
fire_webhook("account.status_changed", {"bp_id": bp_id, "from": old_status, "to": new_status})
```

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/webhooks/make` | (Make.com payload) | 200 |
| `GET` | `/api/webhooks/config` | — | list of webhook configs |
| `PUT` | `/api/webhooks/config/<event_type>` | `{target_url, is_active?, secret?}` | updated |
| `GET` | `/api/webhooks/events` | `?event_type=&direction=&limit=50` | recent events |

### Frontend Changes

Admin section: "Integrations" tab showing:
- Webhook configuration table (event type, target URL, active toggle)
- Recent webhook events log (for debugging)
- Test button per webhook config

---

## Module 6: Attio CRM Sync

**Why:** Sales/management uses Attio as their CRM. Qualified opportunities and handovers should appear there automatically without requiring sellers to use PLT directly.

**Complexity: M | Dependencies: Phase II opportunities + handovers, Module 5 webhooks**

### Database Changes

```sql
CREATE TABLE IF NOT EXISTS attio_sync_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plt_entity_type TEXT NOT NULL,
    plt_entity_id INTEGER NOT NULL,
    attio_object_type TEXT NOT NULL,
    attio_record_id TEXT NOT NULL,
    sync_status TEXT DEFAULT 'synced',
    last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plt_entity_type, plt_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_attio_sync_entity ON attio_sync_map(plt_entity_type, plt_entity_id);
```

Valid `plt_entity_type`: `account`, `contact`, `opportunity`, `handover`
Valid `attio_object_type`: `companies`, `people`, `deals`

### Environment Variables

```env
ATTIO_API_KEY=                           # Attio API key
ATTIO_WORKSPACE_ID=                      # Attio workspace ID
```

### Attio API Integration

```python
ATTIO_API_ENDPOINT = "https://api.attio.com/v2"

def attio_api_call(method, path, payload=None):
    api_key = os.environ.get("ATTIO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ATTIO_API_KEY is required.")

    url = f"{ATTIO_API_ENDPOINT}{path}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    # ... urllib.request pattern
```

### New DB Functions → `db/attio.py`

- `get_attio_sync(plt_entity_type, plt_entity_id)` — get sync mapping
- `upsert_attio_sync(plt_entity_type, plt_entity_id, attio_object_type, attio_record_id)`
- `list_unsynced_entities(entity_type?)` — entities created/updated since last sync
- `mark_sync_error(plt_entity_type, plt_entity_id, error)`

### Sync Functions

```python
def sync_account_to_attio(bp_id):
    """Push account data to Attio as a Company record."""
    account = ACCOUNTS.get(bp_id)
    enrichment = get_enrichment(bp_id)
    existing_sync = get_attio_sync("account", bp_id)

    attio_data = {
        "data": {
            "values": {
                "name": [{"value": account["company_name"]}],
                "industry": [{"value": account.get("industry", "")}],
                "employee_count": [{"value": account.get("employee_count")}],
                "sap_status": [{"value": account.get("sap_status", "")}],
                "plt_score": [{"value": account.get("composite_score", 0)}],
                "plt_tier": [{"value": account.get("tier", "")}],
                "plt_status": [{"value": enrichment.get("account_status", "cold")}],
            }
        }
    }

    if existing_sync:
        # Update existing record
        attio_api_call("PATCH", f"/objects/companies/records/{existing_sync['attio_record_id']}", attio_data)
    else:
        # Create new record
        result = attio_api_call("POST", "/objects/companies/records", attio_data)
        upsert_attio_sync("account", bp_id, "companies", result["id"]["record_id"])

def sync_opportunity_to_attio(opportunity_id):
    """Push opportunity to Attio as a Deal record."""
    opp = get_opportunity(opportunity_id)
    # Map PLT opportunity fields to Attio deal fields
    # Link to company record via attio_sync_map
    ...
```

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/attio/sync/accounts` | `{bp_ids?}` | `{synced: N, errors: N}` |
| `POST` | `/api/attio/sync/opportunities` | `{opportunity_ids?}` | `{synced: N, errors: N}` |
| `POST` | `/api/attio/sync/full` | — | full sync job (202) |
| `GET` | `/api/attio/sync/status` | — | `{last_sync, accounts_synced, opps_synced, errors}` |
| `POST` | `/api/attio/sync/company/<bp_id>` | — | sync single account |

### Webhook-Driven Auto-Sync

Via Module 5 webhooks: when `opportunity.qualified` fires, Make.com scenario triggers `sync_opportunity_to_attio`. When `account.status_changed` fires, sync updated status to Attio.

### Frontend Changes

Admin "Integrations" section: Attio sync status card showing last sync time, records synced, errors. Manual "Full Sync" button. Per-account "Sync to Attio" button on company page.

---

## Module 7: LinkedIn Sales Navigator Enrichment

**Why:** Pull lead lists from SN into PLT target lists. Sync InMail activity. Enhance account data with SN insights.

**Complexity: S | Dependencies: Dripify (Module 4) for automation, company_contacts**

### Design Note

LinkedIn Sales Navigator has **no public API**. Integration works through:
1. **Dripify** — which connects to SN for automation
2. **Manual import** — CSV export from SN into PLT
3. **Browser extension** (future) — Chrome extension to push SN leads into PLT

### Database Changes

```sql
ALTER TABLE company_contacts ADD COLUMN sales_nav_url TEXT DEFAULT '';
ALTER TABLE company_contacts ADD COLUMN sales_nav_imported_at TIMESTAMP;
```

### CSV Import Endpoint

```python
@app.route("/api/sales-navigator/import", methods=["POST"])
@login_required
def import_sales_nav_csv():
    """Import a LinkedIn Sales Navigator CSV export into company_contacts."""
    file = request.files.get("file")
    # Parse CSV: First Name, Last Name, Title, Company, LinkedIn URL, ...
    # Match company to accounts by name fuzzy matching
    # Create contacts with source="sales_navigator"
    ...
```

### API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/sales-navigator/import` | multipart CSV file | `{imported: N, matched: N, unmatched: N}` |
| `GET` | `/api/sales-navigator/import-history` | — | list of past imports |

### Frontend Changes

"Import from Sales Navigator" button on the Apollo pipeline page or a dedicated import section. File upload form with drag-and-drop. Preview table showing matched/unmatched companies before confirming import.

---

## Summary

| Module | Tool | New Tables | New Endpoints | Complexity | Depends On |
|---|---|---|---|---|---|
| 1 | Apollo.io | 1 (+3 cols) | 5 | L | Phase II CRM |
| 2 | AI Outreach Generator | 1 | 7 | M | Phase II opps + intelligence |
| 3 | Instantly.ai | 2 | 6 + webhook | L | Phase II nurture |
| 4 | Dripify | 2 | 6 + webhook | L | Phase II nurture |
| 5 | Make.com | 2 | 4 | M | Modules 1-4 benefit |
| 6 | Attio CRM | 1 | 5 | M | Phase II opps + handovers |
| 7 | LinkedIn SN | 0 (+2 cols) | 2 | S | Module 4 (Dripify) |

**Totals:** 9 new tables, ~35 new API endpoints, 2 new pages, 2 webhook receivers

### Recommended Build Order

1. **Module 5: Make.com Orchestration** — build the webhook infrastructure first, everything else plugs into it
2. **Module 1: Apollo.io** — contact discovery is the highest-value integration, feeds all outreach
3. **Module 2: AI Outreach Generator** — needs contacts from Apollo to generate personalized drafts
4. **Module 3: Instantly.ai** — email campaigns using Apollo contacts + AI drafts
5. **Module 4: Dripify** — LinkedIn campaigns in parallel with email
6. **Module 6: Attio CRM** — sync pipeline data for sales visibility
7. **Module 7: LinkedIn SN** — CSV import, lowest priority since Dripify handles automation

### New Environment Variables (all modules)

```env
# Apollo.io
APOLLO_API_KEY=
APOLLO_MAX_CONTACTS_PER_COMPANY=5
APOLLO_TITLE_FILTERS=CEO,CFO,CIO,CTO,VP,Director,Head

# OpenAI Outreach
OPENAI_OUTREACH_MODEL=gpt-4.1-nano
OPENAI_OUTREACH_MAX_TOKENS=500

# Instantly.ai
INSTANTLY_API_KEY=
INSTANTLY_WORKSPACE_ID=
INSTANTLY_WEBHOOK_SECRET=

# Dripify
DRIPIFY_API_KEY=
DRIPIFY_WEBHOOK_SECRET=

# Make.com
MAKE_WEBHOOK_SECRET=
MAKE_DEFAULT_WEBHOOK_URL=

# Attio CRM
ATTIO_API_KEY=
ATTIO_WORKSPACE_ID=
```

### Migration Strategy

Same as Phase II: `CREATE TABLE IF NOT EXISTS` in `init_db()`, `try/except ALTER TABLE ADD COLUMN` for existing tables. All idempotent on startup.

### Verification

After each module:
1. Run `python app.py` — verify startup, all tables created
2. Test API endpoints with curl (mock external APIs if keys not yet configured)
3. Verify webhook send/receive with Make.com test scenarios
4. Confirm touchpoints auto-created from campaign replies
5. Check Attio records created/updated correctly
6. Verify all existing Phase II features still work
