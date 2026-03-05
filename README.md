# EPI-USE México — Jalisco Territory Intelligence

Dashboard for managing and prioritizing ~2,100 SMB/midmarket accounts in the Jalisco PLT (Partner-Led Territory) for SAP cloud solutions.

## Quick Start

```bash
cd plt-dashboard
# first time only:
cp .env.example .env
# then edit .env with your Resend settings
uv run python app.py
```

Open **http://localhost:5001**

### Environment Variables (.env)

The app auto-loads variables from a local `.env` file at startup.

Required for email login:

- `RESEND_API_KEY`
- `RESEND_FROM` (must be a sender/domain verified in Resend)

Optional:

- `FLASK_SECRET_KEY`
- `FOOTER_CONTACT_EMAIL`
- `BRAVE_API_KEY` (enables Brave Search candidates in URL discovery)
- `OPENAI_API_KEY` (required for Playwright + LLM URL validation jobs)
- `OPENAI_URL_VALIDATION_MODEL` (default: `gpt-5-nano`)
- `OPENAI_URL_VALIDATION_INPUT_COST_PER_1M`
- `OPENAI_URL_VALIDATION_OUTPUT_COST_PER_1M`
- `BRAVE_SEARCH_TIMEOUT_SECONDS`
- `BRAVE_SEARCH_MAX_RESULTS`
- `BRAVE_SEARCH_LOCALES` (default: `MX:es,US:en`)
- `OPENAI_URL_DISCOVERY_MODEL` (default: `gpt-5-nano`)
- `URL_DISCOVERY_DEEP_RESULT_LIMIT` (default: `4`)
- `URL_DISCOVERY_DEEP_FALLBACK_DEFAULT` (`1` enables deep Playwright+LLM fallback)

## Territory Snapshot

| Metric | Value |
|--------|-------|
| Total accounts | 2,132 |
| Tier A (score 70+) | 779 |
| Tier B (score 50–69) | 1,349 |
| Tier C (score <50) | 4 |
| Average composite score | 66.0 |
| Existing SAP (upsell) | 1,132 |
| Net New (GROW greenfield) | 1,000 |

**Top industries:** Retail (495), Professional Services (379), Food & Beverage (270), Mining (235), Manufacturing (203), Hotels/Hospitality (115)

## Features

- **Scoring engine** — Composite 0–100 score based on industry match (40%), company size (25%), SAP relationship (20%), and data completeness (15%). Weights are configurable from the settings panel.
- **Industry classification** — Maps SAP's 27 master codes to 16 target categories, with keyword-based refinement on company names and SIC descriptions (Spanish + English).
- **Dashboard** — Stats cards, score distribution chart, industry breakdown, tier split. Dark mode default with light mode toggle.
- **Account table** — Sortable, filterable, paginated. Search by name or SIC description. Filter by industry, tier, SAP status, stars, and tags.
- **Detail panel** — Slide-out view with full score breakdown, raw xlsx data, manual industry override, notes, tags, and quick actions.
- **Target list** — Separate tab for starred/targeted accounts with dedicated export.
- **Company CRM workspace** — Dedicated `/companies/<bp_id>` page with account-specific contacts, touchpoints, and next actions.
- **URL discovery pipeline** — Dedicated `/pipeline/urls` workspace to generate URL candidates, track coverage, and bulk accept/reject/edit URLs.
  Sources: heuristic domain generation + Brave Search API + deep fallback (Playwright + `gpt-5-nano` over top search results).
- **CSV export** — All accounts or target list only.
- **Presentation view** — Clean summary of top 30 accounts for TEM meetings.
- **SQLite persistence** — All manual edits (overrides, notes, stars, tags) persist across restarts. The source xlsx is never modified.
- **Scoring profile manager** — Create, edit, duplicate, delete, and share named scoring configurations (factor weights + industry score reference). Profile selection is session-scoped per user.
- **Global default profile** — `super_admin` can set any profile as the default used for users on next load (unless they explicitly selected another profile in the current session).

## Stack

- **Backend:** Python, Flask, pandas, openpyxl
- **Frontend:** Vanilla HTML/CSS/JS (no framework)
- **Database:** SQLite (auto-created as `territory.db`)
- **Data source:** `data/PLT_Jalisco_2026.xlsx` (read-only)

## Project Structure

```
plt-dashboard/
├── app.py              # Flask backend and API routes
├── database.py         # SQLite models and operations
├── scoring.py          # Scoring engine (configurable weights)
├── classifier.py       # Industry keyword classifier
├── data_loader.py      # xlsx ingestion and normalization
├── static/
│   ├── style.css            # Dashboard + profile manager styling
│   ├── app.js               # Main dashboard frontend logic
│   ├── company.js           # Company CRM workspace logic
│   ├── url_pipeline.js      # URL pipeline bulk-review logic
│   └── scoring_profiles.js  # Scoring profile manager logic
├── templates/
│   ├── index.html           # Main dashboard template
│   ├── company.html         # Company CRM workspace
│   ├── url_pipeline.html    # URL discovery pipeline workspace
│   ├── login.html           # Email-code login
│   ├── pending.html         # Pending approval page
│   ├── admin.html           # User administration
│   └── scoring_profiles.html # Scoring profile manager
├── data/
│   └── PLT_Jalisco_2026.xlsx
└── territory.db        # SQLite database (auto-created)
```

## Scoring Criteria

| Factor | Default Weight | Logic |
|--------|---------------|-------|
| Industry match | 40% | Hotels/Hospitality = 100, Higher Education = 95, Food & Beverage = 90, Mining = 85, Real Estate/Construction = 85, Manufacturing = 70, Financial Services = 65, down to Unclassified = 20 |
| Company size | 25% | Sweet spot 200–1,000 employees. Returns neutral 50 when size data is unknown (most accounts currently). |
| SAP relationship | 20% | Existing SAP = 90, Net New = 80, Has Business One = 40 |
| Data completeness | 15% | Percentage of actionable fields populated |

### Read Me Tips (What Each Weight Means)

- **Industry match**: This is a fit score against your target SAP cloud industries, not a match against a specific company. The app first decides an account's industry (manual override if set, otherwise SAP master-code mapping, then keyword fallback from company name/SIC), then looks up that industry's predefined score.
- **Company size**: This is based on employee count bands. The current sweet spot is 200–1,000 employees (highest score). If size is missing or invalid, the score is neutral (50).
- **SAP relationship**: This is based on SAP status derived from the source data (`ERP ISP ID`) plus supported status labels: `Existing SAP` (90), `Net New` (80), `Has Business One` (40).
- **Data completeness**: This is the percent of key fields that are populated for an account: `company_name`, `master_industry`, `sic_description`, `region`, `erp_isp_id`, `employee_count`, `revenue`, and `planning_entity_name`.

## Data Source

The xlsx contains 10 columns from SAP's PLT program:

| Column | Description |
|--------|-------------|
| Business Partner ID | Unique account identifier |
| ERP ISP ID | Populated if existing SAP relationship (~52%) |
| Organization Name1 | Company name |
| Planning Entity / Name | Planning-level grouping |
| Default Address Region Descr | Always "Jalisco" |
| Account Executive Name 2026 | Assigned AE |
| Default Master Code Descr | SAP industry sector (27 categories) |
| Default SIC Primary Descr | Granular SIC description (473 unique) |
