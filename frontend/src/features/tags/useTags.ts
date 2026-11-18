import { createContext, createElement, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { getTags } from "../../api/client";
import type { TagCategory, TagValue } from "../../api/types";
import { describeError } from "../../components/ErrorMessage";

export interface TagsState {
  categories: TagCategory[]; // empty until loaded (and after a failed first load)
  loading: boolean;
  error: string | null; // why the lists couldn't be loaded; the last ones loaded stay in `categories`
  refresh: () => void;
}

const TagsContext = createContext<TagsState | null>(null);

/** Load the master lists (GET /tags) when `enabled`, and again on refresh(). */
function useTagLoader(enabled: boolean): TagsState {
  const [categories, setCategories] = useState<TagCategory[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState(0);
  const refresh = useCallback(() => setRequest((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setLoading(true);
    getTags(controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setCategories(loaded);
        setError(null);
        setLoading(false);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        const { title, detail } = describeError(failure);
        setError(`${title} ${detail}`.trim());
        setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, request]);

  return { categories, loading, error, refresh };
}

/** Shares one copy of the lists with every screen below it, so a change on the Tags screen shows everywhere. */
export function TagsProvider({ children }: { children: ReactNode }) {
  const tags = useTagLoader(true);
  return createElement(TagsContext.Provider, { value: tags }, children);
}

/**
 * The master lists for tags: the shared copy inside a TagsProvider (the app), otherwise this component's own, loaded
 * once when it mounts (a screen shown on its own, as in tests).
 */
export function useTags(): TagsState {
  const shared = useContext(TagsContext);
  const own = useTagLoader(shared === null);
  return shared ?? own;
}

/** A category's values that may be newly used (retired ones are hidden from pickers). */
export function activeValues(category: TagCategory): TagValue[] {
  return category.values.filter((value) => value.active);
}

/** The category with this key, if the lists have it. */
export function findCategory(categories: TagCategory[], key: string): TagCategory | undefined {
  return categories.find((category) => category.key === key);
}

/** How to show a code: its value's label, or the code itself when the list doesn't have it. */
export function labelFor(categories: TagCategory[], key: string, code: string): string {
  return findCategory(categories, key)?.values.find((value) => value.code === code)?.label ?? code;
}
