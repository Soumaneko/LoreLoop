import re


def trigrams(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.casefold())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", normalized)
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {
        normalized[index:index + 3]
        for index in range(len(normalized) - 2)
    }


def best_trigram_match(
    query_texts: list[str],
    document_text: str,
) -> tuple[float, set[str]]:
    document_trigrams = trigrams(document_text)
    best_score = 0.0
    best_matches: set[str] = set()
    for query_text in query_texts:
        query_trigrams = trigrams(query_text)
        if not query_trigrams:
            continue
        matched = query_trigrams & document_trigrams
        score = len(matched) / max(
            1,
            min(len(query_trigrams), len(document_trigrams)),
        )
        if score > best_score:
            best_score = score
            best_matches = matched
    return best_score, best_matches
