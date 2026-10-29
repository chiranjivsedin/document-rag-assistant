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

  return (
    <div className="ingest-result ingest-done" role="status">
      <p>
        <strong>Indexing finished{took && ` in ${took} s`}.</strong> {report.added.length} added,{" "}
        {report.updated.length} updated, {report.unchanged.length} unchanged, {report.removed.length} removed,{" "}
        {report.no_text.length} with no text · {report.chunks_stored} chunks stored.
      </p>
      {changed === 0 && <p className="hint">Everything was already up to date.</p>}
      <FileList title="Added" files={report.added} />
      <FileList title="Updated" files={report.updated} />
      <FileList title="Removed" files={report.removed} />
      <FileList title="No text found (skipped)" files={report.no_text} />
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
