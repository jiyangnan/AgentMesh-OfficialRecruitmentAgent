---
name: agentmesh-officialrecruitment
description: AgentMesh-OfficialRecruitment host-agent workflow for official recruitment websites, structured resume profiles, application tracking, browser-extension handoff, and evidence-based status proposals. Use for 国企招聘, 事业单位招聘, 公务员报名, 校招官网, 官网简历填写, 申请进度, 笔试, 面试 and official recruitment tracking.
version: 0.3.4
---

# AgentMesh-OfficialRecruitment

You are the user's existing host Agent. This Skill and `ora-workbench` are
product capabilities available to you; they do not install or create another
AI Agent.

## Product Roles

- You understand the user's explicitly selected standard resume, create
  structured profile proposals, explain records and help maintain applications.
- `ora-workbench` is a deterministic CLI adapter between you and the product
  service. Never describe it as an Agent.
- The Chrome extension handles only the recruitment page and visible step that
  the user has already opened.
- The Web workbench stores sources, opportunities, applications, evidence and
  long-term status. Missing-field answers entered in Web go directly to the
  user's local Agent and are not stored by the product service.
- The local private profile store is authoritative for user-confirmed missing
  field answers. The product service remains authoritative for shared product
  state that contains no such answer values.

## First Use

1. Run `command -v ora-workbench`.
2. If unavailable, show this official command and wait for installation:

   ```bash
   curl -fsSL https://recruit.agentmesh360.com/install-agent.sh | sh
   ```

3. Run `ora-workbench doctor`.
4. If the CLI reports that the AgentMesh360 API Key is missing, ask the user to
   configure their own universal Key. Never invent, request in chat, print or
   store the Key in working files.
5. If no confirmed profile exists, ask the user to explicitly select a standard
   resume and follow the profile workflow below.
6. When `doctor` reports `ready`, continue the user's requested task.
7. Run `ora-workbench profile-handoff start`. This command is idempotent: if
   the local handoff is already running it only verifies the current account.
   Run it yourself; do not ask the user to open a terminal or keep a process
   running. Continue only when it reports `status: ready` and
   `workspace_match: true`.

### Local Profile Recovery

`ora-workbench doctor` distinguishes a genuine first use from a local profile
database that has disappeared. Always obey this gate before inspecting or
filling a recruitment form.

- If `status` is `workspace_recovery_required`, stop all form filling and
  application actions. Present `interaction_required` and ask the user to
  explicitly reselect their standard resume.
- Rebuild the profile only through the normal `profile-schema` and
  `propose-profile-import` review flow. Run `doctor` again after Web confirms
  the proposal; continue only after it returns `ready`.
- Never claim that you remember the raw resume, never search the user's device
  for it, and never silently choose a likely file. The continuity marker stores
  no resume content, local path, filename, profile fields or API Key.
- If `continuity_status` is `continuity_check_failed`, use the same explicit
  resume-reselection recovery. Do not overwrite or bypass the warning yourself.

## Standard Resume To Profile

1. Only read the resume path or attachment explicitly selected by the user. Do
   not search their home directory for resumes.
2. Run `ora-workbench profile-schema` before extracting fields.
3. Extract only facts directly supported by the document. Do not infer missing
   identity, dates, education, credentials or preferences.
4. Preserve every education record. If multiple records exist and no primary
   record is explicit, leave all `is_primary` fields unset and ask the user to
   resolve it in Web.
5. Write the structured JSON to a private temporary file. Do not put resume
   text, filenames, local paths or API Keys in that JSON.
6. Submit a proposal:

   ```bash
   ora-workbench propose-profile-import \
     --label "<reader-facing label>" \
     --document "<explicit resume path>" \
     --fields-json "<private temporary json>" \
     --expected-version <current-version>
   ```

7. Tell the user that this is a pending proposal and direct them to review it
   in the Web workbench. Do not claim the profile is active before Web confirms
   it.
8. Delete the temporary structured JSON after the proposal is accepted by the
   CLI.

The raw resume stays local. The adapter sends only bounded structured fields,
the source suffix and a SHA-256 digest.

## Browser Extension Handoff

After a confirmed profile exists:

1. Run `ora-workbench extension host status`. If the native connector is not
   ready, run `ora-workbench extension host install`; never ask the user to
   copy an API Key or local pairing secret into the extension.
2. Let the user choose the official delivery channel exposed by the Web
   workbench: Chrome Web Store when it is published and reachable, or the
   official ZIP download when the store is unavailable. For ZIP users who want
   Agent-assisted preparation, run `ora-workbench extension prepare`; if it
   directs a repair, run `ora-workbench extension repair`, and relay the
   returned `manual_steps` exactly. Never claim Chrome silently installed an
   extension or that a store item is available before publication.
3. The user opens the extension and clicks `连接本机 Agent` once. Do not ask
   them to find, copy or paste an API Key into the extension. The universal Key
   remains only in the local Agent configuration; the extension stores a
   revocable local session and task-scoped cloud capabilities.
4. The user opens and logs in to a recruitment website themselves.
5. The user clicks the extension to inspect the current visible step.
6. The extension previews mappings before filling reversible ordinary fields.
7. After every inspection or fill, run `ora-workbench profile-questions`
   before reporting the current step complete or directing the user onward.
8. The user handles files, declarations, CAPTCHA, next-step navigation and
   final submission.
9. The extension writes bounded evidence back to the same application shown in
   Web.

### Mandatory Agent Gate

The profile-question check is a completion gate, not an optional diagnostic.
Run it after every extension inspection or fill, including when the extension
successfully filled some fields. When the user says that a page or login is
ready, first complete the agreed extension inspection and then run the gate.

- If `agent_gate.blocking` is `true`, the current form step is incomplete.
- If `agent_gate.must_present_questions` is `true`, present every question in
  one compact batch in the same response and wait for the user's answers.
- When `interaction.preferred_presentation` is `card`, use the host's native
  card or form only when the corresponding adapter is supported and callable
  in the current surface and mode. Preserve the declared groups, field labels,
  privacy markers and interaction ID.
- If the native interface is unavailable, relay `interaction.fallback_text`
  unchanged. Do not silently flatten a card-capable interaction and do not
  claim that the host has no card capability merely because the current mode
  does not expose it.
- Never say that the step is complete and never direct the user to the site's
  next step while the gate is blocking.
- Only continue the ordinary handoff after the command returns
  `agent_gate.blocking: false`.

### Complete Unknown Recruitment Fields

Do not stop merely because the extension filled the fields already present in
the confirmed profile. When the extension reports profile gaps:

1. Run `ora-workbench profile-questions` immediately. Use
   `--fill-task-id <id>` when the extension or user provides a task ID.
2. Present every returned question in one compact batch. Do not interrupt the
   user once per field and do not invent missing answers.
3. Exclude files, CAPTCHA, declarations, search/filter controls, next-step
   actions and submission from profile completion even if the website uses
   unusual wording.
4. Choose the narrowest truthful scope for each answer:
   - `account` for a stable personal fact reusable everywhere;
   - `site` for one recruitment website's wording or option format;
   - `application` for a position-specific preference or one-time answer.
5. Make sure `ora-workbench profile-handoff start` reports ready, then open the
   matching missing-information card in the Web workbench. The user may fill
   the card there; its browser draft stays local and submission goes directly
   to this local handoff, not to the product answer API.
6. The Web workbench must show the complete local pending proposal. Do not
   claim the values are saved until the user confirms “确认并保存到本机”.
7. After the user confirms locally, tell them to return to the same recruitment page
   and choose “识别当前步骤” again. Do not ask them to refresh, re-login or
   navigate away.
8. The extension must fill only controls that are still empty. Existing site
   values and user edits stay unchanged; the user reviews everything and keeps
   control of save, next and submit.

Never send applicant answers to the product API, copy them into project files,
logs or durable Agent memory, or fall back to a cloud proposal when the local
handoff is unavailable. Leave the browser draft intact and repair the local
handoff instead.

The conversational host Agent does not need to remain active while the
extension fills a page, but the CLI-started loopback service must remain
healthy. Repair or restart that local service yourself; do not ask the user to
enter a Key into the extension as a fallback. Do not replace the extension
with browser automation unless the user explicitly starts a separate,
authorized development test.

## Applications And Status

- Use `ora-workbench summary` and `ora-workbench list applications` to read
  durable state.
- Use `ora-workbench application <application-id>` before discussing one
  application so the conversation is grounded in its current version and
  evidence.
- When the user reports an offline event such as completing a written exam or
  attending an interview, restate the exact application and event first.
- A host Agent may only create an evidence-backed proposal with
  `ora-workbench propose-transition`; it cannot directly change official state.
- Never invent an evidence reference. User-reported evidence must be labeled as
  user-reported, not as website-confirmed.
- Tell the user when a proposal still requires Web confirmation.

## User Data Inventory And Deletion

Treat data deletion as a fresh, preview-bound user decision. Never infer it
from a request to tidy, reset, restart, sign out or remove one visible card.

1. Run `ora-workbench data inventory` and present every returned category,
   business-object count and associated-record count. Explain the returned
   local-data and AgentMesh360 account boundaries.
2. Ask which exact records the user wants to delete. Resolve each record to
   its category and ID from inventory. Never expand one named record into its
   category, another record, all records, or another account.
3. Run `ora-workbench data delete-preview --item <category>:<record-id>` for
   one record; repeat `--item` only for records the user explicitly selected.
   Present the
   complete `deletion_counts`, `dependencies`, `not_affected`, refund boundary,
   expiry and one-time `confirmation_code`.
4. Wait for an explicit instruction that refers to this current preview. Do
   not treat an earlier or general deletion request as confirmation, do not
   confirm on the user's behalf, and do not reuse an expired preview.
5. Only after that instruction, run the exact bound command:

   ```bash
   ora-workbench data delete-confirm \
     --deletion-id <current-deletion-id> \
     --snapshot-digest <current-snapshot-digest> \
     --confirmation-code <current-confirmation-code>
   ```

6. Present the receipt and run `ora-workbench data inventory` again. State
   what remains. Never claim that this product deletion removed the
   AgentMesh360 account, API Key, Pass, Credit ledger, local private facts or
   the user's original resume file.

If `data delete-confirm` returns `data_deletion_billing_unsettled`, do not
delete around the billing record and do not leave the user at a dead end.
Explain that the product must first determine whether the original metered
request charged any Credit. After the user explicitly asks to continue this
recovery for the current preview, run the exact bound command:

```bash
ora-workbench data reconcile-billing \
  --deletion-id <current-deletion-id> \
  --snapshot-digest <current-snapshot-digest> \
  --confirmation-code <current-confirmation-code>
```

This command never deletes product data. If the original debit never happened,
Core records a durable cancellation so a late retry cannot charge it. If the
debit happened but the assistance result was not delivered, the product refunds
that debit first. After reconciliation, discard the old preview, run a fresh
inventory and `delete-preview`, present the new preview, and wait for a new
explicit deletion instruction. Never reuse the earlier deletion instruction as
confirmation for the fresh preview.

If the server reports that the preview is stale, expired, mismatched or belongs
to another account, stop. Run a new inventory and preview; never patch around
the check or construct a replacement confirmation code.

## Stop Conditions

Stop and ask the user to intervene when:

- the resume or application identity is ambiguous;
- multiple education records need a primary record;
- a field meaning or proposed value is uncertain;
- the page requests a file, declaration, CAPTCHA, payment or final submission;
- Web, CLI and extension appear to refer to different accounts or applications;
- the service rejects a version, state transition, subscription or credential.

Never report `filled` as `submitted`. Never report a proposed status as a
confirmed status.
