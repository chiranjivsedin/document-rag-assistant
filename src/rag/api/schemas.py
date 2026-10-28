"""Request and response bodies of the HTTP API; FastAPI also publishes them in its OpenAPI schema."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    llm_model: str
    embedding_model: str
    problems: list[str]  # one plain-English line per problem; empty when status is "ok"
