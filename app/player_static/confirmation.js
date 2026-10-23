export function createConfirmationActions({
  state, activePending, selectedVariant, sessionStorage, CONFIRMATION_IN_FLIGHT_KEY,
  setMode, renderPending, renderControls, renderAll, readDraft, newRequestId,
  handleConfirmationEvent, acknowledgeHandledOperation, acknowledgeFailedOperation,
  refreshRecords, refreshRecordsAfterTurn, showToast, showError, stream, subscribe,
  request, loadAll, tokenUsage
}) {
  let uncertainConfirmation = null;

  async function showConfirmationFailure(error, confirmation, completed = false) {
    let outcome = completed ? "confirmed" : "unknown";
    if (!completed) {
      try {
        const result = await request(`/api/player/confirmation?save_id=${encodeURIComponent(confirmation.saveId)}&turn_id=${encodeURIComponent(confirmation.turnId)}`);
        outcome = result.outcome;
      } catch (_lookupError) { /* A lost connection cannot prove a failed commit. */ }
    }
    uncertainConfirmation = ["unknown", "in_progress"].includes(outcome) ? confirmation : null;
    setMode(outcome === "not_confirmed" ? "pending" : "idle");
    renderAll();
    showError(error, undefined, {
      confirmationOutcome: outcome,
      recheckConfirmation: () => showConfirmationFailure(error, confirmation),
    });
    if (outcome === "confirmed") {
      try { await loadAll({ resumeOperation: false }); } catch (_refreshError) { /* Keep the confirmed result visible. */ }
    }
  }

  async function confirmTurn() {
    const pending = activePending(); const variant = selectedVariant();
    if (!pending || !variant) return;
    if (uncertainConfirmation && uncertainConfirmation.saveId === state.activeSave
        && uncertainConfirmation.turnId === variant.turn_id) {
      await showConfirmationFailure(new Error("需要先核实上一次确认的结果。"), uncertainConfirmation);
      return;
    }
    const confirmation = { turnId: variant.turn_id, saveId: state.activeSave };
    let completed = false;
    sessionStorage.setItem(CONFIRMATION_IN_FLIGHT_KEY, "1");
    setMode("confirming"); renderPending(); renderControls();
    try {
      const operationId = await stream("/api/player/confirm", {
        turn_id: variant.turn_id,
        scene_text: readDraft(variant),
        request_id: newRequestId(),
      }, handleConfirmationEvent);
      completed = true;
      await acknowledgeHandledOperation(operationId);
      const storedUsage = tokenUsage.getVariantUsage(variant.turn_id);
      const genUsage = storedUsage?.generation || tokenUsage.getCurrentPendingUsage().generation;
      const confUsage = tokenUsage.getCurrentPendingUsage().confirmation;
      tokenUsage.saveFullVariantUsage(variant.turn_id, {
        generation: genUsage,
        confirmation: confUsage,
      });
      tokenUsage.cleanUpUnconfirmedVariants(
        variant.turn_id,
        pending.variants.map((v) => v.turn_id),
      );
      await loadAll({ scroll: "smooth" });
      tokenUsage.clearPendingUsage();
      showToast("这一回合已经写入旅程");
      refreshRecordsAfterTurn();
    } catch (error) {
      await showConfirmationFailure(error, confirmation, completed);
      refreshRecords();
      await acknowledgeFailedOperation(error);
    }
    finally {
      sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
    }
  }

  async function resumeConfirmationOperation(operation) {
    const confirmation = { turnId: operation.metadata?.turn_id, saveId: state.activeSave };
    let completed = false;
    sessionStorage.setItem(CONFIRMATION_IN_FLIGHT_KEY, "1");
    setMode("confirming");
    renderPending();
    renderControls();
    try {
      await subscribe(
        `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
        handleConfirmationEvent,
      );
      completed = true;
      await acknowledgeHandledOperation(operation.operation_id);
      sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
      await loadAll({ resumeOperation: false });
      showToast("这一回合已经写入旅程");
      refreshRecordsAfterTurn();
      return true;
    } catch (error) {
      sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
      await showConfirmationFailure(error, confirmation, completed);
      refreshRecords();
      await acknowledgeFailedOperation(error, operation.operation_id);
      return false;
    }
  }
  return { confirmTurn, resumeConfirmationOperation };
}
