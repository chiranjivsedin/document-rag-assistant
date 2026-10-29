import { describe, expect, it } from "vitest";

import { ApiError, ApiUnreachableError } from "../api/client";
import { describeError } from "./ErrorMessage";

describe("describeError", () => {
  it.each([
    [422, "k: Input should be less than or equal to 20", "Please check the request"],
    [503, "Can't reach Ollama at http://127.0.0.1:11434.", "A required service is unavailable"],
    [502, "The model service failed: out of memory", "The model couldn't answer"],
    [404, "Not Found", "The API answered with status 404"],
  ])("status %i keeps the server's detail", (status, detail, title) => {
    expect(describeError(new ApiError(status, detail))).toEqual({ title, detail });
  });

  it("points to the server log for the API's generic 500", () => {
    expect(describeError(new ApiError(500, "Internal server error."))).toEqual({
      title: "Something went wrong on the server",
      detail: "The details are in the API server's log.",
    });
  });

  it("shows a deliberate 500 message, such as a missing documents folder", () => {
    const detail = "The documents folder (DOCUMENTS_DIR) doesn't exist on the server.";
    expect(describeError(new ApiError(500, detail))).toEqual({ title: "Something went wrong on the server", detail });
  });

  it("explains an unreachable API", () => {
    expect(describeError(new ApiUnreachableError(new TypeError("Failed to fetch"))).title).toBe("Can't reach the API");
  });

  it("handles anything else", () => {
    expect(describeError(new Error("boom"))).toEqual({ title: "Unexpected error", detail: "boom" });
  });
});
