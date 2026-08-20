from app.runtime.stores import open_confirmed_history_store
from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    adjacent_context,
    document_window,
)
from app.domain.short_term_memory import ShortTermScene


def _scene(
    turn_index: int,
    scene_text: str,
    *,
    user_input: str = "",
) -> ShortTermScene:
    return ShortTermScene(
        scene_id=f"scene_{turn_index:03d}",
        turn_index=turn_index,
        user_input=user_input,
        scene_text=scene_text,
    )


def test_persistent_store_incrementally_indexes_confirmed_turns(tmp_path):
    path = tmp_path / "confirmed_history.db"
    with open_confirmed_history_store(path) as store:
        assert store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="询问灰烬图书馆。",
            scene_text="希尔达说出了图书馆的位置。",
        )
        assert not store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="询问灰烬图书馆。",
            scene_text="希尔达说出了图书馆的位置。",
        )

    with open_confirmed_history_store(path) as reopened:
        assert reopened.turn_count() == 1
        assert reopened.count() == 2
        assert "turn_1_input" in reopened.fts_search("灰烬图书馆")


def test_upsert_repairs_changed_turn_without_touching_other_history(tmp_path):
    path = tmp_path / "confirmed_history.db"
    with open_confirmed_history_store(path) as store:
        for scene in [_scene(1, "旧正文。"), _scene(2, "应继续保留。")]:
            store.upsert_turn(
                scene_id=scene.scene_id,
                turn_index=scene.turn_index,
                user_input=scene.user_input,
                scene_text=scene.scene_text,
            )

        assert store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="",
            scene_text="替换后的确认正文。",
        )
        assert store.turn_count() == 2
        assert [document.turn_index for document in store.list_documents()] == [
            1,
            2,
        ]
        serialized = "\n".join(
            document.text for document in store.list_documents()
        )
        assert "旧正文" not in serialized
        assert "应继续保留" in serialized
        assert "替换后的确认正文" in serialized


def test_clear_removes_documents_and_fts_rows():
    with open_confirmed_history_store() as store:
        store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="",
            scene_text="灰烬图书馆仍然存在。",
        )
        store.clear()

        assert store.turn_count() == 0
        assert store.count() == 0
        assert store.fts_search("灰烬图书馆") == {}


def test_paragraph_windows_are_available_to_all_history_consumers():
    with open_confirmed_history_store() as store:
        store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="",
            scene_text="第一段。\n\n第二段。\n\n第三段。\n\n第四段。",
        )
        documents = store.documents_for_turn(1)
        anchor = documents[1]

        assert [
            document.text
            for document in document_window(anchor, documents)
        ] == ["第一段。", "第二段。", "第三段。"]
        assert adjacent_context(anchor, documents) == (
            "第一段。",
            "第三段。\n\n第四段。",
        )