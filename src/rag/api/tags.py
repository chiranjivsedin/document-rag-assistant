"""The master lists for tags: read by everyone (the app's dropdowns), changed only with the admin key.

The admin endpoints need the X-Admin-Key header to equal the ADMIN_KEY setting (compared in constant time). Without
ADMIN_KEY they answer 403; with a missing or wrong key, 401. The key is never taken from the URL, and never echoed
or logged. Tagging a single document is not admin work: see PUT /documents/{id}/tags.
"""

import hmac
import logging
from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from rag.api.errors import backend_failure
from rag.api.schemas import (
    ErrorResponse,
    NewTagCategory,
    NewTagValue,
    TagCategoryBody,
    TagListResponse,
    TagValueBody,
    TagValueChange,
)
from rag.config import Settings
from rag.dms.tags import InvalidTag, TagCategory, TagConflict, TagInUse, TagNotFound, TagRepository, TagValue

logger = logging.getLogger(__name__)

T = TypeVar("T")
ADMIN_DISABLED = "Admin is disabled on this server."
WRONG_KEY = "Missing or wrong admin key."


def require_admin(settings: Settings, x_admin_key: str | None) -> None:
    """Raise 403 when admin is disabled (no ADMIN_KEY), 401 when the key is missing or wrong. The header is
    compared exactly as received (no trimming), in constant time."""
    if not settings.admin_key:
        raise HTTPException(status_code=403, detail=ADMIN_DISABLED)
    given = (x_admin_key or "").encode("utf-8")
    if not given or not hmac.compare_digest(given, settings.admin_key.encode("utf-8")):
        raise HTTPException(status_code=401, detail=WRONG_KEY)


def tag_routes(settings: Settings, get_tags: Callable[[], TagRepository]) -> APIRouter:
    router = APIRouter(prefix="/tags", tags=["tags"])

    def admin(x_admin_key: Annotated[str | None, Header(include_in_schema=True)] = None) -> None:
        require_admin(settings, x_admin_key)

    admin_errors = {401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}

    @router.get("", response_model=TagListResponse, responses={503: {"model": ErrorResponse}})
    def list_tags() -> TagListResponse:
        """Every category with all its values (active and retired), built-ins first, in sort order."""
        return TagListResponse(categories=[category_body(c) for c in run(lambda: get_tags().categories())])

    @router.post("/categories", status_code=201, response_model=TagCategoryBody, dependencies=[Depends(admin)],
                 responses={**admin_errors, 409: {"model": ErrorResponse}})
    def add_category(body: NewTagCategory) -> TagCategoryBody:
        """Add an extra category (e.g. Module); its values are added next."""
        return category_body(run(lambda: get_tags().add_category(body.key, body.label, body.multiple)))

    @router.post("/categories/{key}/values", status_code=201, response_model=TagValueBody,
                 dependencies=[Depends(admin)],
                 responses={**admin_errors, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
    def add_value(key: str, body: NewTagValue) -> TagValueBody:
        """Add an active value to a category. Codes are folder-name safe and never change."""
        return value_body(run(lambda: get_tags().add_value(key, body.code, body.label)))

    @router.patch("/values/{value_id}", response_model=TagValueBody, dependencies=[Depends(admin)],
                  responses={**admin_errors, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
    def change_value(value_id: int, body: TagValueChange) -> TagValueBody:
        """Rename a value's label, retire it (active=false) or restore it, or move it (sort_order)."""
        return value_body(run(lambda: get_tags().update_value(value_id, label=body.label, active=body.active,
                                                              sort_order=body.sort_order)))

    @router.delete("/values/{value_id}", status_code=204, dependencies=[Depends(admin)],
                   responses={**admin_errors, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
    def delete_value(value_id: int) -> Response:
        """Delete a value nothing uses; one in use (by a document or the index) can only be retired (409)."""
        run(lambda: get_tags().delete_value(value_id))
        return Response(status_code=204)

    def run(action: Callable[[], T]) -> T:
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="DATABASE_URL is not set, so the tag lists can't be stored.")
        try:
            return action()
        except Exception as error:
            raise tag_http_error(error) from None

    return router


def tag_http_error(error: Exception) -> Exception:
    """The HTTP error for a failed tag operation: 404 / 409 / 422 with the repository's client-safe message, 503 or
    502 for a backend failure; anything else is returned unchanged (a generic 500)."""
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, TagNotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, (TagConflict, TagInUse)):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, InvalidTag):
        return HTTPException(status_code=422, detail=str(error))
    failure = backend_failure(error)
    if failure is not None:
        logger.warning("Backend failure in the tag lists", exc_info=error)
        return HTTPException(status_code=failure[0], detail=failure[1])
    return error


def value_body(value: TagValue) -> TagValueBody:
    return TagValueBody(id=value.id, code=value.code, label=value.label, active=value.active)


def category_body(category: TagCategory) -> TagCategoryBody:
    return TagCategoryBody(key=category.key, label=category.label, builtin=category.builtin,
                           multiple=category.multiple, values=[value_body(v) for v in category.values])
