// Response bodies of the HTTP API, mirroring src/rag/api/schemas.py. Keep the two in step.

export interface HealthResponse {
  status: "ok" | "unavailable";
  llm_model: string;
  embedding_model: string;
  problems: string[]; // one plain-English line per problem; empty when status is "ok"
}
