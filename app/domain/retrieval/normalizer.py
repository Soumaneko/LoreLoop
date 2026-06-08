import re
import unicodedata


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip()
