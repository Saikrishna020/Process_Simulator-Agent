from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    request: dict[str, Any] | None
    route: str | None
    validation_errors: list[str]
    retry_count: int
    confirmed: bool | None
    manifest: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    model_id: str | None
