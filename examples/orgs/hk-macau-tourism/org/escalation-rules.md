# Escalation Rules Engine

## Overview
The escalation router is a rules-based triage system that determines whether an issue can be resolved at the manager level or must escalate to the founder. It processes structured escalation requests and returns a routing decision.

---

## Escalation Request Format

Every escalation must be submitted as a structured object:

```json
{
  "escalation_id": "ESC-2026-0001",
  "timestamp": "2026-04-11T14:30:00Z",
  "source_agent": "Payment Agent",
  "source_manager": "Product Manager",
  "category": "payment_failure",
  "severity": "high",
  "summary": "Stripe gateway returning 502 errors for Alipay transactions. 8 failed transactions in last hour totaling $1,200 USD.",
  "amount_involved_usd": 1200,
  "tourists_affected": 8,
  "attempted_resolution": "Checked Stripe status page (no outage). Retried with fresh API keys. Issue persists.",
  "time_sensitive": true,
  "safety_related": false
}
```

---

## Routing Rules (evaluated in priority order)

### Rule 1: IMMEDIATE FOUNDER ESCALATION
**Condition**: `safety_related == true`
**Action**: Route to founder immediately. No manager deliberation.
**Examples**: Tourist medical emergency, scam targeting tourists, dangerous partner activity, safety incident at partner venue.
**Response time**: Founder notified within 5 minutes.

### Rule 2: IMMEDIATE FOUNDER ESCALATION
**Condition**: `category == "security_incident"`
**Action**: Route to founder immediately. Simultaneously notify Product Manager and Ops Manager.
**Examples**: Data breach, unauthorized access, payment data exposure, vulnerability under active exploit.
**Response time**: Founder notified within 5 minutes. Containment actions authorized without waiting.

### Rule 3: IMMEDIATE FOUNDER ESCALATION
**Condition**: `category == "political_sensitivity"`
**Action**: Route to founder. Content frozen (not published/unpublished) until founder decides.
**Examples**: Content about China/HK/Macau sovereignty, protests, political figures, border disputes, content flagged by external party as politically problematic.
**Response time**: Founder reviews within 24 hours. Content stays frozen until resolved.

### Rule 4: BUDGET THRESHOLD
**Condition**: `amount_involved_usd > 200` (single transaction) OR `recurring_monthly_usd > 100`
**Action**: Route to founder for approval.
**Manager pre-work required**: Manager must include cost-benefit analysis and recommendation.
**Examples**: Custom partner deal, tool subscription, refund above CX Manager authority, marketing spend.

### Rule 5: REFUND THRESHOLD
**Condition**: `category == "refund"` AND `amount_involved_usd > 150`
**Action**: Route to founder. CX Manager must include refund justification and tourist interaction history.
**Note**: Refunds ≤ $150 are within CX Manager authority.

### Rule 6: PAYMENT FAILURE THRESHOLD
**Condition**: `category == "payment_failure"` AND (`tourists_affected > 5` OR `amount_involved_usd > 500`)
**Action**: Route to founder. Product Manager must include root cause analysis (or best current theory) and mitigation status.

### Rule 7: DOWNTIME THRESHOLD
**Condition**: `category == "downtime"` AND `duration_minutes > 30`
**Action**: Route to founder. Product Manager must include incident timeline, impact assessment, and recovery ETA.

### Rule 8: REGULATORY ISSUE
**Condition**: `category == "regulatory"` AND `severity in ["high", "critical"]`
**Action**: Route to founder. Ops Manager + Compliance Agent must include regulation reference, impact assessment, and recommended action with compliance posture (conservative recommendation preferred).
**Note**: Low/medium regulatory items are resolved by Ops Manager + Compliance Agent and logged.

### Rule 9: MANAGER DEADLOCK
**Condition**: `category == "deadlock"` AND `rounds >= 2`
**Action**: Route to founder after 2 rounds of manager disagreement.
**Required**: Both managers' positions summarized, with supporting evidence from each side.
**Examples**: Content Manager and Ops Manager disagree on partner communication tone, Product Manager and CX Manager disagree on feature priority.

### Rule 10: PARTNER DISPUTE
**Condition**: `category == "partner_dispute"` AND (`resolution_attempts >= 2` OR `involves_legal_threat`)
**Action**: Route to founder. Ops Manager must include dispute history, partner's position, and recommended resolution.

### Rule 11: REPUTATION THREAT
**Condition**: `category == "reputation"` AND (`negative_reviews >= 3` in 7 days OR `media_mention` OR `formal_complaint`)
**Action**: Route to founder. CX Manager must include review/complaint details, root cause analysis, and proposed response.

### Rule 12: NOVEL SITUATION
**Condition**: `category == "novel"` — situation not covered by any existing rule or SOP
**Action**: Route to founder. Submitting manager must describe why this is novel and propose a handling approach.
**Note**: When the founder's decision should bind future occurrences, add it to the knowledge base (`opc kb add` with `source_task` set to this task ID) so the next agent finds the ruling without re-escalating.

---

## Manager-Resolvable Issues (NOT escalated to founder)

The following categories are handled by the relevant manager without founder involvement:

| Category | Resolver | Conditions |
|----------|----------|------------|
| Content revision | Content Manager | Non-sensitive content, factual disputes resolved within 2 rounds |
| Standard refunds | CX Manager | Amount ≤ $150 USD |
| Minor bugs | Product Manager | No data loss, no security impact, <5 users affected |
| Standard partner onboarding | Ops Manager | Standard terms, 10-20% commission, ≤ 3 month contract |
| Routine compliance checks | Ops Manager + Compliance Agent | No violations found, or low-severity items with clear remediation |
| Feature requests | Product Manager | Within existing technical architecture and budget |
| SEO changes | Content Manager | No content substance changes, metadata/schema only |
| Support inquiries | CX Manager | Standard booking questions, modifications, cancellations within policy |

---

## Peer Audit Triggers

Beyond escalation routing, certain actions trigger mandatory peer audit:

| Action | Peer Auditor | Trigger |
|--------|-------------|---------|
| Partner communication sent | Content Manager | Any external-facing partner email or message (brand voice check) |
| Feature request accepted | Product Manager | CX Manager submits feature request (feasibility check) |
| Payment flow change | Ops Manager + Compliance Agent | Any modification to payment processing (PCI/regulatory check) |
| Content published | CX Manager | Spot-check 20% of published guides (tourist-friendliness check) |

---

## Escalation Response Format

The router returns:

```json
{
  "escalation_id": "ESC-2026-0001",
  "routing_decision": "founder",
  "rule_triggered": "Rule 6: Payment Failure Threshold",
  "priority": "high",
  "required_context": [
    "Root cause analysis from Product Manager",
    "Current mitigation status",
    "Number of affected tourists and total amount"
  ],
  "response_time_target": "2 hours",
  "interim_action": "Payment Agent to switch to backup gateway if available"
}
```

---

## Escalation Metrics (tracked weekly)

- Total escalations to founder
- Escalations by category
- Average resolution time
- Escalation accuracy (were they warranted?)
- False escalations (could have been resolved by manager)
- Missed escalations (should have been escalated but weren't — caught in audit)

Target: Founder handles ≤ 3 escalations per week during steady state.
