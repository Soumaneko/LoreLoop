__all__ = [
    "apply_core_memory_init",
    "batch_classify_stream",
    "confirm_stream",
    "generate_turn_stream",
    "load_debug_state",
    "memory_maintain_stream",
    "preview_core_memory",
    "save_difficulty",
    "save_max_chars",
]


def __getattr__(name: str):
    if name in {"apply_core_memory_init", "preview_core_memory"}:
        from .core_memory import apply_core_memory_init, preview_core_memory

        return {
            "apply_core_memory_init": apply_core_memory_init,
            "preview_core_memory": preview_core_memory,
        }[name]
    if name in {"batch_classify_stream", "memory_maintain_stream"}:
        from .maintenance import batch_classify_stream, memory_maintain_stream

        return {
            "batch_classify_stream": batch_classify_stream,
            "memory_maintain_stream": memory_maintain_stream,
        }[name]
    if name in {"save_difficulty", "save_max_chars"}:
        from .settings import save_difficulty, save_max_chars

        return {
            "save_difficulty": save_difficulty,
            "save_max_chars": save_max_chars,
        }[name]
    if name == "load_debug_state":
        from .state import load_debug_state

        return load_debug_state
    if name == "confirm_stream":
        from .turn_confirmation import confirm_stream

        return confirm_stream
    if name == "generate_turn_stream":
        from .turn_generation import generate_turn_stream

        return generate_turn_stream
    raise AttributeError(f"module 'app.engine' has no attribute {name!r}")
