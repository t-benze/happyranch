# Founder Interaction Model (Feishu)

How the founder communicates with the organization — bot architecture, group chats, notification tiers, reply parsing, and quick commands.

---

## 1. Overview

The founder is the final authority for escalations, approvals, and novel decisions. The founder interacts with the organization via Feishu, using a hybrid bot architecture: each manager agent has its own **custom webhook bot** (with its own name and avatar) for sending messages, while a single **app bot** handles all inbound messages from the founder. This gives you the feel of chatting with 4 distinct managers, without needing 4 separate full Feishu apps.

---

## 2. Hybrid Bot Architecture

```
OUTBOUND (agents → founder):
  4 custom webhook bots (one per manager, each with own name + avatar)
  └── Content Manager Bot  ──→  posts to group chats / sends cards
  └── Product Manager Bot  ──→  posts to group chats / sends cards
  └── Ops Manager Bot      ──→  posts to group chats / sends cards
  └── CX Manager Bot       ──→  posts to group chats / sends cards

INBOUND (founder → agents):
  1 full app bot ("OPC Hub")
  └── Receives all founder messages via Event Subscription
  └── Parses which manager is being addressed
  └── Routes to orchestrator → spawns agent session → replies via that manager's webhook
```

**Why this hybrid?** Feishu custom webhook bots can send messages with their own identity (name + avatar) but cannot receive or listen for replies. The full app bot ("OPC Hub") fills that gap — it receives all founder messages and the orchestrator routes them to the right manager agent session. The response comes back through that manager's webhook bot, so visually the founder sees a reply from "Content Manager" or "CX Manager," not from a generic bot.

```
Founder types: "@Content Manager what's the status of the Shenzhen guide?"
    │
    ▼
OPC Hub app bot receives the message (Event Subscription)
    │
    ▼
Orchestrator parses target: "Content Manager"
    │
    ▼
Orchestrator spins up Content Manager agent session with context
    │
    ▼
Content Manager agent produces response
    │
    ▼
Orchestrator sends response via Content Manager webhook bot
    │
    ▼
Founder sees reply from "📝 Content Manager" in the group chat
    │
    ▼
Founder replies again → OPC Hub receives → cycle continues
```

---

## 3. Feishu Group Chat Structure

Each manager gets a dedicated group chat where the founder can have ongoing conversations with that manager. The custom webhook bot for that manager is the primary poster. The OPC Hub app bot is also in every group to receive founder messages.

```
Feishu workspace
│
├── 📝 Content Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + Content Manager webhook bot
│   ├── Daily reports from Content Manager
│   ├── Founder ↔ Content Manager conversations
│   └── Content-related escalations and approvals
│
├── ⚙️ Product Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + Product Manager webhook bot
│   ├── Daily reports from Product Manager
│   ├── Founder ↔ Product Manager conversations
│   └── Product/payment-related escalations
│
├── 🤝 Ops Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + Ops Manager webhook bot
│   ├── Daily reports from Ops Manager
│   ├── Founder ↔ Ops Manager conversations
│   └── Partner/compliance-related escalations
│
├── 💬 CX Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + CX Manager webhook bot
│   ├── Daily reports from CX Manager
│   ├── Founder ↔ CX Manager conversations
│   └── Support/refund-related escalations
│
├── 🔴 OPC Urgent (group chat)
│   ├── Members: Founder + OPC Hub bot + all 4 webhook bots
│   └── Tier 1 only — safety, security, downtime
│
├── 📊 OPC Weekly (group chat)
│   ├── Members: Founder + OPC Hub bot
│   └── Tier 4 — weekly org-wide summary
│
└── 🔧 OPC System (group chat)
    ├── Members: Founder + OPC Hub bot
    └── Bot health, errors, system notifications
```

This structure means when the founder opens their Feishu, they see group chats named after their managers — just like having 4 direct reports in a real company. Each conversation stays in its domain.

---

## 4. Notification Tiers

### Tier 1: Immediate (urgent push notification)
**Triggers**: Safety incidents, security breaches, system downtime >30min
**Feishu behavior**: Posted to **OPC Urgent** group by the relevant manager's webhook bot. Interactive card with red severity indicator. @mentions the founder. If no response in 15 minutes, re-sends with escalated urgency.
**Expected response time**: < 30 minutes
**Timeout behavior**: Re-notify every 15 minutes. After 1 hour with no response, the orchestrator takes the most conservative safe action available (e.g., pause the affected service, freeze the affected content) and logs what it did.

**Example (posted by CX Manager webhook bot in OPC Urgent):**
```
🔴 URGENT ESCALATION — Safety Incident

Source: Support Agent → CX Manager → Founder
Category: Tourist Safety
Severity: Critical

Summary: Tourist reports being directed to unlicensed tour
operator by our partner "Macau Adventure Tours". Tourist
is currently at the location and feels unsafe.

Agent recommendation: Immediately suspend Macau Adventure
Tours from the platform. Provide tourist with emergency
contacts and legitimate alternative.

Reply:
  • "approved" — suspend partner, assist tourist
  • "deny" — explain alternative action
  • or ask a question for more context
```

### Tier 2: Standard (normal notification)
**Triggers**: Budget approvals >$200, refunds >$150, partner disputes, regulatory ambiguity, manager deadlocks, content with potential political sensitivity
**Feishu behavior**: Posted to the **relevant manager's group chat** by that manager's webhook bot. Standard interactive card, no urgent flag.
**Expected response time**: < 24 hours
**Timeout behavior**: Reminder at 12 hours. Second reminder at 20 hours. At 24 hours, re-notify with "escalation aging" flag. Task remains blocked but other work continues.

**Example (posted by CX Manager webhook bot in CX Manager group chat):**
```
🟡 APPROVAL NEEDED — Refund Request

Source: Support Agent → CX Manager → Founder
Category: Refund (above $150 threshold)
Amount: $280 USD

Summary: Tourist booked Hong Kong harbor tour ($280) but
ferry service was cancelled due to typhoon signal. Partner
"HK Harbor Cruises" confirms cancellation. Tourist
requesting full refund. CX Manager recommends full refund
— force majeure, tourist has no fault.

Tourist satisfaction score: 4.5/5 (loyal customer)
Partner SLA status: In compliance (weather exception)

Reply:
  • "approved" — full refund of $280
  • "partial [amount]" — partial refund
  • "deny" — explain reason
  • or ask a question
```

### Tier 3: Daily Manager Reports
**Triggers**: Each manager agent sends a daily report to the founder.
**Feishu behavior**: Posted to that **manager's group chat** by the manager's webhook bot. The founder can reply in-thread to ask follow-up questions, give new instructions, or adjust priorities — the OPC Hub app bot receives the reply, the orchestrator spins up the manager agent session, and the response comes back via the webhook bot. The conversation continues in-thread until resolved.
**Delivery schedule**: Daily at 9:00 AM (configurable per manager, staggered to avoid a wall of messages).

**Example — posted by Content Manager webhook bot in Content Manager group chat:**
```
📝 Daily Report — Apr 11, 2026

Completed yesterday:
  • Macau visa guide updated (QA passed, published)
  • 2 blog posts in review: "Alipay setup for tourists",
    "HK Airport Express tips"

In progress:
  • Shenzhen day trip guide — Content Writer drafting,
    ETA tomorrow
  • SEO audit of transport guides — 60% complete

Blocked:
  • None

Team health:
  Content Writer: Green (92%) — on track
  QA Agent: Green (96%)
  SEO Agent: Green (91%)

Upcoming this week:
  • Zhuhai border crossing guide (starts Wed)
  • Monthly content freshness review (Fri)

Questions or new priorities? Reply here and I'll adjust.
```

**Example — posted by CX Manager webhook bot in CX Manager group chat:**
```
💬 Daily Report — Apr 11, 2026

Support volume (last 24h): 47 inquiries
  Resolved: 41 (87%)
  Escalated to me: 4
  Pending: 2

Top tourist questions:
  1. "Can I use Alipay without Chinese bank account?" (12x)
  2. "How to get from HK airport to Macau?" (8x)
  3. "Is Macau visa-free for US citizens?" (6x)

Issues flagged:
  • Question #1 is spiking — current guide may be outdated
    since Alipay updated foreign card support last week.
    I've created a content ticket for Content Manager.

Refunds processed: 1 ($85, within my authority)
Refunds pending your approval: 0

Support Agent: Yellow (82%) — resolution rate improving
  (was 78% last week)

Reply here if you want to dig into anything.
```

The founder can reply in any manager's daily report thread:
- "Tell me more about the Alipay question spike" → OPC Hub receives → orchestrator spins up CX Manager → CX Manager webhook bot replies in-thread
- "Reprioritize — I want the Zhuhai guide before Shenzhen" → OPC Hub receives → orchestrator spins up Content Manager → Content Manager webhook bot replies in-thread
- "Why is Support Agent still Yellow?" → CX Manager replies with detailed breakdown in-thread

These threads become ongoing conversations. The orchestrator preserves thread context so follow-up questions work naturally.

### Tier 4: Weekly Summary
**Triggers**: Weekly org-wide rollup — aggregated view across all managers
**Feishu behavior**: Posted to the **OPC Weekly** group chat by the OPC Hub bot. No push. The founder reads when convenient.
**Delivery schedule**: Weekly every Monday at 10:00 AM

**Example:**
```
📊 Weekly OPC Summary — Week of April 6, 2026

Team Health:
  Content accuracy: 94% ✓
  Payment success: 99.2% ✓
  Support resolution: 82% ⚠ (below 85% target, improving)
  Partner API uptime: 96% ⚠

Agent Scorecards:
  Content Writer: Green (92%)
  QA Agent: Green (96%)
  Support Agent: Yellow (82%) — up from 78%
  All others: Green

Escalations this week: 2
  • Refund $280 (approved by you on Apr 8)
  • Partner SLA dispute (resolved by Ops Manager)

Founder interactions this week: 5
  • 2 escalation approvals
  • 1 priority adjustment (via Content Manager chat)
  • 2 status inquiries

Open items: 0
```

---

## 5. Reply Parsing

The OPC Hub app bot receives all founder messages and parses them into structured decisions:

| Founder reply | Parsed as | Orchestrator action |
|---|---|---|
| "approved" / "yes" / "go ahead" / "ok" | `decision: approved` | Resume task with approval |
| "denied" / "no" / "reject" | `decision: denied` | Resume task with denial, agent adjusts |
| "approved, but [condition]" | `decision: approved_conditional` | Resume task with condition injected into context |
| "partial 150" (for refunds) | `decision: partial, amount: 150` | Resume task with partial approval |
| "why?" / "tell me more" / any question | `decision: needs_info` | Orchestrator gathers more context from the relevant agent and sends a follow-up message |
| "hold" / "let me think" / "later" | `decision: deferred` | Reset timeout clock, task stays waiting |
| No reply within timeout | `decision: timeout` | Follow timeout behavior for that tier |

If the bot can't confidently parse the reply, it asks for clarification: "I didn't understand that. Could you reply with 'approved', 'denied', or ask a specific question?"

---

## 6. Founder-Initiated Interaction

The founder doesn't only respond to escalations — they can reach into the system at any time to check on things, set goals, or have a conversation with any manager.

### Talking to a manager

The founder simply opens the relevant manager's group chat and posts a message:

```
Founder (in Content Manager group chat):
  What's the status of the Shenzhen day trip guide?
```

The OPC Hub receives this, the orchestrator identifies it's in the Content Manager group, spins up a Content Manager session with context, and the Content Manager webhook bot replies:

```
Content Manager:
  The Shenzhen day trip guide is currently with Content Writer,
  about 60% drafted. ETA for first draft is tomorrow morning.
  QA review scheduled for Tuesday. On track for Wednesday publication.
```

The founder can continue the conversation naturally:

```
Founder: Push that to top priority. I want it published by Wednesday.

Content Manager: Understood. I'll move it ahead of the Zhuhai guide
and brief the Content Writer now. The QA review will need to happen
Tuesday to hit Wednesday publication. I'll flag you if there's a
blocker.

Founder: Also — make sure it covers the new Shenzhen metro Line 20.
I saw it opened last week.

Content Manager: Good catch. I'll add that to the brief and make sure
QA verifies the station names and operating hours. Noted in the
knowledge base for future Shenzhen content.
```

Each reply from the founder spawns a new agent session for that manager with the full thread history as context. The manager agent can take real actions in response — updating task priorities, briefing worker agents, adding to the knowledge base — not just generating text.

### Setting goals and directives

The founder posts in the relevant manager's group chat:

```
Founder (in Ops Manager group chat):
  I want to onboard 3 new hotel partners in Macau this month.
  Focus on 4-star properties near the Cotai Strip. Budget up to
  $500 total for onboarding costs.

Ops Manager: I'll get Partner Liaison started on sourcing candidates.
Here's my plan:
  Week 1: Identify and vet 5-6 candidates (expect some won't qualify)
  Week 2: Begin onboarding top 3
  Week 3-4: Complete API integration and go live

I'll include progress in my daily reports. The $500 budget is within
your auto-approved threshold so I can proceed. Should I flag you
before committing to each partner, or only if something unusual
comes up?

Founder: Only flag me if something unusual comes up. You have
authority on standard terms.

Ops Manager: Confirmed. I'll proceed with standard terms (10-20%
commission, standard SLA, 3-month initial contract) and only
escalate for exceptions.
```

The orchestrator logs this directive as a standing instruction — it gets added to the Ops Manager's context for future sessions and tracked as a goal in the performance system.

### Quick commands (in any group chat or DM with OPC Hub)

For simple checks, the founder can use short commands:

| Command | Response |
|---|---|
| `status` | Current system status, pending approvals, active tasks |
| `scorecard content_writer` | Content Writer's current 30-day scorecard |
| `scorecard all` | Summary scorecards for all agents |
| `pending` | List all tasks waiting for founder input |
| `pause support_agent` | Pause the Support Agent (with confirmation) |
| `resume support_agent` | Resume a paused agent |
| `health` | Team health summary |

These are handled directly by the orchestrator without spinning up a full agent session — they're fast lookups from the database.

### When the founder's message becomes a task

Sometimes a conversation with a manager leads to new work:

```
Founder (in Product Manager group chat):
  Tourists are complaining that the booking page is slow on mobile
  in mainland China. Can you investigate?

Product Manager: I'll have Dev Agent run a performance audit focusing
on mobile + China network conditions. Likely causes are CDN routing,
unoptimized images, or third-party scripts being blocked by the
Great Firewall. I'll report back within 24 hours.
```

The Product Manager creates a new task (performance audit), assigns it to the Dev Agent, and tracks it. The orchestrator logs the founder's original message as the task origin. The Product Manager's next daily report will include this task's progress.

---

## 7. Feishu Bot Setup Requirements

**OPC Hub (full app bot) — 1 app:**
- Created on Feishu Open Platform (open.feishu.cn)
- App ID and App Secret stored in orchestrator config (encrypted)
- Bot capability enabled
- Event Subscription: `im.message.receive_v1` (to receive founder messages)
- Permissions: `im:message`, `im:message.group_at_msg`, `im:chat`
- Added to all group chats as a member

**Manager webhook bots — 4 custom bots:**
- Created in each manager's group chat via "Add Custom Bot"
- Each has its own name and avatar:
  - 📝 Content Manager (avatar: pen/notebook icon)
  - ⚙️ Product Manager (avatar: gear icon)
  - 🤝 Ops Manager (avatar: handshake icon)
  - 💬 CX Manager (avatar: speech bubble icon)
- Each provides a webhook URL stored in orchestrator config
- Outbound only: `POST {webhook_url}` with message payload
- Optional: webhook signing key for security

**Setup summary:**
- 1 Feishu Open Platform app (OPC Hub) — handles all inbound
- 4 custom webhook bots — handle outbound with distinct identities
- 7 group chats (4 manager chats + urgent + weekly + system)

---

## 8. Audit Trail

Every founder interaction is logged:
```json
{
  "escalation_id": "ESC-2026-0042",
  "feishu_message_id": "om_abc123def456",
  "group_chat": "cx_manager",
  "sent_at": "2026-04-11T14:30:00Z",
  "sent_via": "cx_manager_webhook",
  "tier": "standard",
  "founder_reply": "approved, but verify partner insurance is current first",
  "received_via": "opc_hub_app",
  "replied_at": "2026-04-11T15:12:00Z",
  "response_time_minutes": 42,
  "parsed_decision": "approved_conditional",
  "condition": "verify partner insurance is current first",
  "task_resumed_at": "2026-04-11T15:12:05Z"
}
```

This data feeds into the founder dashboard and helps calibrate whether escalation thresholds are set correctly — if the founder is always approving a certain category, the threshold might be too low.
