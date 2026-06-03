# Feishu Notification Setup

This runbook walks you through enabling Feishu push notifications for
escalations in an HappyRanch org.

## 1. Create a self-built app

1. Log in at https://open.feishu.cn (or https://open.larksuite.com for intl).
2. **Developer Console** → **Create Custom App** → "Self-built app".
3. Note the `App ID` (starts with `cli_`) and `App Secret`.

## 2. Configure permissions

Add the following scopes:

- `im:message` — read incoming messages (required for the event listener)
- `im:message:send_as_bot` — send messages as the bot
- `im:resource` — download attachments (optional but harmless)

Click **Apply for Release** if your tenant requires admin approval; consumer
Feishu accounts can self-approve.

## 3. Enable WebSocket events

1. In the app config, **Events and Callbacks** → **Event Subscription**.
2. Select **WebSocket** mode (not HTTP callback). No public URL is needed.
3. Subscribe to event: `im.message.receive_v1`.

## 4. Add the bot to a 1:1 chat

1. In the Feishu app, search for the bot by name and add it to your contacts.
2. Send the bot any message to create a chat.
3. From the bot's chat info panel, copy the `chat_id` (looks like `oc_xxxx...`).

If the chat ID isn't directly visible in the UI, use a one-shot script with
the SDK to list the bot's joined chats:

```python
import lark_oapi as lark
from lark_oapi.api.im.v1 import ListChatRequest
client = lark.Client.builder().app_id("...").app_secret("...").domain(lark.FEISHU_DOMAIN).build()
resp = client.im.v1.chat.list(ListChatRequest.builder().build())
print(resp.data.items)
```

## 5. Configure HappyRanch

Edit `<runtime>/orgs/<slug>/org/config.yaml` to add the `feishu_notifications`
block with your app credentials:

```yaml
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_aaaaaaaaaaaaaaa
  app_id: cli_xxxxxxxxxxxxxxxx
  app_secret: yyyyyyyyyyyyyyyyyyyyyyyy
  reply_ttl_hours: 72
```

**Security note**: this file now holds secrets. Set restrictive permissions and
never commit the live runtime config to version control:

```bash
chmod 600 <runtime>/orgs/<slug>/org/config.yaml
```

Restart the daemon. On startup, look for log lines like:

```
INFO runtime.daemon.feishu_listener: started Feishu event listener for org=<slug>
```

## 6. Test

Trigger an escalation (e.g. via `happyranch revisit ...` to a stuck task) and
confirm the bot posts in your chat. Reply with `APPROVE\nlooks fine` and
confirm the task transitions to `pending`.

## 7. Script requests (SR-NNN)

When an agent submits a script request (via `happyranch scripts submit`), the
daemon pushes a Feishu post to the configured chat. Reply in the same thread
to act on it:

**Approve and run:**

```
APPROVE
<optional note>
```

The daemon runs the SR with the agent-provided defaults (`cwd_hint`,
300-second timeout). When the script terminates, the daemon posts a threaded
reply with the exit code, duration, and head of stdout/stderr.

**Reject:**

```
REJECT
<reason>
```

The SR transitions to `rejected` with the rationale captured. No
terminal-result follow-up is posted.

**Override defaults:** Use the CLI (`happyranch scripts run --cwd ...
--timeout-seconds ...`) or the web UI — Feishu replies cannot set
`cwd_override` or `timeout_seconds` in v1.

**APPROVE shows the full script in the push body** (up to 1500 chars). Read
it before replying. There is no "are you sure" prompt in Feishu — the
message-body preview is the confirmation surface.

Audit events for the script-request notification flow:
`script_notify_sent`, `script_notify_failed`, `script_reply_processed`,
`script_reply_rejected`, `script_run_result_notify_sent`,
`script_run_result_notify_failed`. Inspect via `happyranch audit --org <slug>
--action script_*`.
