from pydantic import BaseModel, Field


class CoreMemoryItem(BaseModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    content: str = Field(min_length=1)
    priority: int = Field(default=100, ge=0)


class CoreMemory(BaseModel):
    items: list[CoreMemoryItem]