import json
import os
from pathlib import Path

from app.domain.core_memory.schemas import CoreMemory


class CoreMemoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> CoreMemory | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return CoreMemory.model_validate(data)

    def load_required(self) -> CoreMemory:
        core_memory = self.load()
        if core_memory is None:
            raise RuntimeError("Core memory is not initialized. Open the debug UI and complete initialization first.")
        return core_memory

    def save(self, core_memory: CoreMemory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    core_memory.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
