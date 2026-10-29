import { useEffect, useRef, useState } from "react";

import { ApiError, getIngestJob, startIngest } from "../../api/client";
import type { IngestJob } from "../../api/types";
import { ElapsedTimer } from "../../components/ElapsedTimer";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
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
    <div className="ingest">
      <p className="hint">
        Reads the server's documents folder (<code>DOCUMENTS_DIR</code>): new and changed files are indexed, files
        deleted from the folder are removed, and unchanged files are skipped, so repeat runs are quick. Only one
        indexing job runs at a time.
      </p>
      <button type="button" className="primary" onClick={start} disabled={busy}>
        Index documents
      </button>

      {view.kind === "starting" && (
        <p className="ingest-result" role="status">
          Starting… (checking Ollama and the database first)
        </p>
      )}

      {view.kind === "running" && (
        <div className="ingest-result" role="status">
          <p>
            <strong>Indexing…</strong>{" "}
            {view.job ? <ElapsedTimer startedAt={Date.parse(view.job.started_at)} /> : "checking its status"}
          </p>
          {view.followed && <p className="hint">An indexing job was already running, so this page is following it.</p>}
          <p className="hint">
            Job <code>{view.jobId.slice(0, 8)}</code> · status checked every {pollIntervalMs / 1000} s.
          </p>
          {view.pollError && (
            <p className="form-error">
              Couldn't check the job status ({view.pollError.title}: {view.pollError.detail}). Still trying…
            </p>
          )}
        </div>
      )}

      {view.kind === "succeeded" && <IngestReportView job={view.job} />}

      {view.kind === "failed" && (
        <div className="ingest-result">
          <ErrorMessage error={{ title: "Indexing failed", detail: view.job.error ?? "The server gave no reason." }} />
          <p className="hint">Files indexed before the failure stay indexed; run indexing again to finish the rest.</p>
        </div>
      )}

      {view.kind === "lost" && (
        <div className="ingest-result">
          <ErrorMessage
            error={{
              title: "The server no longer knows this job",
              detail:
                "It was probably restarted: job history is kept only in the server's memory. Files indexed before the restart stay indexed; run indexing again to finish the rest.",
            }}
          />
        </div>
      )}

      {view.kind === "start-error" && (
        <div className="ingest-result">
          <ErrorMessage error={view.error} />
        </div>
      )}
    </div>
  );
}
