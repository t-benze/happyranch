# Thread File Attachments Design

> Status: proposed
> Current Source: this spec until implementation lands
> Superseded By: none
> Notes: Designs artifact-backed file references on thread messages, with web and CLI upload convenience.

## Problem

Threads currently carry text messages, decline rows, and system rows. Agents can share durable files through the org-level artifact store, but there is no first-class way to associate a file with a thread message. That makes handoffs awkward: the sender must upload an artifact separately, mention its name in prose, and hope recipients notice and download the right file.

The goal is to let founders and agents send files over threads without introducing a second byte store or weakening the existing agent permission model.

## Goals

- Let founder and agent thread messages include one or more file attachments.
- Keep actual file bytes in the existing org artifact store.
- Let web and CLI users attach local files with a convenient upload-before-send workflow.
- Let agents receive attachment references in prompts and download files through `happyranch artifacts get`.
- Preserve file references in thread detail responses, live message updates, CLI output, and archived transcript markdown.
- Allow file-only messages when at least one attachment is present.

## Non-Goals

- Per-thread private file storage.
- Artifact deletion or cleanup when a thread message send fails after upload succeeds.
- File previews, virus scanning, or content extraction.
- Changing artifact overwrite semantics in this feature.
- Treating attachments as separate thread turns.

## Chosen Approach

Thread routes stay reference-only. A thread message stores attachment metadata that points to existing artifact names. Clients provide the ergonomic workflow:

1. Upload local files to `/api/v1/orgs/{slug}/artifacts`.
2. Send the thread message with `attachments` containing artifact refs.
3. If the message send fails after upload succeeds, keep the artifacts and let the caller retry using the same refs.

This keeps one source of truth for bytes, reuses existing artifact validation, preserves agent sandbox expectations, and still lets the web UI feel like native chat attachment upload.

## Alternatives Considered

### Upload Bytes Through Thread Routes

Thread compose/send/reply could accept multipart requests with message fields and files. This has the nicest raw API for one caller, but duplicates artifact route behavior, complicates OpenAPI and web client mirroring, and creates a second place that must enforce file caps, names, audit, and atomic writes.

### Existing Artifact References Only, No Convenience

Messages could accept only artifact refs and require every caller to upload first. This is the simplest backend shape, but too rough for founder UX and too easy to use incorrectly. The chosen approach keeps the reference-only daemon contract while making web and CLI perform the two-step workflow for users.

### New Per-Thread File Store

Files could live under a thread-specific directory. That gives tidy grouping by thread, but duplicates artifact download/list behavior and creates new lifetime semantics. Shared artifacts already solve org-wide file visibility, which is what thread participants need.

## Data Model

Add a `thread_message_attachments` table:

```sql
CREATE TABLE thread_message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_seq INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    artifact_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    size_bytes INTEGER,
    content_type TEXT,
    uploaded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES threads(id),
    UNIQUE(thread_id, message_seq, ordinal)
);
```

The message sequence remains allocated by `thread_messages`. Attachment rows are children of one message and ordered by `ordinal`. `size_bytes` and `content_type` are metadata snapshots for display and transcript durability; the artifact name remains the download key.

The Python and TypeScript thread message models gain:

```json
"attachments": [
  {
    "artifact_name": "THR-001-20260609T031500-report.pdf",
    "display_name": "report.pdf",
    "size_bytes": 124033,
    "content_type": "application/pdf"
  }
]
```

## API Contract

The following thread write bodies accept optional `attachments`:

- `POST /api/v1/orgs/{slug}/threads`
- `POST /api/v1/orgs/{slug}/threads/compose-as-agent`
- `POST /api/v1/orgs/{slug}/threads/{thread_id}/send`
- `POST /api/v1/orgs/{slug}/threads/{thread_id}/reply`

Input attachment refs are:

```json
{
  "artifact_name": "stored-name.pdf",
  "display_name": "optional display.pdf"
}
```

`display_name` defaults to `artifact_name` when omitted. The daemon validates every referenced artifact exists before appending the message. Message text is valid when `body_markdown.strip()` is non-empty. A message is valid if it has text, at least one attachment, or both.

Thread read routes return attachments on each message:

- `GET /api/v1/orgs/{slug}/threads/{thread_id}`
- `GET /api/v1/orgs/{slug}/threads/{thread_id}/messages`
- thread tail replay events that already emit full `ThreadMessage` objects

Lightweight event previews should include text when present, otherwise a concise attachment preview such as `Attached 2 files: report.pdf, data.csv`.

## Validation

- Empty message with no attachments: `422 empty_body`.
- Unknown artifact ref: `404 artifact_not_found`.
- Duplicate attachment `artifact_name` in one message: `422 duplicate_attachment`.
- More than 5 attachments in one message: `422 too_many_attachments`.
- Invalid or empty display name: `422 invalid_attachment_display_name`.
- Existing artifact upload cap remains 10 MB per file.

Display names are UI labels, not paths. They must be non-empty, at most 200 characters, and must not contain `/`, `\`, or control characters.

## Web UX

The thread composer supports selecting or dropping up to 5 files. Before sending, the web app uploads each pending file to `/artifacts`, using collision-resistant artifact names. A good v1 naming scheme is:

```text
THR-<id-or-draft>-<UTC timestamp>-<sanitized basename>
```

For a new thread before a thread id exists, use a draft prefix such as `thread-draft-<timestamp>-<basename>`.

Composer behavior:

- Show selected files with name, size, upload state, and remove controls.
- Allow send when text or attachments are present.
- Disable send while uploads are in progress.
- If any upload fails, do not send the message and show the failed file.
- If uploads succeed but message send fails, keep the uploaded refs in the composer so retry does not reupload.

Message rendering shows attachment chips or compact cards below the markdown body. Each attachment has a download action that calls the artifact download route.

## CLI UX

Founder convenience for new threads and follow-up messages:

```bash
happyranch threads compose --org demo --subject "Review data" --recipients dev_agent --body "Please inspect this." --attach ./data.csv
```

```bash
happyranch threads send --org demo --thread-id THR-001 --from-file /tmp/msg.json --attach ./report.pdf --attach ./data.csv
```

Agent convenience for compose/reply keeps the callback command single-line and still uses `--from-file` for the structured payload:

```bash
happyranch threads reply --org demo --thread-id THR-001 --from-file /tmp/thread-reply-THR-001-4.json --attach /tmp/analysis.md
```

The CLI reads the JSON body or compose flags, uploads each `--attach` file to artifacts using collision-resistant names, merges the resulting refs with any `attachments` already present in the JSON, then calls the relevant thread route. Agent upload attribution comes from the payload `speaker` for replies and `composer` for agent-initiated compose. Founder upload attribution uses `founder`.

Agent reply remains callback-compatible through `--from-file`; payloads may include attachment refs:

```json
{
  "thread_id": "THR-001",
  "invocation_token": "tok",
  "speaker": "dev_agent",
  "body_markdown": "Attached the analysis.",
  "attachments": [
    {"artifact_name": "THR-001-analysis.md", "display_name": "analysis.md"}
  ],
  "in_response_to_seq": 4
}
```

Agents that need to attach a newly created local file can use the explicit two-step flow:

```bash
happyranch artifacts put /tmp/analysis.md --agent dev_agent --name THR-001-analysis.md --org demo
happyranch threads reply --org demo --thread-id THR-001 --from-file /tmp/thread-reply-THR-001-4.json
```

The callback remains a single-line `happyranch ... --from-file <path> [--attach <path> ...]` invocation for permission matching. The structured callback data stays in the file; attach flags are only local upload convenience.

`happyranch threads show` prints attachments under each message.

## Agent Prompt And Skill

Thread prompts render attachments under each message:

```text
Attachments:
- report.pdf (`artifact:THR-001-report.pdf`, 124033 bytes)
```

`protocol/skills/thread/SKILL.md` should tell agents to download attachments with:

```bash
happyranch artifacts get THR-001-report.pdf --output /tmp/report.pdf --org <slug>
```

Agents may reply with attachment refs in the existing reply payload. If they create a new file during a thread invocation, they can either use `happyranch threads reply --from-file <payload> --attach <path>` or upload it explicitly through `happyranch artifacts put` first and include the artifact ref in the terminal reply payload.

## Transcript Rendering

Archived thread transcripts render attachments immediately after the message body:

```md
Attachments:
- [report.pdf](artifact:THR-001-report.pdf) (124033 bytes)
```

The artifact URI is a stable reference marker, not a filesystem path. It tells readers which artifact to download through the CLI or web UI.

## Audit

Keep existing `thread_message_sent` audit rows for the message. The existing `artifact_put` rows record uploaded bytes. The message audit payload should include an attachment count and artifact names so audit search can connect a thread message to referenced files.

## Testing

Backend tests:

- Database migration creates `thread_message_attachments`.
- Append/list thread messages round-trip attachments.
- All four write paths accept text plus attachments, file-only attachments, and reject empty no-attachment messages.
- Unknown, duplicate, excessive, and invalid-display attachments are rejected.
- Prompt and delta prompt render attachments.
- Transcript rendering includes attachment refs.
- Thread tail replay preserves attachments in full message events.
- OpenAPI snapshot is intentionally regenerated.

CLI tests:

- `threads send --attach` uploads files then sends attachment refs.
- `threads compose --attach` uploads files then sends initial attachment refs.
- `threads reply --attach` uses payload speaker attribution, uploads files, and preserves the single-line callback shape.
- Existing `--from-file` payload attachments pass through.
- `threads show` prints attachment lines.
- Thread skill documents the two-step agent attachment flow.

Web tests:

- API types include `attachments`.
- Composer sends text-only, attachment-only, and mixed messages.
- Upload failure blocks message send and marks the failed file.
- Send failure after upload keeps attachment refs available for retry.
- Message bubbles render attachment download controls.

## Rollout

This is a schema and OpenAPI change. Implementation should land with the migration, route updates, CLI changes, web type/API coverage, and protocol skill update in one branch so agents do not receive attachment prompts before the callback shape is supported.
