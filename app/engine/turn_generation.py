from app.domain.roll.schemas import Difficulty
from app.graph.generation_graph import stream_generation_turn


def generate_turn_stream(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
):
    yield from stream_generation_turn(
        user_input=user_input,
        difficulty=difficulty,
        max_chars=max_chars,
    )
