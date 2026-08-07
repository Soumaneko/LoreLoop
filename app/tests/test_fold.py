from app.graph.fold import fold_node_update, fold_state_keys


def test_fold_node_update_accumulates_events_and_overwrites_keys():
    state = {"events": ["a"], "x": 1}
    events = fold_node_update(state, {"events": ["b", "c"], "x": 2, "y": 3})
    assert events == ["b", "c"]
    assert state == {"events": ["a", "b", "c"], "x": 2, "y": 3}


def test_fold_node_update_excludes_extra_keys_from_overwrite():
    state = {"events": [], "node_errors": []}
    events = fold_node_update(
        state,
        {"events": ["e"], "node_errors": ["boom"], "z": 1},
        exclude=frozenset(("node_errors",)),
    )
    assert events == ["e"]
    assert state["events"] == ["e"]
    assert state["z"] == 1
    assert state["node_errors"] == []


def test_fold_node_update_missing_events_is_append_only():
    state = {"events": ["a"]}
    events = fold_node_update(state, {"x": 7})
    assert events == []
    assert state == {"events": ["a"], "x": 7}


def test_fold_state_keys_skips_events_entirely():
    state = {"events": ["a"]}
    fold_state_keys(state, {"events": ["z"], "x": 9})
    assert state == {"events": ["a"], "x": 9}
