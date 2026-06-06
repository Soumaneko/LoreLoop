import json

from app.core.llm import MockLLM
from app.domain.memory.extractor import MemoryExtractor
from app.domain.memory.store import MemoryStore
from app.workflows.extract_memory import extract_memory_workflow


def _build_llm_response(memories: list[dict]) -> str:
    return json.dumps({"memories": memories}, ensure_ascii=False)


class TestExtractMemoryWorkflow:
    def test_workflow_completes_extract_and_save(self):
        mock = MockLLM()
        mock.set_responses([
            _build_llm_response([
                {
                    "type": "agreement",
                    "subject": "暗号约定",
                    "content": "主角和伊莱娜约定用敲桌面作为暗号。",
                    "entities": ["主角", "伊莱娜"],
                    "location": "旅店",
                    "time_label": "第2日",
                    "visibility": ["主角", "伊莱娜"],
                    "tags": ["暗号"],
                    "certainty": 1.0,
                    "salience": 0.8,
                    "source_scene_id": "scene_002",
                    "source_text_span": "敲两下桌面",
                }
            ])
        ])
        extractor = MemoryExtractor(mock.build_gateway())

        with MemoryStore(":memory:") as store:
            ids = extract_memory_workflow(
                scene_text="主角轻敲两下桌面。",
                scene_context={"scene_id": "scene_002", "location": "旅店", "participants": ["主角", "伊莱娜"]},
                extractor=extractor,
                store=store,
            )

            assert len(ids) == 1
            assert store.count() == 1

    def test_workflow_no_memories(self):
        mock = MockLLM()
        mock.set_responses([_build_llm_response([])])
        extractor = MemoryExtractor(mock.build_gateway())

        with MemoryStore(":memory:") as store:
            ids = extract_memory_workflow(
                scene_text="风吹过街道。",
                scene_context={"scene_id": "scene_099"},
                extractor=extractor,
                store=store,
            )

            assert ids == []
            assert store.count() == 0

    def test_workflow_multiple_memories(self):
        mock = MockLLM()
        mock.set_responses([
            _build_llm_response([
                {
                    "type": "item",
                    "subject": "宝剑",
                    "content": "主角获得了宝剑。",
                    "entities": ["主角"],
                    "location": "铁匠铺",
                    "time_label": None,
                    "visibility": ["主角"],
                    "tags": ["武器"],
                    "certainty": 1.0,
                    "salience": 0.6,
                    "source_scene_id": "scene_001",
                    "source_text_span": "取剑",
                },
                {
                    "type": "fact",
                    "subject": "铁匠的背景",
                    "content": "铁匠认识主角父亲。",
                    "entities": ["主角", "铁匠"],
                    "location": "铁匠铺",
                    "time_label": None,
                    "visibility": ["主角", "铁匠"],
                    "tags": ["背景"],
                    "certainty": 0.8,
                    "salience": 0.7,
                    "source_scene_id": "scene_001",
                    "source_text_span": "认识他父亲",
                },
            ])
        ])
        extractor = MemoryExtractor(mock.build_gateway())

        with MemoryStore(":memory:") as store:
            ids = extract_memory_workflow(
                scene_text="主角取剑，铁匠说认识他父亲。",
                scene_context={"scene_id": "scene_001", "location": "铁匠铺", "participants": ["主角", "铁匠"]},
                extractor=extractor,
                store=store,
            )

            assert len(ids) == 2
            assert store.count() == 2
