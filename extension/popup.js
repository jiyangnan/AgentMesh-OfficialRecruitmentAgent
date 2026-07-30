import {
  assistSessionIdempotencyKey,
  evidenceIdempotencyKey,
  evidencePayload,
  normalizeApiKey,
  normalizeServerUrl,
  observationIdempotencyKey,
  validateFillSession,
  validateFillTask,
} from "./protocol.js";

const CONNECTION_STORAGE_KEY = "ora_connection_v1";
const INSTALLATION_STORAGE_KEY = "ora_installation_id_v1";
const ACTIVE_SESSION_STORAGE_KEY = "ora_active_assist_session_v1";

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
let currentTab = null;
let currentCapability = null;
let currentFrameId = null;

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

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("没有找到当前网页标签页。");
  if (!["http:", "https:"].includes(new URL(tab.url).protocol)) {
    throw new Error("请在真实招聘官网页面中使用辅助填写。");
  }
  return tab;
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
  if (!value) {
    connectedUi(false);
    return;
  }
  try {
    currentServer = normalizeServerUrl(value.server_url);
    currentApiKey = normalizeApiKey(value.api_key);
    serverInput.value = currentServer;
    connection.textContent = "已连接三端工作区";
    connectedUi(true);
  } catch {
    await chrome.storage.local.remove(CONNECTION_STORAGE_KEY);
    connectedUi(false);
  }
}

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  try {
    currentServer = normalizeServerUrl(serverInput.value);
    currentApiKey = normalizeApiKey(apiKeyInput.value);
    await chrome.storage.local.set({
      [CONNECTION_STORAGE_KEY]: {
        server_url: currentServer,
        api_key: currentApiKey,
      },
    });
    apiKeyInput.value = "";
    connection.textContent = "已连接三端工作区";
    connectedUi(true);
    showMessage("连接完成。现在打开任意官网报名步骤并选择“识别当前步骤”。");
  } catch (error) {
    showMessage(error instanceof Error ? error.message : "连接失败。", true);
  }
});

disconnectButton.addEventListener("click", async () => {
  await chrome.storage.local.remove(CONNECTION_STORAGE_KEY);
  await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
  currentServer = null;
  currentApiKey = null;
  currentTask = null;
  currentCapability = null;
  connection.textContent = "尚未连接 AgentMesh360";
  connectedUi(false);
  showMessage("本机扩展连接已清除。");
});

async function createAssistSession(tab) {
  if (!currentServer || !currentApiKey) {
    throw new Error("请先连接 AgentMesh360 三端工作区。");
  }
  const installId = await installationId();
  const idempotencyKey = await assistSessionIdempotencyKey(
    tab.url,
    installId,
    Date.now(),
  );
  const response = await fetch(`${currentServer}/api/v1/assist-sessions`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${currentApiKey}`,
      "Idempotency-Key": idempotencyKey,
    },
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
    return validateFillSession(
      await readTask(active.fill_task_id),
      tab.url,
    );
  } catch {
    await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
    currentCapability = null;
    return null;
  }
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
    currentTask = validateFillTask(observed.result, currentTab.url);
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
      }
    } else {
      connection.textContent =
        `第 ${currentTask.plan.step_index ?? 1} 步需手动处理`;
      showMessage(
        "当前步骤没有可安全自动填写的字段。手动完成后进入下一步，再重新打开扩展。",
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
  fieldCount.textContent = String(task.plan.fields.length);
  const displayFields = task.plan.fields.length
    ? task.plan.fields
    : task.plan.review_fields ?? [];
  const rows = displayFields.map((field) => {
    const row = document.createElement("div");
    row.className = "field-row";
    const label = document.createElement("span");
    const name = document.createElement("strong");
    const reason = document.createElement("small");
    const value = document.createElement("code");
    name.textContent =
      field.site_label || field.profile_field || "待人工处理字段";
    reason.textContent = field.reason;
    value.textContent =
      field.value ??
      (field.action === "manual" || field.action === "review"
        ? "手动"
        : "");
    label.append(name, reason);
    row.append(label, value);
    return row;
  });
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
  try {
    await injectExecutor(currentTab.id, { frameIds: [currentFrameId] });
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId: currentTab.id, frameIds: [currentFrameId] },
      func: async (task) => globalThis.__ORA_EXECUTOR__.execute(task),
      args: [currentTask],
    });
    const result = execution.result;
    if (!result?.ok) throw new Error(result?.message ?? "页面填写失败。");
    await sendEvidence(currentTask.fill_task_id, result);
    connection.textContent =
      `第 ${currentTask.plan.step_index ?? 1} 步已填写`;
    undoButton.disabled = false;
    showMessage(
      `已填写 ${result.field_results.filter((item) => item.status === "filled").length} 个字段，证据已同步到 Web。核对后请手动进入下一步。`,
    );
  } catch (error) {
    connection.textContent = "填写未完成";
    showMessage(error instanceof Error ? error.message : "填写失败。", true);
  } finally {
    executeButton.disabled = false;
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
    showMessage(`已恢复 ${result.field_results.length} 个字段。`);
  } catch (error) {
    showMessage(error instanceof Error ? error.message : "撤销失败。", true);
  }
});

async function sendEvidence(taskId, result) {
  const idempotencyKey = await evidenceIdempotencyKey(taskId, result);
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
}

void restoreConnection();
