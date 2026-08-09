import hashlib

from .schemas import HistoryDocument


def history_document_text_hash(document: HistoryDocument) -> str:
    """Hash exact stored text as UTF-8, without trimming or normalization."""
    return hashlib.sha256(document.text.encode("utf-8")).hexdigest()
