"""Request and response bodies of the HTTP API; FastAPI also publishes them in its OpenAPI schema."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Optional filter: surrounding spaces trimmed; an empty value means "no filter", as in retrieve().
Filter = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    llm_model: str
    embedding_model: str
    problems: list[str]  # one plain-English line per problem; empty when status is "ok"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt filter is an error, not silently ignored

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    product: Filter | None = None
    version: Filter | None = None
    k: int = Field(default=5, ge=1, le=20, description="Number of passages to retrieve")


class ChatSource(BaseModel):
    number: int = Field(description="Matches the [n] citations in the answer")
    source: str | None
    product: str | None
    version: str | None
    doc_type: str | None
    page: int | None
    distance: float = Field(description="Cosine distance to the question: 0 = same meaning, larger = less related")
    excerpt: str = Field(description="Start of the passage, for previews")


class ChatResponse(BaseModel):
    answer: str
    no_answer: bool = Field(description="True when the documents don't contain the answer")
    sources: list[ChatSource]  # empty when no_answer is true
    model: str
    elapsed_ms: int
