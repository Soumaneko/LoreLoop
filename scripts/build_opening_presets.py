"""Compile reviewed story starts through the normal initialization pipeline.

Run with: PYTHONPATH=. .venv/bin/python scripts/build_opening_presets.py
This is offline authoring: stored content supplies the agent outputs, without
model calls. All runtime writes go to a temporary application directory.
"""

import argparse
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class AuthoredOpening:
    def __init__(self, content):
        self.content = content

    def set_trace_context(self, **_kwargs):
        pass

    def run(self, task, input_data, output_schema, **kwargs):
        if task == "opening_foundation":
            payload = self.content["foundation"]
        elif task == "opening_setting_requirements":
            payload = {"specs": [
                {"kind": setting["kind"], "setting_description": setting["summary"]}
                for setting in self.content["settings"]
            ]}
        elif task == "director_setting_create":
            setting = next(item for item in self.content["settings"]
                           if item["kind"] == input_data["kind"]
                           and item["summary"] == input_data["guidance"])
            payload = {key: value for key, value in setting.items() if key != "kind"}
        elif task == "opening_scene":
            payload = self.content["opening_scene"]
        else:
            raise ValueError(f"Unexpected authoring task: {task}")
        result = output_schema.model_validate(payload)
        if kwargs.get("on_attempt_start"):
            kwargs["on_attempt_start"](1)
        if kwargs.get("output_validator"):
            kwargs["output_validator"](result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=("fantasy", "wuxia", "xianxia"),
                        help="Rebuild only this preset, preserving the other archives.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="loreloop-preset-build-") as temporary:
        # Set before importing runtime paths, so authoring cannot use real saves.
        os.environ["LORELOOP_DATA_DIR"] = temporary
        from app.graph.initialization_graph import initialization_stream
        from app.domain.current_state import CurrentState
        from app.runtime.saves import SaveManager
        from app.runtime.session import get_campaign_runtime

        destination = ROOT / "app" / "opening_presets"
        destination.mkdir(exist_ok=True)
        runtime = get_campaign_runtime()
        manager = SaveManager(runtime=runtime)
        catalog = {
            item["id"]: item for item in json.loads(
                (destination / "catalog.json").read_text(encoding="utf-8")
            )
        } if args.preset else {}
        try:
            for preset_id in ((args.preset,) if args.preset else ("fantasy", "wuxia", "xianxia")):
                content = json.loads((ROOT / "scripts" / "opening_presets" / f"{preset_id}.json")
                                     .read_text(encoding="utf-8"))
                public = content["public"]
                assert public["id"] == preset_id
                save = manager.create(public["name"])
                runtime.replace_gateway_factory(lambda: AuthoredOpening(content))
                list(initialization_stream(content))
                pending = runtime.pending_openings.values()[0]
                opening_state = pending.current_state.model_dump()
                opening_state.update(content["opening_state"])
                pending.current_state = CurrentState.model_validate(opening_state)
                with runtime.uow(write=True) as uow:
                    uow.current_state.save(pending.current_state)
                runtime.pending_openings.add(pending)
                record = runtime.initialization.load()
                assert record is not None and record.phase == "opening_pending"
                assert record.foundation is not None
                assert runtime.stores.core_memory.load() == record.core_memory
                assert len(runtime.pending_openings.values()) == 1
                manager.export_to(save.save_id, destination / f"{preset_id}.zip")
                catalog[preset_id] = public
                print(f"Built {preset_id}: {public['name']} (opening_pending)")
        finally:
            runtime.close()
        (destination / "catalog.json").write_text(
            json.dumps(list(catalog.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
