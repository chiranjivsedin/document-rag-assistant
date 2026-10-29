import type { ChatSource } from "../../api/types";
import { FileIcon } from "../../components/Icons";

export function sourceElementId(turnId: number, number: number): string {
  return `turn-${turnId}-source-${number}`;
}

export function previewElementId(turnId: number): string {
  return `turn-${turnId}-preview`;
}

/** Split a source path into its folders (muted) and file name (emphasised). */
function splitPath(path: string | null): [string, string] {
  const value = path ?? "unknown source";
  const cut = value.lastIndexOf("/") + 1;
  return [value.slice(0, cut), value.slice(cut)];
}

function where(source: ChatSource): string {
  const parts = [source.version && `Version ${source.version}`, source.page !== null && `page ${source.page}`];
  return parts.filter(Boolean).join(", ") || source.product || "Document";
}

function tags(source: ChatSource): string[] {
  return [
    source.product,
    source.version && `version ${source.version}`,
    source.doc_type,
    source.page !== null && `page ${source.page}`,
    `distance ${source.distance.toFixed(3)}`,
  ].filter((tag): tag is string => Boolean(tag));
}

interface Props {
  turnId: number;
  sources: ChatSource[];
  cited: Set<number>;
  selected: number | null;
  onSelect: (number: number) => void;
}

/**
 * The passages an answer was built from: a strip of compact cards numbered like the [n] citations, and a preview
 * of the selected passage (chosen from a card or a citation).
 */
export function SourceList({ turnId, sources, cited, selected, onSelect }: Props) {
  const current = sources.find((source) => source.number === selected) ?? null;

  return (
    <section className="sources" aria-label="Sources">
      <div className="section-head">
        <h3>Sources</h3>
        <span>
          {cited.size} of {sources.length} cited
        </span>
      </div>
      <ol className="source-strip">
        {sources.map((source) => {
          const [, file] = splitPath(source.source);
          const isCited = cited.has(source.number);
          return (
            <li
              key={source.number}
              id={sourceElementId(turnId, source.number)}
              className={source.number === selected ? "source source-selected" : "source"}
              aria-current={source.number === selected ? "true" : undefined}
            >
              <button
                type="button"
                className={isCited ? "source-card is-cited" : "source-card"}
                onClick={() => onSelect(source.number)}
                aria-pressed={source.number === selected}
              >
                <span className="source-top">
                  <span className="source-num">{source.number}</span>
                  <span className="source-where">{where(source)}</span>
                </span>
                <span className="source-name">{file}</span>
                <span className="sr-only">{isCited ? ", cited" : ", retrieved, not cited"}</span>
              </button>
            </li>
          );
        })}
      </ol>

      {current ? (
        <div className="source-preview" id={previewElementId(turnId)} tabIndex={-1} aria-label={`Source ${current.number}`}>
          <div className="preview-head">
            <FileIcon />
            <span className="source-path">
              <span className="source-dir">{splitPath(current.source)[0]}</span>
              <span className="source-file">{splitPath(current.source)[1]}</span>
            </span>
          </div>
          <ul className="tag-list" aria-label="Details">
            {tags(current).map((tag) => (
              <li key={tag} className="tag">
                {tag}
              </li>
            ))}
            <li className={cited.has(current.number) ? "tag tag-ok" : "tag"}>
              {cited.has(current.number) ? "cited" : "retrieved, not cited"}
            </li>
          </ul>
          <blockquote className="excerpt">{current.excerpt}</blockquote>
        </div>
      ) : (
        <p className="sources-hint">Select a source or a citation to read the passage.</p>
      )}
    </section>
  );
}
