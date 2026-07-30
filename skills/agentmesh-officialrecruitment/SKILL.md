---
name: agentmesh-officialrecruitment
description: AgentMesh-OfficialRecruitment host-agent workflow for official recruitment websites, structured resume profiles, application tracking, browser-extension handoff, and evidence-based status proposals. Use for 国企招聘, 事业单位招聘, 公务员报名, 校招官网, 官网简历填写, 申请进度, 笔试, 面试 and official recruitment tracking.
version: 0.1.0
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
- The Web workbench stores sources, opportunities, applications, profile
  versions, evidence and long-term status.
- The private product service and database are authoritative for durable state.

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

1. Run `ora-workbench extension-setup` when the user needs installation help.
2. The user opens and logs in to a recruitment website themselves.
3. The user clicks the extension to inspect the current visible step.
4. The extension previews mappings before filling reversible ordinary fields.
5. The user handles files, declarations, CAPTCHA, next-step navigation and
   final submission.
6. The extension writes bounded evidence back to the same application shown in
   Web.

You do not need to remain online while the extension fills a page. Do not try
to replace the extension with browser automation unless the user explicitly
starts a separate, authorized development test.

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
