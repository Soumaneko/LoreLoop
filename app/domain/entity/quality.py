from app.domain.entity.schemas import EntityAliasMatch, EntityProfileUpdateInput, RetrievedEntity


_GENERIC_NPC_NAMES = {
    "登记员",
    "协会登记员",
    "镇长",
    "守卫",
    "民兵",
    "猎人",
    "铁匠",
    "文书",
    "酒保",
    "老板",
    "路人",
    "神秘人",
    "怪物",
}

_GENERIC_NPC_NAME_FRAGMENTS = (
    "登记员",
    "镇长",
    "守卫",
    "民兵",
    "猎人",
    "铁匠",
    "文书",
)

_NPC_KIND_TERMS = (
    "怪物",
    "野猪",
    "狼人",
    "野狼",
    "魔兽",
    "兽群",
    "族群",
)

_GENERIC_LOCATION_NAMES = {
    "铁匠铺",
    "镇东铁匠铺",
    "镇公所",
    "民兵哨所",
    "镇口广场",
    "广场",
    "钟楼",
    "哨所",
    "酒馆",
    "旅店",
    "协会大厅",
}

_GENERIC_ITEM_NAMES = {
    "盾",
    "打火石",
    "任务单",
    "委托单",
    "燃油桶",
    "烈酒",
    "致幻孢子",
    "委托单",
}

_ITEM_KIND_TERMS = (
    "汁液",
    "样本",
    "獠牙",
    "硬皮",
    "藤蔓",
    "异草",
    "茎块",
    "任务单",
    "委托单",
)

_GENERIC_ALIAS_TERMS = (
    "登记员",
    "镇长",
    "守卫",
    "民兵",
    "猎人",
    "铁匠",
    "文书",
    "铁匠铺",
    "镇公所",
    "钟楼",
    "广场",
    "哨所",
    "酒馆",
    "旅店",
    "怪物",
    "野猪",
    "狼人",
)


def is_persistent_entity_candidate(entity_type: str, name: str) -> bool:
    clean = _clean(name)
    if not clean:
        return False
    if entity_type == "npc":
        return _is_persistent_npc(clean)
    if entity_type == "location":
        return _is_persistent_location(clean)
    if entity_type == "item":
        return _is_persistent_item(clean)
    if entity_type == "organization":
        return clean not in {"巡逻队", "民兵队", "商队"}
    return False


def is_acceptable_alias(match: EntityAliasMatch) -> bool:
    alias = _clean(match.new_alias or "")
    if not alias:
        return False
    if alias in _GENERIC_ALIAS_TERMS:
        return False
    if len(alias) <= 3 and any(term in alias for term in _GENERIC_ALIAS_TERMS):
        return False
    if match.matched_entity_type == "npc" and _looks_like_kind_or_role(alias):
        return False
    if match.matched_entity_type == "location" and alias in _GENERIC_LOCATION_NAMES:
        return False
    if match.matched_entity_type == "item" and _is_generic_item(alias):
        return False
    return True


def is_acceptable_retrieved_entity(entity: RetrievedEntity) -> bool:
    return is_persistent_entity_candidate(entity.entity_type, entity.name)


def is_acceptable_profile_update(update: EntityProfileUpdateInput) -> bool:
    return is_persistent_entity_candidate(update.entity_type, update.name)


def _is_persistent_npc(name: str) -> bool:
    if name in _GENERIC_NPC_NAMES:
        return False
    if _looks_like_kind_or_role(name):
        return False
    return True


def _is_persistent_location(name: str) -> bool:
    if name in _GENERIC_LOCATION_NAMES:
        return False
    if len(name) <= 4 and any(name.endswith(term) for term in _GENERIC_LOCATION_NAMES):
        return False
    return True


def _is_persistent_item(name: str) -> bool:
    if name in _GENERIC_ITEM_NAMES:
        return False
    if _is_generic_item(name):
        return False
    return True


def _looks_like_kind_or_role(name: str) -> bool:
    if name in _GENERIC_NPC_NAMES:
        return True
    if any(term in name for term in _GENERIC_NPC_NAME_FRAGMENTS):
        return True
    if any(term in name for term in _NPC_KIND_TERMS):
        return True
    return False


def _is_generic_item(name: str) -> bool:
    return any(name.endswith(term) or term == name for term in _ITEM_KIND_TERMS)


def _clean(value: str) -> str:
    return str(value or "").strip()
