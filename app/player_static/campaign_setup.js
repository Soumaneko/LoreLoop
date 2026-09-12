// Player submission contract for the Campaign Setup proposal.
//
// The generation wire keeps the LLM to one-line / one-sentence shaping; a
// player may edit the proposal and is bounded only by the product caps below.
// Lists stay one item per line and are validated by app.js.
export const CAMPAIGN_SETUP_LIMITS = {
  currencyUnit: 40,
  powerSource: 120,
  limitation: 80,
  civilizationBaseline: 200,
};

function charCount(value) {
  return [...String(value ?? "")].length;
}

export function validateCampaignSetupForSubmit(setup) {
  const baseline = setup?.public_power_baseline || {};
  const fields = [
    ["货币单位", setup?.currency_unit, CAMPAIGN_SETUP_LIMITS.currencyUnit],
    ["力量来源", baseline.source, CAMPAIGN_SETUP_LIMITS.powerSource],
    ["力量限制", baseline.limitation, CAMPAIGN_SETUP_LIMITS.limitation],
    ["文明与技术基准", setup?.civilization_baseline, CAMPAIGN_SETUP_LIMITS.civilizationBaseline],
  ];
  for (const [label, value, max] of fields) {
    const text = String(value ?? "").trim();
    if (!text) throw new Error(`${label}不能为空`);
    if (charCount(text) > max) throw new Error(`${label}不能超过 ${max} 个字`);
  }
  return setup;
}
