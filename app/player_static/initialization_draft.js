// Draft-only helpers for the initialization setup form.
//
// A draft preserves whatever the player has typed so far, including empty
// fields, temporarily over-limit text and unfinished lists. It never runs the
// submit contract in app.js/campaign_setup.js; completeness is checked only at
// initialization_opening. Raw control values live in the draft `form.fields`
// (controls carry a `name`); the structured snapshot supplies generated values
// for controls the player has yet to edit.
export const SETUP_FIELD_NAMES = [
  "setup_currency_unit",
  "setup_power_source",
  "setup_power_prevalence",
  "setup_power_learning_difficulty",
  "setup_power_limitation",
  "setup_peoples_and_beings",
  "setup_major_world_conflicts",
  "setup_dominant_powers",
  "setup_civilization_baseline",
];

export function rawCampaignSetupFields(fields) {
  const source = fields || {};
  return new Set(SETUP_FIELD_NAMES.filter((name) => name in source));
}

// Structured values are a fallback only for fields the raw form did not carry.
// A present raw field wins even when it is an empty string, so partial raw
// fields never wipe the rest of a structured draft (and vice versa).
export function campaignSetupValuesToRestore(campaignSetup, rawFields) {
  const keep = rawCampaignSetupFields(rawFields);
  const proposal = campaignSetup || {};
  const baseline = proposal.public_power_baseline || {};
  const values = {
    setup_currency_unit: proposal.currency_unit || "",
    setup_power_source: baseline.source || "",
    setup_power_prevalence: baseline.prevalence || "少数人掌握",
    setup_power_learning_difficulty: baseline.learning_difficulty || "适中",
    setup_power_limitation: baseline.limitation || "",
    setup_peoples_and_beings: (proposal.peoples_and_beings || []).join("\n"),
    setup_major_world_conflicts: (proposal.major_world_conflicts || []).join("\n"),
    setup_dominant_powers: (proposal.dominant_powers || []).join("\n"),
    setup_civilization_baseline: proposal.civilization_baseline || "",
  };
  return Object.fromEntries(
    Object.entries(values).filter(([name]) => !keep.has(name)),
  );
}

export function abilityRowHasContent(row) {
  const name = String(row?.name ?? "").trim();
  const description = String(row?.description ?? "").trim();
  return Boolean(name || description);
}

export function collectCampaignSetupDraft(values = {}) {
  const baseline = values.publicPowerBaseline || {};
  const splitLines = (text) => String(text ?? "").split(/\r?\n/);
  return {
    version: 2,
    currency_unit: String(values.currencyUnit ?? ""),
    public_power_baseline: {
      source: String(baseline.source ?? ""),
      prevalence: baseline.prevalence || "少数人掌握",
      learning_difficulty: baseline.learningDifficulty || "适中",
      limitation: String(baseline.limitation ?? ""),
    },
    peoples_and_beings: splitLines(values.peoplesAndBeings),
    major_world_conflicts: splitLines(values.majorWorldConflicts),
    dominant_powers: splitLines(values.dominantPowers),
    civilization_baseline: String(values.civilizationBaseline ?? ""),
  };
}
