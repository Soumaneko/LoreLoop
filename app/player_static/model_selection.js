// Pure selection logic for the model settings dialog (no DOM).
// Tested directly with node; app.js only wires these to inputs.

export function findProvider(catalog, preset_id) {
  if (!catalog || !preset_id) return null;
  return catalog.byId[preset_id] || null;
}

// Searchable option list for a provider's models. The label shows the model
// name with its ID in parentheses when the two differ; identical values are
// shown once. Options are keyed by model_id, which is unique per provider.
export function modelOptions(provider) {
  if (!provider || !Array.isArray(provider.models)) return [];
  return provider.models.map((row) => {
    const value = row.model_id || "";
    const name = row.model_name || value;
    const label = name && name !== value ? `${name} (${value})` : value;
    return { value, label };
  });
}

// Filter model options by a case-insensitive substring on the model ID or label.
// An empty query returns every option.
export function filterModels(provider, query) {
  const needle = String(query || "").trim().toLowerCase();
  const options = modelOptions(provider);
  if (!needle) return options;
  return options.filter(
    (option) => option.value.toLowerCase().includes(needle)
      || option.label.toLowerCase().includes(needle),
  );
}

// Whether the given model id is a listed row of the provider. Used to decide if
// a typed value is a known selection or manual/unlisted input.
export function isKnownModel(provider, model_id) {
  if (!provider || !Array.isArray(provider.models) || !model_id) return false;
  return provider.models.some((row) => row.model_id === model_id);
}

export function findRow(provider, model_id) {
  if (!provider || !model_id) return null;
  return provider.models.find((row) => row.model_id === model_id) || null;
}

// A catalog row replaces the previous catalog defaults as a whole, but an
// explicit Protocol/Base URL edit remains authoritative.
export function applyRowDecision(row, fields, { advancedTouched = false } = {}) {
  if (!row) return { ...fields };
  const next = { ...fields };
  if (!advancedTouched) {
    next.protocol = row.protocol;
    next.base_url = row.base_url || "";
  }
  next.model = row.model_id;
  return next;
}

// model_id always mirrors the typed text: matched rows carry the row id,
// manual or delisted input is preserved verbatim so a later save keeps it.
export function selectionOnModelInput(providerId, typedValue, matchedRow) {
  return {
    preset_id: providerId || "custom",
    model_id: matchedRow ? matchedRow.model_id : String(typedValue || "").trim(),
  };
}

// A checked Thinking cannot survive a non-chat protocol: the backend
// rejects the combination, so clear it at selection time. Switching back
// to chat never re-checks (preserve user intent).
export function needsThinkingClear(protocol, thinkingChecked) {
  return protocol !== "openai_chat" && Boolean(thinkingChecked);
}

// Re-display lookup: prefer stored model_id, fall back to stored model
// text; total miss means manual/delisted input whose values stay as-is.
export function lookupSelection(catalog, preset_id, model_id, model) {
  const provider = findProvider(catalog, preset_id);
  if (!provider) {
    return { provider: null, row: null, matchedPreset: false };
  }
  const row = findRow(provider, model_id) || findRow(provider, model) || null;
  return { provider, row, matchedPreset: true };
}
