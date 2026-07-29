from app.domain.current_state import CurrentState, make_default_state


def load_effective_current_state(runtime) -> CurrentState:
    """Load persisted state, then pending-opening state, without writing defaults."""
    with runtime.stores.current_state() as state_store:
        current_state = state_store.load()
    if current_state is not None:
        return current_state

    openings = runtime.pending_openings.values()
    if openings:
        return openings[0].current_state
    return make_default_state()
