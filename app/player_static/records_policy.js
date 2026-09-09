export const RECORDS_POLL_PAUSED_MODES = new Set([
  "generating",
  "confirming",
]);

export function isRecordsPollingPaused(mode) {
  return RECORDS_POLL_PAUSED_MODES.has(mode);
}

export function shouldSuppressRecordsPollError({ startedMode, currentMode }) {
  return (
    isRecordsPollingPaused(startedMode)
    || isRecordsPollingPaused(currentMode)
  );
}
