import { useEffect, useRef, useState } from "react";

import { ApiError, getIngestJob, startIngest } from "../../api/client";
import type { IngestJob } from "../../api/types";
import { ElapsedTimer } from "../../components/ElapsedTimer";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { AlertIcon, IndexIcon } from "../../components/Icons";
import { IngestReportView } from "./IngestReportView";

type View =
  | { kind: "idle" }
  | { kind: "starting" }
  | {
      kind: "running";
      jobId: string;
      job: IngestJob | null; // null until the first poll when following a job started elsewhere
      followed: boolean; // true when the API said a job was already running (409)
      pollError: ErrorText | null; // the last status check failed; still retrying
    }
  | { kind: "succeeded" | "failed"; job: IngestJob }
  | { kind: "lost"; jobId: string }
  | { kind: "start-error"; error: ErrorText };

/** Index the server's documents folder (POST /documents/ingest) and follow the job until it finishes. */
export function IngestPage({ pollIntervalMs = 2000 }: { pollIntervalMs?: number }) {
  const [view, setView] = useState<View>({ kind: "idle" });
  const firstPollDelay = useRef(pollIntervalMs);

  const runningId = view.kind === "running" ? view.jobId : null;

  useEffect(() => {
    if (!runningId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const job = await getIngestJob(runningId, controller.signal);
        if (controller.signal.aborted) return;
        if (job.status === "running") {
          setView((v) => (v.kind === "running" && v.jobId === runningId ? { ...v, job, pollError: null } : v));
          timer = setTimeout(poll, pollIntervalMs);
        } else {
          setView({ kind: job.status, job });
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 404) {
          setView({ kind: "lost", jobId: runningId });
          return;
        }
        // A blip (API restarting, network): keep the job and keep trying; a restart shows up as 404 above.
        const pollError = describeError(error);
        setView((v) => (v.kind === "running" && v.jobId === runningId ? { ...v, pollError } : v));
        timer = setTimeout(poll, pollIntervalMs);
      }
    };

    timer = setTimeout(poll, firstPollDelay.current);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runningId, pollIntervalMs]);

  async function start() {
    setView({ kind: "starting" });
    try {
      const result = await startIngest();
      if (result.kind === "started") {
        firstPollDelay.current = pollIntervalMs; // we already have its first status
        setView({ kind: "running", jobId: result.job.job_id, job: result.job, followed: false, pollError: null });
      } else {
        firstPollDelay.current = 0; // fetch the other job's status straight away
        setView({ kind: "running", jobId: result.jobId, job: null, followed: true, pollError: null });
      }
    } catch (error) {
      setView({ kind: "start-error", error: describeError(error) });
    }
  }

  const busy = view.kind === "starting" || view.kind === "running";

  return (
    <div className="page page-split">
      <div className="page-main">
        <section className="hero-card hero-compact">
          <div>
            <h2>Index the documents folder</h2>
            <p>
              Reads the server's documents folder (<code>DOCUMENTS_DIR</code>) and brings the search index up to date.
              Only one indexing job runs at a time.
            </p>
          </div>
          <button type="button" className="button button-primary" onClick={start} disabled={busy}>
            <IndexIcon />
            Index documents
          </button>
        </section>

        {view.kind === "idle" && (
          <p className="empty-note">No indexing run yet in this session. Results of the next run appear here.</p>
        )}

        {view.kind === "starting" && (
          <div className="panel-card job-card" role="status">
            <div className="progress" aria-hidden="true">
              <span />
            </div>
            <p>Starting… (checking Ollama and the database first)</p>
          </div>
        )}

        {view.kind === "running" && (
          <div className="panel-card job-card" role="status">
            <div className="job-line">
              <span className="dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <strong>Indexing…</strong>
              {view.job ? <ElapsedTimer startedAt={Date.parse(view.job.started_at)} /> : <span>checking its status</span>}
            </div>
            <div className="progress" aria-hidden="true">
              <span />
            </div>
            {view.followed && <p className="hint">An indexing job was already running, so this page is following it.</p>}
            <p className="hint">
              Job <code>{view.jobId.slice(0, 8)}</code>, status checked every {pollIntervalMs / 1000} s.
            </p>
            {view.pollError && (
              <p className="banner banner-warn">
                <AlertIcon />
                <span className="banner-body">
                  Couldn't check the job status ({view.pollError.title}: {view.pollError.detail}). Still trying…
                </span>
              </p>
            )}
          </div>
        )}

        {view.kind === "succeeded" && <IngestReportView job={view.job} />}

        {view.kind === "failed" && (
          <>
            <ErrorMessage error={{ title: "Indexing failed", detail: view.job.error ?? "The server gave no reason." }} />
            <p className="hint">Files indexed before the failure stay indexed; run indexing again to finish the rest.</p>
          </>
        )}

        {view.kind === "lost" && (
          <ErrorMessage
            error={{
              title: "The server no longer knows this job",
              detail:
                "It was probably restarted: job history is kept only in the server's memory. Files indexed before the restart stay indexed; run indexing again to finish the rest.",
            }}
          />
        )}

        {view.kind === "start-error" && <ErrorMessage error={view.error} />}
      </div>

      <aside className="panel-card page-aside" aria-labelledby="how-indexing-works">
        <h3 id="how-indexing-works">How indexing works</h3>
        <ul className="check-list">
          <li>New and changed files are read, split into passages and indexed.</li>
          <li>Unchanged files are skipped, so repeat runs are quick.</li>
          <li>Files deleted from the folder are removed from the index.</li>
          <li>PDF (with selectable text), Markdown and text files are supported.</li>
        </ul>
      </aside>
    </div>
  );
}
