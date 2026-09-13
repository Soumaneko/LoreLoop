const TASK_LABELS = {
  action_extract: "行动解析",
  context_extract: "上下文提取",
  roll_modifier: "判定修正",
  director_agent_decide_generation: "导演 Agent 决策（生成）",
  director_agent_decide_confirmation: "导演 Agent 决策（确认）",
  director_npc_create: "导演角色创建",
  narrative_compose: "正文生成",
  narrative_recompose: "正文重构",
  narrative_rewrite_select: "重写策略",
  narrative_rewrite_from_source: "原文修订",
  narrative_canon_query: "历史事实查询",
  narrative_consistency_check: "叙事一致性检查",
  campaign_setup: "公开世界惯例",
  opening_foundation: "私有开局基础",
  opening_setting_requirements: "世界设定推导",
  opening_scene: "开场生成",
  scene_fact_extract: "场景事实提取",
  state_update: "世界状态更新",
  continuity_project: "连续性账本",
  npc_batch_update: "人物资料批量更新",
  npc_profile_backfill: "人物资料补全",
  action_preview: "行动建议",
  director_event_review_hidden: "导演事件潜伏检查",
  director_event_review_engaged: "导演事件追踪检查",
  director_event_select: "事件选择",
  director_setting_create: "世界设定生成",
};

const PIPELINE_LABELS = {
  generation: "生成管线",
  confirmation: "确认管线",
};

export function pendingFooterMode({ showToken, showVersions, usageData }) {
  if (!showToken && !showVersions) return "none";
  if (showToken) {
    if (usageData?.generation || usageData?.confirmation) return "token";
    if (!showVersions) return "none";
  }
  return "versions";
}

function tokenCount(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function formatted(value) {
  return value === null ? "未提供" : value.toLocaleString("zh-CN");
}

function _formatInputWithCache(inputTokens, cachedTokens, cacheHitRate) {
  const inpStr = formatted(inputTokens);
  if (cachedTokens != null) {
    let rateStr = "";
    if (cacheHitRate != null) {
      rateStr = ` · ${Math.round(cacheHitRate * 100)}%`;
    } else if (inputTokens != null && inputTokens > 0) {
      rateStr = ` · ${Math.round((cachedTokens / inputTokens) * 100)}%`;
    } else {
      rateStr = ` · 0%`;
    }
    return `入 ${inpStr} (缓 ${formatted(cachedTokens)}${rateStr})`;
  }
  return `入 ${inpStr}`;
}

export function renderCardTokenFooter(usageData, { visible = true, detailButtons = false, appendToRow = null } = {}) {
  const gen = usageData?.generation;
  const conf = usageData?.confirmation;

  if (!gen && !conf) return null;

  const footer = document.createElement("div");
  footer.className = "turn-token-footer";
  if (!visible) footer.hidden = true;

  const row = document.createElement("div");
  row.className = "turn-token-row";

  let totalInput = 0;
  let totalOutput = 0;
  let hasInput = false;
  let hasOutput = false;

  if (gen) {
    const item = document.createElement("div");
    item.className = "turn-token-item";
    const tag = document.createElement("span");
    tag.className = "token-tag";
    tag.textContent = "生成管线";
    const val = document.createElement("span");
    val.className = "token-val";
    val.textContent = `${_formatInputWithCache(gen.input_tokens, gen.cached_tokens, gen.cache_hit_rate)} · 出 ${formatted(gen.output_tokens)}`;
    item.append(tag, val);
    row.appendChild(item);

    if (gen.input_tokens != null) { totalInput += gen.input_tokens; hasInput = true; }
    if (gen.output_tokens != null) { totalOutput += gen.output_tokens; hasOutput = true; }

    if (detailButtons && (gen.calls?.length > 0)) {
      const genBtn = document.createElement("button");
      genBtn.className = "turn-token-detail-btn";
      genBtn.type = "button";
      genBtn.textContent = "ⓘ";
      genBtn.title = "生成管线模块明细";
      genBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        genBtn.blur();
        _openPipelineDialog(usageData, "generation", genBtn);
      });
      row.appendChild(genBtn);
    }
  }

  if (conf) {
    if (gen) {
      const divider = document.createElement("span");
      divider.className = "turn-token-divider";
      divider.textContent = "│";
      row.appendChild(divider);
    }
    const item = document.createElement("div");
    item.className = "turn-token-item";
    const tag = document.createElement("span");
    tag.className = "token-tag";
    tag.textContent = "确认管线";
    const val = document.createElement("span");
    val.className = "token-val";
    val.textContent = `${_formatInputWithCache(conf.input_tokens, conf.cached_tokens, conf.cache_hit_rate)} · 出 ${formatted(conf.output_tokens)}`;
    item.append(tag, val);
    row.appendChild(item);

    if (conf.input_tokens != null) { totalInput += conf.input_tokens; hasInput = true; }
    if (conf.output_tokens != null) { totalOutput += conf.output_tokens; hasOutput = true; }

    if (detailButtons && (conf.calls?.length > 0)) {
      const confBtn = document.createElement("button");
      confBtn.className = "turn-token-detail-btn";
      confBtn.type = "button";
      confBtn.textContent = "ⓘ";
      confBtn.title = "确认管线模块明细";
      confBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        confBtn.blur();
        _openPipelineDialog(usageData, "confirmation", confBtn);
      });
      row.appendChild(confBtn);
    }
  }

  if ((hasInput || hasOutput) && (gen && conf)) {
    const divider = document.createElement("span");
    divider.className = "turn-token-divider";
    divider.textContent = "│";
    row.appendChild(divider);

    let totalCached = 0;
    let hasCached = false;
    if (gen?.cached_tokens != null) { totalCached += gen.cached_tokens; hasCached = true; }
    if (conf?.cached_tokens != null) { totalCached += conf.cached_tokens; hasCached = true; }
    let cachedTotalPart = "";
    if (hasCached) {
      const totalHitRate = totalInput > 0 ? ` · ${Math.round((totalCached / totalInput) * 100)}%` : " · 0%";
      cachedTotalPart = ` (缓 ${totalCached.toLocaleString("zh-CN")}${totalHitRate})`;
    }

    const totalItem = document.createElement("div");
    totalItem.className = "turn-token-item turn-token-total";
    const tag = document.createElement("span");
    tag.className = "token-tag";
    tag.textContent = "合计";
    const val = document.createElement("span");
    val.className = "token-val";
    val.textContent = `${(totalInput + totalOutput).toLocaleString("zh-CN")} tok${cachedTotalPart}`;
    totalItem.append(tag, val);
    row.appendChild(totalItem);
  }

  if (appendToRow) {
    row.appendChild(appendToRow);
  }

  footer.appendChild(row);
  return footer;
}

const _USAGE_STORAGE_KEY = "loreloop_token_usage_v1";

function _persistUsageStore(store) {
  try {
    const data = {};
    for (const [key, val] of store) {
      data[key] = val;
    }
    sessionStorage.setItem(_USAGE_STORAGE_KEY, JSON.stringify(data));
  } catch {
    // Quota exceeded or storage unavailable — non-critical.
  }
}

function _restoreUsageStore() {
  try {
    const raw = sessionStorage.getItem(_USAGE_STORAGE_KEY);
    if (!raw) return new Map();
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return new Map();
    const map = new Map();
    for (const [key, val] of Object.entries(data)) {
      if (val && typeof val === "object") map.set(key, val);
    }
    return map;
  } catch {
    return new Map();
  }
}

export function createTokenUsageController({ node }) {
  let visible = false;
  let operationId = null;
  let operationType = "";
  let records = [];

  let pendingGenerationUsage = null;
  let pendingConfirmationUsage = null;
  const turnUsageStore = _restoreUsageStore();

  const render = () => {
    document.querySelectorAll(".turn-token-footer:not(.turn-version-footer)").forEach((el) => {
      el.hidden = !visible;
    });
  };

  const setVisible = (nextVisible) => {
    visible = nextVisible === true;
    render();
  };

  const calculateCurrentSummary = () => {
    let input = 0;
    let output = 0;
    let cached = 0;
    let cacheCreation = 0;
    let hasInput = false;
    let hasOutput = false;
    let hasCached = false;
    let hasCacheCreation = false;
    for (const r of records) {
      if (r.input !== null) { input += r.input; hasInput = true; }
      if (r.output !== null) { output += r.output; hasOutput = true; }
      if (r.cached !== null) { cached += r.cached; hasCached = true; }
      if (r.cacheCreation !== null) { cacheCreation += r.cacheCreation; hasCacheCreation = true; }
    }
    return {
      input_tokens: hasInput ? input : null,
      output_tokens: hasOutput ? output : null,
      cached_tokens: hasCached ? cached : null,
      cache_creation_tokens: hasCacheCreation ? cacheCreation : null,
      cache_hit_rate: (hasCached && hasInput && input > 0)
        ? Math.min(1.0, Math.max(0.0, Math.round((cached / input) * 10000) / 10000))
        : null,
    };
  };

  const handleEvent = (event) => {
    if (event.step === "operation_started") {
      const nextOperationId = event.data?.operation_id || null;
      if (nextOperationId !== operationId || event.data?.resumed === true) {
        operationId = nextOperationId;
        records = [];
      }
      operationType = event.data?.operation_type || "";
      return;
    }
    if (event.step !== "llm_usage") return;
    const data = event.data || {};
    const task = String(data.task || "").trim();
    if (!task) return;
    records.push({
      task,
      callIndex: Math.max(1, Number(data.call_index) || 1),
      attempt: Math.max(1, Number(data.attempt) || 1),
      input: tokenCount(data.input_tokens),
      output: tokenCount(data.output_tokens),
      cached: tokenCount(data.cached_tokens),
      cacheCreation: tokenCount(data.cache_creation_tokens),
    });

    const summary = calculateCurrentSummary();
    if (["generation", "rephrase", "reroll", "feedback_rewrite", "initialization_opening"].includes(operationType)) {
      pendingGenerationUsage = summary;
    } else if (operationType === "confirmation") {
      pendingConfirmationUsage = summary;
    }
  };

  const getCurrentPendingUsage = () => ({
    generation: pendingGenerationUsage,
    confirmation: pendingConfirmationUsage,
  });

  const clearPendingUsage = () => {
    pendingGenerationUsage = null;
    pendingConfirmationUsage = null;
  };

  const getVariantUsage = (turnId) => {
    if (!turnId) return null;
    return turnUsageStore.get(String(turnId)) || null;
  };

  const saveVariantGenerationUsage = (turnId) => {
    if (!turnId || !pendingGenerationUsage) return;
    const key = String(turnId);
    const existing = turnUsageStore.get(key) || {};
    turnUsageStore.set(key, {
      generation: { ...pendingGenerationUsage },
      confirmation: existing.confirmation || null,
    });
    _persistUsageStore(turnUsageStore);
  };

  const saveVariantConfirmationUsage = (turnId) => {
    if (!turnId) return;
    const key = String(turnId);
    const existing = turnUsageStore.get(key) || {};
    const conf = pendingConfirmationUsage
      ? { ...pendingConfirmationUsage }
      : null;
    if (!existing.generation && !conf) return;
    turnUsageStore.set(key, {
      generation: existing.generation || null,
      confirmation: conf,
    });
    _persistUsageStore(turnUsageStore);
  };

  const saveFullVariantUsage = (turnId, usage) => {
    if (!turnId) return;
    const key = String(turnId);
    const gen = usage?.generation || null;
    const conf = usage?.confirmation || null;
    if (!gen && !conf) return;
    turnUsageStore.set(key, { generation: gen, confirmation: conf });
    _persistUsageStore(turnUsageStore);
  };

  const removeVariantUsage = (turnId) => {
    if (!turnId) return;
    turnUsageStore.delete(String(turnId));
    _persistUsageStore(turnUsageStore);
  };

  const cleanUpUnconfirmedVariants = (keepTurnId, allVariantTurnIds) => {
    const keep = String(keepTurnId);
    let changed = false;
    for (const id of allVariantTurnIds) {
      const key = String(id);
      if (key !== keep && turnUsageStore.has(key)) {
        turnUsageStore.delete(key);
        changed = true;
      }
    }
    if (changed) _persistUsageStore(turnUsageStore);
  };

  const isVisible = () => visible;

  return {
    handleEvent,
    setVisible,
    isVisible,
    getCurrentPendingUsage,
    clearPendingUsage,
    getVariantUsage,
    showVariantUsage: (usage) => {}, // no-op: panel is removed
    saveVariantGenerationUsage,
    saveVariantConfirmationUsage,
    saveFullVariantUsage,
    removeVariantUsage,
    cleanUpUnconfirmedVariants,
    turnUsageStore,
  };
}

function _openPipelineDialog(usageData, pipeline, anchorEl) {
  openTokenUsagePipelineDialog({
    dialog: document.getElementById("tokenUsageDialog"),
    contentEl: document.getElementById("tokenUsageDialogContent"),
    usageData,
    pipeline,
    anchorEl,
  });
}

export function openTokenUsagePipelineDialog({ dialog, contentEl, usageData, pipeline, anchorEl }) {
  if (!dialog || !contentEl) return;

  const calls = usageData?.[pipeline]?.calls || [];
  contentEl.replaceChildren();

  if (calls.length === 0) {
    contentEl.appendChild(
      _el("div", "token-usage-dialog-empty", "当前凭证无记录调用明细"),
    );
  } else {
    let totalInput = 0;
    let totalOutput = 0;
    let hasInput = false;
    let hasOutput = false;

    for (const call of calls) {
      const task = String(call.task || "").trim();
      const callIndex = Math.max(1, Number(call.call_index) || 1);
      const attempt = Math.max(1, Number(call.attempt) || 1);
      const inp = tokenCount(call.input_tokens);
      const out = tokenCount(call.output_tokens);
      const cached = tokenCount(call.cached_tokens);
      const hitRate = (call.cache_hit_rate != null)
        ? `${Math.round(call.cache_hit_rate * 100)}%`
        : (cached != null && inp != null && inp > 0)
          ? `${Math.round((cached / inp) * 100)}%`
          : (cached != null)
            ? `0%`
            : null;

      const inputPart = (cached != null)
        ? `输入 ${formatted(inp)} (缓 ${formatted(cached)}${hitRate ? ` · ${hitRate}` : ""})`
        : `输入 ${formatted(inp)}`;

      const row = _el("div", "token-usage-dialog-row");
      const name = _el(
        "div",
        "token-usage-dialog-name",
        `${TASK_LABELS[task] || task}${
          callIndex > 1 ? ` #${callIndex}` : ""
        }${attempt > 1 ? ` · 尝试 ${attempt}` : ""}`,
      );
      const counts = _el("div", "token-usage-dialog-counts");
      counts.append(
        _el("span", "", inputPart),
        _el("span", "", `输出 ${formatted(out)}`),
      );
      row.append(name, counts);
      contentEl.appendChild(row);
    }
  }

  if (dialog.showPopover) dialog.showPopover();
  else dialog.setAttribute("open", "");

  if (anchorEl) {
    const rect = anchorEl.getBoundingClientRect();
    const dialogRect = dialog.getBoundingClientRect();
    let top = rect.bottom + 8;
    let left = rect.left - dialogRect.width / 2 + rect.width / 2;
    if (left < 10) left = 10;
    if (left + dialogRect.width > window.innerWidth - 10) {
      left = window.innerWidth - dialogRect.width - 10;
    }
    if (top + dialogRect.height > window.innerHeight - 10) {
      top = Math.max(10, rect.top - dialogRect.height - 8);
    }
    dialog.style.margin = "0";
    dialog.style.bottom = "auto";
    dialog.style.right = "auto";
    dialog.style.top = `${top}px`;
    dialog.style.left = `${left}px`;
  }
}

function _el(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
