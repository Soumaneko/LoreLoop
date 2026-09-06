// Pure service-management logic for the model settings UI (no DOM).
// Tested directly with node; app.js only wires these to the DOM.

export const CUSTOM_PRESET = "custom";

// Add-mode quick list: only these are shown by default, and only if present in
// the catalog. Presence is checked against the catalog, never guessed. The list
// controls display only and implies no endorsement; OpenRouter is omitted from
// the shipped snapshot so it is not listed.
export const COMMON_PROVIDER_IDS = [
  "openai",
  "anthropic",
  "google",
  "deepseek",
  "moonshotai",
  "opencode-go",
];

// Add candidates: every catalog provider that is not already configured as a
// non-Custom service, plus Custom which is always addable. Delisted providers
// simply are not in the catalog and therefore not offered.
export function buildAddCandidates(catalog, services) {
  const candidates = addableProviders(catalog, services);
  candidates.push({ preset_id: CUSTOM_PRESET, name: "Custom（手工配置）" });
  return candidates;
}

// Every addable catalog provider: configured non-Custom services are excluded.
// Custom is not a catalog entry and is appended separately.
export function addableProviders(catalog, services) {
  const configured = new Set();
  for (const service of services || []) {
    if (service.preset_id && service.preset_id !== CUSTOM_PRESET) {
      configured.add(service.preset_id);
    }
  }
  const result = [];
  for (const provider of (catalog && catalog.providers) || []) {
    const providerId = provider && provider.provider_id;
    if (!providerId || providerId === CUSTOM_PRESET) continue;
    if (configured.has(providerId)) continue;
    result.push({
      preset_id: providerId,
      name: provider.provider_name || providerId,
    });
  }
  return result;
}

// The default add-mode list: common providers (only those present and not yet
// configured) plus Custom. Used when no search query has been entered.
export function commonProviderCandidates(catalog, services) {
  const all = addableProviders(catalog, services);
  const byId = new Map(all.map((candidate) => [candidate.preset_id, candidate]));
  const common = [];
  for (const providerId of COMMON_PROVIDER_IDS) {
    const found = byId.get(providerId);
    if (found) common.push(found);
  }
  return common;
}

// Full searchable add-mode list: every addable provider matching the query by
// provider_id or provider_name (case-insensitive substring). An empty query
// returns the complete addable set.
export function searchProviderCandidates(catalog, services, query) {
  const needle = String(query || "").trim().toLowerCase();
  const all = addableProviders(catalog, services);
  if (!needle) return all;
  return all.filter(
    (candidate) => (
      candidate.preset_id.toLowerCase().includes(needle)
      || candidate.name.toLowerCase().includes(needle)
    ),
  );
}

// A configured provider (non-Custom) can be added only after it is deleted.
export function isConfiguredProvider(preset_id, services) {
  if (!preset_id || preset_id === CUSTOM_PRESET) return false;
  return (services || []).some(
    (service) => service.preset_id === preset_id,
  );
}

// Whether a config_id corresponds to an existing service (used to decide
// create vs edit and to reject unknown ids without falling back to active).
export function findService(services, config_id) {
  if (!config_id) return null;
  return (services || []).find((service) => service.config_id === config_id) || null;
}

// The displayed name for a service: catalog name is kept offline for non
// Custom entries; Custom entries carry the user-supplied name.
export function displayName(service) {
  if (!service) return "";
  if (service.name) return service.name;
  return service.preset_id && service.preset_id !== CUSTOM_PRESET
    ? service.preset_id
    : CUSTOM_PRESET;
}

// A draft must be connection-tested before save only when it is new or a
// connection-affecting field changed; renaming a Custom service alone does
// not require a re-test.
export function shouldRequireTest(isNew, connectionChanged) {
  return Boolean(isNew || connectionChanged);
}

// Public list rows shown on the management page: name, model, active flag.
export function listRows(services, activeConfigId) {
  return (services || []).map((service) => ({
    config_id: service.config_id,
    name: displayName(service),
    model: service.model || "",
    preset_id: service.preset_id || CUSTOM_PRESET,
    has_api_key: Boolean(service.has_api_key),
    active: service.config_id === activeConfigId,
  }));
}
