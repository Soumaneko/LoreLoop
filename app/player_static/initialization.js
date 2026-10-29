import { request, stream, subscribe } from "./api.js";
import { state } from "./state.js";
import { enhanceCustomSelect, syncCustomSelects } from "./custom_select.js";
import { reportClientError } from "./client_diagnostics.js";
import { validateCampaignSetupForSubmit } from "./campaign_setup.js";
import {
  abilityRowHasContent,
  campaignSetupValuesToRestore,
  collectCampaignSetupDraft,
} from "./initialization_draft.js";

export function createInitialization({
  setOperationMode, loadAll, onEvent, acknowledgeHandledOperation,
  acknowledgeFailedOperation, showToast, newRequestId, navigation, requestModels,
}) {
  const $ = (id) => document.getElementById(id);
  const els = {
    continueInitBtn: $("continueInitBtn"),
    initForm: $("initForm"), initSubmitBtn: $("initSubmitBtn"), initError: $("initError"),
    initConfigFields: $("initConfigFields"),
    initAbilitySystem: $("initAbilitySystem"), initCustomAbilityWrap: $("initCustomAbilityWrap"),
    initWorldStyle: $("initWorldStyle"), initCustomWorldStyleWrap: $("initCustomWorldStyleWrap"),
    initConfigActions: $("initConfigActions"), campaignSetupActions: $("campaignSetupActions"), campaignSetupStage: $("campaignSetupStage"),
    campaignSetupBtn: $("campaignSetupBtn"), regenerateCampaignSetupBtn: $("regenerateCampaignSetupBtn"), editInitConfigBtn: $("editInitConfigBtn"),
    setupCurrencyUnit: $("setupCurrencyUnit"), setupPowerSource: $("setupPowerSource"),
    setupPowerPrevalence: $("setupPowerPrevalence"), setupPowerLearningDifficulty: $("setupPowerLearningDifficulty"),
    setupPowerLimitation: $("setupPowerLimitation"),
    setupPeoplesAndBeings: $("setupPeoplesAndBeings"), setupMajorWorldConflicts: $("setupMajorWorldConflicts"),
    setupDominantPowers: $("setupDominantPowers"), setupCivilizationBaseline: $("setupCivilizationBaseline"),
    abilityRows: $("abilityRows"), addAbilityBtn: $("addAbilityBtn"),
    storyInspirationTags: $("storyInspirationTags"), initStageHint: $("initStageHint"),
  };
  let initConfigSnapshot = null;
  let campaignSetupCommitted = false;
  let currentOperation = null;
  let recoveryStage = "campaign_setup";

  function node(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  async function handleInitializationEvent(event) {
    if (onEvent(event)) return;
    if (event.step === "campaign_setup_done") {
      showCampaignSetupStage(event.data || {});
      try {
        await persistInitializationDraft();
      } catch (error) {
        reportClientError(error, "initialization_draft_save_failed");
        showToast("世界惯例已生成，但草稿暂未保存");
      }
    }
  }

  function canEditConfig() {
    return !currentOperation && !initConfigSnapshot && !campaignSetupCommitted;
  }

  function canGenerateSetup(regenerate) {
    return !currentOperation && !campaignSetupCommitted
      && (regenerate ? Boolean(initConfigSnapshot) : !initConfigSnapshot);
  }

  function startOperation(operation) {
    currentOperation = operation;
    if (operation !== "closing") recoveryStage = operation === "opening" ? "opening" : "campaign_setup";
    els.initError.hidden = true;
    if (operation === "closing") refreshControls();
    else setOperationMode("generating");
  }

  function finishOperation() {
    if (currentOperation === "recovery") {
      setOperationMode("generating");
      return;
    }
    const operation = currentOperation;
    currentOperation = null;
    if (state.mode === "generating") {
      setOperationMode(state.playerState?.needs_init ? "needs_init"
        : state.playerState?.pending?.length ? "pending" : "idle");
    } else refreshControls();
    if (["campaign_setup", "regenerate"].includes(operation) && initConfigSnapshot && els.initError.hidden && navigation.isCreation()) {
      $("creationTitle").focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }

  async function handleOperationFailure(error, operationId = null) {
    // A disconnected stream may still have a successful result to replay.
    try {
      const result = await request("/api/player/operations/recoverable");
      if (result.operations.some((operation) => operation.recovery_kind === "initialization"
          && !["failed", "cancelled"].includes(operation.status))) {
        currentOperation = "recovery";
      }
    } catch (lookupError) {
      currentOperation = "recovery";
      reportClientError(lookupError, "initialization_recovery_lookup_failed");
    }
    if (currentOperation !== "recovery") await acknowledgeFailedOperation(error, operationId);
    els.initError.textContent = error.message;
    els.initError.hidden = false;
  }

  async function reloadAfterFailure() {
    try {
      await loadAll({ scroll: null, resumeOperation: false });
    } catch (reloadError) {
      currentOperation = "recovery";
      reportClientError(reloadError, "initialization_reload_failed");
    }
  }

  async function resumeInitializationOperation(operation) {
    if (currentOperation) return false;
    const campaignSetup = operation.metadata?.stage === "campaign_setup";
    startOperation(campaignSetup ? "campaign_setup" : "opening");
    if (navigation.isCreation()) refreshControls();
    try {
      await subscribe(
        `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
        handleInitializationEvent,
      );
      await acknowledgeHandledOperation(operation.operation_id);
      if (campaignSetup) return false;
      return true;
    } catch (error) {
      if (!campaignSetup) await reloadAfterFailure();
      await handleOperationFailure(error, operation.operation_id);
      return false;
    } finally {
      finishOperation();
    }
  }

  function formValue(form, name) {
    return String(new FormData(form).get(name) || "").trim();
  }

  function readInitConfig(form) {
    const rows = [...els.abilityRows.querySelectorAll(".ability-row")];
    const protagonistAbilities = rows.map((row, index) => {
      const name = row.querySelector(".ability-name").value.trim();
      const description = row.querySelector(".ability-description").value.trim();
      if (Boolean(name) !== Boolean(description)) {
        throw new Error(`能力 ${index + 1} 的名称和解释需要同时填写`);
      }
      return name ? { name, description } : null;
    }).filter(Boolean);
    const age = formValue(form, "protagonist_age");
    const abilitySystem = formValue(form, "ability_system") || null;
    const worldStyleValue = formValue(form, "world_style");
    const customWorldStyle = formValue(form, "custom_world_style");
    if (worldStyleValue === "自定义" && !customWorldStyle) {
      throw new Error("自定义世界风格不能为空");
    }
    const world_style = worldStyleValue === "自定义"
      ? customWorldStyle
      : worldStyleValue;
    return {
      version: 4,
      world_name: formValue(form, "world_name") || null,
      world_style,
      ability_system: abilitySystem,
      custom_ability_system: abilitySystem === "自定义" ? formValue(form, "custom_ability_system") || null : null,
      protagonist_gender: formValue(form, "protagonist_gender") || "男",
      protagonist_name: formValue(form, "protagonist_name"),
      protagonist_age: age ? Number(age) : null,
      protagonist_introduction: formValue(form, "protagonist_introduction") || null,
      protagonist_abilities: protagonistAbilities,
      story_expectation: formValue(form, "story_expectation"),
      failure_severity: formValue(form, "failure_severity") || "适中",
    };
  }

  function captureInitializationForm() {
    const fields = {};
    for (const control of els.initForm.querySelectorAll("[name]")) {
      fields[control.name] = control.value;
    }
    const rows = [...els.abilityRows.querySelectorAll(".ability-row")];
    return {
      fields,
      protagonist_abilities: rows.map((row) => ({
        name: row.querySelector(".ability-name").value,
        description: row.querySelector(".ability-description").value,
      })).filter((a) => abilityRowHasContent(a)),
    };
  }

  function restoreInitializationForm(form = {}) {
    els.initForm.reset();
    for (const [name, value] of Object.entries(form.fields || {})) {
      const control = els.initForm.elements[name];
      if (control) control.value = value ?? "";
    }
    const savedWorldStyle = (form.fields && form.fields.world_style) || "";
    const worldStyleOptions = [...els.initWorldStyle.options].map((o) => o.value);
    if (savedWorldStyle && !worldStyleOptions.includes(savedWorldStyle)) {
      els.initWorldStyle.value = "自定义";
      const customTextarea = els.initForm.elements.custom_world_style;
      if (customTextarea) customTextarea.value = savedWorldStyle;
    }
    const abilities = (form.protagonist_abilities || []).filter(
      (a) => abilityRowHasContent(a),
    );
    els.abilityRows.replaceChildren();
    for (const ability of abilities) {
      addAbilityRow(ability.name, ability.description);
    }
    toggleCustomAbilityField();
    toggleCustomWorldStyleField();
  }

  function readCampaignSetup() {
    const currencyUnit = els.setupCurrencyUnit.value.trim();
    const powerSource = els.setupPowerSource.value.trim();
    const limitation = els.setupPowerLimitation.value.trim();
    const peoplesAndBeings = readCampaignSetupLines(
      els.setupPeoplesAndBeings,
      "主要存在主体",
      { minItems: 1, maxItems: 6, maxChars: 10 },
    );
    const majorWorldConflicts = readCampaignSetupLines(
      els.setupMajorWorldConflicts,
      "世界主要冲突",
      { minItems: 1, maxItems: 2, maxChars: 50 },
    );
    const dominantPowers = readCampaignSetupLines(
      els.setupDominantPowers,
      "主导势力",
      { minItems: 1, maxItems: 3, maxChars: 10 },
    );
    const civilizationBaseline = els.setupCivilizationBaseline.value.trim();
    if (!currencyUnit) throw new Error("默认货币单位不能为空");
    if (!powerSource) throw new Error("力量来源不能为空");
    if (!limitation) throw new Error("力量限制不能为空");
    if (!civilizationBaseline) throw new Error("文明与技术基准不能为空");
    return {
      version: 2,
      currency_unit: currencyUnit,
      public_power_baseline: {
        source: powerSource,
        prevalence: els.setupPowerPrevalence.value,
        learning_difficulty: els.setupPowerLearningDifficulty.value,
        limitation,
      },
      peoples_and_beings: peoplesAndBeings,
      major_world_conflicts: majorWorldConflicts,
      dominant_powers: dominantPowers,
      civilization_baseline: civilizationBaseline,
    };
  }

  function readCampaignSetupLines(control, label, {
    minItems,
    maxItems,
    maxChars,
  }) {
    const items = control.value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (items.length < minItems || items.length > maxItems) {
      throw new Error(`${label}需要填写 ${minItems} 至 ${maxItems} 项`);
    }
    const tooLong = items.find((item) => [...item].length > maxChars);
    if (tooLong) throw new Error(`${label}每项不能超过 ${maxChars} 个字`);
    return items;
  }

  function initializationDraftPayload() {
    return {
      version: 4,
      stage: initConfigSnapshot ? "campaign_setup" : "config",
      form: captureInitializationForm(),
      config: initConfigSnapshot,
      campaign_setup: initConfigSnapshot ? collectCampaignSetupDraft({
        currencyUnit: els.setupCurrencyUnit.value,
        publicPowerBaseline: {
          source: els.setupPowerSource.value,
          prevalence: els.setupPowerPrevalence.value,
          learningDifficulty: els.setupPowerLearningDifficulty.value,
          limitation: els.setupPowerLimitation.value,
        },
        peoplesAndBeings: els.setupPeoplesAndBeings.value,
        majorWorldConflicts: els.setupMajorWorldConflicts.value,
        dominantPowers: els.setupDominantPowers.value,
        civilizationBaseline: els.setupCivilizationBaseline.value,
      }) : null,
    };
  }

  async function persistInitializationDraft() {
    if (!state.playerState?.needs_init) return;
    if (state.playerState?.initialization?.phase !== "config") return;
    await request("/api/player/init/draft", {
      method: "POST",
      body: JSON.stringify(initializationDraftPayload()),
    });
  }

  async function loadInitializationDraft(playerState = state.playerState) {
    if (currentOperation === "closing") return;
    const committed = playerState?.initialization;
    if (
      committed?.phase
      && committed.phase !== "config"
      && committed.config
      && committed.campaign_setup
    ) {
      restoreInitializationForm();
      showCampaignSetupStage({
        config: committed.config,
        campaign_setup: committed.campaign_setup,
      }, { committed: true });
      return;
    }
    const result = await request("/api/player/init/draft");
    const draft = result.draft;
    if (!draft) {
      restoreInitializationForm();
      showConfigStage();
      return;
    }
    restoreInitializationForm(draft.form);
    if (draft.stage === "campaign_setup" && draft.config) {
      showCampaignSetupStage({
        config: draft.config,
        campaign_setup: draft.campaign_setup,
      }, { rawFields: draft.form?.fields });
    } else {
      showConfigStage();
    }
  }

  async function saveBeforeLeave() {
    if (currentOperation) {
      showToast(els.initStageHint.textContent);
      return false;
    }
    startOperation("closing");
    try {
      await persistInitializationDraft();
      return true;
    } catch (error) {
      reportClientError(error, "initialization_draft_save_failed");
      els.initError.textContent = `填写内容暂未保存，请重试保存后再离开。${error.message || ""}`;
      els.initError.hidden = false;
      if (navigation.isCreation()) els.initError.scrollIntoView({ block: "nearest" });
      return false;
    } finally {
      finishOperation();
    }
  }

  function toggleCustomAbilityField() {
    const visible = els.initAbilitySystem.value === "自定义";
    els.initCustomAbilityWrap.hidden = !visible;
    els.initForm.elements.custom_ability_system.disabled = !visible;
    els.initForm.elements.custom_ability_system.required = visible;
  }

  function toggleCustomWorldStyleField() {
    const visible = els.initWorldStyle.value === "自定义";
    els.initCustomWorldStyleWrap.hidden = !visible;
    els.initForm.elements.custom_world_style.disabled = !visible;
    els.initForm.elements.custom_world_style.required = visible;
  }

  function addAbilityRow(name = "", description = "") {
    const count = els.abilityRows.children.length;
    if (count >= 3) return;
    const index = count + 1;
    const row = node("div", "ability-row");
    const nameInput = node("input", "ability-name");
    nameInput.placeholder = `能力 ${index} 名称`;
    nameInput.value = name;
    const descInput = node("textarea", "ability-description");
    descInput.rows = 2;
    nameInput.setAttribute("aria-label", `能力 ${index} 名称`);
    descInput.setAttribute("aria-label", `能力 ${index} 说明`);
    descInput.placeholder = `能力 ${index} 的效果、边界或代价`;
    descInput.value = description;
    const removeBtn = node("button", "ability-remove-btn");
    removeBtn.setAttribute("aria-label", `移除能力 ${index}`);
    removeBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    removeBtn.type = "button";
    removeBtn.onclick = () => {
      if (!canEditConfig()) return;
      row.remove(); updateAddAbilityButton();
    };
    row.append(nameInput, descInput, removeBtn);
    els.abilityRows.appendChild(row);
    updateAddAbilityButton();
  }

  function updateAddAbilityButton() {
    els.addAbilityBtn.hidden = els.abilityRows.children.length >= 3;
  }

  function setCampaignSetupControlsDisabled(disabled) {
    for (const control of els.campaignSetupStage.querySelectorAll(
      "input, textarea, select",
    )) {
      control.disabled = disabled;
    }
    syncCustomSelects([
      els.setupPowerPrevalence,
      els.setupPowerLearningDifficulty,
    ]);
  }

  function showCampaignSetupStage(result, { committed = false, rawFields = null } = {}) {
    initConfigSnapshot = result.config;
    campaignSetupCommitted = committed;
    const restored = campaignSetupValuesToRestore(result.campaign_setup, rawFields);
    for (const [name, value] of Object.entries(restored)) {
      const control = els.initForm.elements[name];
      if (control) control.value = value;
    }
    refreshControls();
    if (navigation.isCreation() && !currentOperation && !committed) els.setupCurrencyUnit.focus();
  }

  function showConfigStage() {
    initConfigSnapshot = null;
    campaignSetupCommitted = false;
    els.initError.hidden = true;
    refreshControls();
  }

  function editInitConfig() {
    if (currentOperation || campaignSetupCommitted) return;
    showConfigStage();
    if (navigation.isCreation()) { window.scrollTo({ top: 0, behavior: "instant" }); els.initForm.elements.world_name.focus({ preventScroll: true }); }
  }

  async function requestCampaignSetup({ regenerate = false } = {}) {
    if (!canGenerateSetup(regenerate)) return;
    els.initError.hidden = true;
    let config;
    try {
      if (regenerate && initConfigSnapshot) config = initConfigSnapshot;
      else {
        if (!els.initForm.reportValidity()) return;
        config = readInitConfig(els.initForm);
      }
    } catch (error) {
      els.initError.textContent = error.message;
      els.initError.hidden = false;
      return;
    }
    if (!state.modelConfig?.configured) { await requestModels(() => requestCampaignSetup({ regenerate })); return; }
    let submitted = false;
    startOperation(regenerate ? "regenerate" : "campaign_setup");
    try {
      if (!regenerate) await persistInitializationDraft();
      submitted = true;
      const operationId = await stream("/api/player/init/campaign-setup", {
        ...config,
        request_id: newRequestId(),
      }, handleInitializationEvent);
      await acknowledgeHandledOperation(operationId);
    } catch (error) {
      if (submitted) await handleOperationFailure(error);
      else {
        els.initError.textContent = error.message;
        els.initError.hidden = false;
      }
    } finally {
      finishOperation();
    }
  }

  async function initialize() {
    if (currentOperation) return;
    if (!initConfigSnapshot) {
      els.initError.textContent = "请先生成并确认公开世界惯例";
      els.initError.hidden = false;
      return;
    }
    let campaignSetup;
    try {
      campaignSetup = campaignSetupCommitted
        ? state.playerState.initialization.campaign_setup
        : readCampaignSetup();
      validateCampaignSetupForSubmit(campaignSetup);
    } catch (error) {
      els.initError.textContent = error.message;
      els.initError.hidden = false;
      return;
    }
    const payload = { config: initConfigSnapshot, campaign_setup: campaignSetup };
    if (!state.modelConfig?.configured) { await requestModels(initialize); return; }
    let completed = false;
    let submitted = false;
    startOperation("opening");
    try {
      if (!campaignSetupCommitted) await persistInitializationDraft();
      submitted = true;
      const operationId = await stream("/api/player/init", {
        ...payload,
        request_id: newRequestId(),
      }, handleInitializationEvent);
      await acknowledgeHandledOperation(operationId);
      await loadAll({ scroll: "instant", resumeOperation: false });
      completed = true;
    }
    catch (error) {
      if (submitted) {
        await reloadAfterFailure();
        await handleOperationFailure(error);
      } else {
        els.initError.textContent = error.message;
        els.initError.hidden = false;
      }
    }
    finally {
      finishOperation();
    }
    if (completed && navigation.isCreation()) await navigation.showReading();
  }

  function open() { refreshControls(); return navigation.showCreation(); }

  function renderSummary() {
    if (!initConfigSnapshot) return;
    const config = initConfigSnapshot;
    const lines = [
      `世界：${config.world_name || ""} · ${config.world_style || ""}`,
      `能力体系：${config.custom_ability_system || config.ability_system || ""}`,
      `主角：${config.protagonist_name || ""} · ${config.protagonist_gender || ""}${config.protagonist_age != null ? ` · ${config.protagonist_age} 岁` : ""}`,
      `人物介绍：${config.protagonist_introduction || "未填写"}`,
      `主角能力：${(config.protagonist_abilities || []).map(ability => `${ability.name}：${ability.description}`).join("；") || "未填写"}`,
      `失败惩罚：${config.failure_severity || "适中"}`,
      `故事期待：${config.story_expectation || ""}`,
    ];
    if (campaignSetupCommitted) {
      const setup = state.playerState?.initialization?.campaign_setup;
      if (setup) lines.push(`货币单位：${setup.currency_unit}`, `力量来源：${setup.public_power_baseline.source}`,
        `力量普及率：${setup.public_power_baseline.prevalence}`, `习得难度：${setup.public_power_baseline.learning_difficulty}`,
        `力量限制：${setup.public_power_baseline.limitation}`, `主要存在主体：${setup.peoples_and_beings.join("、")}`,
        `主导势力：${setup.dominant_powers.join("、")}`, `世界冲突：${setup.major_world_conflicts.join("；")}`, `文明与技术：${setup.civilization_baseline}`);
    }
    $("creationSummaryText").textContent = lines.join("\n");
    $("creationOriginalSummary").querySelector("summary").textContent = campaignSetupCommitted
      ? "查看已确认的设定与世界惯例" : `查看初始设定 · ${config.world_name || ""} / ${config.protagonist_name || ""}`;
  }

  function refreshControls() {
    const busy = Boolean(currentOperation);
    const staged = Boolean(initConfigSnapshot);
    const working = busy && currentOperation !== "closing";
    els.initConfigFields.hidden = staged || working;
    $("creationJourneyName").textContent = state.saves?.find(save => save.save_id === state.activeSave)?.name || "";
    $("creationModelNotice").hidden = Boolean(state.modelConfig?.configured);
    $("creationModelsBtn").disabled = busy;
    $("creationOriginalSummary").hidden = !staged || working;
    $("creationConfirmHint").hidden = !staged || campaignSetupCommitted || working;
    $("creationStatus").hidden = !working && !campaignSetupCommitted;
    const phase = currentOperation === "opening" || campaignSetupCommitted || currentOperation === "recovery" && recoveryStage === "opening"
      ? "opening" : staged || working ? "campaign_setup" : "config";
    for (const step of document.querySelectorAll("[data-creation-stage]")) {
      if (step.dataset.creationStage === phase) step.setAttribute("aria-current", "step"); else step.removeAttribute("aria-current");
    }
    $("creationStatusTitle").textContent = currentOperation === "recovery" ? "正在核实上次任务"
      : working ? phase === "opening" ? "正在生成开场" : "正在生成世界惯例"
      : !els.initError.hidden ? "开场暂未完成" : "世界惯例已确认";
    $("creationStatusText").textContent = currentOperation === "recovery" ? "上次任务中途断开，结果尚未核实。核实前请不要重复提交。"
      : working ? phase === "opening" ? "世界惯例已确认，正在撰写故事的第一幕。" : "根据你的设定，整理可供检查的世界常识。"
      : "初始设定与世界惯例已锁定，可以继续生成开场。";
    $("creationConfigStatus").textContent = [initConfigSnapshot?.world_name || els.initForm.elements.world_name.value, initConfigSnapshot?.protagonist_name || els.initForm.elements.protagonist_name.value].filter(Boolean).join(" · ") || "已填写";
    $("creationSetupStatus").textContent = campaignSetupCommitted ? "已确认" : currentOperation === "recovery" ? "核实中" : working ? "生成中" : "待确认";
    $("creationOpeningStatus").textContent = currentOperation === "opening" ? "生成中" : currentOperation === "recovery" && phase === "opening" ? "核实中" : "等待生成";
    renderSummary();
    els.continueInitBtn.hidden = !state.playerState?.needs_init;
    els.continueInitBtn.disabled = state.mode === "switching";
    els.initConfigFields.disabled = busy || staged || campaignSetupCommitted;
    for (const select of els.initConfigFields.querySelectorAll("select")) {
      select.disabled = els.initConfigFields.disabled;
    }
    els.initConfigActions.hidden = staged;
    els.campaignSetupStage.hidden = !staged || working || campaignSetupCommitted;
    els.campaignSetupActions.hidden = !staged;
    setCampaignSetupControlsDisabled(busy || !staged || campaignSetupCommitted);
    els.campaignSetupBtn.disabled = !canGenerateSetup(false);
    els.regenerateCampaignSetupBtn.disabled = !canGenerateSetup(true);
    els.editInitConfigBtn.disabled = busy || campaignSetupCommitted;
    els.editInitConfigBtn.hidden = campaignSetupCommitted;
    els.regenerateCampaignSetupBtn.hidden = campaignSetupCommitted;
    els.initSubmitBtn.disabled = busy || !staged;
    $("saveCreationDraftBtn").disabled = busy;
    $("saveCreationDraftBtn").textContent = currentOperation === "closing" ? "正在保存……" : "保存并返回";
    $("saveCreationDraftBtn").title = busy ? "当前操作完成后可返回" : "保存并返回旅程库";
    els.campaignSetupBtn.textContent = currentOperation === "recovery" ? "正在核实……" : currentOperation === "campaign_setup" ? "正在生成……" : "生成世界惯例";
    els.regenerateCampaignSetupBtn.textContent = currentOperation === "regenerate" ? "正在生成……" : "重新生成";
    els.initSubmitBtn.textContent = currentOperation === "recovery" ? "正在核实……" : currentOperation === "opening" ? "正在生成开场……"
      : campaignSetupCommitted ? "继续生成开场" : "确认惯例并生成开场";
    els.initStageHint.textContent = currentOperation === "recovery"
      ? "任务结果尚未核实，请按 Ctrl+Shift+R 重新加载或重启应用继续恢复；当前不能修改或重新提交。"
      : busy ? (currentOperation === "closing" ? "正在保存填写内容，请稍候。" : "正在创建旅程，请等待当前任务完成。")
      : campaignSetupCommitted ? "初始设定与世界惯例已确认，可以继续生成开场。"
      : staged ? "这些内容会成为故事中稳定的世界常识，确认前可以直接修改。"
      : "先确定故事的世界与主角。标有 * 的内容需要填写，其余可按你的想法补充。";
    syncCustomSelects([
      els.initWorldStyle, els.initAbilitySystem,
      els.initForm.elements.protagonist_gender, els.initForm.elements.failure_severity,
      els.setupPowerPrevalence, els.setupPowerLearningDifficulty,
    ]);
  }

  function bind() {
    els.initAbilitySystem.onchange = toggleCustomAbilityField;
    els.initWorldStyle.onchange = toggleCustomWorldStyleField;
    els.addAbilityBtn.onclick = () => { if (canEditConfig()) addAbilityRow(); };
    els.campaignSetupBtn.onclick = () => requestCampaignSetup();
    els.regenerateCampaignSetupBtn.onclick = () => requestCampaignSetup({ regenerate: true });
    els.editInitConfigBtn.onclick = editInitConfig;
    for (const button of els.storyInspirationTags.querySelectorAll("button[data-value]")) {
      button.onclick = () => {
        if (!canEditConfig()) return;
        const textarea = els.initForm.elements.story_expectation;
        const value = button.dataset.value;
        const existing = textarea.value.trim();
        if (existing.includes(value)) return;
        textarea.value = existing ? `${existing}、${value}` : value;
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      };
    }
    els.initForm.addEventListener("submit", (event) => { event.preventDefault(); initialize(); });
    els.continueInitBtn.onclick = open;
    $("saveCreationDraftBtn").onclick = () => navigation.showLibrary();
    $("creationModelsBtn").onclick = () => requestModels();
    enhanceCustomSelect(els.initWorldStyle);
    enhanceCustomSelect(els.initAbilitySystem);
    enhanceCustomSelect(els.initForm.elements.protagonist_gender);
    enhanceCustomSelect(els.initForm.elements.failure_severity);
    enhanceCustomSelect(els.setupPowerPrevalence);
    enhanceCustomSelect(els.setupPowerLearningDifficulty);
    toggleCustomAbilityField();
    toggleCustomWorldStyleField();
    refreshControls();
  }

  return { bind, loadDraft: loadInitializationDraft, open, canLeave: saveBeforeLeave,
    isBusy: () => Boolean(currentOperation),
    status: () => currentOperation === "recovery" ? els.initStageHint.textContent
      : !els.initError.hidden ? els.initError.textContent : "",
    resumeOperation: resumeInitializationOperation, refreshControls };
}
