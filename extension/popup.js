import {
  assistSessionIdempotencyKey,
  evidenceIdempotencyKey,
  evidencePayload,
  mergeLocalProfileResolution,
  normalizeServerUrl,
  observationIdempotencyKey,
  validateFillSession,
  validateFillTask,
} from "./protocol.js";
import { initializeI18n, locale, localizeMessage, t } from "./i18n.js";

await initializeI18n();

const CONNECTION_STORAGE_KEY = "ora_connection_v1";
const INSTALLATION_STORAGE_KEY = "ora_installation_id_v1";
const ACTIVE_SESSION_STORAGE_KEY = "ora_active_assist_session_v1";
const AUTO_CONNECT_DISABLED_STORAGE_KEY =
  "ora_local_auto_connect_disabled_v1";
const LOCAL_AGENT_URL = "http://127.0.0.1:8765";
const INSTALLATION_DESCRIPTOR_FILE = "agentmesh-installation.json";
const NATIVE_MESSAGING_HOST = "com.agentmesh360.officialrecruitment";
const LOCAL_DEVELOPMENT_SERVERS = [
  "http://127.0.0.1:8010",
  "http://127.0.0.1:8000",
];

const setup = document.querySelector("#setup");
const assist = document.querySelector("#assist");
const connectionForm = document.querySelector("#connection-form");
const taskForm = document.querySelector("#task-form");
const disconnectButton = document.querySelector("#disconnect");
const review = document.querySelector("#review");
const targetOrigin = document.querySelector("#target-origin");
const stepLabel = document.querySelector("#step-label");
const fieldCount = document.querySelector("#field-count");
const gateSummaryBox = document.querySelector("#gate-summary");
const fieldList = document.querySelector("#field-list");
const reviewApproval = document.querySelector("#review-approved");
const executeButton = document.querySelector("#execute");
const undoButton = document.querySelector("#undo");
const connection = document.querySelector("#connection");
const message = document.querySelector("#message");
const openWorkbenchButton = document.querySelector("#open-workbench");

let currentTask = null;
let currentServer = null;
let currentConnectionMode = null;
let currentLocalSessionToken = null;
let currentInstallationDescriptor = null;
let currentTab = null;
let currentCapability = null;
let currentFrameId = null;
let refreshFromTaskId = null;
let localProfileResolutionError = null;

function showMessage(text, error = false) {
  message.hidden = false;
  message.textContent = localizeMessage(text);
  message.classList.toggle("error", error);
}

function setConnection(source, values = {}) {
  connection.textContent = t(source, values);
}

function reviewKey(task) {
  const stepId = task?.plan?.step_id;
  if (!task?.fill_task_id || !stepId || !task.form_fingerprint) return null;
  return [
    task.fill_task_id,
    task.version,
    stepId,
    task.form_fingerprint,
    currentFrameId,
  ].join(":");
}

function resetReviewApproval() {
  reviewApproval.checked = false;
  reviewApproval.disabled = true;
  reviewApproval.dataset.reviewKey = "";
  executeButton.disabled = true;
}

function offerReviewApproval(task) {
  resetReviewApproval();
  if (
    currentFrameId === null ||
    !["ready", "previewed"].includes(task?.status) ||
    !task?.plan?.fields?.length
  ) {
    return;
  }
  const key = reviewKey(task);
  if (!key) return;
  reviewApproval.dataset.reviewKey = key;
  reviewApproval.disabled = false;
}

function currentReviewApproved() {
  const key = reviewKey(currentTask);
  return Boolean(
    key &&
      reviewApproval.checked &&
      !reviewApproval.disabled &&
      reviewApproval.dataset.reviewKey === key,
  );
}

function connectedUi(connected) {
  setup.hidden = connected;
  assist.hidden = !connected;
  disconnectButton.hidden = !connected;
  openWorkbenchButton.disabled = !connected;
  if (!connected) {
    review.hidden = true;
    resetReviewApproval();
    undoButton.disabled = true;
  }
}

function workbenchUrl(serverUrl) {
  const url = new URL(serverUrl);
  url.pathname = "/app/";
  url.search = "";
  if (locale() !== "zh-CN") url.searchParams.set("lang", locale());
  url.hash = "";
  return url.toString();
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
    ? t("已连接本机开发工作台")
    : t("已连接本机 Agent");
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
  currentConnectionMode = "local_development";
  currentLocalSessionToken = null;
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

function developmentAssistSessionHeaders(idempotencyKey) {
  if (currentConnectionMode !== "local_development") {
    throw new Error("浏览器扩展尚未连接本机 Agent。");
  }
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "Idempotency-Key": idempotencyKey,
    "X-ORA-Account": "acct-synthetic-demo",
    "X-ORA-Actor": "extension-local",
    "X-ORA-Surface": "web",
  };
}

function localAgentHeaders(sessionToken = null) {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    ...(sessionToken
      ? { Authorization: `Bearer ${sessionToken}` }
      : {}),
  };
}

function validInstallationDescriptor(value) {
  return Boolean(
    value?.schema_version === 1 &&
      /^orainstall_[0-9a-f]{32}$/.test(value.installation_id ?? "") &&
      /^orapair_[A-Za-z0-9_-]{32,96}$/.test(value.pairing_secret ?? "") &&
      value.local_agent_url === LOCAL_AGENT_URL,
  );
}

async function installationDescriptor() {
  if (currentInstallationDescriptor) return currentInstallationDescriptor;
  if (!chrome.runtime?.getURL) {
    throw new Error("扩展缺少本机配对资料，请让 Agent 重新准备扩展。");
  }
  try {
    const response = await fetch(
      chrome.runtime.getURL(INSTALLATION_DESCRIPTOR_FILE),
      { cache: "no-store" },
    );
    const payload = await response.json();
    if (!response.ok || !validInstallationDescriptor(payload)) {
      throw new Error("invalid_descriptor");
    }
    currentInstallationDescriptor = payload;
    return payload;
  } catch {
    throw new Error("扩展缺少本机配对资料，请让 Agent 运行 extension repair。");
  }
}

async function localAgentRequest(path, payload, sessionToken = null) {
  const response = await fetch(`${LOCAL_AGENT_URL}${path}`, {
    method: "POST",
    headers: localAgentHeaders(sessionToken),
    cache: "no-store",
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(
      result?.error?.message ?? "本机 Agent 暂时无法完成扩展连接。",
    );
  }
  return result;
}

async function persistLocalAgentConnection(result) {
  if (
    result?.status !== "connected" ||
    !/^oralocalsession_[A-Za-z0-9_-]{32,128}$/.test(
      result.session_token ?? "",
    ) ||
    !/^orainstall_[0-9a-f]{32}$/.test(result.installation_id ?? "") ||
    !Number.isFinite(new Date(result.expires_at).getTime()) ||
    new Date(result.expires_at).getTime() <= Date.now()
  ) {
    throw new Error("本机 Agent 返回的扩展连接无效。");
  }
  currentServer = normalizeServerUrl(result.server_url);
  currentConnectionMode = "local_agent";
  currentLocalSessionToken = result.session_token;
  await chrome.storage.local.set({
    [CONNECTION_STORAGE_KEY]: {
      server_url: currentServer,
      mode: currentConnectionMode,
      local_session_token: currentLocalSessionToken,
      installation_id: result.installation_id,
      expires_at: result.expires_at,
    },
    [INSTALLATION_STORAGE_KEY]: result.installation_id,
  });
  await chrome.storage.local.remove(AUTO_CONNECT_DISABLED_STORAGE_KEY);
  connection.textContent = connectionLabel();
  connectedUi(true);
}

async function connectLocalAgentWithDescriptor() {
  const descriptor = await installationDescriptor();
  const result = await localAgentRequest("/v1/extension/connect", {
    installation_id: descriptor.installation_id,
    pairing_secret: descriptor.pairing_secret,
  });
  if (result.installation_id !== descriptor.installation_id) {
    throw new Error("本机 Agent 返回了其他扩展的连接。");
  }
  await persistLocalAgentConnection(result);
}

async function connectLocalAgentWithNativeHost() {
  if (!chrome.runtime?.sendNativeMessage) {
    throw new Error(
      "本机 Agent 连接组件尚未就绪，请先把官网安装指令交给你的 Agent。",
    );
  }
  let result;
  try {
    result = await chrome.runtime.sendNativeMessage(
      NATIVE_MESSAGING_HOST,
      {
        contract_version: "officialrecruitment-native-v1",
        action: "connect",
      },
    );
  } catch {
    throw new Error(
      "本机 Agent 连接组件尚未就绪，请先把官网安装指令交给你的 Agent。",
    );
  }
  if (result?.status === "error") {
    throw new Error(
      result?.error?.message ?? "本机 Agent 暂时无法完成浏览器连接。",
    );
  }
  await persistLocalAgentConnection(result);
}

async function connectLocalAgent() {
  let nativeError = null;
  try {
    await connectLocalAgentWithNativeHost();
    return;
  } catch (error) {
    nativeError = error;
  }
  try {
    await connectLocalAgentWithDescriptor();
  } catch {
    throw nativeError;
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

function comparablePageUrl(value) {
  const url = new URL(value);
  url.hash = "";
  return url.href;
}

async function installationId() {
  if (currentInstallationDescriptor?.installation_id) {
    return currentInstallationDescriptor.installation_id;
  }
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
  let value = stored?.[CONNECTION_STORAGE_KEY];
  if (value?.api_key) {
    const sanitized = { ...value };
    delete sanitized.api_key;
    value = sanitized;
    await chrome.storage.local.set({
      [CONNECTION_STORAGE_KEY]: sanitized,
    });
  }
  if (value) {
    try {
      const restoredServer = normalizeServerUrl(value.server_url);
      const localDevelopment = isLocalDevelopmentServer(restoredServer);
      if (localDevelopment) {
        currentServer = await verifiedDevelopmentServer(restoredServer);
        currentConnectionMode = "local_development";
        currentLocalSessionToken = null;
      } else {
        if (
          value.mode !== "local_agent" ||
          !/^oralocalsession_[A-Za-z0-9_-]{32,128}$/.test(
            value.local_session_token ?? "",
          )
        ) {
          throw new Error("旧连接需要重新配对。");
        }
        const status = await localAgentRequest(
          "/v1/extension/status",
          {},
          value.local_session_token,
        );
        if (
          status.status !== "connected" ||
          status.installation_id !== value.installation_id
        ) {
          throw new Error("本机连接状态无效。");
        }
        currentServer = normalizeServerUrl(status.server_url);
        currentConnectionMode = "local_agent";
        currentLocalSessionToken = value.local_session_token;
      }
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
  connectedUi(false);
}

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  try {
    if (!(await autoConnectLocalDevelopment())) {
      await connectLocalAgent();
    }
    showMessage("已连接本机 Agent。现在可识别当前招聘页面。");
  } catch (error) {
    showMessage(error instanceof Error ? error.message : "连接失败。", true);
  }
});

disconnectButton.addEventListener("click", async () => {
  if (currentLocalSessionToken) {
    try {
      await localAgentRequest(
        "/v1/extension/disconnect",
        {},
        currentLocalSessionToken,
      );
    } catch {
      // Local storage is still cleared if the Agent is temporarily offline.
    }
  }
  await chrome.storage.local.remove(CONNECTION_STORAGE_KEY);
  await chrome.storage.local.set({
    [AUTO_CONNECT_DISABLED_STORAGE_KEY]: true,
  });
  await chrome.storage.session.remove(ACTIVE_SESSION_STORAGE_KEY);
  currentServer = null;
  currentConnectionMode = null;
  currentLocalSessionToken = null;
  currentTask = null;
  currentCapability = null;
  setConnection("尚未连接 AgentMesh360");
  connectedUi(false);
  showMessage("本机扩展连接已清除。");
});

async function createAssistSession(tab) {
  if (!currentServer) {
    throw new Error("请先连接本机 Agent。");
  }
  const installId = await installationId();
  const idempotencyKey = await assistSessionIdempotencyKey(
    tab.url,
    installId,
    undefined,
    refreshFromTaskId,
  );
  let payload;
  if (currentConnectionMode === "local_agent") {
    if (!currentLocalSessionToken) {
      throw new Error("浏览器扩展需要重新连接本机 Agent。");
    }
    payload = await localAgentRequest(
      "/v1/extension/assist-sessions",
      {
        page_url: tab.url,
        page_title: tab.title || null,
        idempotency_key: idempotencyKey,
      },
      currentLocalSessionToken,
    );
  } else {
    const response = await fetch(`${currentServer}/api/v1/assist-sessions`, {
      method: "POST",
      headers: developmentAssistSessionHeaders(idempotencyKey),
      body: JSON.stringify({
        page_url: tab.url,
        page_title: tab.title || null,
        installation_id: installId,
        expires_in_seconds: 900,
      }),
    });
    payload = await response.json();
    if (!response.ok) {
      throw new Error(
        payload?.error?.message ??
          payload?.detail?.message ??
          "创建当前页面辅助会话失败。",
      );
    }
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
  if (
    !questionCount ||
    currentConnectionMode !== "local_agent" ||
    !currentLocalSessionToken
  ) {
    return task;
  }
  try {
    const response = await fetch(
      "http://127.0.0.1:8765/v1/fill-tasks/resolved-fields",
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          Authorization: `Bearer ${currentLocalSessionToken}`,
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
        ? localizeMessage(error.message)
        : t("本机 Agent 尚未启动，暂时无法读取已确认资料。");
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
    setConnection(questionCount ? "第 {step} 步待补档案" : "第 {step} 步已填写", {
      step: task.plan.step_index ?? 1,
    });
    const filledCount = task.plan?.last_local_evidence?.filled_count;
    undoButton.disabled =
      currentFrameId === null || filledCount === 0;
    showMessage(
      questionCount
        ? t("已有信息已经填写，但本步骤仍有 {count} 个档案问题。请回到本机 Agent 集中回答；在新档案确认并重新识别前，不要进入网站下一步。", {
            count: questionCount,
          })
        : currentFrameId === null
        ? "请重新识别当前步骤后再撤销填写。"
        : filledCount === 0
          ? "当前步骤没有改写已有内容。请核对页面，或手动进入网站下一步。"
          : "当前步骤已经填写。核对后可撤销，或手动进入网站下一步。",
    );
    return;
  }
  if (task.status === "ready" || task.status === "previewed") {
    setConnection("第 {step} 步待确认", { step: task.plan.step_index ?? 1 });
    offerReviewApproval(task);
    return;
  }
  if (task.status === "manual_only") {
    setConnection("第 {step} 步需手动处理", {
      step: task.plan.step_index ?? 1,
    });
    const questionCount = task.plan?.profile_questions?.length ?? 0;
    showMessage(
      questionCount
        ? t("本步骤尚未完成：发现 {count} 个档案缺口。请回到本机 Agent 集中回答，确认新档案后再次识别当前步骤。", {
            count: questionCount,
          })
        : "当前步骤没有可安全自动填写的字段。手动完成后进入下一步，再重新打开扩展。",
    );
    return;
  }
  review.hidden = true;
  setConnection(
    task.status === "undone_locally" ? "当前步骤已撤销" : "等待识别当前步骤",
  );
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
  resetReviewApproval();
  undoButton.disabled = true;
  setConnection("正在识别当前步骤");
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
        setConnection("第 {step} 步已填写", {
          step: currentTask.plan.step_index ?? 1,
        });
        undoButton.disabled = false;
        showMessage(
          "当前步骤已经填写。核对后手动点击网站的下一步，再重新打开扩展。",
        );
      } else {
        setConnection("第 {step} 步待确认", {
          step: currentTask.plan.step_index ?? 1,
        });
        offerReviewApproval(currentTask);
        const questionCount =
          currentTask.plan.profile_questions?.length ?? 0;
        const pendingGroups = pendingRepeatGroups(currentTask);
        if (pendingGroups.length) {
          const pendingCount = pendingGroups.reduce(
            (total, item) => total + Number(item.pending_count),
            0,
          );
          showMessage(
            t("档案还有 {count} 条结构化记录未出现在页面。先建立空白记录，系统会重新生成完整预览，再由你确认填写。", {
              count: pendingCount,
            }),
          );
        } else if (questionCount) {
          showMessage(
            localProfileResolutionError
              ? t("{error} 当前仍有 {count} 个档案问题未解决。", {
                  error: localProfileResolutionError,
                  count: questionCount,
                })
              : t("可先填写已有信息；本步骤另有 {count} 个档案问题。请在工作台补充并交给本机 Agent，在确认前不要进入网站下一步。", {
                  count: questionCount,
                }),
          );
        }
      }
    } else {
      setConnection("第 {step} 步需手动处理", {
        step: currentTask.plan.step_index ?? 1,
      });
      const questionCount =
        currentTask.plan.profile_questions?.length ?? 0;
      showMessage(
        questionCount
          ? t("本步骤尚未完成：发现 {count} 个档案缺口。请回到本机 Agent 集中回答，确认新档案后再次识别当前步骤。", {
              count: questionCount,
            })
          : "当前步骤没有可安全自动填写的字段。手动完成后进入下一步，再重新打开扩展。",
      );
    }
  } catch (error) {
    setConnection("识别失败");
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
    const failureMessages = [
      ...new Set(
        inspections
          .map((item) => item.result?.message)
          .filter((value) => typeof value === "string" && value.trim()),
      ),
    ];
    if (failureMessages.length === 1) {
      throw new Error(failureMessages[0]);
    }
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

const GATE_TOPIC_LABELS = {
  cohort: "应届身份",
  political_status: "政治面貌",
  education_level: "学历层次",
  major: "专业",
  age: "年龄",
  english_level: "英语等级",
  work_authorization: "工作许可",
  experience_years: "工作年限",
  salary_expectation: "薪资意向",
  other: "其他门槛",
};

function gateTopicLabel(topic) {
  return t(GATE_TOPIC_LABELS[topic] ?? "其他门槛");
}

function renderGateSummary(items) {
  if (!gateSummaryBox) {
    return;
  }
  const conflicts = items.reduce(
    (total, item) => total + (Number(item.conflict_count) || 0),
    0,
  );
  const suspects = items.reduce(
    (total, item) => total + (Number(item.suspect_count) || 0),
    0,
  );
  if (!items.length) {
    gateSummaryBox.hidden = true;
    gateSummaryBox.replaceChildren();
    return;
  }
  gateSummaryBox.hidden = false;
  gateSummaryBox.className = `gate-summary${
    conflicts ? " gate-summary--conflict" : " gate-summary--suspect"
  }`;
  gateSummaryBox.textContent = conflicts
    ? t("本步骤有 {n} 个疑似硬性门槛，其中 {m} 个与档案冲突", {
        n: conflicts + suspects,
        m: conflicts,
      })
    : t("本步骤有 {n} 个疑似硬性门槛，请对照公告要求核对", {
        n: suspects,
      });
}

function renderTask(task) {
  resetReviewApproval();
  const stepIndex = task.plan.step_index ?? 1;
  stepLabel.textContent = t("第 {step} 步", { step: stepIndex });
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
  fieldCount.textContent = t("可填 {fillable} / {total}{pending}", {
    fillable: task.plan.fields.length,
    total: displayFields.length,
    pending: pendingRecordCount
      ? t(" · 待新增 {count} 条", { count: pendingRecordCount })
      : "",
  });
  renderGateSummary(task.plan.gate_summary ?? []);
  fieldCount.title = t("本次可自动填写字段数 / 当前页面识别字段总数");
  executeButton.textContent = repeatGroups.length
    ? t("建立缺少的记录")
    : t("确认填写");
  const repeatRows = repeatGroups.map((group) => {
    const row = document.createElement("div");
    row.className = "field-row field-row--review";
    const label = document.createElement("span");
    const name = document.createElement("strong");
    const reason = document.createElement("small");
    const value = document.createElement("code");
    name.textContent = group.label;
    reason.textContent = t("先建立空白记录，随后重新生成完整预览");
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
      field.site_label || field.profile_field || t("待人工处理字段");
    const statusCopy = {
      fill: [t("将从已确认档案填写"), field.display_value ?? field.value ?? ""],
      missing: [t("标准简历未提供该信息"), t("待补充")],
      manual: [t("此字段保留手动处理"), t("手动")],
      unmapped: [t("尚未建立可靠字段映射"), t("未识别")],
      review: [t("需要核对后再处理"), t("待核对")],
    };
    const [reasonText, valueText] = statusCopy[action] ?? [
      t("需要人工处理"),
      t("待处理"),
    ];
    reason.textContent = reasonText;
    value.textContent = valueText;
    const gateRisk = field.gate_risk;
    if (gateRisk && (gateRisk.level === "suspect" || gateRisk.level === "conflict")) {
      const badge = document.createElement("em");
      badge.className = `gate-badge gate-badge--${gateRisk.level}`;
      badge.textContent =
        gateRisk.level === "conflict"
          ? t("疑似与档案冲突")
          : t("疑似硬性门槛");
      badge.title = `${gateTopicLabel(gateRisk.topic)} · ${
        gateRisk.evidence || t("请对照公告要求核对")
      }`;
      name.append(badge);
    }
    label.append(name, reason);
    row.append(label, value);
    return row;
  });
  const rows = [...repeatRows, ...fieldRows];
  if (!rows.length) {
    const row = document.createElement("div");
    row.className = "field-row";
    row.textContent = t("当前步骤没有可识别字段");
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
  if (!currentReviewApproved()) {
    showMessage("请先核对当前预览并勾选确认，再执行页面填写。", true);
    return;
  }
  resetReviewApproval();
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
      setConnection("个人档案已有更新");
      showMessage(
        "本次没有填写。请重新选择“识别当前步骤”，核对新计划后再补填空字段。",
      );
      return;
    }
    if (latestTask.version !== currentTask.version) {
      currentTask = validateFillTask(latestTask, currentTab.url);
      renderTask(currentTask);
      setConnection("填写计划已有变化");
      showMessage("本次没有填写。请重新核对当前步骤的最新计划。", true);
      return;
    }
    const repeatGroups = pendingRepeatGroups(currentTask);
    if (repeatGroups.length) {
      setConnection("正在建立缺少的记录");
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
      setConnection("第 {step} 步待确认", {
        step: currentTask.plan.step_index ?? 1,
      });
      const questionCount =
        currentTask.plan.profile_questions?.length ?? 0;
      showMessage(questionCount
        ? t("已建立 {added} 条空白记录。请核对完整预览并填写已有信息；本步骤仍有 {count} 个档案问题，之后必须回到本机 Agent 集中回答。", {
            added: preparationResult.added_count,
            count: questionCount,
          })
        : t("已建立 {added} 条空白记录。请核对更新后的完整预览，再选择“确认填写”。", {
            added: preparationResult.added_count,
          }));
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
    const unresolvedCount = result.field_results.filter((item) =>
      ["missing", "blocked", "fingerprint_mismatch"].includes(
        item.status,
      ),
    ).length;
    undoButton.disabled = filledCount === 0;
    executionCompleted = true;
    if (result.event_type === "fill_failed") {
      setConnection("填写未完成");
      showMessage(
        t("没有字段通过页面回读验证；保留 {preserved} 个已有字段，另有 {unresolved} 个字段未可靠写入。请重新识别当前步骤或手动处理。", {
          preserved: preservedCount,
          unresolved: unresolvedCount,
        }),
        true,
      );
      return;
    }
    const profileQuestionCount =
      currentTask.plan.profile_questions?.length ?? 0;
    setConnection(
      profileQuestionCount || unresolvedCount
        ? "第 {step} 步待补档案"
        : "第 {step} 步已填写",
      { step: currentTask.plan.step_index ?? 1 },
    );
    const preservedSummary = preservedCount
      ? t("；保留 {count} 个已有字段", { count: preservedCount })
      : "";
    const unresolvedSummary = unresolvedCount
      ? t("；{count} 个字段未可靠写入", { count: unresolvedCount })
      : "";
    const fillSummary = filledCount
      ? t("已填写 {count} 个空字段{preserved}{unresolved}。", {
          count: filledCount,
          preserved: preservedSummary,
          unresolved: unresolvedSummary,
        })
      : t("当前步骤没有新的空字段需要填写{preserved}。", {
          preserved: preservedSummary,
        });
    showMessage(profileQuestionCount
      ? t("{summary}本步骤尚未完成：还有 {count} 个档案问题。请回到本机 Agent 集中回答；确认新档案并重新识别前，不要进入网站下一步。", {
          summary: fillSummary,
          count: profileQuestionCount,
        })
      : unresolvedCount
        ? t("{summary}请逐项核对并手动处理未写入字段；完成前不要进入网站下一步。", {
            summary: fillSummary,
          })
        : t("{summary}证据已同步到 Web，核对后请手动进入下一步。", {
            summary: fillSummary,
          }),
    unresolvedCount > 0);
  } catch (error) {
    setConnection("填写未完成");
    showMessage(error instanceof Error ? error.message : "填写失败。", true);
  } finally {
    if (
      !executionCompleted &&
      currentTask &&
      ["ready", "previewed"].includes(currentTask.status)
    ) {
      offerReviewApproval(currentTask);
    } else {
      resetReviewApproval();
    }
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
    setConnection("已撤销当前步骤的填写");
    resetReviewApproval();
    showMessage(
      t("已恢复 {count} 个字段{removed}。如需再次填写，请重新识别当前步骤。", {
        count: result.field_results.length,
        removed:
        result.removed_repeat_group_count
          ? t("；移除 {count} 条本次新增记录", {
              count: result.removed_repeat_group_count,
            })
          : "",
      }),
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

reviewApproval.addEventListener("change", () => {
  executeButton.disabled = !currentReviewApproved();
});

openWorkbenchButton.addEventListener("click", async () => {
  if (!currentServer) return;
  try {
    await chrome.tabs.create({ url: workbenchUrl(currentServer) });
  } catch {
    showMessage("工作台暂时无法打开，请稍后重试。", true);
  }
});

void restorePopupState();
