# SQR Tracker Prototype — Plan

A new view on the SQR page, **SQR Tracker Prototype**, that rebuilds the classic Tracker as a guided workspace instead of a 43-column spreadsheet. The classic Tracker stays untouched and remains the system of record for reference.

## 1. Why

| Pain in the classic Tracker | Prototype answer |
| --- | --- |
| 43 columns, horizontal scrolling, sticky-column juggling | One **record = one screen**. Fields are grouped by stage and shown only where they matter |
| You must know which cell to click, and which columns your role may edit | Editable fields look editable; locked fields say *why* they are locked |
| No sense of "what do I do next?" | Every record carries a **Next step** card and **attention flags** (revision requested, overdue, validity expiring) |
| Status, approval and revision flows are hidden in dropdown cells | **Approve / Request revision** are explicit buttons with a confirmation step |
| Filters live in a collapsed panel | A **pipeline strip** doubles as quick filters; search is always visible |

## 2. Decisions (confirmed)

- **Audience:** everyone with SQR access; permissions identical to today (Admin / PM-ESG edit, Engineer sees and edits only their own submissions through the existing edit form).
- **Layout:** pipeline list on the left + slide-over style record editor on the right.
- **Scope:** full parity: every field, status flows (approval email, revision + Teams chat), undo, history/restore, filters, New Submission / Export / Import (shared header buttons).

## 3. Architecture

Reuse, do not re-implement, business rules.

```
Browser (sqr-tracker-prototype.js)
   │  GET  /sqr/prototype-data/            → schema + options + records (role-scoped)
   │  GET  /sqr/prototype-data/?pk=12      → one fresh record after each save
   │  POST /sqr/<pk>/inline-update/        → existing endpoint (validation, locks, side effects, history)
   │  POST /sqr/<pk>/inline-update/<id>/undo/   → existing undo
   │  GET  /sqr/<pk>/history/  POST …/restore/  → existing history
   ▼
hub/services/sqr_prototype.py   (new, pure functions, unit-tested)
   FIELD_SPECS · derive_stage · derive_attention · derive_next_step · serialize_record
```

- **Single source of truth for fields:** `FIELD_SPECS` (key, label, type, tab, section, choices, unit, help). The JSON `schema` drives the UI, so adding a field is a one-line change.
- **Permissions stay server-side.** Each record ships `editable: [...]` computed from `SqrInlineFieldUpdateView._allowed_fields_for_user` minus the Closed-Lost / Closed-Canceled locks. The client only *reflects* this; the inline endpoint still enforces it.
- **Fresh data after every save:** the client re-fetches the single record, so computed values (amounts, validity dates, warranty/maintenance dates) are always authoritative and never re-implemented in JavaScript.
- **Isolated assets:** `static/js/sqr-tracker-prototype.js`, `static/css/sqr-tracker-prototype.css`, `templates/hub/partials/sqr_prototype.html`. `sqr.html` only gains a toggle button, an include and a small generalisation of the view-toggle script.
- **Lazy load:** data is fetched the first time the tab is opened.

## 4. Stage model (derived, never stored)

| Stage | Rule |
| --- | --- |
| **Opportunity** | not Closed-Won / Lost / Canceled yet |
| **Delivery** | Closed-Won and delivery not finished |
| **Billing** | Closed-Won, delivery finished, not yet billed |
| **Closed** | Lost, Canceled, or fully Billed |

Attention flags: *Revision requested*, *Awaiting approval* (PM), *Validity expiring ≤ 7 days / expired*, *Delivery overdue*, *At risk / Off track*.

## 5. Screen design

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Pipeline  [All 120] [Needs action 9] [Opportunity 48] [Delivery 31] [Billing 12] [Closed 20] │
├───────────────────────────────┬──────────────────────────────────────────────┤
│ 🔍 Search…   Status▾ Health▾  │ SQR-2026-0042 · Acme Corp            [⋯]    │
│ Sort▾   ☐ Mine   Comfortable▾ │ ●Opportunity ─ ○Delivery ─ ○Billing          │
│ ───────────────────────────── │ ┌ Next step ─────────────────────────────┐   │
│ ▌SQR-…0042 Acme   ₱420K       │ │ Awaiting approval → Approve or request │   │
│   Network refresh             │ │ revision          [Approve][Revision]  │   │
│   [For Processing] [Submitted]│ └─────────────────────────────────────────┘   │
│ ▌SQR-…0041 Beta   ₱88K        │ Summary │ Opportunity │ Delivery │ Billing │ History │
│   …                           │  fields grouped in cards, auto-save on commit │
└───────────────────────────────┴──────────────────────────────────────────────┘
```

- **Pipeline list** — card rows (ID · stage, account, service, value, status badges, one attention flag, delivery progress bar); the stage is shown as a coloured left edge and a label. Comfortable/Compact density; chunked rendering (40 at a time); keyboard: ↑/↓ move between cards, `/` focuses search.
- **Editor** — header + stage stepper + Next step card + tabs. Fields **auto-save on commit** (blur / Enter / change) with inline state: saving → saved ✓ → error with message. Escape reverts. A toast offers **Undo** (existing 24 h undo window).
- **Locked fields** show a lock icon and the reason in plain words.
- **Responsive:** ≥ 1100 px two panes; below that the editor becomes a full-height slide-over with a Back button.
- **Visual language:** existing liquid-glass tokens, dark-mode aware, stage colours (Opportunity blue, Delivery teal, Billing amber, Closed slate), respects `prefers-reduced-motion`.
- **Accessibility:** real buttons / tablist with roving focus, `aria-live` save status, visible focus rings, ≥ 4.5:1 text contrast in both themes, labelled controls.

## 6. Flows kept at parity

| Flow | Prototype behaviour |
| --- | --- |
| Approve | Confirmation dialog → *Open Outlook draft* (clipboard transfer via `startSqrApprovalDraft`) or *Download .eml* → status saved through the inline endpoint |
| Request revision | Notes dialog (required) → status + `review_notes` saved → Teams chat opened like today |
| Undo | Toast with server undo URL |
| History / Restore | History tab (Admin / PM) using existing endpoints |
| Engineer edit | "Edit submission" opens the existing edit page; Delete link for Admin |
| Closed-Lost lock | Delivery and billing fields disabled with explanation |
| Stale classic table | After edits, leaving the prototype refreshes the classic Tracker so it never shows old values |

## 7. Delivery steps

1. `hub/services/sqr_prototype.py` — specs, derivations, serializer.
2. `SqrPrototypeDataView` + URL (`sqr/prototype-data/`), role-scoped, `no-store`.
3. Template partial + toggle wiring in `sqr.html`.
4. CSS, then JS (list → editor → field controls → workflows → history).
5. Tests: `hub/test_sqr_prototype.py` (derivations, permissions per role, closed-lost locks, endpoint scope, page render).
6. Browser verification (light + dark, desktop + narrow), regression run of existing SQR tests.

## 8. Out of scope (v1)

Bulk edit, drag-and-drop kanban, new export formats, schema/model changes, replacing the classic Tracker.

## 9. Status

Implemented and verified.

| Area | Files |
| --- | --- |
| Data contract | `hub/services/sqr_prototype.py` |
| Endpoint | `SqrPrototypeDataView` in `hub/views.py`, route `sqr/prototype-data/` |
| UI shell | `templates/hub/partials/sqr_prototype.html`, toggle wiring in `templates/hub/sqr.html` |
| Styles / behaviour | `static/css/sqr-tracker-prototype.css`, `static/js/sqr-tracker-prototype.js` |
| Tests | `hub/test_sqr_prototype.py` (derivations, schema, per-role permissions, closed-lost locks, scope, query count, page render) |

Verified in the browser (light, dark, 1500 px and 700 px): data load, pipeline filters, record selection, edit → server-recomputed totals → Undo, approval and revision dialogs (open, validation, cancel), toggling to and from the classic Tracker, keyboard focus on narrow screens.

Known behaviours to be aware of:

- Approving a record opens the Outlook draft / downloads the `.eml` exactly like the classic Tracker, so it was not exercised against live data.
- After any save, leaving the prototype reloads the page so the server-rendered classic tables never show stale values.
- Delivery/billing locks are enforced by the server; the prototype only reflects them.
