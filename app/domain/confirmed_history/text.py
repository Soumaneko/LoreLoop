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


def support_score(basis_text: str | None, window_text: str) -> float:
    """Lexical support of a basis against a window of text.

    Combines trigram overlap with a distinctive-bigram containment ratio so
    short Chinese 2-char terms (约定, 铜牌, 追查) count even when no shared
    trigram survives. Returns 0.0 for empty basis/window. This is the shared
    deterministic n-gram matcher reused across retrieval domains.
    """
    if not basis_text or not window_text:
        return 0.0
    trigram_score, _ = best_trigram_match([basis_text], window_text)
    return max(trigram_score, _substring_support(basis_text, window_text))


def _substring_support(basis_text: str, window_text: str) -> float:
    basis = _compact(basis_text)
    window = _compact(window_text)
    grams = {
        basis[index:index + 2]
        for index in range(len(basis) - 1)
    }
    if not grams:
        return 0.0
    matched = sum(1 for gram in grams if gram in window)
    return matched / len(grams)


def _compact(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", re.sub(r"\s+", "", text.casefold()))
