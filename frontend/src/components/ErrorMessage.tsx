import { ApiError, ApiUnreachableError } from "../api/client";
import { AlertIcon } from "./Icons";

const GENERIC_500 = "Internal server error."; // what src/rag/api/app.py sends for unexpected errors

export interface ErrorText {
  title: string;
  detail: string;
}

/** Plain-English title and detail for anything the API client can throw. */
export function describeError(error: unknown): ErrorText {
  if (error instanceof ApiUnreachableError) {
    return { title: "Can't reach the API", detail: "Is uvicorn running on 127.0.0.1:8000?" };
  }
  if (error instanceof ApiError) {
    switch (error.status) {
      case 422:
        return { title: "Please check the request", detail: error.detail };
      case 503:
        return { title: "A required service is unavailable", detail: error.detail };
      case 502:
        return { title: "The model couldn't answer", detail: error.detail };
      case 500:
        // For unexpected errors the server sends only "Internal server error." and keeps the details in its log;
        // deliberate 500s (e.g. a missing DOCUMENTS_DIR) carry a message meant for the user.
        return {
          title: "Something went wrong on the server",
          detail: error.detail === GENERIC_500 ? "The details are in the API server's log." : error.detail,
        };
      default:
        return { title: `The API answered with status ${error.status}`, detail: error.detail };
    }
  }
  return { title: "Unexpected error", detail: error instanceof Error ? error.message : String(error) };
}

export function ErrorMessage({ error }: { error: ErrorText }) {
  return (
    <div className="banner banner-error" role="alert">
      <AlertIcon />
      <p className="banner-body">
        <strong>{error.title}.</strong> {error.detail}
      </p>
    </div>
  );
}
