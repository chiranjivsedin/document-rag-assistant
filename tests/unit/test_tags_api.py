import psycopg
import pytest
from fastapi.testclient import TestClient

from rag.api import app as api
from rag.config import get_settings

KEY = "s3cret-admin-key-for-tests"
SETTINGS = get_settings({"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag", "ADMIN_KEY": KEY})
NO_ADMIN = get_settings({"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag"})
ADMIN = {"X-Admin-Key": KEY}

ADMIN_CALLS = [
    ("post", "/tags/categories", {"key": "module", "label": "Module", "multiple": True}),
    ("post", "/tags/categories/doc_type/values", {"code": "faq", "label": "FAQ"}),
    ("patch", "/tags/values/1", {"label": "Content Mgmt"}),
    ("delete", "/tags/values/1", None),
]


def make_client(fake_tags, settings=SETTINGS):
    return TestClient(api.create_app(settings, tags=fake_tags), raise_server_exceptions=False)


def call(client, method, path, body, headers=None, params=None):
    kwargs = {"headers": headers or {}, "params": params}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


def test_get_tags_is_open(fake_tags):
    fake_tags.update_value(fake_tags.value("version", "16.2").id, active=False)
    response = make_client(fake_tags, NO_ADMIN).get("/tags")
    assert response.status_code == 200
    categories = response.json()["categories"]
    assert [(c["key"], c["label"], c["builtin"], c["multiple"]) for c in categories] == [
        ("product", "Product", True, False), ("version", "Version", True, False),
        ("doc_type", "Document type", True, False)]
    assert categories[0]["values"] == [
        {"id": fake_tags.value("product", "content-management").id, "code": "content-management",
         "label": "Content Management", "active": True}]
    assert len(categories[1]["values"]) == 30
    assert categories[1]["values"][0]["code"] == "16.2" and categories[1]["values"][0]["active"] is False


@pytest.mark.parametrize("method, path, body", ADMIN_CALLS)
def test_admin_disabled_without_admin_key(fake_tags, method, path, body):
    response = call(make_client(fake_tags, NO_ADMIN), method, path, body, headers=ADMIN)
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin is disabled on this server."}
    assert fake_tags.calls == []


@pytest.mark.parametrize("method, path, body", ADMIN_CALLS)
@pytest.mark.parametrize("headers, params", [
    ({}, None),                                  # no header
    ({"X-Admin-Key": ""}, None),                 # empty
    ({"X-Admin-Key": f" {KEY} "}, None),         # surrounding spaces
    ({"X-Admin-Key": KEY.upper()}, None),        # wrong case
    ({"X-Admin-Key": KEY[:-1]}, None),           # a prefix
    ({}, {"key": KEY}),                          # as a query parameter
    ({}, {"admin_key": KEY, "x-admin-key": KEY}),
    ({"Authorization": f"Bearer {KEY}"}, None),  # another header
])
def test_admin_key_variants_are_refused(fake_tags, method, path, body, headers, params):
    response = call(make_client(fake_tags), method, path, body, headers=headers, params=params)
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or wrong admin key."}
    assert KEY not in response.text and KEY.upper() not in response.text
    assert fake_tags.calls == []


def test_admin_endpoints_with_the_right_key(fake_tags):
    client = make_client(fake_tags)

    response = client.post("/tags/categories", json={"key": "module", "label": "Module", "multiple": True},
                           headers=ADMIN)
    assert response.status_code == 201
    assert response.json() == {"key": "module", "label": "Module", "builtin": False, "multiple": True, "values": []}

    response = client.post("/tags/categories/module/values", json={"code": "workflow", "label": "Workflow"},
                           headers=ADMIN)
    assert response.status_code == 201
    workflow = response.json()
    assert workflow == {"id": workflow["id"], "code": "workflow", "label": "Workflow", "active": True}

    response = client.patch(f"/tags/values/{workflow['id']}", json={"label": "Workflows", "active": False},
                            headers=ADMIN)
    assert response.status_code == 200
    assert response.json() == {**workflow, "label": "Workflows", "active": False}

    assert client.patch(f"/tags/values/{workflow['id']}", json={"sort_order": 5}, headers=ADMIN).status_code == 200
    assert client.delete(f"/tags/values/{workflow['id']}", headers=ADMIN).status_code == 204

    assert fake_tags.calls == [
        ("add_category", "module", "Module", True),
        ("add_value", "module", "workflow", "Workflow"),
        ("update_value", workflow["id"], "Workflows", False, None),
        ("update_value", workflow["id"], None, None, 5),
        ("delete_value", workflow["id"]),
    ]


def test_a_category_is_single_value_unless_asked(fake_tags):
    response = make_client(fake_tags).post("/tags/categories", json={"key": "module", "label": "Module"},
                                           headers=ADMIN)
    assert response.status_code == 201 and response.json()["multiple"] is False


def test_conflict_is_409_and_in_use_is_409(fake_tags, fake_repository):
    client = make_client(fake_tags)
    response = client.post("/tags/categories/doc_type/values", json={"code": "Release-Notes", "label": "x"},
                           headers=ADMIN)
    assert response.status_code == 409 and "already has" in response.json()["detail"]
    response = client.post("/tags/categories", json={"key": "version", "label": "Versions"}, headers=ADMIN)
    assert response.status_code == 409

    fake_tags.used_in_index.add(("version", "24.4"))
    response = client.delete(f"/tags/values/{fake_tags.value('version', '24.4').id}", headers=ADMIN)
    assert response.status_code == 409
    assert response.json() == {"detail": "'24.4' is in use, so it can only be retired."}


def test_not_found_is_404(fake_tags):
    client = make_client(fake_tags)
    assert client.post("/tags/categories/nope/values", json={"code": "a", "label": "A"},
                       headers=ADMIN).status_code == 404
    assert client.patch("/tags/values/99999", json={"label": "x"}, headers=ADMIN).status_code == 404
    assert client.delete("/tags/values/99999", headers=ADMIN).status_code == 404


@pytest.mark.parametrize("path, body", [
    ("/tags/categories", {"key": "Bad Key", "label": "Module"}),
    ("/tags/categories", {"key": "module", "label": "   "}),
    ("/tags/categories", {"key": "module", "label": "Module", "extra": 1}),
    ("/tags/categories/doc_type/values", {"code": "../up", "label": "Up"}),
    ("/tags/categories/doc_type/values", {"code": "faq"}),
])
def test_invalid_input_is_422(fake_tags, path, body):
    response = make_client(fake_tags).post(path, json=body, headers=ADMIN)
    assert response.status_code == 422


def test_patch_rejects_unknown_fields_and_a_code_change(fake_tags):
    response = make_client(fake_tags).patch("/tags/values/1", json={"code": "other"}, headers=ADMIN)
    assert response.status_code == 422


def test_database_outage_is_503(fake_tags):
    fake_tags.fail_on["categories"] = psycopg.OperationalError("connection refused")
    response = make_client(fake_tags).get("/tags")
    assert response.status_code == 503


def test_tags_without_a_database_url_is_503():
    client = TestClient(api.create_app(get_settings({})), raise_server_exceptions=False)
    response = client.get("/tags")
    assert response.status_code == 503 and "DATABASE_URL" in response.json()["detail"]


def test_tags_are_built_on_first_use_and_a_failure_is_retried(fake_tags, monkeypatch):
    calls = []

    def build(settings):
        calls.append(settings)
        if len(calls) == 1:
            raise psycopg.OperationalError("database starting")
        return fake_tags

    monkeypatch.setattr(api, "build_tags", build)
    client = TestClient(api.create_app(SETTINGS), raise_server_exceptions=False)
    assert calls == []
    assert client.get("/tags").status_code == 503
    assert client.get("/tags").status_code == 200
    assert client.get("/tags").status_code == 200
    assert len(calls) == 2


def test_cors_preflight_allows_the_admin_key_header_and_patch(fake_tags):
    response = make_client(fake_tags).options("/tags/values/1", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "x-admin-key, content-type"})
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "x-admin-key" in response.headers["access-control-allow-headers"].lower()


def test_the_admin_key_is_not_in_the_settings_repr():
    assert KEY not in repr(SETTINGS)
