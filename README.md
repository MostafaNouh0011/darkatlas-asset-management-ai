# Buguard Asset Management — AI Applications Track

A minimal asset management API with a LangChain-powered analysis layer, built for the Buguard AI Internship technical assessment.

## What this is

- **FastAPI** backend storing discovered security assets (domains, subdomains, IPs, services, certificates, technologies) and their relationships in **Postgres**.
- A **LangChain** analysis layer providing four AI-powered capabilities: natural-language querying, risk scoring & summarization, automated enrichment, and natural-language report generation.
- Fully containerized: one command brings up the API and database together.

## Quick start

```bash
git clone <this-repo>
cd buguard-asset-mgmt
cp .env.example .env
# edit .env and set GOOGLE_API_KEY (the project uses Gemini 2.5 Flash via
# langchain-google-genai; to use a different provider, swap the import in
# app/services/analysis_service.py — see the swap-in note at the top of that file)
docker-compose up --build
```

The API is now running at `http://localhost:8000`. Interactive docs (Swagger UI) at `http://localhost:8000/docs`.

To load the sample dataset:

```bash
curl -X POST http://localhost:8000/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-to-a-real-secret" \
  -d @sample_data/seed_dataset.json
```

(Use whatever value you set for `API_KEY` in `.env`.)

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Tests run against an in-memory SQLite database, so they don't require Docker or a live Postgres instance. This was a deliberate choice to keep the test suite fast and runnable in any environment (including CI) without infrastructure dependencies.

**Note on test coverage**: the `/analyze/*` endpoints (LangChain-backed) are not covered by automated tests, since they require a live LLM API key and network access — testing them meaningfully would mean either mocking the LLM (low value — it would mostly test the mock) or hitting the real API in CI (slow, costly, flaky). Instead, these were verified manually; see "Example prompts and outputs" below for real recorded runs.

## Architecture & design decisions

**Data model**: `Asset` (id, type, value, status, first_seen, last_seen, source, tags, metadata) plus a separate `AssetRelationship` table (from_asset_id, to_asset_id, relationship_type). Relationships are modeled as their own table rather than foreign keys directly on `Asset` because the relationship *type* (parent_domain, covers, runs_on, resolves_to) is itself meaningful data, and a single asset can participate in many relationships in both directions.

**Idempotent import**: assets are upserted by their natural `id` (not re-derived or guessed). Re-importing the same data updates `last_seen` and merges `tags`/`metadata` rather than creating duplicates.

**Merge strategy on conflicting data**: when two sources report different values for the same metadata key, the most recently imported value wins (last-write-wins). Tags are unioned, never overwritten. This is a simple, defensible default for the scope of this task — a production system might instead track per-source values and surface conflicts explicitly rather than silently resolving them.

**Stale → active lifecycle**: if an asset with `status=stale` is seen again in a later import, it flips back to `active`. This reflects the real-world case where a previously-dormant asset (e.g. a subdomain that stopped resolving) becomes live again.

**Malformed records don't fail the batch**: each record in an import payload is validated independently. Invalid records (missing required fields, unknown `type` values) are skipped and reported back in the response (`skipped: [...]`), while valid records in the same batch are still processed.

**Authentication**: a lightweight shared-secret API key (`X-API-Key` header) protects the write endpoint (`/import`). This was a deliberate scope decision — a full JWT/OAuth setup would be disproportionate for a minimal internal API in a 1-week assessment, but the principle (writes require auth) is demonstrated.

**Pagination**: `/assets` defaults to 50 results, capped at 200, with `offset`/`limit` params, so a large inventory can't accidentally be returned in one unbounded response.

**Migrations**: tables are created on app startup via `Base.metadata.create_all()` rather than a full Alembic migration setup. For a 1-week minimal-API task this is a reasonable scope cut; Alembic would be the next step for a production system with evolving schema.

## The AI layer: design principle

The single most important design decision in this project is how the LLM is kept from hallucinating asset data. The rule followed throughout:

> **The LLM is a translator and a narrator. It is never an oracle.**

Concretely:
- For natural-language queries, the LLM's only job is to fill in a small structured schema (`AssetFilter`: type, status, tag, expiry cutoff, etc.) from the question. It never sees the database. My own Python code then applies that filter against real rows in Postgres.
- For risk scoring, all actual risk findings (expired certificates, exposed sensitive ports, known end-of-life technologies) are computed by deterministic Python logic, not the LLM. The LLM is only given the pre-computed findings and asked to write a short natural-language summary of them — it cannot introduce a finding that wasn't already in the list it was handed.
- For enrichment/categorization, the LLM is given one asset's real fields (type, value, tags, metadata) and asked to classify it (environment, category, criticality) — again, structured output, grounded in only what it was shown.
- For report generation, the LLM is handed a list of real, already-filtered asset rows and instructed never to mention an asset not present in that list.

This means every LLM call in the system is either (a) translating English into a structured object my own code interprets, or (b) summarizing real data it was explicitly handed — never freely generating facts about the asset inventory from nothing.

## Example prompts and outputs

*All outputs below are real, recorded against this running deployment on 2026-06-27 using Gemini 2.5 Flash via `langchain-google-genai` (the project's LLM provider was swapped from Anthropic to Google after the original key proved to have no credit balance; the chain logic in `analysis_service.py` is provider-agnostic, so only the `_llm()` factory changed).*

### 1. Natural-language query

```bash
curl -X POST http://localhost:8000/analyze/query \
  -H "Content-Type: application/json" \
  -d '{"question": "show me all subdomains that are stale"}'
```

```json
{
  "question": "show me all subdomains that are stale",
  "filter_used": {
    "type": "subdomain",
    "status": "stale",
    "tag_contains": null,
    "value_contains": null,
    "expiry_before": null
  },
  "out_of_scope": false,
  "matches": []
}
```

Gemini translated the English into a structured `AssetFilter` (`type=subdomain`, `status=stale`); my own code ran that filter against Postgres and returned the actual rows. The matches list is empty because `a4` (`staging.example.com`) had been re-seen by an earlier import and was flipped from `stale` → `active` per the documented lifecycle, so there are no stale subdomains in the dataset at the moment this was recorded. Re-importing a stale asset would surface it here.

The LLM was never given the database — it can't hallucinate asset IDs it didn't see.

### 2. Risk summary

```bash
curl -X POST http://localhost:8000/analyze/risk \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "a3"}'
```

```json
{
  "asset_id": "a3",
  "findings": [
    "Certificate for CN=api.example.com expired 541 days ago."
  ],
  "summary": "The certificate for `api.example.com` is expired. This certificate expired 541 days ago, indicating a significant lapse in certificate management that could lead to service disruptions or security warnings."
}
```

The findings list is computed **deterministically** by `compute_risk_findings()` (expiry check against today's date) — the LLM only narrated the list it was handed. The summary cannot introduce a finding that wasn't already in the input, and cannot name an asset not in the input.

### 3. Enrichment

```bash
curl -X POST http://localhost:8000/analyze/enrich \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "a4"}'
```

```json
{ "asset_id": "a4", "enrichment": { "environment": "staging", "category": "web service", "criticality": "medium" } }
```

Gemini correctly inferred `staging` from the asset's `staging` tag and `staging.example.com` naming pattern, and rated it `medium` criticality (lower than the `api.example.com` subdomain recorded earlier, which Gemini classified as `high`). Side-effect: the enrichment is written back to `asset_metadata` on the asset row, so subsequent reads of `/assets/a4` include the new fields:

```json
{
  "id": "a4", "type": "subdomain", "value": "staging.example.com", "status": "active",
  "tags": ["staging"],
  "metadata": { "environment": "staging", "category": "web service", "criticality": "medium" }
}
```

### 4. Report generation

```bash
curl -X POST http://localhost:8000/analyze/report \
  -H "Content-Type: application/json" \
  -d '{"tag_contains": "prod"}'
```

```json
{
  "asset_count": 4,
  "risky_asset_count": 2,
  "report": "**Security Inventory and Risk Report**\n\nThis report summarizes the current inventory of active production assets and highlights any pre-computed risk findings.\n\n**Total Active Production Assets: 4**\n\n---\n\n**Subdomains (1 total)**\n*   `api.example.com`\n    *   *No findings.*\n\n**Services (1 total)**\n*   `23/tcp`\n    *   **Finding:** Service 23/tcp is a sensitive exposure: telnet (unencrypted).\n\n**Technologies (1 total)**\n*   `PHP 5.6`\n    *   **Finding:** Technology PHP 5.6 matches a known end-of-life entry (php 5).\n\n**IP Addresses (1 total)**\n*   `203.0.113.10`\n    *   *No findings.*"
}
```

The four prod-tagged assets (`api.example.com`, `23/tcp`, `PHP 5.6`, `203.0.113.10`) are filtered by Python before any LLM call. Deterministic findings (`compute_risk_findings()` per row) are then computed and handed to the LLM alongside the asset rows — Gemini only narrates the pre-computed findings, it doesn't infer them. The `risky_asset_count: 2` field lets a caller audit the report's claim (2 of 4 prod assets had pre-computed findings) without re-parsing the prose. This is the same grounding pattern `/analyze/risk` uses; without it, an earlier version of this endpoint consistently produced reports that said "no risk findings" even when obvious risks were in the data.

### Out-of-scope query (grounding check)

```bash
curl -X POST http://localhost:8000/analyze/query \
  -H "Content-Type: application/json" \
  -d '{"question": "what is the weather like today"}'
```

```json
{ "question": "what is the weather like today", "filter_used": null, "out_of_scope": true, "matches": [], "message": "This question doesn't appear to be about asset data." }
```

Expected: `out_of_scope: true`, no matches returned. Confirms the LLM correctly declines to fabricate an answer when the question isn't about asset data.

## Known limitations / things I'd do differently with more time

- Tag filtering happens in Python rather than at the SQL level, since `tags` is stored as a JSON column rather than a normalized join table. Fine at this dataset's scale; wouldn't scale to a large inventory without an index strategy change (e.g. Postgres JSONB + GIN index, or a proper `asset_tags` table).
- No agentic tool-calling (the LLM calling functions to fetch its own data) — the bonus mentions this as optional. I deliberately scoped to a simpler, more reliably-grounded pattern (LLM-fills-schema, code-executes-query) given the time constraint, and because it's a stronger anti-hallucination guarantee than letting the LLM drive its own multi-step retrieval.
- LangChain and FastAPI were both new to me going into this assessment — I focused the available time on getting the core data-handling and grounding logic right and well-tested, since that's where correctness matters most, rather than on UI polish or additional bonus features.

## Tech stack

Python · FastAPI · SQLAlchemy · PostgreSQL · LangChain · Pydantic · pytest · Docker / Docker Compose
