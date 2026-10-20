"""Deterministic orchestration: dedupe -> intake -> LLM plan -> policy gate -> execute -> audit.

In production this logic lives behind the MCP server; n8n owns triggers, waits
(approvals, activation on start date) and alerting.
"""
from __future__ import annotations

import random
import time
from datetime import date

from . import intake, policy
from .audit import AuditLog
from .connectors import ConflictError, PermanentError, SimulatedSystem, TransientError
from .planner import PROMPT_VERSION, Planner, plan_hash

RUNS: dict[str, dict] = {}          # event_id -> result  (prod: DynamoDB / Postgres)
ACTIVATION_TARGETS: dict[str, dict] = {}  # event_id -> login/start date (prod: durable run state)
PENDING_APPROVALS: dict[tuple[str, str], dict] = {}
STATE_STORE = None
DOMAIN = "helioshr.com"


def configure_state_store(store) -> None:
    """Attach a durable store for the MCP service; demos/tests may remain in-memory."""
    global STATE_STORE
    STATE_STORE = store
    if store is None:
        return
    RUNS.update(store.load_runs())
    ACTIVATION_TARGETS.update(store.load_activation_targets())
    PENDING_APPROVALS.update(store.load_approvals())


def _with_retry(fn, attempts: int = 4, base: float = 0.2, sleep=time.sleep):
    """Exponential backoff + jitter for transient errors only. Returns (result, attempts_used)."""
    for i in range(1, attempts + 1):
        try:
            return fn(), i
        except TransientError:
            if i == attempts:
                raise
            sleep(min(base * 2 ** (i - 1), 5) * (0.5 + random.random() / 2))
    raise AssertionError("unreachable")


class Run:
    def __init__(self, run_id, systems, audit, planner, sleep):
        self.id, self.sys, self.audit, self.planner, self.sleep = run_id, systems, audit, planner, sleep
        self.steps: list[dict] = []
        self.failures: list[dict] = []
        self.compensations: list[tuple[str, str, list[str]]] = []

    def do(self, system: str, op: str, key: str, **params):
        """One audited, retried, idempotent call. Raises PermanentError/ConflictError on non-retryable failure."""
        self.audit.write(self.id, "orchestrator", f"{system}.{op}.start", params=_redact(params))
        try:
            res, attempts = _with_retry(lambda: self.sys[system].call(op, f"{self.id}:{key}", **params), sleep=self.sleep)
        except TransientError as e:      # retries exhausted -> treat as permanent for this run
            if STATE_STORE:
                STATE_STORE.save_step(self.id, {"system": system, "op": op, "status": "failed",
                                                "error_type": type(e).__name__, "retryable": True})
            self.audit.write(self.id, "orchestrator", f"{system}.{op}.failed",
                             error_type=type(e).__name__, retryable=True, exhausted=True)
            raise PermanentError(str(e)) from e
        except PermanentError as e:
            if STATE_STORE:
                STATE_STORE.save_step(self.id, {"system": system, "op": op, "status": "failed",
                                                "error_type": type(e).__name__, "retryable": False})
            self.audit.write(self.id, "orchestrator", f"{system}.{op}.failed",
                             error_type=type(e).__name__, retryable=False)
            raise
        self.steps.append({"system": system, "op": op, "attempts": attempts, "status": "ok"})
        if STATE_STORE:
            STATE_STORE.save_step(self.id, self.steps[-1])
        self.audit.write(self.id, "orchestrator", f"{system}.{op}.ok", attempts=attempts)
        return res

    def escalate(self, system: str, op: str, err: Exception, hire: dict, items: list[str]):
        failure = {"run_id": self.id, "worker_id": hire["worker_id"], "system": system, "op": op,
                   "error": str(err), "attempts": 4 if getattr(err, "retryable", False) else 1, "items": items}
        try:
            note = self.planner.triage(failure)
            required = ("ticket_title", "ticket_body", "suggested_next_step")
            if not isinstance(note, dict) or any(not isinstance(note.get(k), str) for k in required):
                raise ValueError("triage output did not match the ticket schema")
        except Exception as triage_error:
            note = {
                "ticket_title": f"[Onboarding] {system}.{op} failed for {hire['worker_id']}",
                "ticket_body": f"Run {self.id}: {err}. Ticket drafting failed: {type(triage_error).__name__}.",
                "suggested_next_step": "Check service-account scopes and target resource state, then retry the failed step.",
            }
            self.audit.write(self.id, "orchestrator", "triage.fallback", error=type(triage_error).__name__)
        title_hash = plan_hash({"title": note["ticket_title"]})
        self.audit.write(self.id, "llm:" + self.planner.model, "triage.generated", prompt_version=PROMPT_VERSION,
                         title_hash=title_hash)
        try:
            t = self.do("freshservice", "create_ticket", f"ticket:{system}:{op}",
                        title=note["ticket_title"],
                        body=note["ticket_body"] + "\nNext: " + note["suggested_next_step"], kind="incident")
            ticket_id = t["ticket_id"]
        except PermanentError as ticket_error:
            ticket_id = None
            self.audit.write(self.id, "orchestrator", "escalation.ticket_failed", system=system,
                             error=str(ticket_error))
        self.failures.append({"system": system, "op": op, "error": str(err), "ticket": ticket_id})
        self.audit.write(self.id, "orchestrator", "escalated", ticket=ticket_id, system=system)


def _redact(params: dict) -> dict:
    # Keep audit records useful for operations without copying identity or free text.
    sensitive = {"profile", "to", "body", "text", "title", "login", "candidate", "email"}
    return {k: ("<redacted>" if k.lower() in sensitive else v) for k, v in params.items()}


def run_onboarding(event: dict, *, planner: Planner, systems: dict[str, SimulatedSystem], audit: AuditLog,
                   today: date | None = None, sleep=time.sleep) -> dict:
    today = today or date.today()
    event_id = event["event_id"]
    if event_id in RUNS:                                   # webhook redelivery / duplicate event
        audit.write(RUNS[event_id]["run_id"], "orchestrator", "duplicate_event_ignored", event_id=event_id)
        return {**RUNS[event_id], "duplicate": True}

    run = Run(f"run_{event_id[-8:]}", systems, audit, planner, sleep)
    hire = intake.minimize(event)
    audit.write(run.id, "orchestrator", "intake.received", event_type=event["event_type"],
                worker_id=hire["worker_id"], policy_version=policy.POLICY_VERSION)

    problems = intake.validate(hire, today)
    if problems:                                           # never guess on missing data
        t = run.do("freshservice", "create_ticket", "ticket:intake", title=
                   f"[Onboarding] Incomplete Workday record {hire['worker_id']}",
                   body="; ".join(problems), kind="request")
        audit.write(run.id, "orchestrator", "intake.rejected", problems=problems, ticket=t["ticket_id"])
        return _finish(event_id, run, hire, "NEEDS_INFO", [], [], {}, audit)

    # ---- 1. LLM proposes ----------------------------------------------------
    plan = planner.propose(hire)
    audit.write(run.id, f"llm:{planner.model}", "plan.proposed", prompt_version=PROMPT_VERSION,
                plan_hash=plan_hash(plan), requested=[e["id"] for e in plan["entitlements"]],
                flag_count=len(plan["flags"]))

    # ---- 2. deterministic policy gate --------------------------------------
    decisions = policy.evaluate(hire, [e["id"] for e in plan["entitlements"]])
    for d in decisions:
        audit.write(run.id, "policy-engine", "decision", id=d["id"], verdict=d["verdict"], reason=d["reason"],
                    policy_version=policy.POLICY_VERSION)
    allowed = [d for d in decisions if d["verdict"] == "ALLOW"]
    pending = [d for d in decisions if d["verdict"] == "NEEDS_APPROVAL"]
    denied = [d for d in decisions if d["verdict"] == "DENY"]

    # ---- 3. execute (Okta first: it is the control plane and the critical step) ----
    login = None
    for cand in plan["username_candidates"]:
        try:
            u = run.do("okta", "create_user", f"create:{cand}", login=f"{cand}@{DOMAIN}",
                       worker_id=hire["worker_id"], profile={"title": hire["job_title"], "dept": hire["department"]})
            login = u["login"]
            target = {"login": login, "start_date": hire["start_date"], "run_id": run.id,
                      "worker_id": hire["worker_id"]}
            ACTIVATION_TARGETS[event_id] = target
            if STATE_STORE:
                STATE_STORE.save_activation_target(event_id, target)
            break
        except ConflictError:
            audit.write(run.id, "orchestrator", "okta.username_conflict",
                        candidate_hash=plan_hash({"candidate": cand}))
        except PermanentError as e:
            run.escalate("okta", "create_user", e, hire, [])
            break
    if not login:
        if not run.failures:  # all candidates conflicted
            run.escalate("okta", "create_user", ConflictError("all username candidates taken"), hire, [])
        return _finish(event_id, run, hire, "FAILED", allowed, pending, denied, audit, plan)

    run.compensations.append(("okta", login, []))
    by_system: dict[str, list[str]] = {}
    for d in allowed:
        by_system.setdefault(d["system"], []).append(d["id"])
    for system in ("okta", "google", "slack", "jira"):      # non-critical: continue on failure
        items = by_system.get(system, [])
        if not items:
            continue
        try:
            run.do(system, "add_grants", f"grants:{system}:{plan_hash(items)}", login=login, items=items)
            run.compensations.append((system, login, items))
        except PermanentError as e:
            run.escalate(system, "add_grants", e, hire, items)

    # ---- 4. humans-in-the-loop + content ------------------------------------
    for d in pending:
        try:
            t = run.do("freshservice", "create_ticket", f"approval:{d['id']}",
                       title=f"[Approval] {d['id']} for {hire['first_name']} {hire['last_name']}",
                       body=f"{d['reason']}. Approvers: {', '.join(d['approvers'])}", kind="approval")
            request = {"system": d["system"], "approvers": d["approvers"], "ticket_id": t["ticket_id"],
                       "approvals": {}, "state": "pending"}
            PENDING_APPROVALS[(event_id, d["id"])] = request
            if STATE_STORE:
                STATE_STORE.save_approval(event_id, d["id"], request)
            audit.write(run.id, "orchestrator", "approval.requested", id=d["id"], ticket=t["ticket_id"])
        except PermanentError as e:
            run.escalate("freshservice", "approval_ticket", e, hire, [d["id"]])
    try:
        run.do("freshservice", "create_ticket", "ticket:day1", title=
               f"[Day-1] Hardware & setup: {hire['first_name']} {hire['last_name']}",
               body="\n".join(plan["day_one_checklist"]), kind="request")
    except PermanentError as e:
        run.escalate("freshservice", "day1_ticket", e, hire, [])
    try:
        run.do("slack", "notify", "welcome", to=hire["manager_email"], text=plan["welcome_message"])
        audit.write(run.id, "orchestrator", "welcome.sent", to_role="manager")
    except PermanentError as e:
        run.escalate("slack", "notify_welcome", e, hire, [])

    status = "COMPLETE" if not run.failures and not pending else ("PARTIAL" if run.failures else "AWAITING_APPROVAL")
    return _finish(event_id, run, hire, status, allowed, pending, denied, audit, plan)


def activate_onboarding_run(event_id: str, *, systems: dict[str, SimulatedSystem], audit: AuditLog,
                            today: date | None = None, sleep=time.sleep) -> dict:
    """Activate the staged Okta user on or after the Workday start date; safe to replay."""
    target = ACTIVATION_TARGETS.get(event_id)
    if not target:
        raise ValueError("no staged user associated with event_id")
    run_result = RUNS.get(event_id)
    if not run_result or run_result["status"] in {"FAILED", "NEEDS_INFO", "ROLLED_BACK"}:
        raise ValueError("onboarding run is not eligible for activation")
    today = today or date.today()
    start_date = date.fromisoformat(target["start_date"])
    if today < start_date:
        raise ValueError(f"activation is scheduled for {start_date.isoformat()}")
    run = Run(target["run_id"], systems, audit, Planner(live=False), sleep)
    result = run.do("okta", "activate_user", "activate", login=target["login"])
    audit.write(target["run_id"], "orchestrator", "activation.completed", event_id=event_id,
                status=result["status"])
    return {"event_id": event_id, "status": result["status"], "login": result["login"]}


def record_access_approval(event_id: str, entitlement_id: str, role: str, approved: bool, reason: str,
                           actor: str, *, systems: dict[str, SimulatedSystem], audit: AuditLog,
                           sleep=time.sleep) -> dict:
    """Record one approver decision and grant only after every required role approves."""
    result = RUNS.get(event_id)
    request = PENDING_APPROVALS.get((event_id, entitlement_id))
    if not result or not request:
        raise ValueError("no pending approval for this event and entitlement")
    if request["state"] not in {"pending", "approved_grant_failed"}:
        return {"event_id": event_id, "entitlement_id": entitlement_id, "state": request["state"]}
    if role not in request["approvers"]:
        raise PermissionError("approver role is not required for this entitlement")
    prior = request["approvals"].get(role)
    decision = {"approved": bool(approved), "reason": reason, "actor": actor}
    if prior:
        if prior != decision:
            raise ValueError("this role has already submitted a different decision")
    else:
        if any(entry["actor"] == actor for entry in request["approvals"].values()):
            raise PermissionError("manager and app-owner approvals must come from different people")
        request["approvals"][role] = decision

    if not approved:
        request["state"] = "denied"
    elif all(r in request["approvals"] and request["approvals"][r]["approved"]
             for r in request["approvers"]):
        request["state"] = "approved"
    if STATE_STORE:
        STATE_STORE.save_approval(event_id, entitlement_id, request)
    run_id = result["run_id"]
    audit.write(run_id, actor, "approval.decision", entitlement_id=entitlement_id, role=role,
                approved=bool(approved), reason=reason, state=request["state"])

    if request["state"] == "denied":
        result["pending_approval"].remove(entitlement_id)
        result["denied"].append({"id": entitlement_id, "reason": f"approval denied by {role}: {reason}"})
    elif request["state"] in {"approved", "approved_grant_failed"}:
        target = ACTIVATION_TARGETS.get(event_id)
        if not target:
            raise ValueError("staged account state is missing; cannot apply approved access")
        run = Run(run_id, systems, audit, Planner(live=False), sleep)
        if entitlement_id in result["pending_approval"]:
            result["pending_approval"].remove(entitlement_id)
        try:
            run.do(request["system"], "add_grants", f"approved:{entitlement_id}",
                   login=target["login"], items=[entitlement_id])
            request["state"] = "granted"
            result["granted"].append(entitlement_id)
            result["pending_provision"] = [e for e in result.get("pending_provision", []) if e != entitlement_id]
        except PermanentError as e:
            request["state"] = "approved_grant_failed"
            result.setdefault("pending_provision", []).append(entitlement_id)
            hire = {"worker_id": target.get("worker_id", "unknown")}
            run.escalate(request["system"], "approved_grant", e, hire, [entitlement_id])
            result["failures"].extend(run.failures)
        if STATE_STORE:
            STATE_STORE.save_approval(event_id, entitlement_id, request)

    if not result["pending_approval"]:
        result["status"] = "PARTIAL" if result["failures"] or result.get("pending_provision") else "COMPLETE"
    if STATE_STORE:
        STATE_STORE.save_run(event_id, result)
    audit.write(run_id, "orchestrator", "approval.updated", entitlement_id=entitlement_id,
                state=request["state"], run_status=result["status"])
    return {"event_id": event_id, "entitlement_id": entitlement_id,
            "state": request["state"], "run_status": result["status"]}


def retry_approved_grant(event_id: str, entitlement_id: str, *, systems: dict[str, SimulatedSystem],
                         audit: AuditLog, sleep=time.sleep) -> dict:
    """Retry a grant after its required human approvals were already recorded."""
    request = PENDING_APPROVALS.get((event_id, entitlement_id))
    result = RUNS.get(event_id)
    target = ACTIVATION_TARGETS.get(event_id)
    if not request or request["state"] != "approved_grant_failed" or not result or not target:
        raise ValueError("no approved grant is waiting for retry")
    run = Run(result["run_id"], systems, audit, Planner(live=False), sleep)
    run.do(request["system"], "add_grants", f"approved:{entitlement_id}",
           login=target["login"], items=[entitlement_id])
    request["state"] = "granted"
    result["pending_provision"] = [e for e in result.get("pending_provision", []) if e != entitlement_id]
    if entitlement_id not in result["granted"]:
        result["granted"].append(entitlement_id)
    result["failures"] = [f for f in result["failures"]
                          if not (f.get("system") == request["system"] and f.get("op") == "approved_grant"
                                  and entitlement_id in f.get("items", []))]
    result["status"] = "PARTIAL" if result["failures"] or result["pending_provision"] else "COMPLETE"
    if STATE_STORE:
        STATE_STORE.save_approval(event_id, entitlement_id, request)
        STATE_STORE.save_run(event_id, result)
    audit.write(result["run_id"], "orchestrator", "approval.grant_retried",
                entitlement_id=entitlement_id, status="granted")
    return {"event_id": event_id, "entitlement_id": entitlement_id, "state": "granted",
            "run_status": result["status"]}


def rollback(run_id: str, systems: dict[str, SimulatedSystem], audit: AuditLog, compensations) -> None:
    """Saga compensation for rescinded hires: revoke grants in reverse, suspend the Okta user."""
    for system, login, items in reversed(compensations):
        if items:
            systems[system].call("revoke", f"{run_id}:rb:{system}", login=login, items=items)
        elif system == "okta":
            systems["okta"].call("suspend_user", f"{run_id}:rb:suspend", login=login)
        audit.write(run_id, "orchestrator", "rollback.step", system=system, items=items)
    for event_id, result in RUNS.items():
        if result["run_id"] == run_id:
            result["status"] = "ROLLED_BACK"
            if STATE_STORE:
                STATE_STORE.save_run(event_id, result)


def _finish(event_id, run, hire, status, allowed, pending, denied, audit, plan=None):
    result = {
        "run_id": run.id, "status": status, "worker": f"{hire.get('first_name')} {hire.get('last_name')}",
        "granted": [d["id"] for d in allowed], "pending_approval": [d["id"] for d in pending],
        "denied": [{"id": d["id"], "reason": d["reason"]} for d in denied],
        "failures": run.failures, "retried_steps": [s for s in run.steps if s["attempts"] > 1],
        "flags": (plan or {}).get("flags", []), "compensations": run.compensations,
        "pending_provision": [],
    }
    audit.write(run.id, "orchestrator", "run.finished", status=status, granted=len(allowed),
                pending=len(pending), denied=len(denied), failures=len(run.failures))
    RUNS[event_id] = result
    if STATE_STORE:
        STATE_STORE.save_run(event_id, result)
    return result
