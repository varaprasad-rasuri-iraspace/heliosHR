# 4. Executive Summary — for the Head of People Ops

*(Two slides' worth. Copy each section onto a slide.)*

---

## Slide 1 — What it does and what it improves

### "New hires start Day 1 with everything working."

**Today:** HR posts in Slack → IT manually creates accounts in five tools → 3–5 business days, errors, people start without access.

**With this system:**
1. **You hire someone in Workday.** That's the only step HR does.
2. **The system notices automatically** and an AI assistant reads the role (title, team, location, manager) and drafts the right set of tool access, like a careful IT colleague would.
3. **Hard-coded company rules check every request.** Standard access is granted automatically. Sensitive access (e.g. production systems) goes to the manager and the system owner for approval. Admin-level access is never granted automatically.
4. **Accounts are created in Okta, Google, Slack and Jira within about an hour**, kept dormant, and **switch on the morning the person starts**.
5. **IT gets a hardware ticket, the manager gets a ready-to-send welcome note.**
6. **Everything is recorded** in a tamper-proof log: who got what, why, and who approved it.

### How we'll know it worked

| Measure | Today (est.) | Target |
|---|---|---|
| Time from hire entered to accounts ready | 3–5 business days | **under 1 hour** (live on start date) |
| New hires with full access on Day 1 | not tracked | **≥ 98%** |
| IT hands-on time per hire | hours | **minutes** (exceptions only) |
| Access-related tickets in a new hire's first 30 days | baseline in month 1 | **−70%** |
| Audit evidence assembly | manual, days | **instant report** |
| New-hire "I had what I needed on Day 1" survey | baseline | **≥ 4.5 / 5** |

---

## Slide 2 — Risks, limits, and why you can trust it

### What you need to know to feel confident

- **The AI suggests; the rules decide.** The AI can't create or approve anything itself. It has no passwords. Anything outside the approved access catalog is automatically blocked. We tested a case where HR notes tried to get "admin on everything": it was blocked and flagged.
- **Humans stay in charge of anything sensitive.** Managers and system owners approve elevated access. You'll see a weekly summary of exceptions, not every hire.
- **If something breaks, it tells us, and doesn't make things worse.** Temporary glitches retry automatically. Real failures create a clear IT ticket and an alert; the rest of the hire still proceeds. A cancelled hire can be rolled back with one action.
- **Personal data is minimised.** Salary, ID numbers, birth dates and addresses never leave Workday for this process.
- **Full audit trail** for compliance (SOC 2 / ISO) with a tamper-evident record.

### Honest limitations

- **Quality depends on Workday data.** If a manager or start date is missing, the system stops and asks HR instead of guessing. Clean HR data in = good access out.
- **The access catalog needs an owner.** Security and department heads must agree what each role gets. This is a one-time workshop, then light upkeep.
- **Not covered yet:** hardware shipping, leavers and internal moves (planned next), and tools outside the five in scope.
- **A pilot is required.** We'd run in "recommend-only" mode for the first 2–3 hires (system proposes, IT reviews) before switching on automatic provisioning.

### What we need from you
1. Name an owner for the role-to-access catalog (with Security).
2. Confirm Workday can send a "hire completed" notification.
3. Agree the pilot: 2 weeks, every new hire, success measures above.
