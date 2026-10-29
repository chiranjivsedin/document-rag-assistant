import { FolderIcon, InfoIcon, StackIcon } from "../../components/Icons";

const COMING = [
  "Upload PDF, Markdown and text files from the browser",
  "Organise them by product, version and document type",
  "See which documents are indexed and which changed",
  "Replace, download or remove a document",
];

/**
 * The mini DMS will live here. Listing, uploading and managing documents needs new API endpoints (phase 6D), so
 * for now this screen explains how documents get into the assistant today.
 */
export function DocumentsPage() {
  return (
    <div className="page">
      <section className="hero-card">
        <div className="hero-art" aria-hidden="true">
          <span className="sheet sheet-back" />
          <span className="sheet sheet-mid" />
          <span className="sheet sheet-front">
            <FolderIcon />
          </span>
        </div>
        <div>
          <span className="badge">Coming soon</span>
          <h2>Document library</h2>
          <p>
            One place to upload and organise the documents the assistant answers from. It needs new server features
            first, so it isn't available yet.
          </p>
        </div>
      </section>

      <div className="grid-2">
        <section className="panel-card" aria-labelledby="documents-today">
          <h3 id="documents-today">Add documents today</h3>
          <ol className="steps">
            <li>
              <span>
                Put each file on the server in <code>documents/&lt;product&gt;/&lt;version&gt;/&lt;type&gt;/</code>, for
                example <code>documents/documentum/16.2/release-notes/notes.pdf</code>.
              </span>
            </li>
            <li>
              <span>
                Open <a href="#ingestion">Ingestion</a> and choose Index documents.
              </span>
            </li>
            <li>
              <span>
                Ask in <a href="#chat">Chat</a>, optionally limited to that product or version.
              </span>
            </li>
          </ol>
        </section>

        <section className="panel-card" aria-labelledby="documents-coming">
          <h3 id="documents-coming">
            <StackIcon />
            What the library will do
          </h3>
          <ul className="check-list">
            {COMING.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      </div>

      <p className="banner banner-muted">
        <InfoIcon />
        <span className="banner-body">
          PDF files need selectable text; scanned PDFs and Word files aren't supported yet.{" "}
          <span className="placeholder">Not built yet (planned for phase 6E).</span>
        </span>
      </p>
    </div>
  );
}
