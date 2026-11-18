"""Master data for tagging documents: tag categories, each with a managed list of allowed values.

Nobody types a tag value freely: every value a document carries comes from these lists, so the same thing is always
spelled the same way. There are two kinds of category, one experience for users:

- The three built-in categories (`BUILTIN_KEYS`: product, version, doc_type) are the document's folders,
  DOCUMENTS_DIR/<product>/<version>/<doc_type>/<file>. Their values stay in the documents row's columns and in every
  chunk's columns, as before; the lists here only decide which values an upload may use. Their codes are folder
  names (`check_folder_name()`).
- Extra categories (e.g. Module, Audience), added by an admin, live only here: a document's extra tags are rows of
  the document_tags table, and chat narrows its search to the documents having the chosen values
  (`sources_with_tags()`).

A value has a stable `code` (what documents store; for a built-in category, the folder name) and a `label` (shown,
renamed freely). Codes are never renamed. A value in use is retired (`active = false`: hidden from new use, still
shown and still filtering), never deleted.

Like the documents table, the tables are created by the application (`create_tables()`, idempotent); there are no
migrations. Each call uses its own short connection, like rag.database.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from rag import database
from rag.dms.repository import DocumentRepository
from rag.dms.storage import InvalidDocument, check_folder_name

BUILTIN_KEYS = ("product", "version", "doc_type")
BUILTIN_LABELS = {"product": "Product", "version": "Version", "doc_type": "Document type"}

# The lists a new database starts with (seed()), codes exactly as the documents' folders are named.
SEED_VALUES: dict[str, list[tuple[str, str]]] = {
    "product": [("content-management", "Content Management")],
    "version": [(v, v) for v in (
        "16.2 16.4 16.6 16.7 20.2 20.3 20.4 21.1 21.2 21.3 21.4 22.1 22.2 22.3 22.4 "
        "23.1 23.2 23.3 23.4 24.1 24.2 24.3 24.4 25.1 25.2 25.3 25.4 26.1 26.2 26.4"
    ).split()],
    "doc_type": [
        ("release-notes", "Release notes"),
        ("installation-guide", "Installation guide"),
        ("upgrade-guide", "Upgrade guide"),
        ("admin-guide", "Administration guide"),
    ],
}

MAX_KEY_CHARS = 32
MAX_LABEL_CHARS = 64
CATEGORY_KEY = re.compile(r"[a-z][a-z0-9-]{0,%d}" % (MAX_KEY_CHARS - 1))
_NUMERIC_VERSION = re.compile(r"\d+(?:\.\d+)*")


@dataclass(frozen=True)
class TagValue:
    id: int
    category_key: str
    code: str  # stable: what documents store (for a built-in category, the folder name)
    label: str  # shown; can be renamed
    active: bool  # False = retired: no new use, still shown and still filtering
    sort_order: int


@dataclass(frozen=True)
class TagCategory:
    key: str
    label: str
    builtin: bool  # product, version, doc_type: the document's folders
    multiple: bool  # whether a document may have several values (never for a built-in category)
    values: list[TagValue]


class TagNotFound(Exception):
    """No such category, value or document. The message is safe to show."""


class TagConflict(Exception):
    """The change would break a rule of the lists (a duplicate, a retired or built-in value used as a tag, two values
    in a single-value category). The message is safe to show."""


class TagInUse(Exception):
    """The value is used by a document, a document's tags or the index, so it can only be retired."""


class InvalidTag(ValueError):
    """A key, code or label that isn't allowed. The message is safe to show."""


def version_sort_key(code: str) -> tuple:
    """Versions in numeric order (16.9 before 16.10, 16.7 before 20.2); codes that aren't dotted numbers come after
    all numeric ones, alphabetically."""
    if _NUMERIC_VERSION.fullmatch(code):
        return (0, tuple(int(part) for part in code.split(".")), "")
    return (1, (), code.lower())


def check_category_key(key: str) -> str:
    """A new category's key: lowercase letters, digits and hyphens, starting with a letter; not a built-in key."""
    key = key.strip()
    if key in BUILTIN_KEYS:
        raise TagConflict(f"'{key}' is a built-in category.")
    if not CATEGORY_KEY.fullmatch(key):
        raise InvalidTag(f"The key must be 1-{MAX_KEY_CHARS} lowercase letters, digits or hyphens, starting with a "
                         "letter (for example module).")
    return key


def check_label(label: str) -> str:
    """A label to show: trimmed, inner runs of spaces made one, 1-64 characters."""
    label = " ".join(label.split())
    if not label or len(label) > MAX_LABEL_CHARS:
        raise InvalidTag(f"The label must be 1-{MAX_LABEL_CHARS} characters.")
    return label


def check_code(code: str) -> str:
    """A value's code: a safe folder name (the built-in codes are folders; extra codes follow the same rule)."""
    try:
        return check_folder_name(code, "The code")
    except InvalidDocument as error:
        raise InvalidTag(str(error)) from None


_CREATE = """
CREATE TABLE IF NOT EXISTS {categories} (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key         text NOT NULL UNIQUE,
    label       text NOT NULL,
    builtin     boolean NOT NULL DEFAULT false,
    multiple    boolean NOT NULL DEFAULT false,
    sort_order  integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT (builtin AND multiple))
);
CREATE UNIQUE INDEX IF NOT EXISTS {categories_label_idx} ON {categories} (lower(label));
CREATE TABLE IF NOT EXISTS {values} (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_id integer NOT NULL REFERENCES {categories} (id),
    code        text NOT NULL,
    label       text NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    sort_order  integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS {values_code_idx} ON {values} (category_id, lower(code));
CREATE UNIQUE INDEX IF NOT EXISTS {values_label_idx} ON {values} (category_id, lower(label)) WHERE active;
CREATE TABLE IF NOT EXISTS {document_tags} (
    document_id uuid NOT NULL REFERENCES {documents} (id) ON DELETE CASCADE,
    value_id    integer NOT NULL REFERENCES {values} (id),
    PRIMARY KEY (document_id, value_id)
);
CREATE INDEX IF NOT EXISTS {document_tags_value_idx} ON {document_tags} (value_id);
"""

_VALUE_COLUMNS = "v.id, c.key AS category_key, v.code, v.label, v.active, v.sort_order"


class TagRepository:
    """Reads and writes the tag tables. `prefix` is put before the three table names (tests use their own tables);
    `chunks_table`, when given, is checked too before a built-in value is deleted (a chunk may still use it)."""

    def __init__(self, database_url: str, documents_table: str = "documents", prefix: str = "",
                 chunks_table: str | None = None):
        for name in (documents_table, f"{prefix}tag_categories", *([chunks_table] if chunks_table else [])):
            if not name.isidentifier():
                raise ValueError(f"table names must be plain names, got {name!r}")
        self._database_url = database_url
        self._documents_table = documents_table
        self._chunks_table = chunks_table
        names = {
            "categories": f"{prefix}tag_categories",
            "values": f"{prefix}tag_values",
            "document_tags": f"{prefix}document_tags",
        }
        self._names = {
            **{name: sql.Identifier(table) for name, table in names.items()},
            "documents": sql.Identifier(documents_table),
            "categories_label_idx": sql.Identifier(f"{names['categories']}_label_idx"),
            "values_code_idx": sql.Identifier(f"{names['values']}_code_idx"),
            "values_label_idx": sql.Identifier(f"{names['values']}_label_idx"),
            "document_tags_value_idx": sql.Identifier(f"{names['document_tags']}_value_idx"),
        }

    # Setup

    def create_tables(self) -> None:
        """Create the tables if they don't exist yet (and the documents table they refer to); safe on every start."""
        DocumentRepository(self._database_url, table=self._documents_table).create_table()
        with self._connect() as conn:
            conn.execute(self._sql(_CREATE))

    def seed(self) -> None:
        """Give an empty database the three built-in categories and their starting values. Does nothing once any
        category exists, so a restart never duplicates values or undoes an admin's changes."""
        with self._connect() as conn:
            conn.execute(self._sql("LOCK TABLE {categories} IN SHARE ROW EXCLUSIVE MODE"))  # one seeder at a time
            if conn.execute(self._sql("SELECT EXISTS (SELECT 1 FROM {categories})")).fetchone()[0]:
                return
            for order, key in enumerate(BUILTIN_KEYS):
                category_id = conn.execute(
                    self._sql("INSERT INTO {categories} (key, label, builtin, multiple, sort_order) "
                              "VALUES (%s, %s, true, false, %s) RETURNING id"),
                    (key, BUILTIN_LABELS[key], order),
                ).fetchone()[0]
                values = SEED_VALUES[key]
                if key == "version":
                    values = sorted(values, key=lambda value: version_sort_key(value[0]))
                for value_order, (code, label) in enumerate(values):
                    conn.execute(
                        self._sql("INSERT INTO {values} (category_id, code, label, sort_order) VALUES (%s, %s, %s, %s)"),
                        (category_id, code, label, value_order),
                    )

    # Reading

    def categories(self) -> list[TagCategory]:
        """Every category, built-ins first, each with all its values (active and retired) in sort order."""
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            categories = cur.execute(self._sql(
                "SELECT id, key, label, builtin, multiple FROM {categories} ORDER BY builtin DESC, sort_order, id"
            )).fetchall()
            values = cur.execute(self._sql(
                f"SELECT {_VALUE_COLUMNS} FROM {{values}} v JOIN {{categories}} c ON c.id = v.category_id "
                "ORDER BY v.sort_order, lower(v.label), v.id"
            )).fetchall()
        by_category: dict[str, list[TagValue]] = {}
        for row in values:
            by_category.setdefault(row["category_key"], []).append(TagValue(**row))
        return [TagCategory(key=c["key"], label=c["label"], builtin=c["builtin"], multiple=c["multiple"],
                            values=by_category.get(c["key"], [])) for c in categories]

    def is_active_code(self, category_key: str, code: str) -> bool:
        with self._connect() as conn:
            return conn.execute(self._sql(
                "SELECT EXISTS (SELECT 1 FROM {values} v JOIN {categories} c ON c.id = v.category_id "
                "WHERE c.key = %s AND v.code = %s AND v.active)"
            ), (category_key, code)).fetchone()[0]

    def document_tags(self, document_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[TagValue]]:
        """Each document's extra tags (documents without any are left out), in category then value order."""
        if not document_ids:
            return {}
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(self._sql(
                f"SELECT dt.document_id, {_VALUE_COLUMNS} FROM {{document_tags}} dt "
                "JOIN {values} v ON v.id = dt.value_id JOIN {categories} c ON c.id = v.category_id "
                "WHERE dt.document_id = ANY(%s) ORDER BY c.sort_order, c.id, v.sort_order, lower(v.label), v.id"
            ), (list(document_ids),)).fetchall()
        tags: dict[uuid.UUID, list[TagValue]] = {}
        for row in rows:
            document_id = row.pop("document_id")
            tags.setdefault(document_id, []).append(TagValue(**row))
        return tags

    def sources_with_tags(self, selected: Mapping[str, Sequence[str]]) -> set[str]:
        """The documents (their `source`) that, for every extra category in `selected`, have at least one of its
        codes; retired values count. Categories with no codes are ignored. Raises TagNotFound for a key that isn't
        an extra category."""
        selected = {key: list(codes) for key, codes in selected.items() if codes}
        extra = self._extra_category_keys()
        for key in selected:
            if key not in extra:
                raise TagNotFound(f"Unknown tag category '{key}'." if key not in BUILTIN_KEYS else
                                  f"'{key}' is a built-in category, not an extra tag.")
        conditions: list[sql.Composable] = [sql.SQL("TRUE")]
        params: list[object] = []
        for key, codes in selected.items():
            conditions.append(self._sql(
                "EXISTS (SELECT 1 FROM {document_tags} dt JOIN {values} v ON v.id = dt.value_id "
                "JOIN {categories} c ON c.id = v.category_id "
                "WHERE dt.document_id = d.id AND c.key = %s AND v.code = ANY(%s))"
            ))
            params += [key, codes]
        query = sql.SQL("SELECT d.source FROM {documents} d WHERE {conditions}").format(
            documents=self._names["documents"], conditions=sql.SQL(" AND ").join(conditions))
        with self._connect() as conn:
            return {row[0] for row in conn.execute(query, params)}

    # Changing the lists (admin)

    def add_category(self, key: str, label: str, multiple: bool) -> TagCategory:
        key, label = check_category_key(key), check_label(label)
        with self._connect() as conn:
            taken = conn.execute(self._sql(
                "SELECT key FROM {categories} WHERE key = %s OR lower(label) = lower(%s)"), (key, label)).fetchone()
            if taken:
                raise TagConflict(f"A category with the key '{key}' already exists." if taken[0] == key else
                                  f"A category labelled '{label}' already exists.")
            with _conflicts_as(TagConflict(f"A category with the key '{key}' or label '{label}' already exists.")):
                conn.execute(self._sql(
                    "INSERT INTO {categories} (key, label, builtin, multiple, sort_order) "
                    "SELECT %s, %s, false, %s, coalesce(max(sort_order), 0) + 1 FROM {categories}"
                ), (key, label, multiple))
        return TagCategory(key=key, label=label, builtin=False, multiple=multiple, values=[])

    def add_value(self, category_key: str, code: str, label: str) -> TagValue:
        """Add an active value. Versions are kept in numeric order; other values go at the end of their list."""
        code, label = check_code(code), check_label(label)
        with self._connect() as conn:
            category = conn.execute(self._sql("SELECT id, label FROM {categories} WHERE key = %s FOR UPDATE"),
                                    (category_key,)).fetchone()
            if category is None:
                raise TagNotFound(f"Unknown tag category '{category_key}'.")
            category_id, category_label = category
            if conn.execute(self._sql("SELECT 1 FROM {values} WHERE category_id = %s AND lower(code) = lower(%s)"),
                            (category_id, code)).fetchone():
                raise TagConflict(f"{category_label} already has a value with the code '{code}'.")
            self._check_label_free(conn, category_id, category_label, label)
            with _conflicts_as(TagConflict(f"{category_label} already has the code '{code}' or label '{label}'.")):
                value_id = conn.execute(self._sql(
                    "INSERT INTO {values} (category_id, code, label, sort_order) "
                    "SELECT %s, %s, %s, coalesce(max(sort_order), -1) + 1 FROM {values} WHERE category_id = %s "
                    "RETURNING id"
                ), (category_id, code, label, category_id)).fetchone()[0]
            if category_key == "version":
                self._renumber_versions(conn, category_id)
        return self._value(value_id)

    def update_value(self, value_id: int, label: str | None = None, active: bool | None = None,
                     sort_order: int | None = None) -> TagValue:
        """Rename a value's label, retire (active=False) or restore it, or move it. Its code never changes."""
        current = self._value(value_id)
        new_label = current.label if label is None else check_label(label)
        new_active = current.active if active is None else active
        with self._connect() as conn:
            category_id, category_label = conn.execute(self._sql(
                "SELECT c.id, c.label FROM {values} v JOIN {categories} c ON c.id = v.category_id WHERE v.id = %s"
            ), (value_id,)).fetchone()
            if new_active:
                self._check_label_free(conn, category_id, category_label, new_label, except_id=value_id)
            with _conflicts_as(TagConflict(f"{category_label} already has an active value labelled '{new_label}'.")):
                conn.execute(self._sql(
                    "UPDATE {values} SET label = %s, active = %s, sort_order = coalesce(%s::integer, sort_order) WHERE id = %s"
                ), (new_label, new_active, sort_order, value_id))
        return self._value(value_id)

    def delete_value(self, value_id: int) -> None:
        """Delete a value nothing uses. Raises TagInUse if a document (its folder value or its tags) or, for a
        built-in value, a chunk in the index still uses it: retire it instead."""
        value = self._value(value_id)
        with self._connect() as conn:
            used = conn.execute(self._sql("SELECT EXISTS (SELECT 1 FROM {document_tags} WHERE value_id = %s)"),
                                (value_id,)).fetchone()[0]
            if not used and value.category_key in BUILTIN_KEYS:
                used = conn.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {} WHERE lower({}) = lower(%s))").format(
                    self._names["documents"], sql.Identifier(value.category_key)), (value.code,)).fetchone()[0]
            if not used and value.category_key in BUILTIN_KEYS and self._chunks_table:
                used = database.column_has_value(self._database_url, self._chunks_table, value.category_key,
                                                 value.code)
            if used:
                raise TagInUse(f"'{value.label}' is in use, so it can only be retired.")
            with _conflicts_as(TagInUse(f"'{value.label}' is in use, so it can only be retired.")):
                conn.execute(self._sql("DELETE FROM {values} WHERE id = %s"), (value_id,))

    # A document's extra tags (not admin work)

    def set_document_tags(self, document_id: uuid.UUID, value_ids: Sequence[int]) -> list[TagValue]:
        """Replace a document's extra tags with these values: active values of extra categories, at most one per
        single-value category. A retired value the document already has may stay; it can't be newly added."""
        wanted = list(dict.fromkeys(value_ids))
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            exists = cur.execute(sql.SQL("SELECT 1 FROM {} WHERE id = %s FOR UPDATE").format(
                self._names["documents"]), (document_id,)).fetchone()
            if exists is None:
                raise TagNotFound("Unknown document.")
            rows = [] if not wanted else cur.execute(self._sql(
                f"SELECT {_VALUE_COLUMNS}, c.builtin, c.multiple, c.label AS category_label, "
                "EXISTS (SELECT 1 FROM {document_tags} dt WHERE dt.document_id = %s AND dt.value_id = v.id) AS kept "
                "FROM {values} v JOIN {categories} c ON c.id = v.category_id WHERE v.id = ANY(%s)"
            ), (document_id, wanted)).fetchall()
            found = {row["id"]: row for row in rows}
            missing = [value_id for value_id in wanted if value_id not in found]
            if missing:
                raise TagNotFound(f"Unknown tag value {missing[0]}.")
            per_category: dict[str, int] = {}
            for row in found.values():
                if row["builtin"]:
                    raise TagConflict(f"{row['category_label']} comes from the document's folder; it isn't set as a "
                                      "tag.")
                if not row["active"] and not row["kept"]:
                    raise TagConflict(f"'{row['label']}' is retired and can't be added.")
                per_category[row["category_key"]] = per_category.get(row["category_key"], 0) + 1
                if not row["multiple"] and per_category[row["category_key"]] > 1:
                    raise TagConflict(f"{row['category_label']} takes one value only.")
            cur.execute(self._sql("DELETE FROM {document_tags} WHERE document_id = %s"), (document_id,))
            for value_id in wanted:
                cur.execute(self._sql("INSERT INTO {document_tags} (document_id, value_id) VALUES (%s, %s)"),
                            (document_id, value_id))
        return self.document_tags([document_id]).get(document_id, [])

    # Helpers

    def _extra_category_keys(self) -> set[str]:
        with self._connect() as conn:
            return {row[0] for row in conn.execute(self._sql("SELECT key FROM {categories} WHERE NOT builtin"))}

    def _value(self, value_id: int) -> TagValue:
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(self._sql(
                f"SELECT {_VALUE_COLUMNS} FROM {{values}} v JOIN {{categories}} c ON c.id = v.category_id "
                "WHERE v.id = %s"), (value_id,)).fetchone()
        if row is None:
            raise TagNotFound(f"Unknown tag value {value_id}.")
        return TagValue(**row)

    def _check_label_free(self, conn: psycopg.Connection, category_id: int, category_label: str, label: str,
                          except_id: int | None = None) -> None:
        if conn.execute(self._sql(
            "SELECT 1 FROM {values} WHERE category_id = %s AND active AND lower(label) = lower(%s) "
            "AND id IS DISTINCT FROM %s::integer"
        ), (category_id, label, except_id)).fetchone():
            raise TagConflict(f"{category_label} already has an active value labelled '{label}'.")

    def _renumber_versions(self, conn: psycopg.Connection, category_id: int) -> None:
        rows = conn.execute(self._sql("SELECT id, code FROM {values} WHERE category_id = %s"),
                            (category_id,)).fetchall()
        for order, (value_id, _) in enumerate(sorted(rows, key=lambda row: version_sort_key(row[1]))):
            conn.execute(self._sql("UPDATE {values} SET sort_order = %s WHERE id = %s"), (order, value_id))

    def _sql(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(**{name: self._names[name] for name in _placeholders(template)})

    def _connect(self) -> psycopg.Connection:
        return database.connect(self._database_url)


def _placeholders(template: str) -> list[str]:
    return re.findall(r"(?<!\{)\{(\w+)\}(?!\})", template)


class _conflicts_as:
    """Turn a unique or foreign-key violation (a concurrent change got there first) into `error`."""

    def __init__(self, error: Exception):
        self.error = error

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind, value, traceback) -> bool:
        if isinstance(value, (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation)):
            raise self.error from value
        return False


def builtin_codes(categories: Sequence[TagCategory]) -> dict[str, set[str]]:
    """Every code (active or retired) of each built-in category."""
    return {c.key: {v.code for v in c.values} for c in categories if c.builtin}

