from .schemas import ModifierSource, RollModifier, RollModifierBatch

__all__ = [
    "ModifierSource",
    "RollModifier",
    "RollModifierBatch",
    "RollModifierEvaluator",
]


def __getattr__(name: str):
    if name == "RollModifierEvaluator":
        from .evaluator import RollModifierEvaluator

        return RollModifierEvaluator
    raise AttributeError(name)
