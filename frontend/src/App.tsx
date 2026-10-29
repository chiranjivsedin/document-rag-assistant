import { useEffect, useState, type ComponentType, type ReactNode } from "react";

import { HealthAlert, HealthStatus, useHealth } from "./components/Health";
import {
  ChatIcon,
  CloseIcon,
  FolderIcon,
  IndexIcon,
  LogoMark,
  MenuIcon,
  MonitorIcon,
  MoonIcon,
  PlusIcon,
  SunIcon,
  TagIcon,
} from "./components/Icons";
import { useTheme, type Theme } from "./components/theme";
import { ChatPage, type ChatPrefill, type HistoryItem } from "./features/chat/ChatPage";
import { turnElementId } from "./features/chat/ChatTurn";
import { IngestPage } from "./features/ingest/IngestPage";
import { DocumentsPage } from "./features/library/DocumentsPage";
import { ReleaseNotesPage } from "./features/releases/ReleaseNotesPage";

// One tab per area of the app. The current tab lives in the URL hash (#chat, #documents, ...), so it survives a
// reload and the built app is just index.html: no client-side routes can collide with API paths like /chat.
export const TABS = [
  { id: "chat", label: "Chat" },
  { id: "versions", label: "Release notes" },
  { id: "documents", label: "Documents" },
  { id: "ingestion", label: "Ingestion" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const ICONS: Record<TabId, ComponentType> = {
  chat: ChatIcon,
  documents: FolderIcon,
  versions: TagIcon,
  ingestion: IndexIcon,
};

const THEMES: Array<{ value: Theme; label: string; icon: ComponentType }> = [
  { value: "system", label: "System", icon: MonitorIcon },
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
];

function tabFromHash(): TabId {
  const id = window.location.hash.slice(1);
  return TABS.find((tab) => tab.id === id)?.id ?? "chat";
}

export function App() {
  const [current, setCurrent] = useState<TabId>(tabFromHash);
  const [prefill, setPrefill] = useState<ChatPrefill | null>(null);
  const [chatSession, setChatSession] = useState(0); // a new value starts a fresh conversation
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const health = useHealth();

  useEffect(() => {
    const onHashChange = () => {
      setCurrent(tabFromHash());
      setMenuOpen(false);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const askInChat = (question: string, version: string) => {
    setPrefill({ id: Date.now(), question, version });
    window.location.hash = "#chat";
  };

  const newChat = () => {
    setChatSession((n) => n + 1);
    setHistory([]);
    setPrefill(null);
    setMenuOpen(false);
    window.location.hash = "#chat";
  };

  const showTurn = (id: number) => {
    window.location.hash = "#chat";
    setMenuOpen(false);
    setTimeout(() => document.getElementById(turnElementId(id))?.scrollIntoView?.({ block: "start", behavior: "smooth" }), 0);
  };

  const tab = TABS.find((t) => t.id === current)!;
  const model = health.state.kind === "ok" ? health.state.health.llm_model : null;

  // Every section stays mounted while another tab is open, so a pending answer, a running ingestion job's polling
  // and their results survive tab switches.
  const panels: Record<TabId, ReactNode> = {
    chat: <ChatPage key={chatSession} prefill={prefill} onHistoryChange={setHistory} />,
    documents: <DocumentsPage />,
    versions: <ReleaseNotesPage onAsk={askInChat} />,
    ingestion: <IngestPage />,
  };

  return (
    <div className={menuOpen ? "app menu-open" : "app"}>
      <aside className="sidebar" id="sidebar" aria-label="Main menu">
        <div className="sidebar-top">
          <div className="brand">
            <LogoMark />
            <span className="brand-name">RAG Assistant</span>
          </div>
          <button type="button" className="icon-button menu-close" onClick={() => setMenuOpen(false)} aria-label="Close menu">
            <CloseIcon />
          </button>
        </div>

        <button type="button" className="button button-primary new-chat" onClick={newChat}>
          <PlusIcon />
          New chat
        </button>

        <nav className="nav" role="tablist" aria-label="Sections" aria-orientation="vertical">
          {TABS.map((t) => {
            const Icon = ICONS[t.id];
            return (
              <a
                key={t.id}
                href={`#${t.id}`}
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={t.id === current}
                aria-controls={`panel-${t.id}`}
                className={t.id === current ? "nav-item nav-current" : "nav-item"}
              >
                <Icon />
                <span>{t.label}</span>
              </a>
            );
          })}
        </nav>

        {history.length > 0 && (
          <div className="recent">
            <h2 className="sidebar-heading">This session</h2>
            <ul>
              {history
                .slice(-8)
                .reverse()
                .map((item) => (
                  <li key={item.id}>
                    <button type="button" className="recent-item" onClick={() => showTurn(item.id)} title={item.question}>
                      {item.question}
                    </button>
                  </li>
                ))}
            </ul>
          </div>
        )}

        <div className="sidebar-foot">
          <HealthStatus state={health.state} onRecheck={health.recheck} />
          <div className="theme-switch" role="radiogroup" aria-label="Theme">
            {THEMES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={theme === value}
                className={theme === value ? "theme-option theme-current" : "theme-option"}
                onClick={() => setTheme(value)}
                title={label}
              >
                <Icon />
                <span className="sr-only">{label}</span>
              </button>
            ))}
          </div>
        </div>
      </aside>

      <div className="backdrop" onClick={() => setMenuOpen(false)} aria-hidden="true" />

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="icon-button menu-open-button"
            onClick={() => setMenuOpen(true)}
            aria-label="Open menu"
            aria-expanded={menuOpen}
            aria-controls="sidebar"
          >
            <MenuIcon />
          </button>
          <h1>{tab.label}</h1>
          {model && (
            <span className="model-chip" title="Chat model answering your questions">
              <span className="model-dot" aria-hidden="true" />
              {model}
            </span>
          )}
        </header>

        <div className="content">
          <HealthAlert state={health.state} onRecheck={health.recheck} />
          {TABS.map((t) => (
            <section
              key={t.id}
              id={`panel-${t.id}`}
              role="tabpanel"
              aria-labelledby={`tab-${t.id}`}
              className={`panel panel-${t.id}`}
              hidden={t.id !== current}
            >
              {panels[t.id]}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
