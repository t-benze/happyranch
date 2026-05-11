# Feishu Notification Setup

This runbook walks you through enabling Feishu push notifications for
escalations in an OPC org.

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

## 5. Configure OPC

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
INFO src.daemon.feishu_listener: started Feishu event listener for org=<slug>
```

## 6. Test

Trigger an escalation (e.g. via `opc revisit ...` to a stuck task) and
confirm the bot posts in your chat. Reply with `APPROVE\nlooks fine` and
confirm the task transitions to `pending`.
