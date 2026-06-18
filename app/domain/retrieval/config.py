from pydantic import BaseModel


class RetrievalConfig(BaseModel):
    top_k: int = 20

    entity_weight: float = 3.0
    tag_weight: float = 2.0
    subject_weight: float = 2.5
    content_weight: float = 1.0

    salience_weight: float = 2.0
    confidence_weight: float = 0.5

    low_overlap_penalty: float = 2.0
    generic_content_penalty: float = 1.5
    excessive_length_penalty: float = 1.0

    rrf_k: float = 60.0
    rrf_entity_weight: float = 0.8
    rrf_subject_tag_weight: float = 1.2
    rrf_content_keyword_weight: float = 0.8
    rrf_subtype_intent_weight: float = 0.5
    rrf_scene_context_weight: float = 0.5
    rrf_layer_intent_weight: float = 0.6

    multi_action_bonus: float = 0.01


KIND_FACTOR: dict[str, float] = {
    "character": 0.8,
    "location": 0.9,
    "organization": 1.0,
    "item": 1.2,
    "concept": 1.2,
    "action_keyword": 0.8,
    "past_reference": 1.2,
    "temporal_reference": 0.5,
    "condition": 0.4,
    "unknown": 0.5,
}

IDF_CLAMP_MIN = 0.5
IDF_CLAMP_MAX = 2.0


ACTION_TYPE_SUBTYPE_INTENT_BOOST: dict[str, dict[str, float]] = {
    "signal_ally": {
        "agreement": 5.0,
        "trait": 1.5,
        "relationship": 1.0,
        "fact": 0.5,
        "item": 0.5,
        "event": 0.5,
    },
    "instruct_ally": {
        "agreement": 3.0,
        "relationship": 2.0,
        "trait": 1.5,
        "fact": 0.5,
        "item": 0.5,
        "event": 0.5,
    },
    "inspect_object": {
        "item": 4.0,
        "event": 2.0,
        "fact": 2.0,
        "agreement": 0.5,
        "trait": 0.5,
        "relationship": 0.5,
    },
    "interact_object": {
        "item": 4.0,
        "agreement": 1.0,
        "fact": 0.5,
        "trait": 0.5,
        "relationship": 0.5,
        "event": 0.5,
    },
    "observe": {
        "trait": 2.0,
        "fact": 2.0,
        "event": 1.5,
        "item": 1.5,
        "agreement": 0.5,
        "relationship": 0.5,
    },
    "speak": {
        "relationship": 3.0,
        "agreement": 2.0,
        "trait": 2.0,
        "fact": 1.0,
        "item": 1.0,
        "event": 1.0,
    },
    "hide_or_stealth": {
        "trait": 2.0,
        "fact": 2.0,
        "event": 1.0,
        "agreement": 0.5,
        "relationship": 0.5,
        "item": 0.5,
    },
    "move": {
        "fact": 2.0,
        "event": 1.0,
        "item": 1.0,
        "agreement": 0.5,
        "trait": 0.5,
        "relationship": 0.5,
    },
}


ACTION_TYPE_LAYER_BOOST: dict[str, dict[str, float]] = {
    "observe": {"truth": 1.2, "claim": 0.8, "belief": 0.5, "profile": 0.4},
    "inspect_object": {"truth": 1.2, "claim": 0.6, "belief": 0.4, "profile": 0.4},
    "interact_object": {"truth": 1.0, "claim": 0.6, "belief": 0.4, "profile": 0.5},
    "speak": {"claim": 1.2, "belief": 1.0, "profile": 0.8, "truth": 0.7},
    "signal_ally": {"belief": 1.1, "profile": 1.0, "claim": 0.8, "truth": 0.7},
    "instruct_ally": {"belief": 1.1, "profile": 1.0, "claim": 0.8, "truth": 0.7},
    "hide_or_stealth": {"truth": 1.0, "profile": 0.8, "belief": 0.6, "claim": 0.5},
    "attack": {"truth": 1.0, "profile": 0.8, "belief": 0.4, "claim": 0.3},
    "defend": {"truth": 1.0, "profile": 0.8, "belief": 0.5, "claim": 0.4},
    "cast_or_use_power": {"truth": 1.0, "profile": 0.7, "claim": 0.5, "belief": 0.4},
    "move": {"truth": 1.1, "claim": 0.6, "belief": 0.4, "profile": 0.4},
    "plan": {"truth": 1.0, "claim": 0.9, "belief": 0.8, "profile": 0.8},
    "wait": {"truth": 0.8, "claim": 0.6, "belief": 0.5, "profile": 0.5},
    "other": {"truth": 0.8, "claim": 0.7, "belief": 0.6, "profile": 0.6},
}


ACTION_TYPE_SUBTYPE_BOOST: dict[str, dict[str, float]] = {
    "observe": {
        "fact": 0.5,
        "event": 0.4,
        "location_state": 0.5,
        "item": 0.3,
        "secret": 0.3,
    },
    "inspect_object": {
        "item": 0.6,
        "resource": 0.5,
        "fact": 0.4,
        "location_state": 0.4,
        "secret": 0.3,
    },
    "interact_object": {
        "item": 0.6,
        "resource": 0.5,
        "agreement": 0.3,
        "location_state": 0.3,
    },
    "speak": {
        "testimony": 0.6,
        "rumor": 0.5,
        "belief": 0.5,
        "misunderstanding": 0.4,
        "relationship": 0.4,
        "agreement": 0.4,
    },
    "signal_ally": {
        "relationship": 0.5,
        "agreement": 0.5,
        "belief": 0.4,
        "trait": 0.4,
        "goal": 0.4,
    },
    "instruct_ally": {
        "relationship": 0.5,
        "agreement": 0.5,
        "belief": 0.4,
        "trait": 0.4,
        "goal": 0.4,
    },
    "hide_or_stealth": {
        "location_state": 0.5,
        "trait": 0.4,
        "event": 0.3,
        "secret": 0.3,
    },
    "attack": {
        "trait": 0.5,
        "event": 0.4,
        "fact": 0.3,
        "item": 0.3,
    },
    "defend": {
        "trait": 0.5,
        "event": 0.4,
        "fact": 0.3,
        "item": 0.3,
    },
    "cast_or_use_power": {
        "fact": 0.4,
        "trait": 0.4,
        "item": 0.3,
        "secret": 0.3,
    },
    "move": {
        "location_state": 0.6,
        "event": 0.4,
        "fact": 0.3,
    },
    "plan": {
        "goal": 0.5,
        "agreement": 0.4,
        "fact": 0.3,
        "event": 0.3,
        "testimony": 0.3,
    },
}


PAST_REF_KEYWORDS = [
    "之前", "上次", "老办法", "约好的", "见过的", "那个", "同样的",
]


PAST_REF_SUBTYPE_BOOST: dict[str, float] = {
    "agreement": 3.0,
    "event": 2.0,
    "item": 2.0,
    "trait": 1.5,
    "relationship": 1.0,
}


LOW_WEIGHT_ANCHOR_KINDS = {"condition", "temporal_reference", "unknown"}
