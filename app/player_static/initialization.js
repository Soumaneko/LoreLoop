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
  acknowledgeFailedOperation, showToast, newRequestId,
}) {
  const $ = (id) => document.getElementById(id);
  const els = {
    continueInitBtn: $("continueInitBtn"),
    initDialog: $("initDialog"), initForm: $("initForm"), closeInitDialogBtn: $("closeInitDialogBtn"), initSubmitBtn: $("initSubmitBtn"), initError: $("initError"),
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
        showToast("公开世界惯例已生成，但初始化草稿暂未保存");
      }
    }
  }

  async function resumeInitializationOperation(operation) {
    const campaignSetup = operation.metadata?.stage === "campaign_setup";
    setOperationMode("generating");
    if (!els.initDialog.open) els.initDialog.showModal();
    const button = campaignSetup ? els.campaignSetupBtn : els.initSubmitBtn;
    const oldText = button.textContent;
    button.disabled = true;
    button.textContent = campaignSetup ? "正在生成……" : "正在生成开场……";
    try {
      await subscribe(
        `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
        handleInitializationEvent,
      );
      await acknowledgeHandledOperation(operation.operation_id);
      if (campaignSetup) {
        setOperationMode("needs_init");
        return false;
      } else {
        els.initDialog.close();
        return true;
      }
    } catch (error) {
      setOperationMode("needs_init");
      els.initError.textContent = error.message;
      els.initError.hidden = false;
      await acknowledgeFailedOperation(error, operation.operation_id);
      return false;
    } finally {
      button.disabled = false;
      button.textContent = oldText;
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

  async function loadInitializationDraft() {
    const committed = state.playerState?.initialization;
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
      editInitConfig();
      return;
    }
    restoreInitializationForm(draft.form);
    if (draft.stage === "campaign_setup" && draft.config) {
      showCampaignSetupStage({
        config: draft.config,
        campaign_setup: draft.campaign_setup,
      }, { rawFields: draft.form?.fields });
    } else {
      editInitConfig();
    }
  }

  async function closeInitializationDialog() {
    try {
      await persistInitializationDraft();
    } catch (error) {
      reportClientError(error, "initialization_draft_save_failed");
      showToast("初始化草稿暂未保存，请重试关闭");
      return;
    }
    els.initDialog.close();
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
    const descInput = node("input", "ability-description");
    descInput.placeholder = `能力 ${index} 的效果、边界或代价`;
    descInput.value = description;
    const removeBtn = node("button", "ability-remove-btn", "×");
    removeBtn.type = "button";
    removeBtn.onclick = () => { row.remove(); updateAddAbilityButton(); };
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
    els.initConfigFields.disabled = true;
    els.initConfigActions.hidden = true;
    els.campaignSetupStage.hidden = false;
    els.campaignSetupActions.hidden = false;
    setCampaignSetupControlsDisabled(committed);
    els.editInitConfigBtn.hidden = committed;
    els.regenerateCampaignSetupBtn.hidden = committed;
    els.initSubmitBtn.textContent = committed
      ? "继续生成开场"
      : "确认惯例并生成开场";
    els.initStageHint.textContent = committed
      ? "公开世界惯例和 CoreMemory 已确认。继续生成私有开局基础与开场正文。"
      : "检查并修改公开世界惯例；提交后将正式建立 CoreMemory，不能返回修改。";
    els.setupCurrencyUnit.focus();
  }

  function editInitConfig() {
    initConfigSnapshot = null;
    campaignSetupCommitted = false;
    els.initConfigFields.disabled = false;
    els.initConfigActions.hidden = false;
    els.campaignSetupStage.hidden = true;
    els.campaignSetupActions.hidden = true;
    setCampaignSetupControlsDisabled(true);
    els.editInitConfigBtn.hidden = false;
    els.regenerateCampaignSetupBtn.hidden = false;
    els.initSubmitBtn.textContent = "确认惯例并生成开场";
    els.initStageHint.textContent = "填写世界、主角与长期体验要求，然后生成可确认的公开世界惯例。";
    els.initError.hidden = true;
  }

  async function requestCampaignSetup({ regenerate = false } = {}) {
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
    const button = regenerate ? els.regenerateCampaignSetupBtn : els.campaignSetupBtn;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "正在生成……";
    els.initConfigFields.disabled = true;
    try {
      if (!regenerate) await persistInitializationDraft();
      const operationId = await stream("/api/player/init/campaign-setup", {
        ...config,
        request_id: newRequestId(),
      }, handleInitializationEvent);
      await acknowledgeHandledOperation(operationId);
    } catch (error) {
      els.initError.textContent = error.message;
      els.initError.hidden = false;
      await acknowledgeFailedOperation(error);
    } finally {
      button.disabled = false;
      button.textContent = oldText;
      if (!initConfigSnapshot) els.initConfigFields.disabled = false;
    }
  }

  async function initialize() {
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
    setOperationMode("generating"); els.initSubmitBtn.disabled = true; els.initSubmitBtn.textContent = "正在生成开场……"; els.initError.hidden = true;
    try {
      if (!campaignSetupCommitted) await persistInitializationDraft();
      const operationId = await stream("/api/player/init", {
        ...payload,
        request_id: newRequestId(),
      }, handleInitializationEvent);
      await acknowledgeHandledOperation(operationId);
      els.initDialog.close();
      await loadAll({ scroll: "instant" });
    }
    catch (error) {
      await acknowledgeFailedOperation(error);
      try {
        await loadAll({ scroll: null, resumeOperation: false });
      } catch (reloadError) {
        reportClientError(reloadError, "initialization_reload_failed");
      }
      setOperationMode("needs_init"); els.initError.textContent = error.message; els.initError.hidden = false;
    }
    finally {
      els.initSubmitBtn.disabled = false;
      if (!campaignSetupCommitted) {
        els.initSubmitBtn.textContent = "确认惯例并生成开场";
      }
      refreshControls();
    }
  }

  function open() {
    if (!els.initDialog.open) els.initDialog.showModal();
  }

  function hide() {
    if (els.initDialog.open) els.initDialog.close();
  }

  function refreshControls() {
    els.continueInitBtn.hidden = !state.playerState?.needs_init;
    els.continueInitBtn.disabled = state.mode === "switching";
    syncCustomSelects([
      els.initWorldStyle, els.initAbilitySystem,
      els.initForm.elements.protagonist_gender, els.initForm.elements.failure_severity,
      els.setupPowerPrevalence, els.setupPowerLearningDifficulty,
    ]);
  }

  function bind() {
    els.initAbilitySystem.onchange = toggleCustomAbilityField;
    els.initWorldStyle.onchange = toggleCustomWorldStyleField;
    els.addAbilityBtn.onclick = () => addAbilityRow();
    els.campaignSetupBtn.onclick = () => requestCampaignSetup();
    els.regenerateCampaignSetupBtn.onclick = () => requestCampaignSetup({ regenerate: true });
    els.editInitConfigBtn.onclick = editInitConfig;
    for (const button of els.storyInspirationTags.querySelectorAll("button[data-value]")) {
      button.onclick = () => {
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
    els.closeInitDialogBtn.onclick = closeInitializationDialog;
    els.initDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeInitializationDialog();
    });
    enhanceCustomSelect(els.initWorldStyle);
    enhanceCustomSelect(els.initAbilitySystem);
    enhanceCustomSelect(els.initForm.elements.protagonist_gender);
    enhanceCustomSelect(els.initForm.elements.failure_severity);
    enhanceCustomSelect(els.setupPowerPrevalence);
    enhanceCustomSelect(els.setupPowerLearningDifficulty);
    toggleCustomAbilityField();
    toggleCustomWorldStyleField();
  }

  return { bind, loadDraft: loadInitializationDraft, open, hide,
    resumeOperation: resumeInitializationOperation, refreshControls };
}
