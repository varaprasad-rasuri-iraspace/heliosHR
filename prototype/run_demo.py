"""Offline demo, or configure HELIOS_MODEL + its provider key for a live LLM.

Examples: anthropic/model-id + ANTHROPIC_API_KEY, openai/model-id + OPENAI_API_KEY,
or gemini/model-id + GEMINI_API_KEY.

Scenario 1 (Priya): Okta username conflict + Slack 429 -> handled automatically -> COMPLETE.
Scenario 2 (Marcus): prompt-injection in HR notes + cross-dept request + Jira 403 -> policy gate,
                     approval ticket, LLM-written incident ticket -> PARTIAL.
Then: duplicate webhook delivery is ignored, and the audit chain is verified (and tamper-tested).
"""
import json
import sys
from datetime import date
from pathlib import Path

from onboarding import orchestrator
from onboarding.audit import AuditLog
from onboarding.connectors import build_systems
from onboarding.planner import Planner

HERE = Path(__file__).parent
TODAY = date(2026, 10, 19)


def load(name):
    return json.loads((HERE / "sample_events" / name).read_text())


def show(title, result):
    print(f"\n=== {title} ===")
    print(f"status        : {result['status']}")
    print(f"granted       : {len(result['granted'])} -> {', '.join(result['granted'])}")
    print(f"needs approval: {result['pending_approval'] or '-'}")
    print(f"denied        : {[d['id'] + ' (' + d['reason'] + ')' for d in result['denied']] or '-'}")
    print(f"retried steps : {result['retried_steps'] or '-'}")
    print(f"failures      : {result['failures'] or '-'}")
    print(f"flags         : {result['flags'] or '-'}")


def main():
    audit_path = HERE / "audit_demo.jsonl"
    audit_path.unlink(missing_ok=True)
    audit = AuditLog(audit_path)
    planner = Planner()
    print(f"planner mode: {planner.mode} ({planner.model})")

    # Scenario 1
    systems = build_systems({"slack": {"add_grants": ["429"]}})
    systems["okta"].users["priya.nair@helioshr.com"] = {"worker_id": "W-000111", "status": "ACTIVE", "profile": {}}
    ev1 = load("hire_priya.json")
    r1 = orchestrator.run_onboarding(ev1, planner=planner, systems=systems, audit=audit, today=TODAY, sleep=lambda s: None)
    show("Priya Nair (CS, London)", r1)

    # Duplicate delivery of the same Workday event
    dup = orchestrator.run_onboarding(ev1, planner=planner, systems=systems, audit=audit, today=TODAY)
    print(f"\nduplicate webhook ignored: {dup.get('duplicate', False)}")

    # Scenario 2
    systems2 = build_systems({"jira": {"add_grants": ["403"]}})
    r2 = orchestrator.run_onboarding(load("hire_marcus.json"), planner=planner, systems=systems2, audit=audit,
                                     today=TODAY, sleep=lambda s: None)
    show("Marcus Lee (Sales, injection attempt)", r2)
    print("\nFreshService tickets created for Marcus:")
    for t in systems2["freshservice"].tickets:
        print(f"  {t['id']} [{t['kind']}] {t['title']}")

    ok, msg = audit.verify()
    print(f"\naudit chain: {len(audit.records())} records, verify -> {ok} ({msg})")
    # tamper test
    lines = audit_path.read_text().splitlines()
    lines[3] = lines[3].replace("decision", "decisioN") if "decision" in lines[3] else lines[3].replace("run_", "RUN_")
    audit_path.write_text("\n".join(lines) + "\n")
    ok, msg = AuditLog(audit_path).verify()
    print(f"after tampering with one record -> verify: {ok} ({msg})")


if __name__ == "__main__":
    sys.exit(main())
