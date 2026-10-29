import type { ChatSource } from "../../api/types";

export function sourceElementId(turnId: number, number: number): string {
  return `turn-${turnId}-source-${number}`;
}

function describe(source: ChatSource): string {
  const parts = [
    source.product,
    source.version && `version ${source.version}`,
    source.doc_type,
    source.page !== null && `page ${source.page}`,
  ];
  return parts.filter(Boolean).join(" · ");
}

interface Props {
  turnId: number;
  sources: ChatSource[];
  cited: Set<number>;
  highlighted: number | null;
}

/** The passages the answer was built from, numbered like the [n] citations. */
export function SourceList({ turnId, sources, cited, highlighted }: Props) {
  return (
    <section className="sources" aria-label="Sources">
      <h4>Sources</h4>
      <ol>
        {sources.map((source) => (
          <li
            key={source.number}
            id={sourceElementId(turnId, source.number)}
            tabIndex={-1}
            className={source.number === highlighted ? "source source-highlighted" : "source"}
            aria-current={source.number === highlighted ? "true" : undefined}
          >
            <div className="source-head">
              <span className="source-number">[{source.number}]</span>
              <code>{source.source ?? "unknown source"}</code>
              {cited.has(source.number) ? (
                <span className="badge">cited</span>
              ) : (
                <span className="badge badge-muted">retrieved, not cited</span>
              )}
            </div>
            <div className="source-meta">
              {describe(source)}
              {describe(source) && " · "}distance {source.distance.toFixed(3)}
            </div>
            <blockquote className="excerpt">{source.excerpt}</blockquote>
          </li>
        ))}
      </ol>
    </section>
  );
}
