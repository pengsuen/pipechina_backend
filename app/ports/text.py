from __future__ import annotations

# 文本大模型端口，要求Provider直接返回经过校验的结构化模型。
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class TextLLMProvider(Protocol):
    """与模型厂商无关的结构化文本生成接口。"""

    name: str
    model: str

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T: ...
