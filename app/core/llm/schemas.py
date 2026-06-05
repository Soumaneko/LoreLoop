from pydantic import BaseModel


class EchoResult(BaseModel):
    text: str
    length: int


class MemoryExtractResult(BaseModel):
    pass  # placeholder, to be defined later
