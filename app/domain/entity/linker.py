from app.domain.entity.schemas import EntityProfile
from app.domain.entity.store import EntityStore, _match_score


GENERIC_TERMS = {"玩家", "主角", "你", "他", "她", "它", "众人", "所有人", "人们"}


class EntityMemoryLinker:
    def __init__(self, entity_store: EntityStore):
        self._store = entity_store

    def link_records(self, memories: list) -> list[tuple[str, str]]:
        created: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for memory in memories:
            names = [memory.subject, *memory.entities]
            for name in names:
                entity = self._match(name)
                if entity is None:
                    continue
                pair = (entity.entity_id, memory.id)
                if pair in seen:
                    continue
                seen.add(pair)
                if self._store.link_memory(entity.entity_id, memory.id):
                    created.append(pair)
                self._store.append_source_memory(entity.entity_id, memory.id)
        return created

    def _match(self, memory_entity_name: str) -> EntityProfile | None:
        term = str(memory_entity_name).strip()
        if not term or term in GENERIC_TERMS:
            return None

        best_score = 0.0
        best_entity: EntityProfile | None = None
        for entity in self._store.list_all():
            score = _match_score(term, entity.name, entity.aliases)
            if score > best_score:
                best_score = score
                best_entity = entity
            elif score == best_score and best_entity is not None:
                if entity.confidence > best_entity.confidence:
                    best_entity = entity
                elif (
                    entity.confidence == best_entity.confidence
                    and entity.updated_at > best_entity.updated_at
                ):
                    best_entity = entity

        if best_score >= 0.6:
            return best_entity
        return None
