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

  function fieldLabel(value) {
    return text(value)
      .replace(/^[*＊\s:：]+/, "")
      .replace(/[*＊\s:：]+$/, "");
  }

  const REPEAT_GROUP_LABELS = Object.freeze({
    education: "教育经历",
    internship: "实习经历",
    work: "工作经历",
    project: "项目经历",
    campus_role: "校内职务",
    activity: "活动实践",
    certificate: "证书",
    skill: "专业技能",
    language: "语言能力",
  });

  function repeatGroupForSection(section) {
    if (!section) return null;
    const explicit = section.getAttribute("data-ora-repeat-group");
    const subtitle = section.querySelector(".subtitle");
    const rawLabel = fieldLabel(
      subtitle?.textContent || section.getAttribute("aria-label") || "",
    );
    const label = rawLabel
      .replace(/[（(]\s*\d+\s*[）)]/g, "")
      .split(/保\s*存|删\s*除/)[0]
      .trim();
    const group =
      explicit ||
      Object.entries(REPEAT_GROUP_LABELS).find(
        ([, candidate]) => candidate === label,
      )?.[0];
    if (!group || !(group in REPEAT_GROUP_LABELS)) return null;
    const explicitIndex = section.getAttribute("data-ora-repeat-index");
    const parsedIndex =
      explicitIndex !== null
        ? Number.parseInt(explicitIndex, 10)
        : null;
    return {
      group,
      label: REPEAT_GROUP_LABELS[group],
      index:
        Number.isInteger(parsedIndex) && parsedIndex >= 0
          ? parsedIndex
          : null,
    };
  }

  function repeatContextFor(element) {
    const section = element.closest(".set_i_div, [data-ora-repeat-group]");
    const info = repeatGroupForSection(section);
    if (!info) return null;
    if (info.index !== null) {
      return { repeat_group: info.group, repeat_index: info.index };
    }
    const matching = Array.from(
      document.querySelectorAll(".set_i_div, [data-ora-repeat-group]"),
    ).filter(
      (candidate) => repeatGroupForSection(candidate)?.group === info.group,
    );
    const index = matching.indexOf(section);
    return index >= 0
      ? { repeat_group: info.group, repeat_index: index }
      : null;
  }

  function precedingTableLabel(element) {
    const cell = element.closest("td, th");
    const row = cell?.parentElement;
    if (!cell || !row || row.tagName.toLowerCase() !== "tr") return "";
    const cells = Array.from(row.children);
    const index = cells.indexOf(cell);
    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
      const candidate = cells[cursor];
      if (!candidate.matches("td, th")) continue;
      if (
        candidate.querySelector(
          "input, select, textarea, button",
        )
      ) {
        break;
      }
      const label = fieldLabel(candidate.textContent);
      if (label) return label;
    }
    return "";
  }

  function structuralFieldHeading(element) {
    let descendant = element;
    let ancestor = element.parentElement;
    for (let depth = 0; ancestor && ancestor !== document.body && depth < 16; depth += 1) {
      const controls = ancestor.querySelectorAll("input, select, textarea");
      if (controls.length > 12) break;
      const children = Array.from(ancestor.children);
      const branchIndex = children.findIndex(
        (child) => child === descendant || child.contains(descendant),
      );
      if (branchIndex > 0) {
        const wrapperHint = `${ancestor.id} ${ancestor.className}`;
        for (let index = branchIndex - 1; index >= 0; index -= 1) {
          const candidate = children[index];
          if (
            candidate.querySelector(
              "input, select, textarea, button, [role=button]",
            )
          ) {
            continue;
          }
          const candidateText = text(candidate.textContent);
          const candidateHint = `${candidate.id} ${candidate.className}`;
          const fieldStructure =
            /(?:^|[-_\s])(?:form[-_\s]?item|field|entry|row|cell)(?:$|[-_\s])/i.test(
              wrapperHint,
            );
          const headingStructure =
            candidate.matches("label, dt, th, legend") ||
            /(?:^|[-_\s])(?:title|label|caption|heading|name)(?:$|[-_\s])/i.test(
              candidateHint,
            );
          if (
            candidateText &&
            candidateText.length <= 80 &&
            (fieldStructure || headingStructure)
          ) {
            return candidateText;
          }
        }
      }
      descendant = ancestor;
      ancestor = ancestor.parentElement;
    }
    return "";
  }

  function hasRequiredMarker(element) {
    if (
      element.hasAttribute("required") ||
      element.getAttribute("aria-required") === "true"
    ) {
      return true;
    }
    const candidates = [];
    const parent = element.closest("label");
    if (parent) candidates.push(parent.textContent);
    if (element.id) {
      document
        .querySelectorAll(`label[for="${CSS.escape(element.id)}"]`)
        .forEach((label) => candidates.push(label.textContent));
    }
    const cell = element.closest("td, th");
    const row = cell?.parentElement;
    if (cell && row?.tagName.toLowerCase() === "tr") {
      const cells = Array.from(row.children);
      const index = cells.indexOf(cell);
      for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
        const candidate = cells[cursor];
        if (!candidate.matches("td, th")) continue;
        if (
          candidate.querySelector(
            "input, select, textarea, button",
          )
        ) {
          break;
        }
        candidates.push(candidate.textContent);
        break;
      }
    }
    const structuralHeading = structuralFieldHeading(element);
    if (structuralHeading) candidates.push(structuralHeading);
    return candidates.some((candidate) => /[*＊]/.test(candidate || ""));
  }

  function sectionLabelFor(element) {
    return repeatGroupForSection(
      element.closest(".set_i_div, [data-ora-repeat-group]"),
    )?.label || "";
  }

  function labelsFor(element) {
    const labels = [];
    const parent = element.closest("label");
    if (parent) labels.push(fieldLabel(parent.textContent));
    if (element.id) {
      document
        .querySelectorAll(`label[for="${CSS.escape(element.id)}"]`)
        .forEach((label) => labels.push(fieldLabel(label.textContent)));
    }
    if (element.getAttribute("aria-label")) {
      labels.push(fieldLabel(element.getAttribute("aria-label")));
    }
    if (!labels.length) {
      const tableLabel = precedingTableLabel(element);
      if (tableLabel) labels.push(tableLabel);
    }
    if (!labels.length) {
      const structuralHeading = structuralFieldHeading(element);
      if (structuralHeading) labels.push(fieldLabel(structuralHeading));
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
        labels.push(fieldLabel(wrapperText));
      }
    }
    const sectionLabel = sectionLabelFor(element);
    if (sectionLabel) labels.push(sectionLabel);
    return [...new Set(labels.filter(Boolean))];
  }

  function optionLabelFor(element) {
    const parent = element.closest("label");
    if (parent) return fieldLabel(parent.textContent);
    const parts = [];
    let sibling = element.nextSibling;
    while (sibling) {
      if (
        sibling instanceof HTMLInputElement ||
        sibling instanceof HTMLSelectElement ||
        sibling instanceof HTMLTextAreaElement ||
        sibling instanceof HTMLButtonElement
      ) {
        break;
      }
      const value = fieldLabel(sibling.textContent);
      if (value) parts.push(value);
      sibling = sibling.nextSibling;
    }
    return parts.join(" ") || element.getAttribute("value") || "on";
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
                label: optionLabelFor(element),
              },
            ]
          : [];
    const repeatContext = repeatContextFor(element);
    return {
      tag,
      control_type: type,
      field_id: element.id || null,
      name: element.getAttribute("name") || null,
      labels: labelsFor(element),
      placeholder: element.getAttribute("placeholder") || null,
      autocomplete: element.getAttribute("autocomplete") || null,
      required: hasRequiredMarker(element),
      disabled: element.hasAttribute("disabled"),
      readonly: element.hasAttribute("readonly"),
      constraints,
      options,
      ...(repeatContext || {}),
    };
  }

  function interactionKindFor(element) {
    if (
      !(element instanceof HTMLInputElement) ||
      !element.hasAttribute("readonly")
    ) {
      return null;
    }
    const handler = element.getAttribute("onclick") || "";
    if (/\bselectGraduateSchool\s*\(/.test(handler)) {
      return "hotjob_school_picker";
    }
    if (/\bchooseItemValues\s*\(/.test(handler)) {
      return "hotjob_taxonomy_picker";
    }
    if (/\bWdatePicker\s*\(/.test(handler)) {
      return /dateFmt\s*:\s*['"]yyyy-MM['"]/.test(handler)
        ? "hotjob_month_picker"
        : "hotjob_date_picker";
    }
    return null;
  }

  async function signature(element) {
    const metadata = fieldMetadata(element);
    const payload = {
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
    };
    if (metadata.repeat_group !== undefined) {
      payload.repeat_group = metadata.repeat_group;
      payload.repeat_index = metadata.repeat_index;
    }
    return sha256(stableStringify(payload));
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
    const fields = observableControls(root);
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

  function dispatchValueEvents(element) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function hasExistingValue(element, root) {
    if (element instanceof HTMLSelectElement) {
      if (!text(element.value)) return false;
      const selected = element.selectedOptions[0];
      const selectedLabel = text(selected?.textContent).toLowerCase();
      return !(
        element.selectedIndex === 0 &&
        /^(?:请选择|请选择一项|select|please select)/.test(selectedLabel)
      );
    }
    if (
      element instanceof HTMLInputElement &&
      element.type === "radio"
    ) {
      if (!element.name) return element.checked;
      return Array.from(
        root.querySelectorAll(
          `input[type="radio"][name="${CSS.escape(element.name)}"]`,
        ),
      ).some((member) => member.checked);
    }
    if (
      element instanceof HTMLInputElement &&
      element.type === "checkbox"
    ) {
      return element.checked;
    }
    return Boolean(text(element.value));
  }

  function dispatchSearchEvents(element) {
    dispatchValueEvents(element);
    element.dispatchEvent(
      new KeyboardEvent("keyup", { bubbles: true, key: "Unidentified" }),
    );
  }

  async function waitForValue(read, timeoutMs = 2500) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() <= deadline) {
      const value = read();
      if (value) return value;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return null;
  }

  async function waitForStableValue(
    read,
    timeoutMs = 1200,
    stableMs = 250,
  ) {
    const deadline = Date.now() + timeoutMs;
    let stableSince = null;
    while (Date.now() <= deadline) {
      if (read()) {
        stableSince ??= Date.now();
        if (Date.now() - stableSince >= stableMs) return true;
      } else {
        stableSince = null;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return false;
  }

  function sameText(left, right) {
    return text(left).toLocaleLowerCase() === text(right).toLocaleLowerCase();
  }

  function expectedChecked(value) {
    return ["true", "1", "yes", "on"].includes(
      String(value).toLowerCase(),
    );
  }

  function fieldValueMatches(element, field) {
    if (!element?.isConnected || !isRendered(element)) return false;
    if (element instanceof HTMLSelectElement) {
      const selected = element.selectedOptions[0];
      return (
        sameText(element.value, field.value) ||
        sameText(selected?.textContent, field.value)
      );
    }
    if (
      element instanceof HTMLInputElement &&
      element.type === "checkbox"
    ) {
      return element.checked === expectedChecked(field.value);
    }
    if (
      element instanceof HTMLInputElement &&
      element.type === "radio"
    ) {
      return element.checked && sameText(element.value, field.value);
    }
    return sameText(element.value, field.value);
  }

  function restoreOriginal(element, original) {
    if (!element?.isConnected) return false;
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
        dispatchValueEvents(member.element);
      }
      return members.length > 0;
    }
    nativeSet(element, "value", original.value);
    if (original.title === null) {
      element.removeAttribute("title");
    } else {
      element.setAttribute("title", original.title);
    }
    if ("checked" in element) {
      nativeSet(element, "checked", original.checked);
    }
    for (const related of original.related || []) {
      const relatedElement = document.querySelector(related.selector);
      if (!relatedElement) continue;
      nativeSet(relatedElement, "value", related.value);
      if (related.title === null) {
        relatedElement.removeAttribute("title");
      } else {
        relatedElement.setAttribute("title", related.title);
      }
      dispatchValueEvents(relatedElement);
    }
    dispatchValueEvents(element);
    return true;
  }

  function visibleElement(selector, root = document) {
    return Array.from(root.querySelectorAll(selector)).find(isRendered) || null;
  }

  function closeVisiblePicker(root) {
    const cancel =
      visibleElement(
        'input[value="取消"], a[title="取消"], [onclick="cancel();"]',
        root,
      ) ||
      visibleElement(
        'input[value="取消"], a[title="取消"], [onclick="cancel();"]',
      );
    cancel?.click();
  }

  async function fillHotjobSchoolPicker(element, value) {
    element.click();
    const search = await waitForValue(
      () =>
        visibleElement(
          'input[placeholder*="学校名称"], input[placeholder*="学校关键字"], input.search-school-input',
        ),
      6000,
    );
    if (!search) {
      return { ok: false, reasonCode: "school_picker_not_opened" };
    }
    let picker = search.parentElement;
    while (picker && picker !== document.body) {
      if (
        /请选择学校/.test(text(picker.textContent)) &&
        picker.querySelectorAll("a").length >= 5
      ) {
        break;
      }
      picker = picker.parentElement;
    }
    picker = picker || document.body;
    nativeSet(search, "value", value);
    dispatchSearchEvents(search);
    const exactSchool = await waitForValue(
      () =>
        Array.from(
          picker.querySelectorAll("a[title], a"),
        ).find(
          (candidate) =>
            isRendered(candidate) &&
            sameText(
              candidate.getAttribute("title") || candidate.textContent,
              value,
            ),
        ) || null,
      2000,
    );
    if (exactSchool) {
      exactSchool.click();
    } else {
      const manualLink = Array.from(
        picker.querySelectorAll("a"),
      ).find(
        (candidate) =>
          isRendered(candidate) &&
          /手动添加/.test(text(candidate.textContent)),
      );
      manualLink?.click();
      const manual = await waitForValue(
        () =>
          visibleElement(
            '.add-school-input, #cOther, input[placeholder*="手动添加"], input[placeholder*="院校名称"]',
            picker,
          ),
        1000,
      );
      const confirm = await waitForValue(
        () =>
          visibleElement(
            '.add-school-ok, #sure, input[value="确认"], button[value="确认"]',
            picker,
          ),
        1000,
      );
      if (!manual || !confirm) {
        return { ok: false, reasonCode: "school_option_not_found" };
      }
      nativeSet(manual, "value", value);
      dispatchValueEvents(manual);
      confirm.click();
    }
    const filled = await waitForValue(
      () => (sameText(element.value, value) ? true : null),
      1500,
    );
    if (!filled) {
      closeVisiblePicker(picker);
      return { ok: false, reasonCode: "school_value_not_applied" };
    }
    return { ok: true, reasonCode: null };
  }

  async function fillHotjobTaxonomyPicker(element, value) {
    element.click();
    const picker = await waitForValue(() => {
      const candidate = document.getElementById("main_content");
      return candidate && isRendered(candidate) ? candidate : null;
    });
    if (!picker) {
      return { ok: false, reasonCode: "taxonomy_picker_not_opened" };
    }

    const search = visibleElement(
      'input.select_subject_temp, input[placeholder*="专业名称"]',
      picker,
    );
    if (search) {
      nativeSet(search, "value", value);
      dispatchSearchEvents(search);
    }
    const exactOption = await waitForValue(
      () =>
        Array.from(
          picker.querySelectorAll(
            "[data-value], [title], .ac_results li, td, li",
          ),
        ).find(
          (candidate) =>
            candidate !== search &&
            isRendered(candidate) &&
            sameText(
              candidate.getAttribute("title") || candidate.textContent,
              value,
            ),
        ) || null,
      800,
    );
    if (exactOption) {
      exactOption.click();
      const confirm = visibleElement(
        '[onclick="makeSure();"], [onclick^="makeSure("]',
        picker,
      );
      confirm?.click();
    } else {
      const manualLink = visibleElement(".add-subject-link", picker);
      manualLink?.click();
      const manual = await waitForValue(
        () => visibleElement(".add-subject-input", picker),
        500,
      );
      const confirm = visibleElement(".add-subject-ok", picker);
      if (!manual || !confirm) {
        closeVisiblePicker(picker);
        return { ok: false, reasonCode: "taxonomy_option_not_found" };
      }
      nativeSet(manual, "value", value);
      dispatchValueEvents(manual);
      confirm.click();
    }
    const filled = await waitForValue(
      () => (sameText(element.value, value) ? true : null),
      1500,
    );
    if (!filled) {
      closeVisiblePicker(picker);
      return { ok: false, reasonCode: "taxonomy_value_not_applied" };
    }
    return { ok: true, reasonCode: null };
  }

  async function fillSupportedInteraction(element, field) {
    if (field.interaction_kind === "hotjob_school_picker") {
      return fillHotjobSchoolPicker(element, field.value);
    }
    if (field.interaction_kind === "hotjob_taxonomy_picker") {
      return fillHotjobTaxonomyPicker(element, field.value);
    }
    if (field.interaction_kind === "hotjob_month_picker") {
      if (!/^\d{4}-\d{2}$/.test(field.value)) {
        return { ok: false, reasonCode: "invalid_month_value" };
      }
      nativeSet(element, "value", field.value);
      dispatchValueEvents(element);
      return { ok: true, reasonCode: null };
    }
    if (field.interaction_kind === "hotjob_date_picker") {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(field.value)) {
        return { ok: false, reasonCode: "invalid_date_value" };
      }
      nativeSet(element, "value", field.value);
      dispatchValueEvents(element);
      return { ok: true, reasonCode: null };
    }
    return { ok: false, reasonCode: "unsupported_interaction" };
  }

  function relatedOriginals(element, interactionKind) {
    if (interactionKind !== "hotjob_taxonomy_picker") return [];
    const handler = element.getAttribute("onclick") || "";
    const match = handler.match(
      /\bchooseItemValues\s*\(\s*[^,]+,\s*['"]([^'"]+)['"]/,
    );
    const related = match ? document.getElementById(match[1]) : null;
    if (!related) return [];
    return [
      {
        selector: selectorFor(related),
        value: related.value,
        title: related.getAttribute("title"),
      },
    ];
  }

  async function inspectForm(form) {
    const fields = observableControls(form);
    const fieldSignatures = [];
    for (const field of fields) {
      fieldSignatures.push(await signature(field));
    }
    return {
      form_fingerprint: await fingerprint(form),
      field_signatures: fieldSignatures,
    };
  }

  const TRANSIENT_SELECTOR_CLASS =
    /active|checked|disabled|empty|error|filled|focus|hidden|hover|invalid|loading|open|readonly|required|selected|success|valid|visible/i;
  const GENERATED_SELECTOR_CLASS =
    /^(?:sc-|stylest__)|^(?=[A-Za-z0-9]{5,14}$)(?=.*[a-z])(?=.*[A-Z])/;

  function selectorClassTokens(element) {
    return Array.from(element.classList || [])
      .filter((value) => /^[A-Za-z][A-Za-z0-9_-]*$/.test(value))
      .filter((value) => !TRANSIENT_SELECTOR_CLASS.test(value))
      .filter((value) => !GENERATED_SELECTOR_CLASS.test(value))
      .sort((left, right) => left.length - right.length);
  }

  function uniqueSimpleSelector(element) {
    const candidates = [];
    if (element.id) candidates.push(`#${CSS.escape(element.id)}`);
    for (const className of selectorClassTokens(element)) {
      candidates.push(`.${CSS.escape(className)}`);
    }
    return (
      candidates
        .filter((selector) => selector.length <= 500)
        .filter((selector) => {
          const matches = document.querySelectorAll(selector);
          return matches.length === 1 && matches[0] === element;
        })
        .sort((left, right) => left.length - right.length)[0] || null
    );
  }

  function compactChildSegment(element) {
    const parent = element.parentElement;
    if (!parent) return element.tagName.toLowerCase();
    const children = Array.from(parent.children);
    const tag = element.tagName.toLowerCase();
    const childPosition = children.indexOf(element) + 1;
    const sameTag = children.filter(
      (candidate) => candidate.tagName === element.tagName,
    );
    const typePosition = sameTag.indexOf(element) + 1;
    const candidates = [
      `:nth-child(${childPosition})`,
      `${tag}:nth-of-type(${typePosition})`,
    ];
    if (sameTag.length === 1) candidates.push(tag);
    for (const className of selectorClassTokens(element)) {
      candidates.push(`.${CSS.escape(className)}`);
      candidates.push(`${tag}.${CSS.escape(className)}`);
    }
    return candidates
      .filter((selector) => {
        try {
          return (
            element.matches(selector) &&
            children.filter((candidate) => candidate.matches(selector))
              .length === 1
          );
        } catch {
          return false;
        }
      })
      .sort((left, right) => left.length - right.length)[0];
  }

  function selectorFromAnchor(anchor, anchorSelector, element) {
    if (anchor === element) return anchorSelector;
    const parts = [];
    let current = element;
    while (current && current !== anchor) {
      parts.unshift(compactChildSegment(current));
      current = current.parentElement;
    }
    if (current !== anchor || !parts.length) return null;
    return `${anchorSelector} > ${parts.join(" > ")}`;
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
    let anchor = element;
    while (anchor && anchor !== document.body) {
      const anchorSelector = uniqueSimpleSelector(anchor);
      if (anchorSelector) {
        const selector = selectorFromAnchor(
          anchor,
          anchorSelector,
          element,
        );
        if (selector && selector.length <= 500) {
          const matches = document.querySelectorAll(selector);
          if (matches.length === 1 && matches[0] === element) {
            return selector;
          }
        }
      }
      anchor = anchor.parentElement;
    }
    const bodySelector = selectorFromAnchor(
      document.body,
      "body",
      element,
    );
    if (bodySelector && bodySelector.length <= 500) {
      const matches = document.querySelectorAll(bodySelector);
      if (matches.length === 1 && matches[0] === element) {
        return bodySelector;
      }
    }
    throw new Error("无法为当前表单字段生成稳定定位器。");
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

  function observableControls(root) {
    return Array.from(
      root.querySelectorAll(
        'input:not([type="hidden"]), select, textarea',
      ),
    ).filter(isRendered);
  }

  function repeatGroupSections(root, group) {
    const scopes = [root];
    if (root !== document) scopes.push(document);
    const seen = new Set();
    const sections = [];
    for (const scope of scopes) {
      for (const section of scope.querySelectorAll(
        ".set_i_div, [data-ora-repeat-group]",
      )) {
        if (seen.has(section)) continue;
        seen.add(section);
        if (repeatGroupForSection(section)?.group === group) {
          sections.push(section);
        }
      }
    }
    return sections;
  }

  function repeatGroupCounts(root) {
    const counts = {};
    for (const element of observableControls(root)) {
      const context = repeatContextFor(element);
      if (!context) continue;
      counts[context.repeat_group] = Math.max(
        counts[context.repeat_group] || 0,
        context.repeat_index + 1,
      );
    }
    return counts;
  }

  function repeatAddControl(root, group) {
    const groupLabel = REPEAT_GROUP_LABELS[group];
    if (!groupLabel) return null;
    const candidates = Array.from(
      new Set([
        ...root.querySelectorAll(
          'a, button, input[type="button"], [role="button"], [onclick]',
        ),
        ...document.querySelectorAll(
          'a, button, input[type="button"], [role="button"], [onclick]',
        ),
      ]),
    );
    return (
      candidates.find((candidate) => {
        if (!isRendered(candidate)) return false;
        const copy = text(
          candidate.textContent || candidate.getAttribute("value") || "",
        ).replace(/\s+/g, "");
        if (
          ![
            `增加更多${groupLabel}`,
            `新增${groupLabel}`,
            `添加${groupLabel}`,
          ].some((expected) => copy.includes(expected))
        ) {
          return false;
        }
        const type = (candidate.getAttribute("type") || "").toLowerCase();
        const handler = candidate.getAttribute("onclick") || "";
        return (
          type !== "submit" &&
          !/(?:submit|save|保存|完成|下一步)/i.test(handler)
        );
      }) || null
    );
  }

  function canPrepareRepeatGroups(root) {
    const hostname = location.hostname.toLowerCase();
    return (
      hostname === "hotjob.cn" ||
      hostname.endsWith(".hotjob.cn") ||
      Boolean(root.querySelector("[data-ora-repeat-group]"))
    );
  }

  function rollbackRepeatAdditions(sections) {
    for (const section of [...sections].reverse()) {
      if (section?.isConnected) section.remove();
    }
  }

  async function prepareRepeatGroups(task) {
    if (location.origin !== task.allowed_origin) {
      return { ok: false, message: "当前页面来源与填写任务不一致。" };
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
    const beforeFingerprint = await fingerprint(candidate.root);
    if (beforeFingerprint !== task.form_fingerprint) {
      return { ok: false, message: "报名步骤结构已经变化，请重新审阅。" };
    }
    const requirements = (task.plan?.repeat_groups || []).filter(
      (item) => item.pending_count > 0,
    );
    if (!requirements.length) {
      return { ok: true, changed: false, added_count: 0, groups: [] };
    }
    if (!canPrepareRepeatGroups(candidate.root)) {
      return {
        ok: false,
        message: "当前官网尚未验证可安全自动新增重复记录，请手动建立空白记录后重新识别。",
      };
    }

    const addedSections = [];
    const groups = [];
    for (const requirement of requirements) {
      const counts = repeatGroupCounts(candidate.root);
      let observedCount = counts[requirement.group] || 0;
      if (observedCount !== requirement.observed_count) {
        rollbackRepeatAdditions(addedSections);
        return {
          ok: false,
          message: `${requirement.label}的页面记录数已经变化，请重新识别。`,
        };
      }
      if (!repeatAddControl(candidate.root, requirement.group)) {
        rollbackRepeatAdditions(addedSections);
        return {
          ok: false,
          message: `没有找到${requirement.label}的安全新增入口。`,
        };
      }
      const groupAdditions = [];
      while (observedCount < requirement.desired_count) {
        const addControl = repeatAddControl(
          candidate.root,
          requirement.group,
        );
        if (!addControl) {
          rollbackRepeatAdditions(addedSections);
          return {
            ok: false,
            message: `${requirement.label}新增过程中入口消失，已撤回本轮新增记录。`,
          };
        }
        const beforeSections = new Set(
          repeatGroupSections(candidate.root, requirement.group),
        );
        addControl.click();
        const newSections = await waitForValue(() => {
          const additions = repeatGroupSections(
            candidate.root,
            requirement.group,
          ).filter((section) => !beforeSections.has(section));
          return additions.length ? additions : null;
        }, 3000);
        if (!newSections || newSections.length !== 1) {
          rollbackRepeatAdditions([
            ...addedSections,
            ...(newSections || []),
          ]);
          return {
            ok: false,
            message: `${requirement.label}新增没有生效。招聘网站可能已经登录过期或页面没有响应，请重新登录后再识别当前步骤。`,
          };
        }
        const nextCount =
          repeatGroupCounts(candidate.root)[requirement.group] || 0;
        if (nextCount !== observedCount + 1) {
          rollbackRepeatAdditions([...addedSections, ...newSections]);
          return {
            ok: false,
            message: `${requirement.label}新增结构无法可靠定位。`,
          };
        }
        addedSections.push(newSections[0]);
        groupAdditions.push(newSections[0]);
        observedCount = nextCount;
      }
      groups.push({
        group: requirement.group,
        added_count: groupAdditions.length,
      });
    }
    globalThis.__ORA_REPEAT_ADDITIONS__ =
      globalThis.__ORA_REPEAT_ADDITIONS__ || {};
    globalThis.__ORA_REPEAT_ADDITIONS__[task.fill_task_id] = addedSections;
    return {
      ok: true,
      changed: addedSections.length > 0,
      added_count: addedSections.length,
      groups,
    };
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
        (kind === "page" ? 10_000 : 0) +
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

  function looksLikeFullPageFormContainer(element) {
    if (
      element === document.body ||
      element.matches("form") ||
      element.closest("form") ||
      element.matches(MODAL_SELECTOR) ||
      element.closest(MODAL_SELECTOR)
    ) {
      return false;
    }
    const semanticHint = text(
      [
        element.id,
        element.className,
        element.getAttribute("role"),
        element.getAttribute("aria-label"),
        element.getAttribute("data-testid"),
      ]
        .filter(Boolean)
        .join(" "),
    );
    if (
      element.getAttribute("role") !== "form" &&
      !/(?:^|[-_\s])(?:application|apply|candidate|registration|resume|profile)?[-_\s]?form(?:$|[-_\s])/i.test(
        semanticHint,
      )
    ) {
      return false;
    }
    const controls = editableControls(element);
    if (controls.length < 4) return false;
    const labeledCount = controls.filter(
      (control) => labelsFor(control).length > 0,
    ).length;
    if (labeledCount < Math.min(3, controls.length)) return false;
    const hasApplicationAction = Array.from(
      element.querySelectorAll(
        'button, input[type="button"], input[type="submit"], [role="button"]',
      ),
    ).some(
      (candidate) =>
        isRendered(candidate) &&
        /(?:保存|提交|完成|下一步|继续|报名|申请|save|submit|next|continue|apply)/i.test(
          text(candidate.textContent || candidate.getAttribute("value") || ""),
        ),
    );
    return hasApplicationAction;
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
    const pageCandidates = Array.from(
      document.querySelectorAll(
        '[role="form"], [id*="form" i], [class*="form" i], [data-testid*="form" i]',
      ),
    )
      .filter(
        (root) =>
          isRendered(root) && looksLikeFullPageFormContainer(root),
      )
      .map((root) => rootCandidate(root, "page"))
      .filter(Boolean);
    const candidates = [
      ...formCandidates,
      ...dialogCandidates,
      ...pageCandidates,
    ]
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
    const elements = observableControls(root);
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
    const undoKey = task.plan?.step_id
      ? `${task.fill_task_id}:${task.plan.step_id}`
      : task.fill_task_id;
    if (globalThis.__ORA_FILL_UNDO__?.[undoKey]) {
      return {
        ok: false,
        message: "当前步骤已经填写，请先撤销本次填写后再重试。",
      };
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
          'input[type="file"], input[type="password"], input[type="submit"], input[type="button"], input[type="reset"], input[type="image"], button, [disabled]',
        )
      ) {
        results.push({
          field_signature: field.field_signature,
          status: "blocked",
          reason_code: "manual_boundary",
        });
        continue;
      }
      const observedInteraction = interactionKindFor(element);
      if (
        field.interaction_kind &&
        observedInteraction !== field.interaction_kind
      ) {
        results.push({
          field_signature: field.field_signature,
          status: "blocked",
          reason_code: "interaction_structure_changed",
        });
        continue;
      }
      if (element.hasAttribute("readonly") && !field.interaction_kind) {
        results.push({
          field_signature: field.field_signature,
          status: "blocked",
          reason_code: "manual_boundary",
        });
        continue;
      }
      if (hasExistingValue(element, candidate.root)) {
        results.push({
          field_signature: field.field_signature,
          status: "skipped",
          reason_code: "already_has_value",
        });
        continue;
      }
      const original = {
        selector: field.selector,
        value: element.value,
        title: element.getAttribute("title"),
        checked: Boolean(element.checked),
        field_signature: field.field_signature,
        related: relatedOriginals(element, field.interaction_kind),
        radioGroup:
          element instanceof HTMLInputElement &&
          element.type === "radio"
            ? radioSnapshots.get(element.name || element)
            : null,
      };
      let writeAttempted = false;
      let failureReason = null;
      if (field.interaction_kind) {
        const interactionResult = await fillSupportedInteraction(
          element,
          field,
        );
        writeAttempted = interactionResult.ok;
        failureReason = interactionResult.reasonCode;
      } else if (element instanceof HTMLSelectElement) {
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
        writeAttempted = true;
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
        writeAttempted = true;
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
        writeAttempted = true;
      } else {
        nativeSet(element, "value", field.value);
        writeAttempted = true;
      }
      if (!field.interaction_kind && writeAttempted) {
        dispatchValueEvents(element);
      }
      const applied =
        writeAttempted &&
        (await waitForStableValue(() => fieldValueMatches(element, field)));
      if (!applied) {
        restoreOriginal(element, original);
        results.push({
          field_signature: field.field_signature,
          status: "missing",
          reason_code: failureReason || "value_not_applied",
        });
        continue;
      }
      originals.push(original);
      results.push({
        field_signature: field.field_signature,
        status: "filled",
        reason_code: null,
      });
    }
    globalThis.__ORA_FILL_UNDO__ =
      globalThis.__ORA_FILL_UNDO__ || {};
    const repeatAdditions =
      globalThis.__ORA_REPEAT_ADDITIONS__?.[task.fill_task_id] || [];
    if (originals.length || repeatAdditions.length) {
      globalThis.__ORA_FILL_UNDO__[undoKey] = {
        pageFingerprint,
        originals,
        repeatAdditions,
      };
    } else {
      delete globalThis.__ORA_FILL_UNDO__[undoKey];
    }
    const filledCount = results.filter(
      (item) => item.status === "filled",
    ).length;
    const failedCount = results.filter((item) =>
      ["missing", "blocked", "fingerprint_mismatch"].includes(
        item.status,
      ),
    ).length;
    return {
      ok: true,
      event_type:
        filledCount === 0 && failedCount > 0
          ? "fill_failed"
          : "fill_executed",
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
      const restored = restoreOriginal(element, original);
      results.push({
        field_signature: original.field_signature,
        status: restored ? "filled" : "missing",
        reason_code: restored ? "restored" : "field_not_found",
      });
    }
    let removedRepeatGroupCount = 0;
    for (const section of [...(entry.repeatAdditions || [])].reverse()) {
      if (!section?.isConnected) continue;
      section.remove();
      removedRepeatGroupCount += 1;
    }
    if (globalThis.__ORA_REPEAT_ADDITIONS__) {
      delete globalThis.__ORA_REPEAT_ADDITIONS__[taskId];
    }
    delete globalThis.__ORA_FILL_UNDO__[undoKey];
    return {
      ok: true,
      event_type: "fill_undone",
      step_id: stepId,
      page_fingerprint: entry.pageFingerprint,
      field_results: results,
      removed_repeat_group_count: removedRepeatGroupCount,
    };
  }

  globalThis.__ORA_EXECUTOR__ = Object.freeze({
    discoverPage,
    execute,
    inspectForm,
    observePage,
    prepareRepeatGroups,
    undo,
  });
})();
