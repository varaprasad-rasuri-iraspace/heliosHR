"""Workday event intake: signature check, data minimisation, validation.

Only an allow-list of fields ever reaches the LLM or the audit log. SSN, DOB,
home address and compensation are dropped here and never leave this function.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import date

ALLOWED_FIELDS = (
    "worker_id", "first_name", "last_name", "preferred_name", "job_title",
    "department", "location", "country", "worker_type", "manager_email",
    "start_date", "cost_center", "hr_notes",
)


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Workday -> n8n webhook is HMAC-SHA256 signed; reject anything else."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def minimize(event: dict) -> dict:
    worker = event["worker"]
    return {k: worker.get(k) for k in ALLOWED_FIELDS}


def validate(hire: dict, today: date) -> list[str]:
    """Blocking problems -> run stops and HR gets a ticket instead of guessing."""
    problems = []
    for required in ("worker_id", "first_name", "last_name", "department", "start_date"):
        if not hire.get(required):
            problems.append(f"missing field: {required}")
    if not hire.get("manager_email"):
        problems.append("missing manager_email (cannot route approvals or welcome note)")
    try:
        if date.fromisoformat(hire["start_date"]) < today:
            problems.append("start_date is in the past")
    except (KeyError, TypeError, ValueError):
        problems.append("start_date unparseable")
    return problems
