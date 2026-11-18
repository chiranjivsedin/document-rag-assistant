import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { useState } from "react";

import type { TagCategory } from "../api/types";
import { FilterChips, type FilterChipsProps } from "./FilterChips";

const VERSIONS = ["1.0", "9.9", "16.2", "16.4", "20.2"];

const TAGS: TagCategory[] = [
  { key: "product", label: "Product", builtin: true, multiple: false, values: [
    { id: 1, code: "acme-dms", label: "Acme DMS", active: true },
    { id: 2, code: "content-management", label: "Content Management", active: true },
  ] },
  { key: "version", label: "Version", builtin: true, multiple: false, values: [
    ...VERSIONS.map((code, i) => ({ id: 10 + i, code, label: code, active: true })),
    { id: 19, code: "0.9", label: "0.9", active: false },
  ] },
  { key: "doc_type", label: "Document type", builtin: true, multiple: false, values: [
    { id: 20, code: "release-notes", label: "Release notes", active: true },
    { id: 21, code: "install-guide", label: "Installation guide", active: true },
  ] },
  { key: "module", label: "Module", builtin: false, multiple: false, values: [
    { id: 30, code: "workflow", label: "Workflow", active: true },
    { id: 31, code: "search", label: "Search", active: false },
  ] },
  { key: "audience", label: "Audience", builtin: false, multiple: true, values: [
    { id: 40, code: "admins", label: "Administrators", active: true },
    { id: 41, code: "developers", label: "Developers", active: true },
  ] },
];

const BUILTINS = ["product", "version", "doc_type"];

/** FilterChips with its selection kept in state, as a screen would; every change is also recorded. */
function Harness({ initial = {}, onChange = () => {}, ...props }: Partial<FilterChipsProps> & { initial?: Record<string, string[]> }) {
  const [selected, setSelected] = useState<Record<string, string[]>>(initial);
  return (
    <>
      <FilterChips
        categories={TAGS}
        alwaysShown={BUILTINS}
        includeRetired
        {...props}
        selected={selected}
        onChange={(next) => {
          onChange(next);
          setSelected(next);
        }}
      />
      <button type="button">Elsewhere</button>
    </>
  );
}

const chip = (name: string) => screen.getByRole("button", { name });
const optionNames = (listbox: HTMLElement) => within(listbox).getAllByRole("option").map((o) => o.textContent);

/** ResizeObserver stand-in reporting `width` for every observed element. */
function stubWidth(width: number) {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback([{ target, contentRect: { width } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {}
      disconnect() {}
    },
  );
}

describe("FilterChips: chips", () => {
  it("shows the always-shown categories as chips in one row, unset ones as Any", () => {
    render(<Harness />);
    const group = screen.getByRole("group", { name: "Filters" });
    expect(within(group).getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual([
      "Product: Any", "Version: Any", "Document type: Any", "+ Filter"]);
    expect(chip("Document type: Any").textContent).toContain("Type:"); // short visible label
    expect(chip("Product: Any").className).not.toContain("is-set");
    expect(chip("Product: Any").getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
  });

  it("opens a menu with a search box for Version that narrows the list; short lists have none", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(chip("Version: Any"));
    expect(chip("Version: Any").getAttribute("aria-expanded")).toBe("true");
    const listbox = screen.getByRole("listbox", { name: "Version" });
    expect(optionNames(listbox)).toEqual([...VERSIONS, "0.9 (retired)"]);
    const search = screen.getByRole("searchbox", { name: "Search Version" });
    expect(document.activeElement).toBe(search);
    await user.type(search, "16.");
    expect(optionNames(listbox)).toEqual(["16.2", "16.4"]);
    await user.type(search, "x");
    expect(screen.getByText("No matches")).toBeTruthy();

    await user.click(chip("Version: Any")); // the chip toggles its menu
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.click(chip("Product: Any"));
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("choosing a value closes the menu, updates the chip and reports the change", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await user.click(chip("Version: Any"));
    await user.click(screen.getByRole("option", { name: "16.2" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: ["16.2"] });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(chip("Version: 16.2").className).toContain("is-set");
    expect(document.activeElement).toBe(chip("Version: 16.2"));

    await user.click(chip("Version: 16.2"));
    expect(screen.getByRole("option", { name: "16.2" }).getAttribute("aria-selected")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: [] });
    expect(chip("Version: Any")).toBeTruthy();
  });

  it("offers Clear all once two filters are set, and it clears every one", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness initial={{ version: ["16.2"] }} onChange={onChange} />);
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
    await user.click(chip("Product: Any"));
    await user.click(screen.getByRole("option", { name: "Acme DMS" }));
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onChange).toHaveBeenLastCalledWith({});
    expect(document.activeElement).toBe(chip("Product: Any")); // not lost with the vanished button
    expect(chip("Version: Any")).toBeTruthy();
  });

  it("+ Filter lists only the categories without a chip, and adds the chosen one with its menu open", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(chip("+ Filter"));
    const menu = screen.getByRole("menu", { name: "Add a filter" });
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Module", "Audience"]);
    await user.click(within(menu).getByRole("menuitem", { name: "Module" }));
    expect(chip("Module: Any").getAttribute("aria-expanded")).toBe("true");
    await user.click(screen.getByRole("option", { name: "Workflow" }));

    await user.click(chip("+ Filter"));
    expect(within(screen.getByRole("menu")).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Audience"]);
    await user.click(screen.getByRole("menuitem", { name: "Audience" }));
    expect(screen.queryByRole("button", { name: "+ Filter" })).toBeNull(); // nothing left to add
  });

  it("a multi-value category takes several ticks and stays open", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness alwaysShown={["audience"]} onChange={onChange} />);
    await user.click(chip("Audience: Any"));
    expect(screen.getByRole("listbox", { name: "Audience" }).getAttribute("aria-multiselectable")).toBe("true");
    await user.click(screen.getByRole("option", { name: "Administrators" }));
    await user.click(screen.getByRole("option", { name: "Developers" }));
    expect(onChange).toHaveBeenLastCalledWith({ audience: ["admins", "developers"] });
    expect(screen.getAllByRole("option").map((o) => o.getAttribute("aria-selected"))).toEqual(["true", "true"]);
    await user.click(screen.getByRole("option", { name: "Administrators" }));
    expect(onChange).toHaveBeenLastCalledWith({ audience: ["developers"] });
    expect(chip("Audience: Developers")).toBeTruthy();
  });

  it("marks retired values only when they are included", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness />);
    await user.click(chip("Version: Any"));
    expect(screen.getByRole("option", { name: "0.9 (retired)" })).toBeTruthy();
    unmount();

    render(<Harness includeRetired={false} />);
    await user.click(chip("Version: Any"));
    expect(optionNames(screen.getByRole("listbox"))).toEqual(VERSIONS);
  });

  it("shows a chosen code the lists don't have", () => {
    render(<Harness initial={{ version: ["7.7"] }} />);
    expect(chip("Version: 7.7").className).toContain("is-set");
  });
});

describe("FilterChips: keyboard and focus", () => {
  it("Enter opens, arrows move, Enter picks; Escape closes and returns focus to the chip", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    chip("Product: Any").focus();
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "Acme DMS" }));
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "Content Management" }));
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "Acme DMS" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.activeElement).toBe(chip("Product: Any"));
    expect(onChange).not.toHaveBeenCalled();

    await user.keyboard(" "); // Space opens too
    await user.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenLastCalledWith({ product: ["content-management"] });
    expect(document.activeElement).toBe(chip("Product: Content Management"));
  });

  it("moves from the search box into the list with the down arrow", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(chip("Version: Any"));
    await user.type(screen.getByRole("searchbox"), "20");
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "20.2" }));
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(screen.getByRole("searchbox"));
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(chip("Version: Any"));
  });

  it("Enter in the search box picks the only match and never submits a surrounding form", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn((event: Event) => event.preventDefault());
    const onChange = vi.fn();
    render(
      <form onSubmit={(event) => onSubmit(event.nativeEvent)}>
        <Harness onChange={onChange} />
      </form>,
    );
    await user.click(chip("Version: Any"));
    await user.type(screen.getByRole("searchbox"), "16{Enter}");
    expect(onChange).not.toHaveBeenCalled(); // two matches: nothing picked
    await user.type(screen.getByRole("searchbox"), ".4{Enter}");
    expect(onChange).toHaveBeenLastCalledWith({ version: ["16.4"] });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("closes when clicking outside", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(chip("Product: Any"));
    expect(screen.getByRole("listbox")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});

describe("FilterChips: settings and narrow screens", () => {
  it("puts the settings behind a settings button", async () => {
    const user = userEvent.setup();
    render(<Harness settings={<label>Passages <input type="number" defaultValue={3} /></label>} />);
    expect(screen.queryByLabelText("Passages")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Answer settings" }));
    expect(screen.getByRole("dialog", { name: "Answer settings" })).toBeTruthy();
    expect(screen.getByLabelText("Passages")).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("Passages")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Answer settings" }));
  });

  it("below the narrow width shows one Filters (n) button with a panel, and the set filters as removable tags", async () => {
    stubWidth(400);
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Harness
        initial={{ version: ["16.2"], module: ["workflow"] }}
        onChange={onChange}
        settings={<label>Passages <input type="number" defaultValue={3} /></label>}
      />,
    );
    expect(screen.queryByRole("button", { name: /^Product:/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Answer settings" })).toBeNull();
    const button = chip("Filters (2)");
    expect(screen.getAllByRole("listitem").map((tag) => tag.textContent)).toEqual(["Version 16.2", "Module Workflow"]);

    await user.click(button);
    const panel = screen.getByRole("dialog", { name: "Filters" });
    expect(within(panel).getAllByRole("button", { name: /: / }).map((b) => b.getAttribute("aria-label"))).toEqual([
      "Product: Any", "Version: 16.2", "Document type: Any", "Module: Workflow", "Audience: Any"]);
    expect(within(panel).getByLabelText("Passages")).toBeTruthy();

    await user.click(within(panel).getByRole("button", { name: "Product: Any" }));
    await user.click(screen.getByRole("option", { name: "Acme DMS" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: ["16.2"], module: ["workflow"], product: ["acme-dms"] });
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy(); // the panel stays open
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(chip("Filters (3)"));

    await user.click(screen.getByRole("button", { name: "Remove Version 16.2" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: [], module: ["workflow"], product: ["acme-dms"] });
    expect(chip("Filters (2)")).toBeTruthy();
  });
});
