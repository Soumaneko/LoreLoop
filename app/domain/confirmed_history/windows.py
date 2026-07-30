from collections.abc import Iterable

from .schemas import HistoryDocument


def document_window(
    anchor: HistoryDocument,
    turn_documents: Iterable[HistoryDocument],
    *,
    before: int = 1,
    after: int = 1,
) -> list[HistoryDocument]:
    if anchor.source_type != "scene_paragraph" or anchor.paragraph_index is None:
        return [anchor]
    paragraphs = {
        document.paragraph_index: document
        for document in turn_documents
        if (
            document.source_type == "scene_paragraph"
            and document.paragraph_index is not None
        )
    }
    return [
        paragraphs[index]
        for index in range(
            anchor.paragraph_index - before,
            anchor.paragraph_index + after + 1,
        )
        if index in paragraphs
    ]


def adjacent_context(
    anchor: HistoryDocument,
    turn_documents: Iterable[HistoryDocument],
    *,
    before: int = 2,
    after: int = 2,
) -> tuple[str | None, str | None]:
    window = document_window(
        anchor,
        turn_documents,
        before=before,
        after=after,
    )
    preceding = [
        document.text
        for document in window
        if (
            document.paragraph_index is not None
            and anchor.paragraph_index is not None
            and document.paragraph_index < anchor.paragraph_index
        )
    ]
    following = [
        document.text
        for document in window
        if (
            document.paragraph_index is not None
            and anchor.paragraph_index is not None
            and document.paragraph_index > anchor.paragraph_index
        )
    ]
    return "\n\n".join(preceding) or None, "\n\n".join(following) or None
