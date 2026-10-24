import { request } from "./api.js";
import {
  applyRowDecision,
  findProvider as findCatalogProvider,
  findRow as findCatalogRow,
  modelOptions,
  needsThinkingClear,
  selectionOnModelInput,
} from "./model_selection.js";
import {
  canActivateService,
  renderProviderOptionsView,
  findService as findConfigService,
  listRows as buildServiceRows,
  shouldRequireTest as shouldDraftRequireTest,
} from "./model_configs.js";
import { createModelCombobox } from "./model_combobox.js";

export function createModelSettings({ canManage, onBusyChange, reload, showToast }) {
  const $ = (id) => document.getElementById(id);
  const els = {
    modelConfigDialog: $("modelConfigDialog"), modelConfigForm: $("modelConfigForm"),
    closeModelConfigBtn: $("closeModelConfigBtn"), modelConfigIntro: $("modelConfigIntro"), modelPresetNote: $("modelPresetNote"),
    modelProvider: $("modelProvider"), modelApiKey: $("modelApiKey"),
    modelKeyHint: $("modelKeyHint"), modelBaseUrl: $("modelBaseUrl"), modelName: $("modelName"), modelNameMenu: $("modelNameMenu"),
    modelProtocol: $("modelProtocol"), modelAdvanced: $("modelAdvanced"),
    modelRefreshBtn: $("modelRefreshBtn"), modelCatalogStatus: $("modelCatalogStatus"),
    modelThinking: $("modelThinking"), modelTestResult: $("modelTestResult"), testModelBtn: $("testModelBtn"), saveModelBtn: $("saveModelBtn"), saveModelOnlyBtn: $("saveModelOnlyBtn"),
    modelProviderSearch: $("modelProviderSearch"), modelProviderResults: $("modelProviderResults"), modelProviderPanel: $("modelProviderPanel"), modelProviderLabel: $("modelProviderLabel"), modelCustomBtn: $("modelCustomBtn"),
    modelProviderSelected: $("modelProviderSelected"), modelProviderChange: $("modelProviderChange"),
    modelProviderExpand: $("modelProviderExpand"), modelConnectionFields: $("modelConnectionFields"), modelConfigActions: $("modelConfigActions"),
    modelConfigsDialog: $("modelConfigsDialog"), modelConfigsForm: $("modelConfigsForm"), closeModelConfigsBtn: $("closeModelConfigsBtn"),
    modelConfigsIntro: $("modelConfigsIntro"), modelConfigsList: $("modelConfigsList"), addModelServiceBtn: $("addModelServiceBtn"),
  };
  const listIntro = els.modelConfigsIntro.textContent;
  let modelConfigBusy = false;
  let modelConnectionVerified = false;
  let modelCatalogCache = null;
  let modelSelection = { preset_id: "custom", model_id: "" };
  let modelAdvancedTouched = false;
  let modelConfigsCache = null;
  let modelDraftConfigId = "";
  let modelDraftIsNew = false;
  let modelDraftActivate = true;
  let modelDraftDirty = false;
  let modelDraftNeedsTest = true;
  let modelDraftOriginal = null;
  let modelCatalogToken = 0;
  let modelCatalogLoading = false;
  let modelCatalogError = false;
  let modelCombobox = null;
  let modelProviderPickerOpen = false;
  let modelProvidersExpanded = false;
  let modelEditorSession = 0;

  function available() {
    return !modelConfigBusy && canManage();
  }

  function showModelTestResult(status, message) {
    els.modelTestResult.dataset.status = status;
    els.modelTestResult.textContent = message;
    els.modelTestResult.hidden = false;
  }

  function invalidateModelConnectionTest() {
    modelConnectionVerified = false;
    els.modelTestResult.hidden = true;
  }

  function modelConfigPayload() {
    const payload = {
      api_key: els.modelApiKey.value.trim(),
      base_url: els.modelBaseUrl.value.trim(),
      model: els.modelName.value.trim(),
      thinking: els.modelThinking.checked,
      protocol: els.modelProtocol.value,
      preset_id: modelSelection.preset_id,
      model_id: els.modelName.value.trim(),
      name: catalogProviderById(modelCatalogCache, modelSelection.preset_id)?.provider_name || modelDraftOriginal?.name || modelSelection.preset_id,
    };
    if (modelDraftConfigId) payload.config_id = modelDraftConfigId;
    return payload;
  }

  function catalogProviderById(catalog, preset_id) {
    return findCatalogProvider(catalog, preset_id);
  }

  function catalogModelRow(provider, model_id) {
    return findCatalogRow(provider, model_id);
  }

  async function loadModelCatalog({ force = false } = {}) {
    if (modelCatalogCache && !force) return modelCatalogCache;
    const token = ++modelCatalogToken;
    modelCatalogLoading = true;
    modelCatalogError = false;
    renderModelCatalogStatus("loading");
    try {
      const data = await request(force ? "/api/player/model-presets?refresh=1" : "/api/player/model-presets");
      if (token !== modelCatalogToken) return null;
      const providers = Array.isArray(data.providers) ? data.providers : [];
      const byId = {};
      for (const provider of providers) {
        if (provider && provider.provider_id) byId[provider.provider_id] = provider;
      }
      modelCatalogCache = {
        providers,
        byId,
        source: data.source || "snapshot",
      };
      modelCatalogLoading = false;
      renderModelCatalogStatus(providers.length ? "ready" : "empty");
      return modelCatalogCache;
    } catch (error) {
      if (token !== modelCatalogToken) return null;
      modelCatalogLoading = false;
      modelCatalogError = true;
      renderModelCatalogStatus("error");
      throw error;
    }
  }

  function renderModelCatalogStatus(status) {
    if (!els.modelCatalogStatus) return;
    const messages = {
      loading: "正在加载模型目录……",
      ready: "",
      empty: "模型目录暂无可用条目，可继续使用 Custom 或已保存的配置。",
      error: "模型目录暂时不可用，可继续使用 Custom 或已保存的配置。",
    };
    els.modelCatalogStatus.dataset.status = status;
    els.modelCatalogStatus.textContent = messages[status] || "";
    els.modelCatalogStatus.hidden = status === "ready";
  }

  async function refreshModelCatalog() {
    if (!available()) return;
    const session = modelEditorSession;
    setModelConfigBusy(true);
    try {
      const catalog = await loadModelCatalog({ force: true });
      if (session !== modelEditorSession || !els.modelConfigDialog.open || !catalog) return;
      renderProviderOptions(catalog);
      updateModelCombobox(catalogProviderById(catalog, modelSelection.preset_id));
      showToast("模型目录已刷新");
    } catch (error) {
      if (session === modelEditorSession) showToast(error.message || "模型目录刷新失败");
    } finally {
      setModelConfigBusy(false);
    }
  }

  function initModelConfigEditor() {
    modelCombobox = createModelCombobox({
      input: els.modelName,
      menu: els.modelNameMenu,
      onSelect: () => onModelNameChanged(),
    });
    els.modelProviderSearch.addEventListener("input", () => renderProviderOptions(modelCatalogCache));
    els.modelProviderSearch.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        els.modelProviderResults.querySelector("button")?.focus();
      }
      if (event.key === "Enter") {
        event.preventDefault();
        els.modelProviderResults.querySelector("button")?.click();
      }
    });
    els.modelProviderResults.addEventListener("keydown", (event) => {
      const buttons = [...els.modelProviderResults.querySelectorAll("button")];
      const index = buttons.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        els.modelProviderSearch.focus();
      }
    });
    els.modelCustomBtn.onclick = () => chooseProvider("custom");
    els.modelProviderExpand.onclick = () => {
      if (!available()) return;
      modelProvidersExpanded = !modelProvidersExpanded;
      renderProviderOptions(modelCatalogCache);
      els.modelProviderResults.scrollTop = 0;
    };
    els.modelProviderChange.onclick = () => {
      if (!modelDraftIsNew || !available()) return;
      modelProviderPickerOpen = true;
      modelProvidersExpanded = false;
      els.modelProviderSearch.value = "";
      modelCombobox.close();
      renderProviderOptions(modelCatalogCache);
      els.modelProviderSearch.focus();
    };
    els.modelConfigForm.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.tagName === "INPUT") event.preventDefault();
    });
  }

  function chooseProvider(id) {
    if (!modelDraftIsNew || !available()) return;
    els.modelProvider.textContent = "";
    els.modelProvider.append(providerOption(id, id));
    els.modelProvider.value = id;
    onProviderChanged();
    modelProviderPickerOpen = false;
    renderProviderOptions(modelCatalogCache);
    els.modelProviderChange.focus();
  }

  function showModelPresetNote(message) {
    if (!message) {
      els.modelPresetNote.hidden = true;
      els.modelPresetNote.textContent = "";
      return;
    }
    els.modelPresetNote.hidden = false;
    els.modelPresetNote.textContent = message;
  }

  function providerOption(preset_id, name) {
    const option = document.createElement("option");
    option.value = preset_id;
    option.textContent = name;
    return option;
  }

  function renderProviderOptions(catalog) {
    renderProviderOptionsView(catalog, {
      els, modelDraftIsNew, modelProviderPickerOpen, modelSelection,
      modelDraftOriginal, modelConfigsCache, modelProvidersExpanded,
      modelCatalogLoading, modelCatalogError, catalogProviderById, chooseProvider
    });
    refreshControls();
  }

  function renderProviderFieldPlaceholder(config) {
    const preset = modelSelection.preset_id || "custom";
    els.modelProvider.textContent = "";
    els.modelProvider.append(providerOption(preset, config.name || preset));
    els.modelProvider.value = preset;
    renderProviderOptions(modelCatalogCache);
  }

  function updateModelCombobox(provider) {
    modelCombobox.setModels(modelOptions(provider));
  }

  function applyCatalogRow(row) {
    if (!row) return;
    const next = applyRowDecision(
      row,
      {
        protocol: els.modelProtocol.value,
        base_url: els.modelBaseUrl.value,
        model: els.modelName.value,
      },
      { advancedTouched: modelAdvancedTouched },
    );
    els.modelProtocol.value = next.protocol;
    els.modelBaseUrl.value = next.base_url;
    els.modelName.value = next.model;
    if (needsThinkingClear(next.protocol, els.modelThinking.checked)) {
      els.modelThinking.checked = false;
    }
    modelSelection = selectionOnModelInput(
      els.modelProvider.value,
      next.model,
      row,
    );
    syncThinkingField();
  }

  function syncEndpointFields() {
    const custom = modelSelection.preset_id === "custom";
    // A fresh catalog draft has no inferred target until a row or user supplies it.
    els.modelBaseUrl.required = modelDraftIsNew && !custom;
    els.modelProtocol.required = true;
  }

  function syncModelKeyHint() {
    const original = modelDraftOriginal;
    const hasKey = !modelDraftIsNew && Boolean(original?.has_api_key);
    const changed = hasKey && (
      modelSelection.preset_id !== original.preset_id
      || els.modelProtocol.value !== original.protocol
      || els.modelBaseUrl.value.trim() !== original.base_url
    );
    els.modelApiKey.placeholder = "填写 API Key";
    els.modelKeyHint.textContent = changed
      ? "服务已改变，请填写此服务的 Key。保存前原配置仍有效。"
      : hasKey
        ? "此配置已保存 Key，留空保留；密钥不会传回浏览器。"
        : "请填写此服务的 Key。测试不会保存配置。";
  }

  function markConnectionDirty() {
    modelDraftDirty = true;
    modelDraftNeedsTest = shouldDraftRequireTest(modelDraftIsNew, true);
    invalidateModelConnectionTest();
    syncModelKeyHint();
  }

  function onProviderChanged() {
    const value = els.modelProvider.value || "custom";
    modelSelection = { preset_id: value, model_id: "" };
    els.modelName.value = "";
    els.modelApiKey.value = "";
    els.modelBaseUrl.value = "";
    els.modelProtocol.value = value === "custom" ? "openai_chat" : "";
    modelAdvancedTouched = false;
    updateModelCombobox(catalogProviderById(modelCatalogCache, value));
    syncEndpointFields();
    syncThinkingField();
    markConnectionDirty();
  }

  function onModelNameChanged() {
    const catalog = modelCatalogCache;
    const provider = catalogProviderById(catalog, els.modelProvider.value);
    const row = catalogModelRow(provider, els.modelName.value.trim());
    if (row) {
      applyCatalogRow(row);
    } else {
      modelSelection = selectionOnModelInput(
        els.modelProvider.value,
        els.modelName.value.trim(),
        null,
      );
    }
    syncThinkingField();
    syncEndpointFields();
    markConnectionDirty();
  }

  function renderModelConfigForm(config, { required = false } = {}) {
    els.modelConfigDialog.dataset.required = required ? "true" : "false";
    els.closeModelConfigBtn.hidden = required;
    els.modelConfigIntro.textContent = required
      ? "开始故事前，请先配置并测试本机使用的模型服务。配置不会写入或导出到故事存档。"
      : "模型配置只保存在本机，不会写入或导出到任何故事存档。";
    els.modelApiKey.value = "";
    modelDraftOriginal = { ...config };
    els.modelBaseUrl.value = config.base_url || "";
    els.modelName.value = config.model || "";
    els.modelProtocol.value = config.protocol || "openai_chat";
    els.modelThinking.checked = Boolean(config.thinking);
    modelSelection = {
      preset_id: config.preset_id || "custom",
      model_id: config.model_id || "",
    };
    modelAdvancedTouched = false;
    modelDraftDirty = false;
    modelDraftNeedsTest = shouldDraftRequireTest(modelDraftIsNew, false);
    els.modelProviderSearch.value = "";
    showModelPresetNote("");
    renderProviderFieldPlaceholder(config);
    syncThinkingField();
    updateModelCombobox(null);
    syncEndpointFields();
    syncModelKeyHint();
    invalidateModelConnectionTest();
    void enhanceModelConfigWithCatalog(config);
  }

  async function enhanceModelConfigWithCatalog(config) {
    const session = modelEditorSession;
    try {
      const catalog = await loadModelCatalog();
      if (session !== modelEditorSession || !els.modelConfigDialog.open || !catalog) return;
      renderProviderOptions(catalog);
      updateModelCombobox(catalogProviderById(catalog, modelSelection.preset_id));
    } catch (error) {
      if (session !== modelEditorSession || !els.modelConfigDialog.open) return;
      renderProviderOptions(modelCatalogCache);
      showModelPresetNote("模型目录暂时不可用，仍可填写服务地址和模型，或使用已保存配置。");
    }
  }

  function syncThinkingField() {
    const protocol = els.modelProtocol.value;
    const isChat = protocol === "openai_chat";
    els.modelThinking.disabled = !isChat;
    if (!isChat && els.modelThinking.checked) {
      els.modelThinking.checked = false;
    }
  }


  function openModelConfig(config, {
    required = false,
    configId = "",
    isNew = false,
    activate = true,
  } = {}) {
    if (!available()) return;
    modelDraftConfigId = configId;
    modelDraftIsNew = isNew;
    modelProviderPickerOpen = isNew;
    modelProvidersExpanded = false;
    els.modelProviderSearch.value = "";
    modelDraftActivate = activate;
    modelEditorSession += 1;
    modelCombobox.close();
    els.modelProvider.disabled = !isNew;
    renderModelConfigForm(config || {
      configured: false,
      base_url: "",
      model: "",
      thinking: false,
      protocol: "openai_chat",
      preset_id: "custom",
      model_id: "",
      name: "",
    }, { required });
    refreshControls();
    if (!els.modelConfigDialog.open) els.modelConfigDialog.showModal();
    window.setTimeout(() => (
      isNew ? els.modelProviderSearch : config?.has_api_key ? els.modelBaseUrl : els.modelApiKey
    ).focus(), 0);
  }

  function setModelConfigBusy(busy) {
    modelConfigBusy = busy;
    if (busy) modelCombobox.close();
    refreshControls();
    onBusyChange();
  }

  function refreshControls() {
    const busy = modelConfigBusy;
    for (const input of els.modelConfigForm.querySelectorAll("input, select, button, textarea")) {
      input.disabled = busy;
    }
    els.modelProvider.disabled = busy || !modelDraftIsNew;
    els.modelConnectionFields.disabled = busy || (modelDraftIsNew && modelProviderPickerOpen);
    els.modelThinking.disabled = busy || els.modelProtocol.value !== "openai_chat";
    els.closeModelConfigBtn.hidden = els.modelConfigDialog.dataset.required === "true";
    els.closeModelConfigBtn.title = busy ? "当前操作完成后可关闭" : "关闭";
    els.closeModelConfigsBtn.disabled = busy;
    els.closeModelConfigsBtn.title = busy ? "当前操作完成后可关闭" : "关闭";
    els.addModelServiceBtn.disabled = busy;
    for (const button of els.modelConfigsList.querySelectorAll("button[data-service-action]")) {
      const service = findConfigService(modelConfigsCache?.services || [], button.dataset.configId);
      button.disabled = busy || (button.dataset.serviceAction === "activate"
        && !canActivateService(service, modelConfigsCache?.active_config_id));
    }
  }

  async function testModelConnection() {
    if (!available()) return;
    if (modelDraftIsNew && modelProviderPickerOpen) return;
    if (!els.modelConfigForm.reportValidity()) return;
    setModelConfigBusy(true);
    showModelTestResult("pending", "正在连接模型服务……");
    try {
      const result = await request("/api/player/model-configs/test", {
        method: "POST",
        body: JSON.stringify(modelConfigPayload()),
      });
      modelConnectionVerified = Boolean(result.success);
      if (result.success) {
        showModelTestResult(
          "success",
          `连接成功 · ${result.model} · ${Number(result.latency_ms).toFixed(0)} ms`,
        );
      } else {
        showModelTestResult(
          "error",
          `${result.error?.message || "连接失败"}（${result.error?.code || "unknown"}，${Number(result.latency_ms).toFixed(0)} ms）`,
        );
      }
    } catch (error) {
      modelConnectionVerified = false;
      showModelTestResult("error", error.message || "连接检查没有完成");
    } finally {
      setModelConfigBusy(false);
    }
  }

  async function refreshActiveConfig(message) {
    try {
      await reload({ scroll: "instant", resumeOperation: false });
      showToast(message);
    } catch (error) {
      showToast(`${message}，但当前状态暂未刷新：${error.message}`);
    }
  }

  async function saveModelSettings(activate = modelDraftActivate) {
    if (!available() || (modelDraftIsNew && modelProviderPickerOpen)) return;
    if (!els.modelConfigForm.reportValidity()) return;
    if (modelDraftNeedsTest && !modelConnectionVerified) {
      showModelTestResult("error", "请先使用当前填写内容完成一次连接测试。修改任一字段后需要重新测试。");
      return;
    }
    const payload = { ...modelConfigPayload(), activate };
    setModelConfigBusy(true);
    try {
      const configs = await request("/api/player/model-configs", {
        method: "POST", body: JSON.stringify(payload),
      });
      modelConfigsCache = configs;
      els.modelApiKey.value = "";
      els.modelConfigDialog.close();
      showList();
      const message = activate ? "模型配置已保存并立即启用" : "模型配置已保存";
      if (activate || payload.config_id === configs.active_config_id) await refreshActiveConfig(message);
      else showToast(message);
    } catch (error) {
      showModelTestResult("error", error.message || "模型配置未能保存");
    } finally {
      setModelConfigBusy(false);
    }
  }

  async function refreshModelConfigs() {
    modelConfigsCache = await request("/api/player/model-configs");
    return modelConfigsCache;
  }

  function renderModelConfigsList() {
    const data = modelConfigsCache || { services: [], active_config_id: "" };
    const rows = buildServiceRows(data.services || [], data.active_config_id || "");
    els.modelConfigsList.textContent = "";
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "尚未添加任何模型服务。";
      els.modelConfigsList.append(empty);
      return;
    }
    for (const row of rows) {
      const div = document.createElement("div");
      div.className = "service-row";
      const head = document.createElement("div");
      head.className = "service-row-head";
      const name = document.createElement("span");
      name.className = "service-row-name";
      name.textContent = row.name;
      head.append(name);
      if (row.active) {
        const badge = document.createElement("span");
        badge.className = "service-row-active";
        badge.textContent = "当前使用";
        head.append(badge);
      }
      const meta = document.createElement("div");
      meta.className = "service-row-meta";
      meta.textContent = `${row.model || "（未选择模型）"}`;
      const actions = document.createElement("div");
      actions.className = "service-row-actions";
      if (!row.active) {
        const enable = document.createElement("button");
        enable.type = "button";
        enable.dataset.serviceAction = "activate";
        enable.dataset.configId = row.config_id;
        enable.textContent = "启用";
        enable.onclick = () => activateModelService(row.config_id);
        actions.append(enable);
      }
      const edit = document.createElement("button");
      edit.type = "button";
      edit.dataset.serviceAction = "edit";
      edit.dataset.configId = row.config_id;
      edit.textContent = "编辑";
      edit.onclick = () => editModelService(row.config_id);
      actions.append(edit);
      if (!row.active) {
        const del = document.createElement("button");
        del.type = "button";
        del.dataset.serviceAction = "delete";
        del.dataset.configId = row.config_id;
        del.textContent = "删除";
        del.onclick = () => deleteModelService(row);
        actions.append(del);
      }
      div.append(head, meta, actions);
      if (!row.has_api_key) {
        const missing = document.createElement("div");
        missing.className = "service-row-missing-key";
        missing.textContent = "此服务缺少 Key，请编辑并填写。";
        div.append(missing);
      }
      els.modelConfigsList.append(div);
    }
    refreshControls();
  }

  function showList() {
    els.modelConfigsIntro.textContent = listIntro;
    renderModelConfigsList();
    if (!els.modelConfigsDialog.open) els.modelConfigsDialog.showModal();
  }

  async function openModelConfigs() {
    if (!available()) return;
    if (els.modelConfigDialog.open) {
      els.modelConfigDialog.focus();
      return;
    }
    setModelConfigBusy(true);
    showList();
    try {
      await refreshModelConfigs();
      renderModelConfigsList();
    } catch (error) {
      els.modelConfigsIntro.textContent = `${error.message || "服务列表暂未加载"}；请关闭后重新打开重试。`;
    } finally {
      setModelConfigBusy(false);
    }
  }

  function editModelService(config_id) {
    if (!available()) return;
    const service = findConfigService(modelConfigsCache?.services || [], config_id);
    if (!service) {
      showToast("服务已不存在，请重新打开列表");
      return;
    }
    els.modelConfigsDialog.close();
    openModelConfig(service, { configId: config_id, isNew: false, activate: true });
  }

  function addModelService() {
    if (!available()) return;
    els.modelConfigsDialog.close();
    openModelConfig({ configured: false, has_api_key: false, base_url: "", model: "",
      thinking: false, protocol: "openai_chat", preset_id: "custom", model_id: "", name: "",
    }, { isNew: true, activate: true });
  }

  async function activateModelService(config_id) {
    if (!available()) return;
    const service = findConfigService(modelConfigsCache?.services || [], config_id);
    if (!canActivateService(service, modelConfigsCache?.active_config_id)) return;
    setModelConfigBusy(true);
    try {
      modelConfigsCache = await request(`/api/player/model-configs/${encodeURIComponent(config_id)}/activate`, { method: "POST" });
      renderModelConfigsList();
      await refreshActiveConfig("已启用该服务");
    } catch (error) {
      showToast(error.message || "启用失败");
    } finally {
      setModelConfigBusy(false);
    }
  }

  async function deleteModelService(row) {
    if (!available() || row.config_id === modelConfigsCache?.active_config_id) return;
    if (!window.confirm(`确定删除服务「${row.name}」？此操作不可撤销。`)) return;
    if (!available() || row.config_id === modelConfigsCache?.active_config_id
        || !findConfigService(modelConfigsCache?.services || [], row.config_id)) return;
    setModelConfigBusy(true);
    try {
      modelConfigsCache = await request(`/api/player/model-configs/${encodeURIComponent(row.config_id)}`, { method: "DELETE" });
      renderModelConfigsList();
      showToast("服务已删除");
    } catch (error) {
      showToast(error.message || "删除失败");
    } finally {
      setModelConfigBusy(false);
    }
  }

  function closeEditor() {
    if (modelConfigBusy) { showToast("正在处理模型服务，请等待完成"); return; }
    if (els.modelConfigDialog.dataset.required === "true") return;
    if (modelDraftDirty && !window.confirm("有未保存的修改，确定放弃？")) return;
    els.modelConfigDialog.close();
    showList();
  }

  function closeList() {
    if (modelConfigBusy) { showToast("正在处理模型服务，请等待完成"); return; }
    els.modelConfigsDialog.close();
  }

  function bind() {
    initModelConfigEditor();
    els.closeModelConfigBtn.onclick = closeEditor;
    els.closeModelConfigsBtn.onclick = closeList;
    els.modelConfigsDialog.addEventListener("cancel", (event) => { event.preventDefault(); closeList(); });
    els.addModelServiceBtn.onclick = addModelService;
    els.saveModelOnlyBtn.onclick = () => saveModelSettings(false);

    els.testModelBtn.onclick = testModelConnection;
    els.modelRefreshBtn.onclick = refreshModelCatalog;
    els.modelConfigForm.addEventListener("submit", (event) => {
      event.preventDefault();
      saveModelSettings();
    });
    for (const input of els.modelConfigForm.querySelectorAll("input")) {
      if (input === els.modelProviderSearch) continue;
      input.addEventListener("input", markConnectionDirty);
      input.addEventListener("change", markConnectionDirty);
    }
    els.modelProvider.addEventListener("change", onProviderChanged);
    els.modelName.addEventListener("change", onModelNameChanged);
    els.modelProtocol.addEventListener("change", () => {
      modelAdvancedTouched = true;
      syncThinkingField();
      markConnectionDirty();
    });
    els.modelBaseUrl.addEventListener("change", () => {
      modelAdvancedTouched = true;
      markConnectionDirty();
    });
    els.modelConfigDialog.addEventListener("close", () => { modelEditorSession += 1; modelCombobox.close(); });
    els.modelConfigDialog.addEventListener("cancel", (event) => { event.preventDefault(); closeEditor(); });
    refreshControls();
  }

  return { bind, open: openModelConfigs, isBusy: () => modelConfigBusy };
}
