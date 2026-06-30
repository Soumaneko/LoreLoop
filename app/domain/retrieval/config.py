from pydantic import BaseModel


class RetrievalConfig(BaseModel):
    top_k: int = 8

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
    rrf_memory_kind_intent_weight: float = 0.5
    rrf_scene_context_weight: float = 0.5
    rrf_evidence_intent_weight: float = 0.6


KIND_FACTOR: dict[str, float] = {
    "character": 0.8,
    "location": 0.9,
    "organization": 1.0,
    "item": 1.2,
    "concept": 1.2,
    "action_keyword": 0.8,
    "unknown": 0.5,
}

IDF_CLAMP_MIN = 0.5
IDF_CLAMP_MAX = 2.0


ACTION_TYPE_MEMORY_KIND_BOOST: dict[str, dict[str, float]] = {
    "signal_ally": {
        "commitment": 4.0,
        "relationship_state": 2.0,
        "action_constraint": 1.0,
    },
    "instruct_ally": {
        "commitment": 3.0,
        "relationship_state": 2.0,
        "action_constraint": 2.0,
    },
    "inspect_object": {
        "resource_access": 3.0,
        "open_thread": 2.0,
        "belief": 1.0,
    },
    "interact_object": {
        "resource_access": 4.0,
        "action_constraint": 1.5,
        "commitment": 1.0,
    },
    "observe": {
        "open_thread": 2.5,
        "belief": 2.0,
        "action_constraint": 1.0,
    },
    "speak": {
        "belief": 3.0,
        "commitment": 2.5,
        "relationship_state": 2.0,
        "resource_access": 1.0,
    },
    "hide_or_stealth": {
        "action_constraint": 3.0,
        "open_thread": 1.5,
        "belief": 1.0,
    },
    "move": {
        "open_thread": 2.0,
        "action_constraint": 1.5,
        "resource_access": 0.5,
    },
}

PAST_REF_KEYWORDS = [
    "之前", "上次", "老办法", "约好的", "见过的", "那个", "同样的",
]


PAST_REF_MEMORY_KIND_BOOST: dict[str, float] = {
    "commitment": 3.0,
    "open_thread": 2.5,
    "resource_access": 2.0,
    "relationship_state": 1.5,
    "belief": 1.5,
    "action_constraint": 1.0,
}


LOW_WEIGHT_ANCHOR_KINDS = {"unknown"}
