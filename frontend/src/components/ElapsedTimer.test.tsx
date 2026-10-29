import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ElapsedTimer } from "./ElapsedTimer";

afterEach(() => {
  vi.useRealTimers();
});

describe("ElapsedTimer", () => {
  it("counts whole seconds from the start time", () => {
    vi.useFakeTimers();
    render(<ElapsedTimer startedAt={Date.now()} />);
    expect(screen.getByText("0 s")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText("3 s")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(42_000);
    });
    expect(screen.getByText("45 s")).toBeTruthy();
  });
});
