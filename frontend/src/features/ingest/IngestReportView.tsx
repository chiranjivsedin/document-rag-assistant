import type { IngestJob } from "../../api/types";

function seconds(job: IngestJob): string | null {
  if (!job.finished_at) return null;
  return ((Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000).toFixed(1);
}

function FileList({ title, files }: { title: string; files: string[] }) {
  if (files.length === 0) return null;
  return (
    <div className="report-files">
      <h4>
        {title} ({files.length})
      </h4>
      <ul>
        {files.map((file) => (
          <li key={file}>
            <code>{file}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The result of a finished ingestion job: counts first, then the files in each group (same groups as ingest.py). */
export function IngestReportView({ job }: { job: IngestJob }) {
  const report = job.report!;
  const changed = report.added.length + report.updated.length + report.removed.length + report.no_text.length;
  const took = seconds(job);
  const stats: Array<[string, number]> = [
    ["Added", report.added.length],
    ["Updated", report.updated.length],
    ["Unchanged", report.unchanged.length],
    ["Removed", report.removed.length],
    ["No text", report.no_text.length],
    ["Chunks stored", report.chunks_stored],
  ];

  return (
    <div className="panel-card result-card" role="status">
      <div className="result-head">
        <strong>Indexing finished{took && ` in ${took} s`}.</strong>
        {changed === 0 && <span className="muted">Everything was already up to date.</span>}
      </div>
      <dl className="stats" aria-label="Indexing results">
        {stats.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {changed > 0 && (
        <div className="report-groups">
          <FileList title="Added" files={report.added} />
          <FileList title="Updated" files={report.updated} />
          <FileList title="Removed" files={report.removed} />
          <FileList title="No text found (skipped)" files={report.no_text} />
        </div>
      )}
      {report.unchanged.length > 0 && (
        <details>
          <summary>
            {report.unchanged.length} unchanged {report.unchanged.length === 1 ? "file" : "files"}
          </summary>
          <ul>
            {report.unchanged.map((file) => (
              <li key={file}>
                <code>{file}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
