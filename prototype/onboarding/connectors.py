"""Simulated target systems with idempotency + fault injection.

Real mapping (see docs/03-implementation-spec.md):
  okta        POST /api/v1/users?activate=false ; PUT /api/v1/groups/{gid}/users/{uid}
  google      Admin SDK Directory: members.insert (accounts normally created via Okta SCIM)
  slack       SCIM POST /scim/v2/Users ; conversations.invite
  jira        Atlassian Admin API (group membership) / project role actors
  freshservice POST /api/v2/tickets
"""
from __future__ import annotations


class TransientError(Exception):
    """429 / 5xx / timeout: safe to retry."""
    retryable = True


class PermanentError(Exception):
    """403 / 404 / validation: retrying will not help."""
    retryable = False


class ConflictError(PermanentError):
    """409: resource exists and belongs to someone else."""


class SimulatedSystem:
    def __init__(self, name: str, script: dict[str, list[str]] | None = None):
        self.name = name
        self.script = {k: list(v) for k, v in (script or {}).items()}  # op -> fault queue
        self.users: dict[str, dict] = {}
        self.grants: dict[str, set[str]] = {}
        self.tickets: list[dict] = []
        self.messages: list[dict] = []
        self._idem: dict[str, dict] = {}

    def call(self, op: str, idem_key: str, **p) -> dict:
        if idem_key in self._idem:                       # idempotent replay: no side effects
            return {**self._idem[idem_key], "idempotent_replay": True}
        fault = self.script[op].pop(0) if self.script.get(op) else None
        if fault == "429":
            raise TransientError(f"{self.name}.{op}: HTTP 429 rate limited")
        if fault == "503":
            raise TransientError(f"{self.name}.{op}: HTTP 503")
        if fault == "403":
            raise PermanentError(f"{self.name}.{op}: HTTP 403 forbidden (service account lacks scope or project archived)")
        result = getattr(self, f"_op_{op}")(**p)
        self._idem[idem_key] = result
        return result

    # --- operations -------------------------------------------------------
    def _op_create_user(self, login: str, worker_id: str, profile: dict) -> dict:
        existing = self.users.get(login)
        if existing and existing["worker_id"] != worker_id:
            raise ConflictError(f"{self.name}.create_user: HTTP 409 login '{login}' taken by another worker")
        self.users[login] = {"worker_id": worker_id, "status": "STAGED", "profile": profile}
        return {"id": f"{self.name}-{len(self.users):04d}", "login": login, "status": "STAGED"}

    def _op_add_grants(self, login: str, items: list[str]) -> dict:
        self.grants.setdefault(login, set()).update(items)
        return {"granted": sorted(items)}

    def _op_revoke(self, login: str, items: list[str]) -> dict:
        self.grants.get(login, set()).difference_update(items)
        return {"revoked": sorted(items)}

    def _op_suspend_user(self, login: str) -> dict:
        if login in self.users:
            self.users[login]["status"] = "SUSPENDED"
        return {"login": login, "status": "SUSPENDED"}

    def _op_activate_user(self, login: str) -> dict:
        if login not in self.users:
            raise PermanentError(f"{self.name}.activate_user: user '{login}' not found")
        self.users[login]["status"] = "ACTIVE"
        return {"login": login, "status": "ACTIVE"}

    def _op_create_ticket(self, title: str, body: str, kind: str) -> dict:
        tid = f"FS-{1000 + len(self.tickets)}"
        self.tickets.append({"id": tid, "title": title, "body": body, "kind": kind})
        return {"ticket_id": tid}

    def _op_notify(self, to: str, text: str) -> dict:
        self.messages.append({"to": to, "text": text})
        return {"delivered": True}


def build_systems(scripts: dict[str, dict] | None = None) -> dict[str, SimulatedSystem]:
    scripts = scripts or {}
    return {n: SimulatedSystem(n, scripts.get(n))
            for n in ("okta", "google", "slack", "jira", "aws", "freshservice")}
