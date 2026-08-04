import {
  assistSessionIdempotencyKey,
  evidenceIdempotencyKey,
  evidencePayload,
  mergeLocalProfileResolution,
  normalizeApiKey,
  normalizeServerUrl,
  observationIdempotencyKey,
  validateFillSession,
  validateFillTask,
} from "./protocol.js";

const CONNECTION_STORAGE_KEY = "ora_connection_v1";
const INSTALLATION_STORAGE_KEY = "ora_installation_id_v1";
const ACTIVE_SESSION_STORAGE_KEY = "ora_active_assist_session_v1";
const AUTO_CONNECT_DISABLED_STORAGE_KEY =
  "ora_local_auto_connect_disabled_v1";
const LOCAL_DEVELOPMENT_SERVERS = [
  "http://127.0.0.1:8010",
  "http://127.0.0.1:8000",
];

const setup = document.querySelector("#setup");
const assist = document.querySelector("#assist");
const connectionForm = document.querySelector("#connection-form");
const taskForm = document.querySelector("#task-form");
const serverInput = document.querySelector("#server-url");
const apiKeyInput = document.querySelector("#api-key");
const disconnectButton = document.querySelector("#disconnect");
const review = document.querySelector("#review");
const targetOrigin = document.querySelector("#target-origin");
const stepLabel = document.querySelector("#step-label");
const fieldCount = document.querySelector("#field-count");
const fieldList = document.querySelector("#field-list");
const executeButton = document.querySelector("#execute");
const undoButton = document.querySelector("#undo");
const connection = document.querySelector("#connection");
const message = document.querySelector("#message");

let currentTask = null;
let currentServer = null;
let currentApiKey = null;
let currentConnectionMode = null;
let currentTab = null;
let currentCapability = null;
let currentFrameId = null;
let refreshFromTaskId = null;
let localProfileResolutionError = null;

function showMessage(text, error = false) {
  message.hidden = false;
  message.textContent = text;
  message.classList.toggle("error", error);
}

function connectedUi(connected) {
  setup.hidden = connected;
  assist.hidden = !connected;
  disconnectButton.hidden = !connected;
  if (!connected) {
    review.hidden = true;
    executeButton.disabled = true;
    undoButton.disabled = true;
  }
}

function isLocalDevelopmentServer(value) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "http:" &&
      ["127.0.0.1", "localhost"].includes(url.hostname) &&
      ["8000", "8010"].includes(url.port)
    );
  } catch {
    return false;
  }
}

function connectionLabel() {
  return currentConnectionMode === "local_development"
    ? "已连接本机开发工作台"
    : "已连接三端工作区";
}

function updateApiKeyRequirement() {
  apiKeyInput.required = !isLocalDevelopmentServer(serverInput.value);
}

async function probeDevelopmentServer(serverUrl) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 800);
  try {
    const response = await fetch(`${serverUrl}/api/v1/health`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const payload = await response.json();
    if (
      payload?.status !== "ok" ||
      payload?.environment !== "development" ||
      payload?.auth_mode !== "development"
    ) {
      return null;
    }
    return normalizeServerUrl(serverUrl);
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

async function verifiedDevelopmentServer(serverUrl) {
  const verified = await probeDevelopmentServer(serverUrl);
  if (!verified) {
    throw new Error(
      "本机工作台身份校验失败。请确认开发服务正在 8010 或 8000 端口运行。",
    );
  }
  return verified;
}

async function autoConnectLocalDevelopment() {
  const candidates = await Promise.all(
    LOCAL_DEVELOPMENT_SERVERS.map(probeDevelopmentServer),
  );
  const serverUrl = candidates.find(Boolean);
  if (!serverUrl) return false;
  currentServer = serverUrl;
  currentApiKey = null;
  currentConnectionMode = "local_development";
  serverInput.value = currentServer;
  updateApiKeyRequirement();
  await chrome.storage.local.set({
    [CONNECTION_STORAGE_KEY]: {
      server_url: currentServer,
      mode: currentConnectionMode,
    },
  });
  connection.textContent = connectionLabel();
  connectedUi(true);
  showMessage("已自动连接本机开发工作台，可直接识别当前步骤。");
  return true;
}

function assistSessionHeaders(idempotencyKey) {
  const headers = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "Idempotency-Key": idempotencyKey,
  };
  if (currentApiKey) {
    headers.Authorization = `Bearer ${currentApiKey}`;
    return headers;
  }
  if (currentConnectionMode !== "local_development") {
    throw new Error("线上工作台需要 AgentMesh360 API Key。");
  }
  return {
    ...headers,
    "X-ORA-Account": "acct-synthetic-demo",
    "X-ORA-Actor": "extension-local",
    "X-ORA-Surface": "web",
  };
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("没有找到当前网页标签页。");
  if (!["http:", "https:"].includes(new URL(tab.url).protocol)) {
    throw new Error("请在真实招聘官网页面中使用辅助填写。");
  }
  return tab;
}

function comparablePageUrl(value) {
  const url = new URL(value);
  url.hash = "";
  return url.href;
}

async function installationId() {
  const stored = await chrome.storage.local.get(INSTALLATION_STORAGE_KEY);
  if (stored?.[INSTALLATION_STORAGE_KEY]) {
    return stored[INSTALLATION_STORAGE_KEY];
  }
  const value = `chrome-${crypto.randomUUID()}`;
  await chrome.storage.local.set({ [INSTALLATION_STORAGE_KEY]: value });
  return value;
}

async function restoreConnection() {
  const stored = await chrome.storage.local.get(CONNECTION_STORAGE_KEY);
  const value = stored?.[CONNECTION_STORAGE_KEY];
  if (value) {
    try {
      const restoredServer = normalizeServerUrl(value.server_url);
      const localDevelopment = isLocalDevelopmentServer(restoredServer);
      currentServer = localDevelopment
        ? await verifiedDevelopmentServer(restoredServer)
        : restoredServer;
      currentConnectionMode = localDevelopment
        ? "local_development"
        : "production";
      currentApiKey =
        !localDevelopment && value.api_key
          ? normalizeApiKey(value.api_key)
          : null;
      if (currentConnectionMode === "production" && !currentApiKey) {
        throw new Error("线上连接缺少 API Key。");
      }
      serverInput.value = currentServer;
      updateApiKeyRequirement();
      connection.textContent = connectionLabel();
      connectedUi(true);
      return;
    } catch {
      await chrome.storage.local.remove(CONNECTION_STORAGE_KEY);
    }
  }
  const disabled = await chrome.storage.local.get(
    AUTO_CONNECT_DISABLED_STORAGE_KEY,
  );
  if (!disabled?.[AUTO_CONNECT_DISABLED_STORAGE_KEY]) {
    if (await autoConnectLocalDevelopment()) return;
  }
  updateApiKeyRequirement();
  connectedUi(false);
}

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  try {
    const requestedServer = normalizeServerUrl(serverInput.value);
    const localDevelopment = isLocalDevelopmentServer(requestedServer);
    currentServer = localDevelopment
      ? await verifiedDevelopmentServer(requestedServer)
      : requestedServer;
    currentConnectionMode = localDevelopment
      ? "local_development"
      : "production";
    currentApiKey =
      !localDevelopment && apiKeyInput.value.trim()
        ? normalizeApiKey(apiKeyInput.value)
        : null;
    if (currentConnectionMode === "production" && !currentApiKey) {
      throw new Error("线上工作台需要 AgentMesh360 API Key。");
    }
    await chrome.storage.local.set({
      [CONNECTION_STORAGE_KEY]: {
        server_url: currentServer,
        ...(currentApiKey ? { api_key: currentApiKey } : {}),
        mode: currentConnectionMode,
      },
    });
    await chrome.storage.local.remove(
      AUTO_CONNECT_DISABLED_STORAGE_KEY,
    );
    apiKeyInput.value = "";
    connection.textContent = connectionLabel();
    connectedUi(true);
    showMessage("连接完成。现在打开任意官网报名步骤并选择“识别当前步骤”。");
  } catch (error) {
    showMessage(error instanceof Error ? error.message : "连接失败。", true);
  }
});

disconnectButton.addEventListener("click", async () => {
  await chrome.storage.local.remove(CONNECTION_STORAGE_KEY);
  await chrome.storage.local.set({
    [AUTO_CONNECT_DISABLED_STORAGE_KEY]: true,
  });
  await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
  currentServer = null;
  currentApiKey = null;
  currentConnectionMode = null;
  currentTask = null;
  currentCapability = null;
  connection.textContent = "尚未连接 AgentMesh360";
  connectedUi(false);
  showMessage("本机扩展连接已清除。");
});

async function createAssistSession(tab) {
  if (!currentServer) {
    throw new Error("请先连接 AgentMesh360 三端工作区。");
  }
  const installId = await installationId();
  const idempotencyKey = await assistSessionIdempotencyKey(
    tab.url,
    installId,
    undefined,
    refreshFromTaskId,
  );
  const response = await fetch(`${currentServer}/api/v1/assist-sessions`, {
    method: "POST",
    headers: assistSessionHeaders(idempotencyKey),
    body: JSON.stringify({
      page_url: tab.url,
      page_title: tab.title || null,
      installation_id: installId,
      expires_in_seconds: 900,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(
      payload?.error?.message ??
        payload?.detail?.message ??
        "创建当前页面辅助会话失败。",
    );
  }
  const task = validateFillSession(payload.result.task, tab.url);
  const active = {
    server_url: currentServer,
    fill_task_id: task.fill_task_id,
    extension_capability: payload.extension_capability,
    allowed_origin: task.allowed_origin,
    expires_at: task.expires_at,
  };
  await chrome.storage.session.set({
    [ACTIVE_SESSION_STORAGE_KEY]: active,
  });
  currentCapability = active.extension_capability;
  refreshFromTaskId = null;
  return task;
}

async function restoreActiveSession(tab) {
  const stored = await chrome.storage.session.get(
    ACTIVE_SESSION_STORAGE_KEY,
  );
  const active = stored?.[ACTIVE_SESSION_STORAGE_KEY];
  if (
    !active ||
    active.server_url !== currentServer ||
    active.allowed_origin !== new URL(tab.url).origin ||
    new Date(active.expires_at).getTime() <= Date.now()
  ) {
    return null;
  }
  currentCapability = active.extension_capability;
  try {
    const task = validateFillSession(
      await readTask(active.fill_task_id),
      tab.url,
    );
    if (task.status === "revoked") {
      await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
      currentCapability = null;
      return null;
    }
    if (task.profile_update_available) {
      refreshFromTaskId = task.fill_task_id;
      await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
      currentCapability = null;
      return null;
    }
    const boundPageUrl = task.plan?.top_page_url ?? task.form_url;
    if (
      boundPageUrl &&
      comparablePageUrl(boundPageUrl) !== comparablePageUrl(tab.url)
    ) {
      currentCapability = null;
      return null;
    }
    return task;
  } catch {
    await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
    currentCapability = null;
    return null;
  }
}

async function hydrateLocalProfileFields(task) {
  const questionCount = task.plan?.profile_questions?.length ?? 0;
  localProfileResolutionError = null;
  if (!questionCount || !currentApiKey) return task;
  try {
    const response = await fetch(
      "http://127.0.0.1:8765/v1/fill-tasks/resolved-fields",
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          Authorization: `Bearer ${currentApiKey}`,
        },
        cache: "no-store",
        body: JSON.stringify({ fill_task_id: task.fill_task_id }),
      },
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(
        payload?.error?.message ?? "本机 Agent 没有返回已确认资料。",
      );
    }
    return mergeLocalProfileResolution(task, payload);
  } catch (error) {
    localProfileResolutionError =
      error instanceof Error
        ? error.message
        : "本机 Agent 尚未启动，暂时无法读取已确认资料。";
    return task;
  }
}

function restoreTaskUi(task) {
  currentTask = task;
  currentFrameId = Number.isInteger(task.plan?.frame_id)
    ? task.plan.frame_id
    : null;
  renderTask(task);
  if (task.status === "executed_locally") {
    const questionCount = task.plan?.profile_questions?.length ?? 0;
    connection.textContent = questionCount
      ? `第 ${task.plan.step_index ?? 1} 步待补档案`
      : `第 ${task.plan.step_index ?? 1} 步已填写`;
    const filledCount = task.plan?.last_local_evidence?.filled_count;
    undoButton.disabled =
      currentFrameId === null || filledCount === 0;
    showMessage(
      questionCount
        ? `已有信息已经填写，但本步骤仍有 ${questionCount} 个档案问题。请回到本机 Agent 集中回答；在新档案确认并重新识别前，不要进入网站下一步。`
        : currentFrameId === null
        ? "请重新识别当前步骤后再撤销填写。"
        : filledCount === 0
          ? "当前步骤没有改写已有内容。请核对页面，或手动进入网站下一步。"
          : "当前步骤已经填写。核对后可撤销，或手动进入网站下一步。",
    );
    return;
  }
  if (task.status === "ready" || task.status === "previewed") {
    connection.textContent =
      `第 ${task.plan.step_index ?? 1} 步待确认`;
    executeButton.disabled = currentFrameId === null;
    return;
  }
  if (task.status === "manual_only") {
    connection.textContent =
      `第 ${task.plan.step_index ?? 1} 步需手动处理`;
    const questionCount = task.plan?.profile_questions?.length ?? 0;
    showMessage(
      questionCount
        ? `本步骤尚未完成：发现 ${questionCount} 个档案缺口。请回到本机 Agent 集中回答，确认新档案后再次识别当前步骤。`
        : "当前步骤没有可安全自动填写的字段。手动完成后进入下一步，再重新打开扩展。",
    );
    return;
  }
  review.hidden = true;
  connection.textContent =
    task.status === "undone_locally"
      ? "当前步骤已撤销"
      : "等待识别当前步骤";
}

async function restorePopupState() {
  await restoreConnection();
  if (!currentServer) return;
  try {
    currentTab = await activeTab();
    const restored = await restoreActiveSession(currentTab);
    const task = restored
      ? await hydrateLocalProfileFields(restored)
      : null;
    if (task?.plan?.fields) {
      restoreTaskUi(task);
    } else if (refreshFromTaskId) {
      showMessage("个人档案已有更新。选择“识别当前步骤”即可在当前页面补填空字段。");
    }
  } catch {
    // Connection remains usable; the user can explicitly inspect the page.
  }
}

function pendingRepeatGroups(task) {
  return (task.plan?.repeat_groups || []).filter(
    (item) => Number(item.pending_count) > 0,
  );
}

async function observeTask(sessionTask, observation) {
  const observationKey = await observationIdempotencyKey(
    sessionTask.fill_task_id,
    observation,
    sessionTask.version,
  );
  const response = await fetch(
    `${currentServer}/api/v1/fill-tasks/${sessionTask.fill_task_id}/observe`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Authorization: `Bearer ${currentCapability}`,
        "Idempotency-Key": observationKey,
      },
      body: JSON.stringify(observation),
    },
  );
  const observed = await response.json();
  if (!response.ok) {
    throw new Error(
      observed?.error?.message ??
        observed?.detail?.message ??
        "生成当前步骤的填写计划失败。",
    );
  }
  return validateFillTask(observed.result, currentTab.url);
}

taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  review.hidden = true;
  executeButton.disabled = true;
  undoButton.disabled = true;
  connection.textContent = "正在识别当前步骤";
  try {
    currentTab = await activeTab();
    const sessionTask =
      (await restoreActiveSession(currentTab)) ??
      (await createAssistSession(currentTab));

    const discovered = await discoverCurrentStep(currentTab);
    currentFrameId = discovered.frameId;
    const observation = {
      ...discovered.observation,
      top_page_url: currentTab.url,
      frame_id: currentFrameId,
    };
    currentTask = await hydrateLocalProfileFields(
      await observeTask(sessionTask, observation),
    );
    renderTask(currentTask);
    if (currentTask.plan.fields.length) {
      if (currentTask.status === "ready") {
        await sendEvidence(currentTask.fill_task_id, {
          event_type: "fill_previewed",
          step_id: currentTask.plan.step_id,
          page_fingerprint: currentTask.form_fingerprint,
          field_results: [],
        });
        currentTask.status = "previewed";
      }
      if (currentTask.status === "executed_locally") {
        connection.textContent =
          `第 ${currentTask.plan.step_index ?? 1} 步已填写`;
        undoButton.disabled = false;
        showMessage(
          "当前步骤已经填写。核对后手动点击网站的下一步，再重新打开扩展。",
        );
      } else {
        connection.textContent =
          `第 ${currentTask.plan.step_index ?? 1} 步待确认`;
        executeButton.disabled = false;
        const questionCount =
          currentTask.plan.profile_questions?.length ?? 0;
        const pendingGroups = pendingRepeatGroups(currentTask);
        if (pendingGroups.length) {
          const pendingCount = pendingGroups.reduce(
            (total, item) => total + Number(item.pending_count),
            0,
          );
          showMessage(
            `档案还有 ${pendingCount} 条结构化记录未出现在页面。先建立空白记录，系统会重新生成完整预览，再由你确认填写。`,
          );
        } else if (questionCount) {
          showMessage(
            localProfileResolutionError
              ? `${localProfileResolutionError} 当前仍有 ${questionCount} 个档案问题未解决。`
              : `可先填写已有信息；本步骤另有 ${questionCount} 个档案问题。请在工作台补充并交给本机 Agent，在确认前不要进入网站下一步。`,
          );
        }
      }
    } else {
      connection.textContent =
        `第 ${currentTask.plan.step_index ?? 1} 步需手动处理`;
      const questionCount =
        currentTask.plan.profile_questions?.length ?? 0;
      showMessage(
        questionCount
          ? `本步骤尚未完成：发现 ${questionCount} 个档案缺口。请回到本机 Agent 集中回答，确认新档案后再次识别当前步骤。`
          : "当前步骤没有可安全自动填写的字段。手动完成后进入下一步，再重新打开扩展。",
      );
    }
  } catch (error) {
    connection.textContent = "识别失败";
    showMessage(error instanceof Error ? error.message : "识别失败。", true);
  }
});

async function readTask(taskId) {
  const response = await fetch(`${currentServer}/api/v1/fill-tasks/${taskId}`, {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${currentCapability}`,
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(
      payload?.error?.message ??
        payload?.detail?.message ??
        "读取辅助填写会话失败。",
    );
  }
  return payload;
}

async function discoverCurrentStep(tab) {
  await injectExecutor(tab.id, { allFrames: true });
  const inspections = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true },
    func: async () => {
      try {
        const discovered = await globalThis.__ORA_EXECUTOR__.discoverPage();
        return { ok: true, ...discovered };
      } catch (error) {
        return {
          ok: false,
          message:
            error instanceof Error
              ? error.message
              : "当前框架没有可观察的报名表。",
        };
      }
    },
  });
  const currentOrigin = new URL(tab.url).origin;
  const candidates = inspections
    .filter(
      (item) =>
        item.result?.ok &&
        new URL(item.result.observation.page_url).origin === currentOrigin,
    )
    .sort((left, right) => right.result.score - left.result.score);
  if (!candidates.length) {
    throw new Error(
      "当前页面及其同源内嵌区域中没有可识别的报名步骤。请先打开报名弹窗或点击任意待填写字段。",
    );
  }
  if (
    candidates.length > 1 &&
    candidates[0].result.score === candidates[1].result.score
  ) {
    throw new Error(
      "当前页面存在多个同等优先级的报名步骤，请先点击目标步骤中的任意字段。",
    );
  }
  return {
    frameId: candidates[0].frameId,
    observation: candidates[0].result.observation,
  };
}

function renderTask(task) {
  const stepIndex = task.plan.step_index ?? 1;
  stepLabel.textContent = `第 ${stepIndex} 步`;
  targetOrigin.textContent = new URL(
    task.plan.frame_url ?? task.form_url,
  ).host;
  const executableBySignature = new Map(
    task.plan.fields.map((field) => [field.field_signature, field]),
  );
  const reviewedFields = task.plan.review_fields ?? [];
  const displayFields = reviewedFields.length
    ? reviewedFields.map((field) => ({
        ...field,
        ...executableBySignature.get(field.field_signature),
      }))
    : task.plan.fields;
  const repeatGroups = pendingRepeatGroups(task);
  const pendingRecordCount = repeatGroups.reduce(
    (total, item) => total + Number(item.pending_count),
    0,
  );
  fieldCount.textContent = `可填 ${task.plan.fields.length} / ${displayFields.length}${
    pendingRecordCount ? ` · 待新增 ${pendingRecordCount} 条` : ""
  }`;
  fieldCount.title = "本次可自动填写字段数 / 当前页面识别字段总数";
  executeButton.textContent = repeatGroups.length
    ? "建立缺少的记录"
    : "确认填写";
  const repeatRows = repeatGroups.map((group) => {
    const row = document.createElement("div");
    row.className = "field-row field-row--review";
    const label = document.createElement("span");
    const name = document.createElement("strong");
    const reason = document.createElement("small");
    const value = document.createElement("code");
    name.textContent = group.label;
    reason.textContent = "先建立空白记录，随后重新生成完整预览";
    value.textContent = `${group.observed_count} → ${group.desired_count}`;
    label.append(name, reason);
    row.append(label, value);
    return row;
  });
  const fieldRows = displayFields.map((field) => {
    const row = document.createElement("div");
    const action = field.action ?? "fill";
    row.className = `field-row field-row--${action}`;
    const label = document.createElement("span");
    const name = document.createElement("strong");
    const reason = document.createElement("small");
    const value = document.createElement("code");
    name.textContent =
      field.site_label || field.profile_field || "待人工处理字段";
    const statusCopy = {
      fill: ["将从已确认档案填写", field.display_value ?? field.value ?? ""],
      missing: ["标准简历未提供该信息", "待补充"],
      manual: ["此字段保留手动处理", "手动"],
      unmapped: ["尚未建立可靠字段映射", "未识别"],
      review: ["需要核对后再处理", "待核对"],
    };
    const [reasonText, valueText] = statusCopy[action] ?? [
      "需要人工处理",
      "待处理",
    ];
    reason.textContent = reasonText;
    value.textContent = valueText;
    label.append(name, reason);
    row.append(label, value);
    return row;
  });
  const rows = [...repeatRows, ...fieldRows];
  if (!rows.length) {
    const row = document.createElement("div");
    row.className = "field-row";
    row.textContent = "当前步骤没有可识别字段";
    rows.push(row);
  }
  fieldList.replaceChildren(...rows);
  review.hidden = false;
}

async function injectExecutor(tabId, target) {
  await chrome.scripting.executeScript({
    target: { tabId, ...target },
    files: ["executor.js"],
  });
}

executeButton.addEventListener("click", async () => {
  if (
    !currentTask ||
    !currentTab?.id ||
    !currentServer ||
    currentFrameId === null
  ) {
    return;
  }
  executeButton.disabled = true;
  message.hidden = true;
  let executionCompleted = false;
  try {
    const latestTask = validateFillSession(
      await readTask(currentTask.fill_task_id),
      currentTab.url,
    );
    if (latestTask.profile_update_available) {
      refreshFromTaskId = currentTask.fill_task_id;
      await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
      currentTask = null;
      currentCapability = null;
      review.hidden = true;
      connection.textContent = "个人档案已有更新";
      showMessage(
        "本次没有填写。请重新选择“识别当前步骤”，核对新计划后再补填空字段。",
      );
      return;
    }
    if (latestTask.version !== currentTask.version) {
      currentTask = validateFillTask(latestTask, currentTab.url);
      renderTask(currentTask);
      connection.textContent = "填写计划已有变化";
      showMessage("本次没有填写。请重新核对当前步骤的最新计划。", true);
      return;
    }
    const repeatGroups = pendingRepeatGroups(currentTask);
    if (repeatGroups.length) {
      connection.textContent = "正在建立缺少的记录";
      await injectExecutor(currentTab.id, { frameIds: [currentFrameId] });
      const [preparation] = await chrome.scripting.executeScript({
        target: { tabId: currentTab.id, frameIds: [currentFrameId] },
        func: async (task) =>
          globalThis.__ORA_EXECUTOR__.prepareRepeatGroups(task),
        args: [currentTask],
      });
      const preparationResult = preparation.result;
      if (!preparationResult?.ok) {
        throw new Error(
          preparationResult?.message ?? "建立重复记录失败。",
        );
      }
      const preparedTask = currentTask;
      const discovered = await discoverCurrentStep(currentTab);
      if (discovered.frameId !== currentFrameId) {
        throw new Error("新增记录后报名步骤所在框架发生变化，请重新识别。");
      }
      const observation = {
        ...discovered.observation,
        top_page_url: currentTab.url,
        frame_id: currentFrameId,
        preparation_step_id: preparedTask.plan.step_id,
      };
      currentTask = await hydrateLocalProfileFields(
        await observeTask(preparedTask, observation),
      );
      renderTask(currentTask);
      if (currentTask.status === "ready") {
        await sendEvidence(currentTask.fill_task_id, {
          event_type: "fill_previewed",
          step_id: currentTask.plan.step_id,
          page_fingerprint: currentTask.form_fingerprint,
          field_results: [],
        });
        currentTask.status = "previewed";
      }
      connection.textContent =
        `第 ${currentTask.plan.step_index ?? 1} 步待确认`;
      const questionCount =
        currentTask.plan.profile_questions?.length ?? 0;
      showMessage(questionCount
        ? `已建立 ${preparationResult.added_count} 条空白记录。请核对完整预览并填写已有信息；本步骤仍有 ${questionCount} 个档案问题，之后必须回到本机 Agent 集中回答。`
        : `已建立 ${preparationResult.added_count} 条空白记录。请核对更新后的完整预览，再选择“确认填写”。`);
      return;
    }
    await injectExecutor(currentTab.id, { frameIds: [currentFrameId] });
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId: currentTab.id, frameIds: [currentFrameId] },
      func: async (task) => globalThis.__ORA_EXECUTOR__.execute(task),
      args: [currentTask],
    });
    const result = execution.result;
    if (!result?.ok) throw new Error(result?.message ?? "页面填写失败。");
    await sendEvidence(currentTask.fill_task_id, result);
    const filledCount = result.field_results.filter(
      (item) => item.status === "filled",
    ).length;
    const preservedCount = result.field_results.filter(
      (item) =>
        item.status === "skipped" &&
        item.reason_code === "already_has_value",
    ).length;
    undoButton.disabled = filledCount === 0;
    executionCompleted = true;
    const profileQuestionCount =
      currentTask.plan.profile_questions?.length ?? 0;
    connection.textContent = profileQuestionCount
      ? `第 ${currentTask.plan.step_index ?? 1} 步待补档案`
      : `第 ${currentTask.plan.step_index ?? 1} 步已填写`;
    const fillSummary = `已填写 ${filledCount} 个空字段${
      preservedCount ? `；保留 ${preservedCount} 个已有字段` : ""
    }。`;
    showMessage(profileQuestionCount
      ? `${fillSummary}本步骤尚未完成：还有 ${profileQuestionCount} 个档案问题。请回到本机 Agent 集中回答；确认新档案并重新识别前，不要进入网站下一步。`
      : `${fillSummary}证据已同步到 Web，核对后请手动进入下一步。`);
  } catch (error) {
    connection.textContent = "填写未完成";
    showMessage(error instanceof Error ? error.message : "填写失败。", true);
  } finally {
    executeButton.disabled = executionCompleted;
  }
});

undoButton.addEventListener("click", async () => {
  if (
    !currentTask ||
    !currentTab?.id ||
    !currentServer ||
    currentFrameId === null
  ) {
    return;
  }
  undoButton.disabled = true;
  try {
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId: currentTab.id, frameIds: [currentFrameId] },
      func: (taskId, stepId) =>
        globalThis.__ORA_EXECUTOR__.undo(taskId, stepId),
      args: [currentTask.fill_task_id, currentTask.plan.step_id],
    });
    const result = execution.result;
    if (!result?.ok) throw new Error(result?.message ?? "撤销失败。");
    await sendEvidence(currentTask.fill_task_id, result);
    connection.textContent = "已撤销当前步骤的填写";
    executeButton.disabled = false;
    showMessage(
      `已恢复 ${result.field_results.length} 个字段${
        result.removed_repeat_group_count
          ? `；移除 ${result.removed_repeat_group_count} 条本次新增记录`
          : ""
      }。`,
    );
  } catch (error) {
    showMessage(error instanceof Error ? error.message : "撤销失败。", true);
  }
});

async function sendEvidence(taskId, result) {
  const idempotencyKey = await evidenceIdempotencyKey(
    taskId,
    result,
    currentTask?.version ?? null,
  );
  const response = await fetch(
    `${currentServer}/api/v1/fill-tasks/${taskId}/evidence`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Authorization: `Bearer ${currentCapability}`,
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(evidencePayload(result)),
    },
  );
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(
      payload?.error?.message ??
        payload?.detail?.message ??
        "证据回传失败。",
    );
  }
  const serverTask = payload?.result?.fill_task;
  if (
    currentTask &&
    serverTask?.fill_task_id === currentTask.fill_task_id
  ) {
    currentTask.status = serverTask.status;
    currentTask.version = serverTask.version;
  }
}

serverInput.addEventListener("input", updateApiKeyRequirement);

void restorePopupState();
