from pydantic import BaseModel


class EchoResult(BaseModel):
    text: str
    length: int
