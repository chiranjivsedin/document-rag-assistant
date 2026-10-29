import { useState } from "react";

import { AlertIcon, ArrowRightIcon, InfoIcon, SparkIcon, TagIcon } from "../../components/Icons";

export const DEFAULT_VERSION = "16.2";

const TOPICS = [
  { icon: SparkIcon, title: "What's new", build: (v: string) => `What is new in version ${v}?` },
  { icon: TagIcon, title: "Deprecated features", build: (v: string) => `Which features are deprecated in version ${v}?` },
  { icon: AlertIcon, title: "Removed features", build: (v: string) => `Which features were removed in version ${v}?` },
];

/**
 * Release notes and deprecated features for one product version. A dedicated view is planned (phase 6F); until
 * then this screen places version-scoped questions in Chat, which answers from the indexed documents.
 */
export function ReleaseNotesPage({ onAsk }: { onAsk: (question: string, version: string) => void }) {
  const [version, setVersion] = useState(DEFAULT_VERSION);
  const chosen = version.trim();

  return (
    <div className="page">
      <section className="hero-card hero-compact">
        <div>
          <h2>Ask about a version</h2>
          <p>
            Choose a version, then pick a topic. The question opens in Chat limited to that version's documents, so the
            answer cites only its release notes and feature lists.
          </p>
        </div>
        <label className="version-field">
          <span>Version</span>
          <input value={version} onChange={(event) => setVersion(event.target.value)} maxLength={100} />
        </label>
      </section>

      <ul className="topic-grid" aria-label="Questions for this version">
        {TOPICS.map(({ icon: Icon, title, build }) => {
          const question = build(chosen || DEFAULT_VERSION);
          return (
            <li key={title}>
              <button type="button" className="topic" onClick={() => onAsk(question, chosen)} disabled={!chosen}>
                <span className="topic-icon">
                  <Icon />
                </span>
                <span className="topic-title">{title}</span>
                <span className="topic-question">{question}</span>
                <span className="topic-action">
                  Ask in Chat
                  <ArrowRightIcon />
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <p className="banner banner-muted">
        <InfoIcon />
        <span className="banner-body">
          Answers depend on the indexed documents. Until a version's release notes are added and indexed, questions
          about it get "I don't know". <span className="placeholder">A dedicated view is not built yet (planned for phase
          6F).</span>
        </span>
      </p>
    </div>
  );
}
