"""FastAPI backend for the Sublime Security Email Analyzer."""

import logging
import time
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.msg_converter import convert_or_parse_email
from app.rule_scanner import SublimeAnalyzer
from app.models import (
    ScanResult,
    AnalyzerRuleResult,
    AnalyzerRule,
    AnalyzerQueryResult,
)

app = FastAPI(title="Sublime Security Email Analyzer")
logger = logging.getLogger(__name__)

# Allow frontend to call the API (useful when served from a different origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files from the same origin
FRONTEND_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def root():
    """Serve the main frontend page."""
    return FileResponse(str(FRONTEND_DIR / "index.html"))


def _build_rule_results(raw_results: list) -> list:
    """Normalize the analyzer's rule_results into typed models.

    The REST API returns ``matched`` (bool). The Python module returns
    ``result`` (bool). We check both and prefer ``matched``.

    Skips insights/actions that lack a ``rule`` dict or a ``severity``,
    since they are not detection rules.
    """
    out = []
    for r in raw_results:
        rule_raw = r.get("rule") or {}
        # Insights/actions have a rule object but no severity field.
        # Real detection rules always have a severity (info, low, medium, high, critical).
        if not rule_raw.get("severity"):
            continue
        # REST API uses "matched"; Python module uses "result"
        matched = r.get("matched") if "matched" in r else r.get("result")
        out.append(
            AnalyzerRuleResult(
                rule=AnalyzerRule(
                    id=rule_raw.get("id", "unknown"),
                    name=rule_raw.get("name", "Unnamed Rule"),
                    severity=rule_raw.get("severity", "medium"),
                    source=rule_raw.get("source"),
                ),
                result=matched,
                success=r.get("success", True),
                error=r.get("error"),
                execution_time=r.get("execution_time", 0.0),
            )
        )
    return out


def _build_query_results(raw_results: list) -> list:
    """Normalize the analyzer's query_results into typed models."""
    out = []
    for r in raw_results:
        out.append(
            AnalyzerQueryResult(
                name=r.get("name"),
                result=r.get("result"),
                success=r.get("success", True),
                error=r.get("error"),
            )
        )
    return out


def _compute_recommendation(rule_results: list) -> tuple[str, str]:
    """Score matched rules by severity and return (level, action_text).

    Levels (highest-severity wins):
      block     → any critical match
      high      → any high match
      moderate  → score ≥ 3 or any medium match
      low       → any low match
      info      → only info matches
      clean     → no matches

    Score weights: critical=10, high=5, medium=3, low=1, info=0.
    """
    matched = [r for r in rule_results if r.result is True]
    if not matched:
        return "clean", "No threat indicators detected."

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for r in matched:
        sev = (r.rule.severity or "medium").lower()
        counts[sev] = counts.get(sev, 0) + 1

    score = (
        counts["critical"] * 10
        + counts["high"] * 5
        + counts["medium"] * 3
        + counts["low"] * 1
    )

    if counts["critical"]:
        return (
            "block",
            "BLOCK / QUARANTINE — Critical threat indicators detected. "
            "Do not interact with this email.",
        )
    if counts["high"]:
        return (
            "high",
            "HIGH RISK — Do not interact without additional verification. "
            "Escalate to your security team if unsure.",
        )
    if score >= 3 or counts["medium"]:
        return (
            "moderate",
            "MODERATE RISK — Review carefully before interacting. "
            "Verify sender identity and inspect links/attachments.",
        )
    if counts["low"]:
        return (
            "low",
            "LOW RISK — Minor indicators present. Exercise standard caution.",
        )
    # info only
    return (
        "info",
        "INFORMATIONAL — No immediate threats, but review findings for awareness.",
    )


def _allowed_email_ext(filename: str) -> bool:
    return filename.lower().endswith((".msg", ".eml"))


@app.post("/scan", response_model=ScanResult)
async def scan_email_file(
    file: UploadFile = File(..., description="Outlook .msg or .eml file to analyze"),
):
    """
    Upload a .msg or .eml file and analyze it with the free
    Sublime Security Analyzer API (https://analyzer.sublime.security).
    """
    start_time = time.perf_counter()

    # Validate input
    if not file.filename or not _allowed_email_ext(file.filename):
        raise HTTPException(
            status_code=400, detail="Only .msg and .eml files are accepted."
        )

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Parse or convert to .eml
    try:
        eml_bytes, metadata = convert_or_parse_email(contents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Analyze via Sublime Security free Analyzer API
    try:
        analyzer = SublimeAnalyzer()
        analysis = await analyzer.analyze_message(eml_bytes)
    except Exception as exc:
        logger.exception("Sublime Analyzer request failed")
        raise HTTPException(
            status_code=502, detail=f"Sublime Analyzer request failed: {exc}"
        ) from exc

    rule_results = _build_rule_results(analysis.get("rule_results", []))
    query_results = _build_query_results(analysis.get("query_results", []))
    matched = [r for r in rule_results if r.result is True]
    rec_level, rec_action = _compute_recommendation(rule_results)

    duration_ms = (time.perf_counter() - start_time) * 1000

    return ScanResult(
        filename=file.filename,
        msg_size_bytes=len(contents),
        eml_size_bytes=len(eml_bytes),
        sender=metadata.get("sender"),
        recipients=metadata.get("recipients", []),
        subject=metadata.get("subject"),
        date=metadata.get("date"),
        attachment_names=metadata.get("attachment_names", []),
        rule_results=rule_results,
        query_results=query_results,
        rules_matched_count=len(matched),
        scan_duration_ms=round(duration_ms, 2),
        recommended_action=rec_action,
        recommendation_level=rec_level,
    )


@app.post("/convert")
async def convert_only(
    file: UploadFile = File(..., description="Outlook .msg or .eml file to convert"),
):
    """
    Convert or pass through an email file and return both bytes and metadata.
    """
    if not file.filename or not _allowed_email_ext(file.filename):
        raise HTTPException(
            status_code=400, detail="Only .msg and .eml files are accepted."
        )

    contents = await file.read()
    try:
        eml_bytes, metadata = convert_or_parse_email(contents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return JSONResponse(
        content={
            "filename": file.filename,
            "metadata": metadata,
            "eml_base64": eml_bytes.decode("utf-8", errors="replace"),
        }
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/debug")
async def debug_upload(
    file: UploadFile = File(..., description="Any file to inspect"),
):
    """
    Return the first 200 bytes of the uploaded file and its detected MIME type.
    Useful for figuring out what Outlook.com actually sent you.
    """
    contents = await file.read()
    preview = contents[:200]
    return {
        "filename": file.filename,
        "size": len(contents),
        "content_type": file.content_type,
        "hex_preview": preview.hex(),
        "text_preview": preview.decode("utf-8", errors="replace"),
    }
