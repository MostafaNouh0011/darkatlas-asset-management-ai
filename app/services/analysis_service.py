"""
LangChain analysis layer.

CORE DESIGN PRINCIPLE (this is your anti-hallucination guardrail, and the
thing to explain clearly in the README + interview):

  The LLM is NEVER given write access to the database and is NEVER asked to
  "answer from memory." For every feature, the pattern is:

    1. LLM's only job is to fill in a small structured schema
       (e.g. AssetFilter) from natural language.
    2. Your own Python code applies that schema against REAL rows fetched
       from Postgres.
    3. If the LLM needs to produce prose (a summary, a report), it is handed
       the real rows as context and instructed to summarize *only* what's in
       front of it — it never sees the full dataset or invents IDs.

  This means the LLM is a translator and a narrator, never an oracle.

Swap-in note: this file uses langchain-google-genai. To switch provider, swap
ChatGoogleGenerativeAI for the equivalent (ChatAnthropic from langchain_anthropic,
ChatOpenAI from langchain_openai) and change the model name — the rest of
the chain logic is provider-agnostic.
"""
import os
from datetime import datetime, timezone
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Asset
from app.schemas import AssetFilter

# Known end-of-life / risky technologies for the risk-scoring feature.
# A real system would pull this from a maintained feed; hardcoded here is a
# stated, documented assumption for the scope of this task.
EOL_TECHNOLOGIES = {"php 5", "windows server 2008", "openssl 1.0", "tls 1.0", "tls 1.1"}
SENSITIVE_PORTS = {"23/tcp": "telnet (unencrypted)", "21/tcp": "ftp (unencrypted)",
                    "3389/tcp": "rdp exposed", "445/tcp": "smb exposed"}


def _llm():
    """Lazily construct the LLM client so importing this module never fails
    just because an API key isn't set yet (useful for tests)."""
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,  # deterministic-ish: we want structured translation, not creativity
        google_api_key=os.getenv("GOOGLE_API_KEY"),
    )


# ---------------------------------------------------------------------------
# Feature 1: Natural-language asset query
# ---------------------------------------------------------------------------

QUERY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You translate a security analyst's plain-English question into a "
     "structured filter over an asset database. You do NOT answer the "
     "question yourself and you do NOT invent data. You only fill in the "
     "fields of the filter schema that the question implies. Asset types "
     "are: domain, subdomain, ip_address, service, certificate, technology. "
     "Statuses are: active, stale, archived. "
     "If the question is not about asset/security data at all (e.g. "
     "'what's the weather'), set out_of_scope to true and leave other "
     "fields empty."),
    ("human", "{question}"),
])


def parse_nl_query(question: str) -> AssetFilter:
    """Turn an English question into a structured AssetFilter. This is the
    ONLY place the LLM touches the query — it never sees the database."""
    llm = _llm().with_structured_output(AssetFilter)
    chain = QUERY_PROMPT | llm
    return chain.invoke({"question": question})


def apply_filter(db: Session, f: AssetFilter, limit: int = 50) -> list[Asset]:
    """Apply a structured filter against real rows. Pure Python/SQL — no LLM."""
    stmt = select(Asset)
    if f.type:
        stmt = stmt.where(Asset.type == f.type)
    if f.status:
        stmt = stmt.where(Asset.status == f.status)
    if f.value_contains:
        stmt = stmt.where(Asset.value.contains(f.value_contains))
    rows = db.execute(stmt.limit(500)).scalars().all()  # cap before python-side filtering
    if f.tag_contains:
        rows = [r for r in rows if f.tag_contains in r.tags]
    if f.expiry_before:
        cutoff = f.expiry_before
        rows = [r for r in rows
                if r.type == "certificate"
                and r.asset_metadata.get("expires")
                and r.asset_metadata["expires"] < cutoff]
    return rows[:limit]


def nl_query(db: Session, question: str) -> dict:
    parsed = parse_nl_query(question)
    if parsed.out_of_scope:
        return {"question": question, "filter_used": None, "out_of_scope": True,
                "matches": [], "message": "This question doesn't appear to be about asset data."}
    matches = apply_filter(db, parsed)
    return {
        "question": question,
        "filter_used": parsed.model_dump(exclude={"out_of_scope"}),
        "out_of_scope": False,
        "matches": [{"id": a.id, "type": a.type, "value": a.value, "status": a.status,
                      "tags": a.tags, "metadata": a.asset_metadata} for a in matches],
    }


# ---------------------------------------------------------------------------
# Feature 2: Risk scoring & summarization
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a security analyst writing a concise risk summary. You will "
     "be given a list of pre-computed risk findings (already determined by "
     "deterministic code, not by you). Write 2-4 sentences summarizing them "
     "for a human reader. Do NOT add findings that aren't in the list. Do "
     "NOT invent asset names not present in the findings."),
    ("human", "Findings:\n{findings}"),
])


def compute_risk_findings(asset: Asset) -> list[str]:
    """Deterministic risk logic — no LLM involved. This is the part that
    must be reliable and auditable; the LLM only narrates it afterward."""
    findings = []
    if asset.type == "certificate":
        expires = asset.asset_metadata.get("expires")
        if expires:
            try:
                expiry_date = datetime.fromisoformat(expires).replace(tzinfo=timezone.utc)
                days_left = (expiry_date - datetime.now(timezone.utc)).days
                if days_left < 0:
                    findings.append(f"Certificate for {asset.value} expired {-days_left} days ago.")
                elif days_left <= 30:
                    findings.append(f"Certificate for {asset.value} expires in {days_left} days (expiring soon).")
            except ValueError:
                findings.append(f"Certificate for {asset.value} has an unparseable expiry date: {expires}.")
    if asset.type == "service":
        for port_key, label in SENSITIVE_PORTS.items():
            if port_key in asset.value:
                findings.append(f"Service {asset.value} is a sensitive exposure: {label}.")
    if asset.type == "technology":
        tech_name = asset.value.lower()
        for eol in EOL_TECHNOLOGIES:
            if eol in tech_name:
                findings.append(f"Technology {asset.value} matches a known end-of-life entry ({eol}).")
    if not findings:
        findings.append(f"No significant risk findings for {asset.value}.")
    return findings


def risk_summary(db: Session, asset_id: str) -> dict:
    asset = db.get(Asset, asset_id)
    if asset is None:
        return {"error": f"No asset with id {asset_id}"}
    findings = compute_risk_findings(asset)
    llm = _llm()
    chain = SUMMARY_PROMPT | llm
    summary = chain.invoke({"findings": "\n".join(f"- {f}" for f in findings)})
    return {"asset_id": asset_id, "findings": findings, "summary": summary.content}


# ---------------------------------------------------------------------------
# Feature 3: Automated enrichment & categorization
# ---------------------------------------------------------------------------

class EnrichmentResult(BaseModel):
    environment: str   # prod / staging / dev / unknown
    category: str
    criticality: str   # low / medium / high


ENRICH_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You classify a discovered security asset. Given its type, value, "
     "tags, and metadata, output: environment (prod/staging/dev/unknown — "
     "infer from naming patterns like 'prod', 'stg', 'dev', 'test' in the "
     "value or tags; default unknown if no signal), category (a short "
     "label like 'web service', 'mail infra', 'internal tool'), and "
     "criticality (low/medium/high, based on exposure and naming). Base "
     "your answer only on the data given — do not assume facts not present."),
    ("human", "type: {type}\nvalue: {value}\ntags: {tags}\nmetadata: {metadata}"),
])


def enrich_asset(db: Session, asset_id: str) -> dict:
    asset = db.get(Asset, asset_id)
    if asset is None:
        return {"error": f"No asset with id {asset_id}"}
    llm = _llm().with_structured_output(EnrichmentResult)
    chain = ENRICH_PROMPT | llm
    result = chain.invoke({
        "type": asset.type, "value": asset.value,
        "tags": asset.tags, "metadata": asset.asset_metadata,
    })
    asset.asset_metadata = {**asset.asset_metadata, **result.model_dump()}
    db.commit()
    return {"asset_id": asset_id, "enrichment": result.model_dump()}


# ---------------------------------------------------------------------------
# Feature 4: Natural-language report generation
# ---------------------------------------------------------------------------

REPORT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You write a short inventory/risk report for a security team. You will "
     "be given asset rows AND a list of pre-computed risk findings (already "
     "determined by deterministic code, not by you) for any rows that have "
     "them. Group by type, mention counts, and surface the pre-computed "
     "findings faithfully. Do NOT add findings that aren't in the list. Do "
     "NOT invent asset names or values that are not in the provided rows. "
     "If a row has no findings listed, do not invent any for it."),
    ("human", "Assets ({count} total):\n{rows}\n\nPre-computed findings:\n{findings}"),
])


def generate_report(db: Session, f: AssetFilter | None = None) -> dict:
    assets = apply_filter(db, f, limit=200) if f else \
        db.execute(select(Asset).limit(200)).scalars().all()

    # Mirror the /analyze/risk pattern: compute deterministic findings per
    # row BEFORE handing anything to the LLM, then hand the LLM only the
    # rows that have real findings. The earlier version of this function
    # asked the LLM to infer risks from raw rows, which it consistently
    # declined to do even when obvious risks (telnet on 23/tcp, EOL PHP)
    # were present in the data.
    rows_with_findings = []
    for a in assets:
        findings = [f for f in compute_risk_findings(a)
                    if not f.startswith(f"No significant risk findings")]
        rows_text = f"- [{a.type}] {a.value} (status={a.status}, tags={a.tags})"
        if findings:
            rows_text += "\n  Findings:\n" + "\n".join(f"    - {f}" for f in findings)
            rows_with_findings.append((a, findings))
    rows_block = "\n".join(
        f"- [{a.type}] {a.value} (status={a.status}, tags={a.tags})"
        for a in assets
    ) or "(none)"
    findings_block = "\n".join(
        f"- {a.value}: {'; '.join(fs)}"
        for a, fs in rows_with_findings
    ) or "(no pre-computed findings)"

    llm = _llm()
    chain = REPORT_PROMPT | llm
    report = chain.invoke({"count": len(assets), "rows": rows_block, "findings": findings_block})
    return {
        "asset_count": len(assets),
        "risky_asset_count": len(rows_with_findings),
        "report": report.content,
    }
