"""The API serving the built web app (FRONTEND_DIST): files, the index.html fallback, and the API untouched."""

import logging
import mimetypes
import os

import pytest
from fastapi.testclient import TestClient

from rag.api import app as api
from rag.config import get_settings

INDEX = "<!doctype html><title>RAG Assistant</title><div id=root></div>"
SCRIPT = "console.log('app');"
SECRET = "must never be served"


@pytest.fixture(autouse=True)
def no_backends(monkeypatch):
    monkeypatch.setattr(api, "check_services", lambda settings, need_chat_model: [])

    def unavailable(settings):
        raise RuntimeError("no document library in these tests")

    monkeypatch.setattr(api, "build_library", unavailable)


@pytest.fixture
def dist(tmp_path):
    """A build folder like Vite's, plus a file next to it that must stay out of reach."""
    build = tmp_path / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text(INDEX, encoding="utf-8")
    (build / "assets" / "index-abc123.js").write_text(SCRIPT, encoding="utf-8")
    (build / "assets" / "index-abc123.css").write_text("body{}", encoding="utf-8")
    (build / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return build


def client_for(frontend_dist=None) -> TestClient:
    env = {"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag"}
    if frontend_dist is not None:
        env["FRONTEND_DIST"] = str(frontend_dist)
    return TestClient(api.create_app(get_settings(env)), raise_server_exceptions=False)


# --- Off by default: the API alone, exactly as before ---------------------------------------------------------

def test_without_frontend_dist_the_root_is_a_plain_404():
    client = client_for()
    response = client.get("/")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert client.get("/health").json()["status"] == "ok"


# --- The web app ------------------------------------------------------------------------------------------------

def test_root_serves_index_html_and_revalidates_it(dist):
    response = client_for(dist).get("/")
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"  # a new build is picked up at once
    assert response.headers["x-content-type-options"] == "nosniff"


def test_head_on_the_root_works(dist):
    response = client_for(dist).head("/")
    assert response.status_code == 200
    assert response.text == ""


@pytest.mark.parametrize("path", ["/chat-history", "/library/documents", "/some/deep/client/route", "/index"])
def test_unknown_page_paths_get_the_app(dist, path):
    """The app opens whatever path it is loaded from (its tabs live in the URL hash, which never reaches us)."""
    response = client_for(dist).get(path)
    assert response.status_code == 200
    assert response.text == INDEX


def test_hashed_assets_are_cached_for_a_year(dist):
    client = client_for(dist)
    script = client.get("/assets/index-abc123.js")
    assert script.status_code == 200
    assert script.text == SCRIPT
    assert "javascript" in script.headers["content-type"]
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/assets/index-abc123.css").headers["content-type"].startswith("text/css")


def test_a_repeated_request_can_be_answered_not_modified(dist):
    client = client_for(dist)
    first = client.get("/assets/index-abc123.js")
    again = client.get("/assets/index-abc123.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_top_level_build_files_are_served(dist):
    response = client_for(dist).get("/favicon.svg")
    assert response.status_code == 200
    assert response.text == "<svg/>"
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/assets/missing-123.js", "/assets/", "/logo.png", "/old/main.js"])
def test_missing_files_are_404_not_the_page(dist, path):
    """A browser asking for a script must never get HTML back."""
    response = client_for(dist).get(path)
    assert response.status_code == 404
    assert INDEX not in response.text


@pytest.mark.parametrize("path", [
    "/../secret.txt",
    "/%2e%2e/secret.txt",
    "/assets/%2e%2e/%2e%2e/secret.txt",
    "/..%2fsecret.txt",
    "/..%5csecret.txt",
])
def test_files_outside_the_build_folder_are_never_served(dist, path):
    response = client_for(dist).get(path)
    assert SECRET not in response.text
    assert response.status_code == 404


def test_an_absolute_windows_path_is_never_served(dist, tmp_path):
    drive_path = str(tmp_path / "secret.txt").replace("\\", "/")  # e.g. C:/Users/.../secret.txt on Windows
    response = client_for(dist).get("/" + drive_path)
    assert SECRET not in response.text


@pytest.mark.parametrize("char", ["%3C", "%3E", "%7C", "%22", "%2A", "%3F"])  # < > | " * ?
def test_names_windows_cannot_hold_are_not_server_errors(dist, char, caplog):
    """On Windows, looking up such a name fails with an OSError: it must be a plain miss, not a logged 500."""
    client = client_for(dist)
    page = client.get(f"/a{char}b")
    assert page.status_code == 200  # a page path like any other
    assert page.text == INDEX
    for path in (f"/a{char}b.js", f"/assets/a{char}b.js", f"/assets/{char}"):
        assert client.get(path).status_code == 404, path
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize(("path", "content_type"), [
    ("/", "text/html; charset=utf-8"),
    ("/assets/index-abc123.js", "text/javascript; charset=utf-8"),
    ("/assets/index-abc123.css", "text/css; charset=utf-8"),
    ("/favicon.svg", "image/svg+xml"),
    ("/assets/font-abc123.woff2", "font/woff2"),
])
def test_content_types_do_not_depend_on_the_windows_registry(dist, monkeypatch, path, content_type):
    """Python reads file types from the Windows registry, which other software can change. A script sent as
    text/plain is never run (the page's script is a module, and nosniff forbids guessing): a blank page."""
    (dist / "assets" / "font-abc123.woff2").write_bytes(b"wOF2")
    mimetypes.guess_type("x.js")  # load the table, then spoil it the way a registry entry would
    for suffix in (".js", ".css", ".html", ".svg", ".woff2"):
        monkeypatch.setitem(mimetypes._db.types_map[True], suffix, "text/plain")
    assert mimetypes.guess_type("x.js")[0] == "text/plain"

    response = client_for(dist).get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type


# --- The API is unchanged when the web app is on ----------------------------------------------------------------

def test_api_routes_answer_as_before(dist):
    client = client_for(dist)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["content-type"] == "application/json"
    assert health.json()["status"] == "ok"
    assert client.get("/openapi.json").json()["info"]["title"] == "RAG prototype API"
    assert client.get("/docs").status_code == 200


def test_a_wrong_method_on_an_api_path_stays_405(dist):
    response = client_for(dist).get("/chat")  # /chat is POST only
    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}


@pytest.mark.parametrize("path", ["/documents/ingest/no-such-job", "/health/extra", "/chat/x", "/docs/nothing"])
def test_unknown_paths_under_api_prefixes_stay_json_404s(dist, path):
    response = client_for(dist).get(path)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert INDEX not in response.text


@pytest.mark.parametrize("path", ["/documents/a/b/c", "/documents/some-id/file/extra"])
def test_unmatched_document_paths_get_the_api_404_not_the_page(dist, path):
    """No /documents route matches these, so they reach the web app's fallback, which must refuse them."""
    response = client_for(dist).get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("path", ["/HEALTH/extra", "/Health", "/Docs/x", "/Documents/a/b/c", "/OpenAPI.json/x"])
def test_api_prefixes_are_reserved_in_any_letter_case(dist, path):
    response = client_for(dist).get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_unknown_job_keeps_its_own_404_message(dist):
    response = client_for(dist).get("/documents/ingest/no-such-job")
    assert "Unknown ingestion job" in response.json()["detail"]


def test_other_methods_on_page_paths_are_not_the_app(dist):
    client = client_for(dist)
    for method in ("POST", "PUT", "DELETE"):
        response = client.request(method, "/some/page")
        assert response.status_code == 404
        assert INDEX not in response.text


def test_without_an_assets_folder_the_app_still_serves(dist):
    for item in (dist / "assets").iterdir():
        item.unlink()
    (dist / "assets").rmdir()
    client = client_for(dist)
    assert client.get("/").text == INDEX
    assert client.get("/assets/index-abc123.js").status_code == 404


# --- Misconfiguration fails at startup, clearly ----------------------------------------------------------------

def test_a_build_folder_without_index_html_stops_startup(tmp_path):
    with pytest.raises(RuntimeError, match="no index.html"):
        api.create_app(get_settings({"FRONTEND_DIST": str(tmp_path)}))


def test_a_missing_build_folder_stops_startup(tmp_path):
    with pytest.raises(RuntimeError, match="npm run build"):
        api.create_app(get_settings({"FRONTEND_DIST": str(tmp_path / "nowhere")}))


def test_the_startup_error_shows_where_a_relative_path_points(tmp_path, monkeypatch):
    """A relative FRONTEND_DIST is taken from the folder the API starts in: the error says which folder that was."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as error:
        api.create_app(get_settings({"FRONTEND_DIST": "web/dist"}))
    assert os.path.join(os.getcwd(), "web", "dist") in str(error.value)
