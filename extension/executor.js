(() => {
  function stableStringify(value) {
    if (Array.isArray(value)) {
      return `[${value.map(stableStringify).join(",")}]`;
    }
    if (value !== null && typeof value === "object") {
      const keys = Object.keys(value).sort();
      return `{${keys
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

  function canonicalPageUrl(value, base = location.href) {
    const url = new URL(value, base);
    url.hash = "";
    return url.href;
  }

  function text(value) {
    return value?.replace(/\s+/g, " ").trim() ?? "";
  }

  function labelsFor(element) {
    const labels = [];
    const parent = element.closest("label");
    if (parent) labels.push(text(parent.textContent));
    if (element.id) {
      document
        .querySelectorAll(`label[for="${CSS.escape(element.id)}"]`)
        .forEach((label) => labels.push(text(label.textContent)));
    }
    if (element.getAttribute("aria-label")) {
      labels.push(text(element.getAttribute("aria-label")));
    }
    if (!labels.length && element.parentElement) {
      const wrapper = element.parentElement;
      const wrapperControls = wrapper.querySelectorAll(
        "input, select, textarea",
      );
      const wrapperText = text(wrapper.textContent);
      if (
        wrapperControls.length === 1 &&
        wrapperText &&
        wrapperText.length <= 200
      ) {
        labels.push(wrapperText);
      }
    }
    return [...new Set(labels.filter(Boolean))];
  }

  function isDeclarationControl(element) {
    if (
      !(element instanceof HTMLInputElement) ||
      !["checkbox", "radio"].includes(element.type)
    ) {
      return false;
    }
    const describedBy = (element.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => text(document.getElementById(id)?.textContent));
    const accessibleContext = text(
      [
        ...labelsFor(element),
        ...describedBy,
        element.getAttribute("aria-label"),
      ]
        .filter(Boolean)
        .join(" "),
    );
    const context = text(
      [
        accessibleContext,
        element.name,
        element.id,
      ]
        .filter(Boolean)
        .join(" "),
    );
    if (
      element.type === "checkbox" &&
      (!accessibleContext || element.required)
    ) {
      return true;
    }
    return /(?:我|本人)(?:已)?(?:同意|声明|承诺|保证|知悉|阅读|授权|遵守|无异议)|确认(?:上述|以上|所填|所述|信息(?:真实|无误)|内容|声明|资料|报名|申请)|(?:隐私政策|用户协议|服务协议|报名须知|申请须知|诚信承诺)|\b(?:agree(?:d|ment)?|declaration|attest(?:ation)?|consent|terms?)\b/i.test(
      context,
    );
  }

  function fieldMetadata(element) {
    const tag = element.tagName.toLowerCase();
    const type =
      tag === "input"
        ? (element.getAttribute("type") || "text").toLowerCase()
        : tag === "button"
          ? (element.getAttribute("type") || "submit").toLowerCase()
          : tag;
    const constraintNames = [
      "pattern",
      "min",
      "max",
      "minlength",
      "maxlength",
      "step",
      "inputmode",
    ];
    const constraints = constraintNames
      .filter((name) => element.getAttribute(name))
      .map((name) => [name, element.getAttribute(name)]);
    const options =
      tag === "select"
        ? Array.from(element.options).map((option) => ({
            value: option.value,
            label: text(option.textContent),
          }))
        : tag === "input" && ["radio", "checkbox"].includes(type)
          ? [
              {
                value: element.getAttribute("value") || "on",
                label:
                  labelsFor(element)[0] ||
                  element.getAttribute("value") ||
                  "on",
              },
            ]
          : [];
    return {
      tag,
      control_type: type,
      field_id: element.id || null,
      name: element.getAttribute("name") || null,
      labels: labelsFor(element),
      placeholder: element.getAttribute("placeholder") || null,
      autocomplete: element.getAttribute("autocomplete") || null,
      required: element.hasAttribute("required"),
      disabled: element.hasAttribute("disabled"),
      readonly: element.hasAttribute("readonly"),
      constraints,
      options,
    };
  }

  async function signature(element) {
    const metadata = fieldMetadata(element);
    return sha256(
      stableStringify({
        tag: metadata.tag,
        type: metadata.control_type,
        id: metadata.field_id,
        name: metadata.name,
        labels: metadata.labels,
        placeholder: metadata.placeholder,
        autocomplete: metadata.autocomplete,
        required: metadata.required,
        disabled: metadata.disabled,
        readonly: metadata.readonly,
        constraints: metadata.constraints,
        options: metadata.options,
      }),
    );
  }

  function rootSubmission(root) {
    if (root instanceof HTMLFormElement) {
      return {
        actionUrl: canonicalPageUrl(
          root.getAttribute("action") || "",
          location.href,
        ),
        method: (root.getAttribute("method") || "get").toLowerCase(),
      };
    }
    return {
      actionUrl: canonicalPageUrl(location.href),
      method: "dialog",
    };
  }

  async function fingerprint(root) {
    const fields = Array.from(
      root.querySelectorAll("input, select, textarea, button"),
    );
    const fieldSignatures = [];
    for (const field of fields) fieldSignatures.push(await signature(field));
    const submission = rootSubmission(root);
    return sha256(
      stableStringify({
        action_url: submission.actionUrl,
        method: submission.method,
        extraction_version: "form-extraction-v1",
        field_signatures: fieldSignatures,
      }),
    );
  }

  function nativeSet(element, property, value) {
    const prototype =
      element instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : element instanceof HTMLSelectElement
            ? HTMLSelectElement.prototype
            : null;
    const setter =
      prototype &&
      Object.getOwnPropertyDescriptor(prototype, property)?.set;
    if (setter) {
      setter.call(element, value);
    } else {
      element[property] = value;
    }
  }

  async function inspectForm(form) {
    const fields = Array.from(
      form.querySelectorAll("input, select, textarea, button"),
    );
    const fieldSignatures = [];
    for (const field of fields) {
      fieldSignatures.push(await signature(field));
    }
    return {
      form_fingerprint: await fingerprint(form),
      field_signatures: fieldSignatures,
    };
  }

  function selectorFor(element) {
    if (
      element.id &&
      document.querySelectorAll(`#${CSS.escape(element.id)}`).length === 1
    ) {
      return `#${CSS.escape(element.id)}`;
    }
    const tag = element.tagName.toLowerCase();
    const name = element.getAttribute("name");
    if (name) {
      const escapedName = name
        .replaceAll("\\", "\\\\")
        .replaceAll('"', '\\"');
      const selector = `${tag}[name="${escapedName}"]`;
      if (document.querySelectorAll(selector).length === 1) return selector;
    }
    const parts = [];
    let current = element;
    while (current && current !== document.body) {
      const currentTag = current.tagName.toLowerCase();
      const siblings = Array.from(current.parentElement?.children ?? []).filter(
        (item) => item.tagName === current.tagName,
      );
      const position = siblings.indexOf(current) + 1;
      parts.unshift(`${currentTag}:nth-of-type(${position})`);
      current = current.parentElement;
      if (parts.join(" > ").length > 450) break;
    }
    const selector = `body > ${parts.join(" > ")}`;
    if (
      selector.length > 500 ||
      document.querySelectorAll(selector).length !== 1
    ) {
      throw new Error("无法为当前表单字段生成稳定定位器。");
    }
    return selector;
  }

  function isRendered(element) {
    if (element.closest('[hidden], [aria-hidden="true"]')) return false;
    let current = element;
    while (current && current !== document.documentElement) {
      const style = globalThis.getComputedStyle?.(current);
      if (
        style &&
        (style.display === "none" || style.visibility === "hidden")
      ) {
        return false;
      }
      current = current.parentElement;
    }
    return true;
  }

  const MODAL_SELECTOR = [
    '[role="dialog"]',
    '[aria-modal="true"]',
    "dialog[open]",
    ".modal",
    ".modal-dialog",
    ".ant-modal",
    ".el-dialog",
    ".ivu-modal",
    ".arco-modal",
    ".van-popup",
    ".layui-layer",
  ].join(", ");

  function editableControls(root) {
    return Array.from(
      root.querySelectorAll(
        'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])',
      ),
    ).filter(isRendered);
  }

  function rootCandidate(root, kind) {
    if (!isRendered(root)) return null;
    const controls = editableControls(root);
    if (!controls.length) return null;
    const focused = Boolean(
      document.activeElement && root.contains(document.activeElement),
    );
    const modal =
      kind === "dialog" ||
      Boolean(root.matches(MODAL_SELECTOR) || root.closest(MODAL_SELECTOR));
    return {
      root,
      kind,
      controlCount: controls.length,
      focused,
      modal,
      score:
        (focused ? 1_000_000 : 0) +
        (modal ? 100_000 : 0) +
        controls.length,
    };
  }

  function looksLikeStepContainer(element) {
    if (element.matches(MODAL_SELECTOR)) return true;
    const style = globalThis.getComputedStyle?.(element);
    const zIndex = Number.parseInt(style?.zIndex || "", 10);
    const positionedOverlay =
      ["fixed", "absolute"].includes(style?.position) &&
      Number.isFinite(zIndex) &&
      zIndex > 0;
    if (!positionedOverlay) return false;
    const controls = editableControls(element);
    if (controls.length > 1) return true;
    return /(?:第?\s*\d+\s*\/\s*\d+|下一步|上一步|取消|确认|继续|报名|申请|\bnext\b|\bcontinue\b|\bcancel\b)/i.test(
      text(element.textContent),
    );
  }

  function formLessStepRoot(control) {
    let current = control.parentElement;
    while (current && current !== document.body) {
      if (isRendered(current) && looksLikeStepContainer(current)) {
        return current;
      }
      current = current.parentElement;
    }
    return null;
  }

  function chooseStepRoot() {
    const formCandidates = Array.from(document.forms)
      .map((form) => rootCandidate(form, "form"))
      .filter(Boolean);
    const dialogRoots = new Set();
    for (const control of editableControls(document)) {
      if (control.closest("form")) continue;
      const root = formLessStepRoot(control);
      if (root) dialogRoots.add(root);
    }
    const dialogCandidates = Array.from(dialogRoots)
      .map((root) => rootCandidate(root, "dialog"))
      .filter(Boolean);
    const candidates = [...formCandidates, ...dialogCandidates]
      .filter(Boolean)
      .sort((left, right) => right.score - left.score);
    if (!candidates.length) {
      throw new Error("当前页面没有可观察的报名步骤。");
    }
    if (
      candidates.length > 1 &&
      candidates[0].score === candidates[1].score
    ) {
      throw new Error("页面包含多个报名步骤，请先点击目标步骤中的任意字段。");
    }
    return candidates[0];
  }

  async function observeRoot(root) {
    const elements = Array.from(
      root.querySelectorAll("input, select, textarea, button"),
    );
    const fields = [];
    for (const element of elements) {
      fields.push({
        field_signature: await signature(element),
        selector: selectorFor(element),
        ...fieldMetadata(element),
      });
    }
    const submission = rootSubmission(root);
    return {
      page_url: canonicalPageUrl(location.href),
      action_url: submission.actionUrl,
      method: submission.method,
      extraction_version: "form-extraction-v1",
      form_fingerprint: await fingerprint(root),
      fields,
    };
  }

  async function discoverPage() {
    const candidate = chooseStepRoot();
    return {
      score: candidate.score,
      focused: candidate.focused,
      modal: candidate.modal,
      control_count: candidate.controlCount,
      observation: await observeRoot(candidate.root),
    };
  }

  async function observePage() {
    return (await discoverPage()).observation;
  }

  async function execute(task) {
    if (location.origin !== task.allowed_origin) {
      return { ok: false, message: "当前页面来源与填写任务不一致。" };
    }
    const resolved = [];
    for (const field of task.plan.fields) {
      const matches = document.querySelectorAll(field.selector);
      if (matches.length !== 1) {
        return {
          ok: false,
          message: `字段定位结果不是唯一值：${field.profile_field}`,
        };
      }
      const element = matches[0];
      if ((await signature(element)) !== field.field_signature) {
        return {
          ok: false,
          message: `字段结构已经变化：${field.profile_field}`,
        };
      }
      resolved.push([field, element]);
    }
    let candidate;
    try {
      candidate = chooseStepRoot();
    } catch (error) {
      return {
        ok: false,
        message:
          error instanceof Error ? error.message : "无法识别当前报名步骤。",
      };
    }
    if (resolved.some(([, element]) => !candidate.root.contains(element))) {
      return { ok: false, message: "目标字段不属于当前报名步骤。" };
    }
    const pageFingerprint = await fingerprint(candidate.root);
    if (pageFingerprint !== task.form_fingerprint) {
      return { ok: false, message: "报名步骤结构已经变化，请重新审阅。" };
    }

    const originals = [];
    const results = [];
    const radioSnapshots = new Map();
    for (const [, element] of resolved) {
      if (
        element instanceof HTMLInputElement &&
        element.type === "radio"
      ) {
        const groupName = element.name || null;
        const groupKey = groupName || element;
        if (!radioSnapshots.has(groupKey)) {
          const group = groupName
            ? Array.from(
                candidate.root.querySelectorAll(
                  `input[type="radio"][name="${CSS.escape(groupName)}"]`,
                ),
              )
            : [element];
          radioSnapshots.set(
            groupKey,
            group.map((member) => ({
              element: member,
              checked: member.checked,
            })),
          );
        }
      }
    }
    for (const [field, element] of resolved) {
      if (isDeclarationControl(element)) {
        results.push({
          field_signature: field.field_signature,
          status: "blocked",
          reason_code: "manual_declaration",
        });
        continue;
      }
      if (
        element.matches(
          'input[type="file"], input[type="password"], input[type="submit"], input[type="button"], input[type="reset"], input[type="image"], button, [disabled], [readonly]',
        )
      ) {
        results.push({
          field_signature: field.field_signature,
          status: "blocked",
          reason_code: "manual_boundary",
        });
        continue;
      }
      originals.push({
        selector: field.selector,
        value: element.value,
        checked: Boolean(element.checked),
        field_signature: field.field_signature,
        radioGroup:
          element instanceof HTMLInputElement &&
          element.type === "radio"
            ? radioSnapshots.get(element.name || element)
            : null,
      });
      if (element instanceof HTMLSelectElement) {
        const option = Array.from(element.options).find(
          (item) =>
            item.value === field.value ||
            text(item.textContent) === field.value,
        );
        if (!option) {
          results.push({
            field_signature: field.field_signature,
            status: "missing",
            reason_code: "option_not_found",
          });
          continue;
        }
        nativeSet(element, "value", option.value);
      } else if (
        element instanceof HTMLInputElement &&
        element.type === "checkbox"
      ) {
        nativeSet(
          element,
          "checked",
          ["true", "1", "yes", "on"].includes(
            String(field.value).toLowerCase(),
          ),
        );
      } else if (
        element instanceof HTMLInputElement &&
        element.type === "radio"
      ) {
        if (element.value !== field.value) {
          results.push({
            field_signature: field.field_signature,
            status: "missing",
            reason_code: "radio_value_mismatch",
          });
          continue;
        }
        nativeSet(element, "checked", true);
      } else {
        nativeSet(element, "value", field.value);
      }
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      results.push({
        field_signature: field.field_signature,
        status: "filled",
        reason_code: null,
      });
    }
    globalThis.__ORA_FILL_UNDO__ =
      globalThis.__ORA_FILL_UNDO__ || {};
    const undoKey = task.plan?.step_id
      ? `${task.fill_task_id}:${task.plan.step_id}`
      : task.fill_task_id;
    globalThis.__ORA_FILL_UNDO__[undoKey] = {
      pageFingerprint,
      originals,
    };
    return {
      ok: true,
      event_type: "fill_executed",
      step_id: task.plan?.step_id ?? null,
      page_fingerprint: pageFingerprint,
      field_results: results,
    };
  }

  function undo(taskId, stepId = null) {
    const undoKey = stepId ? `${taskId}:${stepId}` : taskId;
    const entry = globalThis.__ORA_FILL_UNDO__?.[undoKey];
    if (!entry) {
      return { ok: false, message: "没有可撤销的本地填写记录。" };
    }
    const results = [];
    for (const original of entry.originals) {
      const element = document.querySelector(original.selector);
      if (!element) {
        results.push({
          field_signature: original.field_signature,
          status: "missing",
          reason_code: "field_not_found",
        });
        continue;
      }
      if (original.radioGroup) {
        const members = original.radioGroup.filter(
          (member) => member.element?.isConnected,
        );
        for (const member of members) {
          nativeSet(member.element, "checked", false);
        }
        for (const member of members) {
          if (member.checked) {
            nativeSet(member.element, "checked", true);
          }
          member.element.dispatchEvent(
            new Event("input", { bubbles: true }),
          );
          member.element.dispatchEvent(
            new Event("change", { bubbles: true }),
          );
        }
        results.push({
          field_signature: original.field_signature,
          status: members.length ? "filled" : "missing",
          reason_code: members.length
            ? "restored"
            : "radio_group_not_found",
        });
        continue;
      }
      nativeSet(element, "value", original.value);
      if ("checked" in element) {
        nativeSet(element, "checked", original.checked);
      }
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      results.push({
        field_signature: original.field_signature,
        status: "filled",
        reason_code: "restored",
      });
    }
    delete globalThis.__ORA_FILL_UNDO__[undoKey];
    return {
      ok: true,
      event_type: "fill_undone",
      step_id: stepId,
      page_fingerprint: entry.pageFingerprint,
      field_results: results,
    };
  }

  globalThis.__ORA_EXECUTOR__ = Object.freeze({
    discoverPage,
    execute,
    inspectForm,
    observePage,
    undo,
  });
})();
