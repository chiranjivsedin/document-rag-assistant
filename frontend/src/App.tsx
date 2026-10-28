import { useEffect, useState } from "react";

import { HealthBanner } from "./components/HealthBanner";

// One tab per area of the app. The current tab lives in the URL hash (#chat, #documents, ...), so it survives a
// reload and the built app is just index.html: no client-side routes can collide with API paths like /chat.
export const TABS = [
  {
    id: "chat",
    label: "Chat",
    summary: "Ask questions about the indexed documents and see the cited sources.",
    phase: "6B",
  },
  {
    id: "documents",
    label: "Documents",
    summary: "The mini DMS: browse, upload and manage documents by product, version and type.",
    phase: "6E",
  },
  {
    id: "versions",
    label: "Release notes",
    summary: "Release notes and deprecated features for a product version, starting with 16.2.",
    phase: "6F",
  },
  {
    id: "ingestion",
    label: "Ingestion",
    summary: "Index the documents folder and follow the job until it finishes.",
    phase: "6C",
  },
] as const;

type TabId = (typeof TABS)[number]["id"];

function tabFromHash(): TabId {
  const id = window.location.hash.slice(1);
  return TABS.find((tab) => tab.id === id)?.id ?? "chat";
}

export function App() {
  const [current, setCurrent] = useState<TabId>(tabFromHash);

  useEffect(() => {
    const onHashChange = () => setCurrent(tabFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const tab = TABS.find((t) => t.id === current)!;

  return (
    <div className="app">
      <header className="app-header">
        <h1>RAG Assistant</h1>
        <nav role="tablist" aria-label="Sections">
          {TABS.map((t) => (
            <a
              key={t.id}
              href={`#${t.id}`}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={t.id === current}
              aria-controls="panel"
              className={t.id === current ? "tab tab-current" : "tab"}
            >
              {t.label}
            </a>
          ))}
        </nav>
      </header>
      <HealthBanner />
      <main id="panel" role="tabpanel" aria-labelledby={`tab-${tab.id}`} className="panel">
        <h2>{tab.label}</h2>
        <p>{tab.summary}</p>
        <p className="placeholder">Not built yet (planned for phase {tab.phase}).</p>
      </main>
    </div>
  );
}
