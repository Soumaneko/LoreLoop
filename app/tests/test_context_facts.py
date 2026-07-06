import json

import pytest

from app.domain.action.schemas import Anchor
from app.domain.context_facts.extractor import ContextExtractor
from app.domain.context_facts.schemas import (
    ContextExtractInput,
    ContextFactSet,
)
from app.domain.context_facts.views import project_context_facts
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.entity.schemas import EntityProfile
from app.domain.entity.store import EntityStore
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.scene_summary.schemas import SceneSummary
from app.workflows.extract_context_facts import retrieve_context_fact_memories
from app.workflows.select_context_profiles import select_context_profiles


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="客栈上房",
            participants=["萧云痕", "顾晚棠"],
        ),
        player=PlayerState(name="萧云痕", position="床沿边"),
        npcs=[NPCState(name="顾晚棠", location="客栈上房")],
        resources=ResourcesState(inventory=[InventoryItem(name="麂皮褡裢")]),
    )


def _core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="core_world",
            category="world",
            content="当前世界为武侠原创世界。",
            priority=100,
        )
    ])


def test_context_fact_set_assigns_fact_ids_from_drafts():
    result = ContextFactSet.from_drafts(
        turn_focus="萧云痕询问顾晚棠如何应对盐帮。",
        facts=[
            {"fact": "顾晚棠因账册摹本被盐帮追杀。"},
            {"fact": "萧云痕已承诺庇护顾晚棠三日。"},
        ],
    )

    assert [fact.fact_id for fact in result.facts] == ["f1", "f2"]


def test_context_fact_set_rejects_duplicate_fact_ids():
    with pytest.raises(ValueError, match="fact_id"):
        ContextFactSet(
            turn_focus="测试",
            facts=[
                {"fact_id": "f1", "fact": "事实一。"},
                {"fact_id": "f1", "fact": "事实二。"},
            ],
        )


def test_context_extractor_projects_required_inputs_and_hides_fact_id_schema():
    class SpyGateway:
        def __init__(self):
            self.input_data = None

        def run(self, **kwargs):
            self.input_data = kwargs["input_data"]
            return kwargs["output_schema"].model_validate({
                "turn_focus": "萧云痕询问顾晚棠如何应对盐帮。",
                "facts": [
                    {
                        "fact": "顾晚棠曾透露账册摹本不在身上，只有她知道存放处。",
                    }
                ],
            })

    gateway = SpyGateway()
    result = ContextExtractor(gateway).extract(
        ContextExtractInput(
            user_input="我告诉顾晚棠盐帮动向，问她有没有解决方法。",
            core_memory=_core_memory(),
            current_state=_state(),
            scene_summaries=[
                SceneSummary(
                    level=1,
                    content="顾晚棠曾透露账册摹本不在身上。",
                    turn_start=1,
                    turn_end=2,
                )
            ],
            retrieved_memories=CandidateRetrievalResult(
                candidates=[
                    MemoryCandidate(
                        memory_id="m1",
                        memory_kind="resource_access",
                        evidence_mode="reported",
                        subject="走私账册摹本",
                        content="顾晚棠持有账册摹本，且不在她身上。",
                        entities=["顾晚棠", "账册摹本"],
                        salience=0.9,
                        score=1.0,
                    )
                ],
                total_scanned=1,
                total_returned=1,
            ),
            entity_profiles=[
                EntityProfile(
                    entity_type="item",
                    name="麂皮褡裢",
                    summary="萧云痕随身携带的褡裢。",
                ).semantic_view()
            ],
            npc_profiles=[
                NPCProfile(
                    name="顾晚棠",
                    role="万象阁阁主",
                    attitude_to_player="部分信任但仍戒备",
                ).director_view()
            ],
        )
    )

    assert result.facts[0].fact_id == "f1"
    assert "core_world" in gateway.input_data["core_memory_json"]
    assert "scene_001" in gateway.input_data["current_state_json"]
    assert "账册摹本" in gateway.input_data["retrieved_memories_json"]
    assert "顾晚棠曾透露账册摹本" in gateway.input_data["scene_summaries_json"]
    assert "麂皮褡裢" in gateway.input_data["entity_profiles_json"]
    assert "顾晚棠" in gateway.input_data["npc_profiles_json"]
    schema = json.loads(gateway.input_data["output_schema_json"])
    draft_def_name = next(
        name
        for name in schema["$defs"]
        if name.endswith("ContextFactDraft")
    )
    assert "fact_id" not in schema["$defs"][draft_def_name]["properties"]


def test_context_fact_memory_retrieval_uses_context_anchors():
    anchors = [
        Anchor(
            text="盐帮",
            normalized="盐帮",
            kind="organization",
            source="explicit",
            confidence=1.0,
        )
    ]
    with MemoryStore(":memory:") as store:
        store.save_one_returning_id(
            Memory(
                memory_kind="open_thread",
                evidence_mode="observed",
                subject="盐帮追兵",
                content="盐帮追兵仍在客栈外搜寻顾晚棠。",
                entities=["盐帮", "顾晚棠"],
                tags=["追兵"],
                salience=0.9,
            )
        )

        result = retrieve_context_fact_memories(
            user_input="询问顾晚棠如何解决盐帮追捕。",
            anchors=anchors,
            memory_store=store,
        )

    assert result.total_returned == 1
    assert result.candidates[0].subject == "盐帮追兵"
    assert "盐帮" in result.candidates[0].matched_anchors


def test_context_facts_projection_outputs_simple_fact_list():
    facts = ContextFactSet(
        turn_focus="判断盐帮威胁。",
        facts=[
            {
                "fact_id": "f1",
                "fact": "盐帮追兵仍在客栈外搜寻顾晚棠。",
            }
        ],
    )

    projected = json.loads(project_context_facts(facts))

    assert projected == [
        {
            "fact_id": "f1",
            "fact": "盐帮追兵仍在客栈外搜寻顾晚棠。",
        }
    ]


def test_select_context_profiles_matches_state_and_user_input(tmp_path):
    entity_store = EntityStore(str(tmp_path / "entities.db"))
    npc_store = NPCProfileStore(str(tmp_path / "npcs.db"))
    try:
        entity_store.upsert(
            EntityProfile(
                entity_type="item",
                name="麂皮褡裢",
                aliases=["褡裢"],
                summary="萧云痕随身携带的包裹。",
            )
        )
        entity_store.upsert(
            EntityProfile(
                entity_type="organization",
                name="盐帮",
                summary="正在追捕顾晚棠的组织。",
            )
        )
        npc_store.upsert(
            NPCProfile(
                name="顾晚棠",
                aliases=["顾阁主"],
                role="万象阁阁主",
            )
        )

        result = select_context_profiles(
            user_input="外面都是盐帮追兵，我让顾阁主在这里躲一阵。",
            current_state=_state(),
            entity_store=entity_store,
            npc_store=npc_store,
        )
    finally:
        entity_store.close()
        npc_store.close()

    assert {profile.name for profile in result.entity_profiles} >= {"麂皮褡裢", "盐帮"}
    assert [profile.name for profile in result.npc_profiles] == ["顾晚棠"]
    assert {anchor.text for anchor in result.anchors} >= {"顾晚棠", "麂皮褡裢", "盐帮"}
