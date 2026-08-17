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
    ["8000", "8010"].includes(url.port);
  const production =
    url.protocol === "https:" &&
    (url.hostname === "agentmesh360.com" ||
      url.hostname.endsWith(".agentmesh360.com")) &&
    (url.port === "" || url.port === "443");
  if (!localDevelopment && !production) {
    throw new Error(
      "只允许连接 AgentMesh360 官方工作台或本机 8000/8010 开发端口。",
    );
  }
  url.pathname = "";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

export async function assistSessionIdempotencyKey(
  pageUrl,
  installationId,
  timeBucket = Math.floor(Date.now() / 300000),
  refreshFromTaskId = null,
) {
  const url = new URL(pageUrl);
  url.hash = "";
  const digest = await sha256(
    stableStringify({
      installation_id: installationId,
      page_url: url.toString(),
      time_bucket: timeBucket,
      ...(refreshFromTaskId
        ? { refresh_from_task_id: refreshFromTaskId }
        : {}),
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
  const pendingRepeatGroups = (task.plan.repeat_groups || []).some(
    (item) => Number(item.pending_count) > 0,
  );
  if (
    task.status !== "manual_only" &&
    task.plan.fields.length === 0 &&
    !pendingRepeatGroups
  ) {
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

export function mergeLocalProfileResolution(task, resolution) {
  if (
    !resolution ||
    resolution.contract_version !== "local-profile-resolution-v1" ||
    resolution.fill_task_id !== task.fill_task_id ||
    !Array.isArray(resolution.resolved_question_ids) ||
    !Array.isArray(resolution.fields)
  ) {
    throw new Error("本机 Agent 返回的资料补充格式无效。");
  }
  const questions = Array.isArray(task.plan?.profile_questions)
    ? task.plan.profile_questions
    : [];
  const allowedBindings = new Map();
  questions.forEach((question) => {
    (question.bindings || []).forEach((binding) => {
      if (
        typeof binding?.field_signature === "string" &&
        typeof binding?.selector === "string" &&
        typeof binding?.control_type === "string"
      ) {
        allowedBindings.set(binding.field_signature, {
          question_id: question.question_id,
          selector: binding.selector,
          control_type: binding.control_type,
        });
      }
    });
  });
  const resolvedIds = new Set(resolution.resolved_question_ids);
  const localFields = resolution.fields.map((field) => {
    const binding = allowedBindings.get(field?.field_signature);
    if (
      !binding ||
      !resolvedIds.has(binding.question_id) ||
      field.question_id !== binding.question_id ||
      field.selector !== binding.selector ||
      field.control_type !== binding.control_type ||
      field.source !== "local_confirmed_profile_fact" ||
      typeof field.value !== "string" ||
      field.value.length > 4000
    ) {
      throw new Error("本机 Agent 返回了当前任务范围之外的资料字段。");
    }
    return { ...field };
  });
  const localBySignature = new Map(
    localFields.map((field) => [field.field_signature, field]),
  );
  const existingFields = (task.plan.fields || []).filter(
    (field) => !localBySignature.has(field.field_signature),
  );
  const reviewFields = (task.plan.review_fields || []).map((field) => {
    const local = localBySignature.get(field.field_signature);
    return local
      ? {
          ...field,
          action: "fill",
          profile_field: local.profile_field,
          reason: local.reason,
          value: local.value,
          display_value: local.display_value,
        }
      : field;
  });
  const merged = {
    ...task,
    status:
      task.status === "manual_only" && localFields.length
        ? "ready"
        : task.status,
    plan: {
      ...task.plan,
      fields: [...existingFields, ...localFields],
      review_fields: reviewFields,
      profile_questions: questions.filter(
        (question) => !resolvedIds.has(question.question_id),
      ),
      local_profile_resolution: {
        resolved_question_ids: [...resolvedIds],
        field_count: localFields.length,
      },
    },
  };
  return validateFillTask(merged, task.form_url);
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

export function validateEvidenceAcknowledgement(
  taskId,
  result,
  serverTask,
) {
  const expectedStatus = {
    fill_previewed: "previewed",
    fill_executed: "executed_locally",
    fill_undone: "undone_locally",
    fill_failed: "failed_locally",
  }[result?.event_type];
  if (
    !expectedStatus ||
    serverTask?.fill_task_id !== taskId ||
    serverTask?.status !== expectedStatus
  ) {
    throw new Error("工作台没有确认页面执行结果，请重试证据同步。");
  }

  const expectedEvidence = {
    event_type: result.event_type,
    filled_count: result.field_results.filter(
      (item) => item.status === "filled",
    ).length,
    preserved_count: result.field_results.filter(
      (item) =>
        item.status === "skipped" &&
        item.reason_code === "already_has_value",
    ).length,
    blocked_count: result.field_results.filter((item) =>
      ["missing", "blocked", "fingerprint_mismatch"].includes(item.status),
    ).length,
  };
  const acknowledgedEvidence = serverTask.plan?.last_local_evidence;
  if (
    !acknowledgedEvidence ||
    Object.entries(expectedEvidence).some(
      ([key, value]) => acknowledgedEvidence[key] !== value,
    )
  ) {
    throw new Error("工作台确认的执行证据计数不一致，请重试同步。");
  }
  return serverTask;
}

export async function evidenceIdempotencyKey(
  taskId,
  result,
  taskVersion = null,
) {
  const digest = await sha256(
    stableStringify({
      task_id: taskId,
      task_version: taskVersion,
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
