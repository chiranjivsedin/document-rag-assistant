import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";

import type { TagCategory } from "../api/types";
import { CheckIcon, ChevronDownIcon, SearchIcon, SettingsIcon, SmallCloseIcon } from "./Icons";

/** Chosen codes per category key; an empty or missing list means "Any" (no filter). */
export type FilterSelection = Record<string, string[]>;

export interface FilterChipsProps {
  categories: TagCategory[];
  selected: FilterSelection;
  onChange(next: FilterSelection): void;
  /** Categories that always have a chip, in this order (e.g. product, version, doc_type); others come via + Filter. */
  alwaysShown: string[];
  /** Offer retired values too, marked "(retired)" (filters: documents keep them and they still match). */
  includeRetired: boolean;
  /** Extra controls behind a settings button at the end of the row (Chat: Passages); in the narrow panel too. */
  settings?: ReactNode;
  /** The accessible name of the row and of the narrow panel. */
  label?: string;
  /** Below this width of the row (px) the chips fold into one "Filters (n)" button with a panel. */
  narrowBelow?: number;
}

const SEARCH_OVER = 8; // a value list longer than this gets a search box (Version always has one)
const SHORT_LABELS: Record<string, string> = { doc_type: "Type" };
const BUILTIN_LABELS: Record<string, string> = { product: "Product", version: "Version", doc_type: "Document type" };
const MENU_WIDTH = 240;
const MENU_HEIGHT = 330; // about the tallest menu: search, eight values, Clear

/** The category with this key, or a stand-in without values (lists not loaded, or a key they don't have). */
function categoryFor(categories: TagCategory[], key: string): TagCategory {
  return (
    categories.find((category) => category.key === key) ?? {
      key,
      label: BUILTIN_LABELS[key] ?? key,
      builtin: key in BUILTIN_LABELS,
      multiple: false,
      values: [],
    }
  );
}

const shortLabel = (category: TagCategory) => SHORT_LABELS[category.key] ?? category.label;

/** How a chosen code shows: its value's label, or the code when the list doesn't have it. */
function valueLabel(category: TagCategory, code: string): string {
  return category.values.find((value) => value.code === code)?.label ?? code;
}

function chipText(category: TagCategory, codes: string[]): string {
  if (codes.length === 0) return "Any";
  const first = valueLabel(category, codes[0]);
  return codes.length === 1 ? first : `${first} +${codes.length - 1}`;
}

interface Option {
  code: string;
  label: string;
  retired: boolean;
}

/** A menu's choices: the values in list order (retired ones only if included or chosen), then unknown chosen codes. */
function optionsFor(category: TagCategory, codes: string[], includeRetired: boolean): Option[] {
  const options = category.values
    .filter((value) => value.active || includeRetired || codes.includes(value.code))
    .map((value) => ({ code: value.code, label: value.label, retired: !value.active }));
  const unknown = codes.filter((code) => !category.values.some((value) => value.code === code));
  return [...options, ...unknown.map((code) => ({ code, label: code, retired: false }))];
}

/** Calls `onDismiss` when the user presses the mouse outside `wrap` while `open`. */
function useDismiss(open: boolean, wrap: RefObject<HTMLElement | null>, onDismiss: () => void) {
  const latest = useRef(onDismiss);
  latest.current = onDismiss;
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(event.target as Node)) latest.current();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, wrap]);
}

/** Where a pop-up fits: above its anchor when there's more room there (the docked composer), right-aligned near the edge. */
function usePlacement(open: boolean, anchor: RefObject<HTMLElement | null>, width: number, height: number): string {
  const [placement, setPlacement] = useState("");
  useLayoutEffect(() => {
    if (!open || !anchor.current) return;
    const rect = anchor.current.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom;
    const up = below < height && rect.top > below;
    const end = rect.left + width > window.innerWidth - 8 && rect.right - width >= 8;
    setPlacement(`${up ? " is-up" : ""}${end ? " is-end" : ""}`);
  }, [open, anchor, width, height]);
  return placement;
}

/** The shared behaviour of a button with a pop-up: Escape closes it and returns focus, so does leaving it by Tab. */
function usePopup(open: boolean, setOpen: (open: boolean) => void, width = MENU_WIDTH, height = MENU_HEIGHT) {
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  const placement = usePlacement(open, button, width, height);
  useDismiss(open, wrap, () => setOpen(false));

  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) button.current?.focus();
  }

  const wrapProps = {
    ref: wrap,
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === "Escape" && open) {
        event.preventDefault();
        event.stopPropagation(); // only the innermost open pop-up closes
        close(true);
      }
    },
    onBlur: (event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      if (open && next && wrap.current && !wrap.current.contains(next)) setOpen(false);
    },
  };
  return { button, id, placement, close, wrapProps };
}

/** Moves focus among `items` with the arrow keys, Home and End; returns true when it handled the key. */
function moveFocus(event: KeyboardEvent, items: HTMLElement[], before?: HTMLElement | null): boolean {
  const index = items.indexOf(event.currentTarget as HTMLElement);
  let target: HTMLElement | null | undefined;
  if (event.key === "ArrowDown") target = items[Math.min(index + 1, items.length - 1)];
  else if (event.key === "ArrowUp") target = index <= 0 ? (before ?? items[0]) : items[index - 1];
  else if (event.key === "Home") target = items[0];
  else if (event.key === "End") target = items.at(-1);
  else return false;
  event.preventDefault();
  target?.focus();
  return true;
}

function ValueMenu({
  id,
  category,
  codes,
  includeRetired,
  placement,
  onToggle,
  onClear,
}: {
  id: string;
  category: TagCategory;
  codes: string[];
  includeRetired: boolean;
  placement: string;
  onToggle: (code: string) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const options = optionsFor(category, codes, includeRetired);
  const withSearch = category.key === "version" || options.length > SEARCH_OVER;
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? options.filter((o) => o.label.toLowerCase().includes(needle) || o.code.toLowerCase().includes(needle))
    : options;
  const tabbable = Math.max(0, shown.findIndex((option) => codes.includes(option.code)));
  const items = () => Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);

  useEffect(() => {
    if (withSearch) search.current?.focus();
    else items()[tabbable]?.focus();
  }, []);

  function onSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      items()[0]?.focus();
    } else if (event.key === "Enter") {
      event.preventDefault(); // never submit the form around the chips
      const exact = shown.find((option) => option.label.toLowerCase() === needle);
      const pick = shown.length === 1 ? shown[0] : exact;
      if (pick) onToggle(pick.code);
    }
  }

  function onOptionKey(event: KeyboardEvent<HTMLLIElement>, code: string) {
    if (moveFocus(event, items(), withSearch ? search.current : null)) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onToggle(code);
    }
  }

  return (
    <div className={`filter-pop filter-menu${placement}`}>
      {withSearch && (
        <div className="filter-search">
          <SearchIcon />
          <input
            ref={search}
            type="search"
            aria-label={`Search ${category.label}`}
            aria-controls={id}
            placeholder={`Search ${category.label.toLowerCase()}…`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onSearchKey}
          />
        </div>
      )}
      <ul
        ref={list}
        id={id}
        role="listbox"
        className="filter-options"
        aria-label={category.label}
        aria-multiselectable={category.multiple || undefined}
      >
        {shown.map((option, index) => {
          const chosen = codes.includes(option.code);
          return (
            <li
              key={option.code}
              role="option"
              aria-selected={chosen}
              tabIndex={index === tabbable ? 0 : -1}
              className="filter-option"
              onClick={() => onToggle(option.code)}
              onKeyDown={(event) => onOptionKey(event, option.code)}
            >
              <span>
                {option.label}
                {option.retired && includeRetired && (
                  <>
                    {" "}
                    <span className="filter-retired">(retired)</span>
                  </>
                )}
              </span>
              {chosen && <CheckIcon />}
            </li>
          );
        })}
      </ul>
      {shown.length === 0 && <p className="filter-empty">{options.length ? "No matches" : "No values in this list"}</p>}
      <div className="filter-menu-foot">
        <button type="button" className="filter-clear" onClick={onClear} disabled={codes.length === 0}>
          Clear
        </button>
      </div>
    </div>
  );
}

/** One category's chip: "Version: 16.2 ▾", opening a listbox of its values. */
function Chip({
  category,
  codes,
  open,
  setOpen,
  includeRetired,
  compact = false,
  onSet,
}: {
  category: TagCategory;
  codes: string[];
  open: boolean;
  setOpen: (open: boolean) => void;
  includeRetired: boolean;
  compact?: boolean; // in the narrow panel: the value only, next to its label
  onSet: (codes: string[]) => void;
}) {
  const { button, id, placement, close, wrapProps } = usePopup(open, setOpen);
  const text = chipText(category, codes);

  function toggle(code: string) {
    if (!category.multiple) {
      onSet([code]);
      close(true);
    } else {
      onSet(codes.includes(code) ? codes.filter((c) => c !== code) : [...codes, code]);
    }
  }

  return (
    <span className="filter-anchor" {...wrapProps}>
      <button
        ref={button}
        type="button"
        className={`filter-chip${codes.length ? " is-set" : ""}`}
        aria-label={`${category.label}: ${text}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        {!compact && <span className="filter-chip-key">{shortLabel(category)}:</span>}
        <span className="filter-chip-value">{text}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <ValueMenu
          id={id}
          category={category}
          codes={codes}
          includeRetired={includeRetired}
          placement={placement}
          onToggle={toggle}
          onClear={() => {
            onSet([]);
            close(true);
          }}
        />
      )}
    </span>
  );
}

/** "+ Filter": a menu of the categories that have no chip yet. */
function AddFilter({
  categories,
  open,
  setOpen,
  onAdd,
}: {
  categories: TagCategory[];
  open: boolean;
  setOpen: (open: boolean) => void;
  onAdd: (key: string) => void;
}) {
  const { button, id, placement, wrapProps } = usePopup(open, setOpen, 200, 40 + 34 * categories.length);
  const menu = useRef<HTMLDivElement>(null);
  const items = () => Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);

  useEffect(() => {
    if (open) items()[0]?.focus();
  }, [open]);

  return (
    <span className="filter-anchor" {...wrapProps}>
      <button
        ref={button}
        type="button"
        className="filter-chip filter-add"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        + Filter
      </button>
      {open && (
        <div ref={menu} id={id} role="menu" aria-label="Add a filter" className={`filter-pop filter-menu filter-add-menu${placement}`}>
          {categories.map((category) => (
            <button
              key={category.key}
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="filter-option"
              onClick={() => onAdd(category.key)}
              onKeyDown={(event) => moveFocus(event, items())}
            >
              {category.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

/** The settings button (⚙) and its small dialog. */
function Settings({ open, setOpen, children }: { open: boolean; setOpen: (open: boolean) => void; children: ReactNode }) {
  const { button, id, placement, wrapProps } = usePopup(open, setOpen, 240, 140);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) dialog.current?.querySelector<HTMLElement>("input, select, button")?.focus();
  }, [open]);

  return (
    <span className="filter-anchor filter-settings" {...wrapProps}>
      <button
        ref={button}
        type="button"
        className="filter-icon-button"
        aria-label="Answer settings"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        <SettingsIcon />
      </button>
      {open && (
        <div ref={dialog} id={id} role="dialog" aria-label="Answer settings" className={`filter-pop filter-settings-pop${placement}`}>
          {children}
        </div>
      )}
    </span>
  );
}

/**
 * Filters as a row of same-height chips, each opening a menu of its category's values (search, ticks, Clear), with
 * "+ Filter" for further categories and "Clear all" once two are set. When the row is narrower than `narrowBelow`,
 * it folds into one "Filters (n)" button with a panel, and shows the chosen values as removable tags.
 */
export function FilterChips({
  categories,
  selected,
  onChange,
  alwaysShown,
  includeRetired,
  settings,
  label = "Filters",
  narrowBelow = 500,
}: FilterChipsProps) {
  const root = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  const [added, setAdded] = useState<string[]>([]); // categories given a chip with + Filter
  const [openChip, setOpenChip] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const measure = (width: number) => width > 0 && setNarrow(width < narrowBelow); // 0: not laid out (yet)
    measure(element.getBoundingClientRect().width); // before the first paint, so a narrow screen never flashes the row
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => measure(entries[0]?.contentRect.width ?? 0));
    observer.observe(element);
    return () => observer.disconnect();
  }, [narrowBelow]);

  useEffect(() => {
    // Whatever was open belongs to the other layout.
    setOpenChip(null);
    setAddOpen(false);
    setSettingsOpen(false);
    setPanelOpen(false);
  }, [narrow]);

  const codesOf = (key: string) => selected[key] ?? [];
  const isSet = (key: string) => codesOf(key).length > 0;
  const setKeys = Object.keys(selected).filter(isSet);
  const count = setKeys.length;
  const unknownSet = setKeys.filter((key) => !alwaysShown.includes(key) && !categories.some((c) => c.key === key));

  // The wide row: the always-shown chips, then (in list order) those added or set, then set keys the lists lack.
  const chipKeys = [
    ...alwaysShown,
    ...categories.map((c) => c.key).filter((key) => !alwaysShown.includes(key) && (added.includes(key) || isSet(key))),
    ...unknownSet,
  ];
  const addable = categories.filter((c) => !chipKeys.includes(c.key) && c.values.length > 0);
  // The narrow panel: every filter.
  const panelKeys = [
    ...alwaysShown,
    ...categories.map((c) => c.key).filter((key) => !alwaysShown.includes(key)),
    ...unknownSet,
  ];

  const setCodes = (key: string, codes: string[]) => onChange({ ...selected, [key]: codes });
  const chipFor = (key: string, compact = false) => (
    <Chip
      key={key}
      category={categoryFor(categories, key)}
      codes={codesOf(key)}
      open={openChip === key}
      setOpen={(open) => setOpenChip(open ? key : null)}
      includeRetired={includeRetired}
      compact={compact}
      onSet={(codes) => setCodes(key, codes)}
    />
  );

  // After "Clear all" (which then disappears), keyboard focus goes to the first chip rather than the page.
  const focusFirstChip = useRef(false);
  useEffect(() => {
    if (!focusFirstChip.current) return;
    focusFirstChip.current = false;
    root.current?.querySelector<HTMLElement>(".filter-chip")?.focus();
  });

  function clearAll() {
    setAdded([]);
    setOpenChip(null);
    onChange({});
  }

  if (narrow) {
    return (
      <div className="filter-bar is-narrow" ref={root}>
        <div className="filter-row" role="group" aria-label={label}>
          {count > 0 && (
            <ul className="filter-tags" aria-label="Chosen filters">
              {panelKeys.filter(isSet).flatMap((key) => {
                const category = categoryFor(categories, key);
                return codesOf(key).map((code) => (
                  <li key={`${key}:${code}`} className="filter-tag">
                    {`${shortLabel(category)} ${valueLabel(category, code)}`}
                    <button
                      type="button"
                      aria-label={`Remove ${category.label} ${valueLabel(category, code)}`}
                      onClick={() => setCodes(key, codesOf(key).filter((c) => c !== code))}
                    >
                      <SmallCloseIcon />
                    </button>
                  </li>
                ));
              })}
            </ul>
          )}
          <FilterPanel open={panelOpen} setOpen={setPanelOpen} label={label} count={count} onClearAll={clearAll}>
            {panelKeys.map((key) => (
              <div className="filter-panel-row" key={key}>
                <span className="filter-panel-label" aria-hidden="true">
                  {categoryFor(categories, key).label}
                </span>
                {chipFor(key, true)}
              </div>
            ))}
            {settings && <div className="filter-panel-settings">{settings}</div>}
          </FilterPanel>
        </div>
      </div>
    );
  }

  return (
    <div className="filter-bar" ref={root}>
      <div className="filter-row" role="group" aria-label={label}>
        {chipKeys.map((key) => chipFor(key))}
        {addable.length > 0 && (
          <AddFilter
            categories={addable}
            open={addOpen}
            setOpen={setAddOpen}
            onAdd={(key) => {
              setAddOpen(false);
              setAdded((current) => [...current, key]);
              setOpenChip(key);
            }}
          />
        )}
        {count >= 2 && (
          <button
            type="button"
            className="filter-clear-all"
            onClick={() => {
              focusFirstChip.current = true;
              clearAll();
            }}
          >
            Clear all
          </button>
        )}
        {settings && (
          <Settings open={settingsOpen} setOpen={setSettingsOpen}>
            {settings}
          </Settings>
        )}
      </div>
    </div>
  );
}

/** The narrow layout's "Filters (n)" button and its panel (option B of the approved mockup). */
function FilterPanel({
  open,
  setOpen,
  label,
  count,
  onClearAll,
  children,
}: {
  open: boolean;
  setOpen: (open: boolean) => void;
  label: string;
  count: number;
  onClearAll: () => void;
  children: ReactNode;
}) {
  const { button, id, placement, close, wrapProps } = usePopup(open, setOpen, 320, 360);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) panel.current?.querySelector<HTMLElement>("button")?.focus();
  }, [open]);

  return (
    <span className="filter-anchor filter-panel-anchor" {...wrapProps}>
      <button
        ref={button}
        type="button"
        className={`filter-chip${count ? " is-set" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        <span className="filter-chip-value">{`Filters (${count})`}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <div ref={panel} id={id} role="dialog" aria-label={label} className={`filter-pop filter-panel${placement}`}>
          {children}
          <div className="filter-panel-foot">
            <button type="button" className="button button-ghost button-small" onClick={onClearAll}>
              Clear all
            </button>
            <button type="button" className="button button-primary button-small" onClick={() => close(true)}>
              Done
            </button>
          </div>
        </div>
      )}
    </span>
  );
}
