function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function normalizeServerUrl(value) {
  const url = new URL(value);
  const localDevelopment =
    url.protocol === "http:" &&
    ["127.0.0.1", "localhost"].includes(url.hostname) &&
    url.port === "8000";
  const production =
    url.protocol === "https:" &&
    (url.hostname === "agentmesh360.com" ||
      url.hostname.endsWith(".agentmesh360.com")) &&
    (url.port === "" || url.port === "443");
  if (!localDevelopment && !production) {
    throw new Error(
      "只允许连接 AgentMesh360 官方工作台或本机 8000 端口。",
    );
  }
  url.pathname = "";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

export function normalizeApiKey(value) {
  const key = String(value ?? "").trim();
  if (key.length < 24 || key.length > 512 || /\s/.test(key)) {
    throw new Error("请输入有效的 AgentMesh360 API Key。");
  }
  return key;
}

export async function assistSessionIdempotencyKey(
  pageUrl,
  installationId,
  timeBucket = Math.floor(Date.now() / 300000),
) {
  const url = new URL(pageUrl);
  url.hash = "";
  const digest = await sha256(
    stableStringify({
      installation_id: installationId,
      page_url: url.toString(),
      time_bucket: timeBucket,
    }),
  );
  return `assist-${digest}`;
}

export function parseHandoff(value) {
  let handoff;
  try {
    handoff = JSON.parse(value);
  } catch {
    throw new Error("辅助填写配对码格式无效。");
  }
  if (
    handoff?.protocol !== "ora-fill-handoff-v1" ||
    !/^fill_[0-9a-f]{24}$/.test(handoff.fill_task_id ?? "") ||
    !/^oraext_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(
      handoff.extension_capability ?? "",
    )
  ) {
    throw new Error("辅助填写配对码缺少有效的任务或短期凭证。");
  }
  return {
    server_url: normalizeServerUrl(handoff.server_url),
    fill_task_id: handoff.fill_task_id,
    extension_capability: handoff.extension_capability,
  };
}

export function validateFillTask(task, currentUrl) {
  if (!task || typeof task !== "object") {
    throw new Error("填写任务格式无效。");
  }
  if (
    ![
      "ready",
      "previewed",
      "manual_only",
      "executed_locally",
    ].includes(task.status)
  ) {
    throw new Error(`填写任务当前状态为 ${task.status}，不能执行。`);
  }
  if (new Date(task.expires_at).getTime() <= Date.now()) {
    throw new Error("填写任务已经过期。");
  }
  const currentOrigin = new URL(currentUrl).origin;
  if (currentOrigin !== task.allowed_origin) {
    throw new Error(
      `当前页面来源为 ${currentOrigin}，任务只允许 ${task.allowed_origin}。`,
    );
  }
  if (!Array.isArray(task.plan?.fields)) {
    throw new Error("填写任务没有可执行字段。");
  }
  if (task.status !== "manual_only" && task.plan.fields.length === 0) {
    throw new Error("填写任务没有可执行字段。");
  }
  return task;
}

export function validateFillSession(task, currentUrl) {
  if (!task || typeof task !== "object") {
    throw new Error("辅助填写会话格式无效。");
  }
  if (
    task.status === "revoked" ||
    ![
      "awaiting_form",
      "ready",
      "manual_only",
      "previewed",
      "executed_locally",
      "undone_locally",
      "failed_locally",
    ].includes(task.status)
  ) {
    throw new Error(`辅助填写会话当前状态为 ${task.status}，不能继续。`);
  }
  if (new Date(task.expires_at).getTime() <= Date.now()) {
    throw new Error("辅助填写会话已经过期。");
  }
  const currentOrigin = new URL(currentUrl).origin;
  if (currentOrigin !== task.allowed_origin) {
    throw new Error(
      `当前页面来源为 ${currentOrigin}，会话只允许 ${task.allowed_origin}。`,
    );
  }
  return task;
}

export function validateFillIntent(task, currentUrl) {
  if (!task || typeof task !== "object") {
    throw new Error("填写任务格式无效。");
  }
  if (task.status !== "awaiting_form") {
    throw new Error(`填写任务当前状态为 ${task.status}，不能观察表单。`);
  }
  if (new Date(task.expires_at).getTime() <= Date.now()) {
    throw new Error("填写任务已经过期。");
  }
  const currentOrigin = new URL(currentUrl).origin;
  if (currentOrigin !== task.allowed_origin) {
    throw new Error(
      `当前页面来源为 ${currentOrigin}，任务只允许 ${task.allowed_origin}。`,
    );
  }
  return task;
}

export function evidencePayload(result) {
  return {
    event_type: result.event_type,
    step_id: result.step_id ?? null,
    page_fingerprint: result.page_fingerprint,
    field_results: result.field_results.map((item) => ({
      field_signature: item.field_signature,
      status: item.status,
      reason_code: item.reason_code ?? null,
    })),
  };
}

export async function evidenceIdempotencyKey(taskId, result) {
  const digest = await sha256(
    stableStringify({
      task_id: taskId,
      evidence: evidencePayload(result),
    }),
  );
  return `extension-${digest}`;
}

export async function observationIdempotencyKey(
  taskId,
  observation,
  sessionVersion = null,
) {
  const digest = await sha256(
    stableStringify({
      task_id: taskId,
      session_version: sessionVersion,
      observation,
    }),
  );
  return `observation-${digest}`;
}
