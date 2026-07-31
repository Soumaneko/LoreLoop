from __future__ import annotations

import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory

from .schemas import SettingMaintenanceResult, WorldSetting


class SettingMaintainer:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def maintain(
        self,
        *,
        core_memory: CoreMemory,
        seeds: list[WorldSetting],
        mature_settings: list[WorldSetting],
        trace_id: str | None = None,
    ) -> SettingMaintenanceResult:
        if not seeds:
            return SettingMaintenanceResult()
        seed_by_id = {setting.setting_id: setting for setting in seeds}
        mature_by_id = {
            setting.setting_id: setting for setting in mature_settings
        }
        return cast(
            SettingMaintenanceResult,
            self._llm.run(
                task="setting_maintain",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "seed_settings_json": json.dumps(
                        [_seed_view(setting) for setting in seeds],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "mature_settings_json": json.dumps(
                        [_mature_view(setting) for setting in mature_settings],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        SettingMaintenanceResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=SettingMaintenanceResult,
                temperature=0.2,
                trace_id=trace_id,
                output_validator=lambda output: _validate_maintenance_result(
                    cast(SettingMaintenanceResult, output),
                    seed_by_id=seed_by_id,
                    mature_by_id=mature_by_id,
                ),
            ),
        )


def _seed_view(setting: WorldSetting) -> dict:
    return {
        "setting_id": setting.setting_id,
        "label": setting.label,
        "kind": setting.kind,
        "basic_concept": setting.basic_concept,
        "visibility": setting.visibility,
        "related_setting_ids": setting.related_setting_ids,
        "sources": [
            source.model_dump(mode="json") for source in setting.sources
        ],
    }


def _mature_view(setting: WorldSetting) -> dict:
    return {
        "setting_id": setting.setting_id,
        "label": setting.label,
        "kind": setting.kind,
        "basic_concept": setting.basic_concept,
        "visibility": setting.visibility,
        "maturity": setting.maturity,
        "status": setting.status,
        "prepared_anchor": (
            setting.prepared_anchor.model_dump(mode="json")
            if setting.prepared_anchor is not None
            else None
        ),
        "sources": [
            source.model_dump(mode="json") for source in setting.sources
        ],
    }


def _validate_maintenance_result(
    result: SettingMaintenanceResult,
    *,
    seed_by_id: dict[str, WorldSetting],
    mature_by_id: dict[str, WorldSetting],
) -> None:
    assigned_seed_ids = [
        seed_id
        for group in result.groups
        for seed_id in group.seed_ids
    ]
    if len(assigned_seed_ids) != len(set(assigned_seed_ids)):
        raise ValueError(
            "Setting maintenance must assign each seed exactly once; "
            "a seed appeared in multiple groups"
        )
    expected_seed_ids = set(seed_by_id)
    actual_seed_ids = set(assigned_seed_ids)
    if actual_seed_ids != expected_seed_ids:
        missing = sorted(expected_seed_ids - actual_seed_ids)
        unknown = sorted(actual_seed_ids - expected_seed_ids)
        raise ValueError(
            "Setting maintenance must assign every input seed exactly once; "
            f"missing={missing}, unknown={unknown}"
        )

    independent_labels: set[str] = set()
    mature_labels = {
        _normalize(setting.label) for setting in mature_by_id.values()
    }
    supplement_targets: set[str] = set()
    for group in result.groups:
        if group.resolution == "reject":
            continue
        if group.resolution == "independent":
            normalized_label = _normalize(group.canonical_label or "")
            if normalized_label in mature_labels or any(
                _normalize(seed_by_id[seed_id].label) in mature_labels
                for seed_id in group.seed_ids
            ):
                raise ValueError(
                    "An independent seed group exactly matches an active usable "
                    "setting label; it must be contained or supplement"
                )
            if normalized_label in independent_labels:
                raise ValueError(
                    "Independent maintenance groups must not produce duplicate "
                    "canonical labels"
                )
            independent_labels.add(normalized_label)
            continue

        target = mature_by_id.get(group.target_setting_id or "")
        if target is None:
            raise ValueError(
                "Setting maintenance supplement/contained target must be an "
                "input active usable setting"
            )
        if target.maturity != "usable" or target.status != "active":
            raise ValueError(
                "Setting maintenance target must be active and usable"
            )
        if group.resolution != "supplement":
            continue
        target_id = target.setting_id
        if target_id in supplement_targets:
            raise ValueError(
                "All supplements for one mature setting must be consolidated "
                "into one maintenance group"
            )
        supplement_targets.add(target_id)
        if target.prepared_anchor is not None:
            if not group.supplemented_definition:
                raise ValueError(
                    "Supplementing a prepared setting requires an updated "
                    "summary definition"
                )
            additions = [
                _normalize(addition.fact)
                for addition in group.fact_additions
                if _normalize(addition.fact)
            ]
            if len(additions) != len(set(additions)):
                raise ValueError(
                    "A supplement group must not add the same fact twice"
                )
            existing = {
                _normalize(fact)
                for fact in target.prepared_anchor.setting_facts
                if _normalize(fact)
            }
            if len(existing | set(additions)) > 16:
                raise ValueError(
                    "Supplement would exceed the setting_facts limit; merge "
                    "overlapping facts more compactly"
                )


def _normalize(value: str) -> str:
    return "".join(str(value).strip().casefold().split())
