def rrf_fusion(
    ranked_lists: list[dict[str, float]],
    weights: list[float],
    k: float = 60.0,
) -> dict[str, float]:
    all_ids: set[str] = set()
    for rl in ranked_lists:
        all_ids.update(rl.keys())

    memory_scores: dict[str, float] = {mid: 0.0 for mid in all_ids}

    for rl, w in zip(ranked_lists, weights):
        if not rl or w == 0:
            continue
        sorted_items = sorted(rl.items(), key=lambda x: x[1], reverse=True)

        rank = 0
        prev_score = None
        tie_count = 0

        for memory_id, score in sorted_items:
            if score <= 0:
                break

            if prev_score is None or score < prev_score:
                rank = rank + 1 + tie_count
                tie_count = 0
            else:
                tie_count += 1

            prev_score = score
            memory_scores[memory_id] += w / (k + rank)

    return memory_scores
