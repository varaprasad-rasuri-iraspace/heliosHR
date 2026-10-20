"""Access policy: the deterministic gate between the LLM's proposal and any write.

Tiers
  0  baseline        -> always granted automatically
  1  role-based      -> auto if within the hire's department scope
  2  sensitive       -> human approval (manager + app owner)
  3  never automated -> always denied here; goes through a manual, ticketed process

In production this catalog lives in git (policy-as-code), reviewed by Security,
and is versioned; the policy version is stamped on every audit record.
"""
from __future__ import annotations

from dataclasses import dataclass

POLICY_VERSION = "2026.10.1"


@dataclass(frozen=True)
class Entitlement:
    id: str
    system: str
    tier: int
    description: str
    departments: tuple[str, ...] = ()   # empty = any department
    contractor_ok: bool = True


_E = Entitlement
CATALOG: dict[str, Entitlement] = {e.id: e for e in [
    # tier 0 - everyone
    _E("okta:group:everyone", "okta", 0, "Base SSO group"),
    _E("google:group:all-staff", "google", 0, "All-staff mailing list / Drive baseline"),
    _E("slack:channel:general", "slack", 0, "#general"),
    _E("slack:channel:new-hires", "slack", 0, "#new-hires"),
    _E("jira:project:HELP", "jira", 0, "Internal help project (read/create)"),
    # tier 1 - engineering
    _E("okta:group:eng", "okta", 1, "Engineering SSO group", ("Engineering",)),
    _E("google:group:eng", "google", 1, "eng@ list", ("Engineering",)),
    _E("slack:channel:eng", "slack", 1, "#eng", ("Engineering",)),
    _E("slack:channel:eng-oncall", "slack", 1, "#eng-oncall", ("Engineering",), contractor_ok=False),
    _E("jira:project:PLAT", "jira", 1, "Platform project", ("Engineering",)),
    # tier 1 - customer success
    _E("okta:group:cs", "okta", 1, "CS SSO group", ("Customer Success",)),
    _E("slack:channel:cs-emea", "slack", 1, "#cs-emea", ("Customer Success",)),
    _E("slack:channel:cs-na", "slack", 1, "#cs-na", ("Customer Success",)),
    _E("jira:project:CSOPS", "jira", 1, "CS ops project", ("Customer Success",)),
    # tier 1 - sales
    _E("okta:group:sales", "okta", 1, "Sales SSO group", ("Sales",)),
    _E("slack:channel:sales", "slack", 1, "#sales", ("Sales",)),
    _E("slack:channel:deals", "slack", 1, "#deals", ("Sales",), contractor_ok=False),
    _E("jira:project:SALESOPS", "jira", 1, "Sales ops project", ("Sales",)),
    # tier 2 - needs human approval
    _E("jira:project:SEC", "jira", 2, "Security findings project"),
    _E("aws:role:prod-readonly", "aws", 2, "Read-only prod AWS role"),
    # tier 3 - never automated
    _E("salesforce:profile:admin", "salesforce", 3, "Salesforce System Administrator"),
    _E("jira:role:site-admin", "jira", 3, "Atlassian site admin"),
    _E("okta:group:super-admins", "okta", 3, "Okta super admins"),
]}

BASELINE_IDS = [e.id for e in CATALOG.values() if e.tier == 0]

DEPT_DEFAULTS = {
    "Engineering": ["okta:group:eng", "google:group:eng", "slack:channel:eng", "jira:project:PLAT"],
    "Customer Success": ["okta:group:cs", "jira:project:CSOPS"],
    "Sales": ["okta:group:sales", "slack:channel:sales", "jira:project:SALESOPS"],
}


def catalog_for_prompt() -> list[dict]:
    """What the LLM is allowed to see. Tier-3 is deliberately not advertised."""
    return [
        {"id": e.id, "tier": e.tier, "description": e.description, "departments": list(e.departments)}
        for e in CATALOG.values() if e.tier < 3
    ]


def evaluate(hire: dict, requested_ids: list[str]) -> list[dict]:
    """Return one decision per entitlement: ALLOW | NEEDS_APPROVAL | DENY."""
    dept = hire.get("department")
    contractor = hire.get("worker_type") == "contractor"
    decisions: list[dict] = []
    seen: set[str] = set()

    for eid in list(dict.fromkeys(requested_ids)) + [b for b in BASELINE_IDS if b not in requested_ids]:
        if eid in seen:
            continue
        seen.add(eid)
        ent = CATALOG.get(eid)
        d = {"id": eid, "system": ent.system if ent else "unknown", "approvers": []}
        if ent is None:
            d.update(verdict="DENY", reason="not in access catalog (possible hallucination or injection)")
        elif ent.tier == 3:
            d.update(verdict="DENY", reason="tier 3: never automated; use manual privileged-access request")
        elif contractor and not ent.contractor_ok:
            d.update(verdict="DENY", reason="not permitted for contractors")
        elif ent.tier == 2:
            d.update(verdict="NEEDS_APPROVAL", reason="tier 2: sensitive access",
                     approvers=["manager", "app-owner"])
        elif ent.tier == 1 and ent.departments and dept not in ent.departments:
            d.update(verdict="NEEDS_APPROVAL", reason=f"cross-department request ({dept} -> {ent.departments[0]})",
                     approvers=["manager", "app-owner"])
        else:
            d.update(verdict="ALLOW", reason="mandatory baseline" if eid not in requested_ids else "within policy")
        decisions.append(d)
    return decisions
