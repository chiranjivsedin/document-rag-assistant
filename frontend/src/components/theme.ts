import { useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";

const KEY = "rag-assistant-theme";

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system"; // storage blocked (private mode, policies): follow the system
  }
}

/** The colour theme: follows the system unless the user picks one; remembered in this browser. */
export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(stored);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      // not remembered; the choice still applies for this visit
    }
  }, [theme]);

  return [theme, setTheme];
}
