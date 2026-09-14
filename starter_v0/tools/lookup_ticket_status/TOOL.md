---
name: lookup_ticket_status
track: bonus
kind: local_status
provider: mock_ticket_store
requires_env: []
inputs: [ticket_id]
outputs: [ticket_id, ticket, untrusted_text, source, snapshot_at, trust_boundary]
side_effect: false
---
# lookup_ticket_status

Read-only lookup of one existing fictional helpdesk ticket by ticket ID. It
answers "ticket của tôi đang xử lý tới đâu?" — a capability no core tool covers.
It never creates, updates or closes tickets (that is `create_ticket`).

## Input contract

| Arg | Type | Rule |
|---|---|---|
| `ticket_id` | string, required | `INC-1234` (fixture) or `LAB-1A2B3C4D` (created locally by `create_ticket`). Trimmed and upper-cased. |

## Data sources

- `INC-xxxx` → `helpdesk_data/tickets.json` (`source: mock_ticket_fixture`).
- `LAB-xxxxxxxx` → `tickets/<id>.json` written by `create_ticket` after confirmation
  (`source: local_ticket_store`, status defaults to `open`). This lets the agent
  look up a ticket it just created.

## Output contract

Success:

```json
{
  "tool": "lookup_ticket_status",
  "ticket_id": "INC-1001",
  "ticket": {"ticket_id": "INC-1001", "summary": "...", "priority": "high", "status": "in_progress",
             "assigned_to": "NetOps Team", "asset_id": "LT-204", "created_at": "...", "updated_at": "..."},
  "untrusted_text": [],
  "source": "mock_ticket_fixture",
  "snapshot_at": "2026-03-14T08:00:00Z",
  "trust_boundary": "..."
}
```

Errors (always `{"tool": "lookup_ticket_status", "error": <code>, ...}`):

| Error | When |
|---|---|
| `invalid_ticket_id_type` | `ticket_id` is not a string (number, list, object) |
| `missing_ticket_id` | empty / whitespace — the agent should `clarify`, not guess |
| `invalid_ticket_id_format` | not `INC-1234` / `LAB-1A2B3C4D`; includes `looks_like` + `suggested_tool` when the value is an asset, employee or change ID. The raw input is not echoed back. |
| `ticket_not_found` | valid format but no such ticket |

## Guardrails

- **No side effect:** only reads files; nothing is written.
- **Field allow-list:** only the fields above are returned, even if a ticket file has more.
- **Untrusted summaries:** ticket summaries are user-authored. Instruction-like text
  (`SYSTEM:`, `ignore previous`, `call create_ticket`, …) is withheld from `summary`
  and returned in `untrusted_text`.
- **Path safety:** local lookup only runs after the ID matches the strict pattern,
  so it cannot read outside `tickets/`.

## Smoke test

From `starter_v0/` (no provider key, no network, writes only to a temp dir):

```powershell
uv run python tools/lookup_ticket_status/smoke_test.py
```
