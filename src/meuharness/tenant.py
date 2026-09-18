from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

_CURRENT_ORGANIZATION_ID: ContextVar[str | None] = ContextVar(
    "actis_current_organization_id", default=None
)


def normalize_organization_id(value: Any) -> str | None:
    organization_id = str(value or "").strip()
    return organization_id or None


def current_organization_id() -> str | None:
    return _CURRENT_ORGANIZATION_ID.get()


def set_current_organization_id(organization_id: Any) -> Token[str | None]:
    return _CURRENT_ORGANIZATION_ID.set(normalize_organization_id(organization_id))


def reset_current_organization_id(token: Token[str | None]) -> None:
    _CURRENT_ORGANIZATION_ID.reset(token)


@contextmanager
def organization_context(organization_id: Any) -> Iterator[None]:
    token = set_current_organization_id(organization_id)
    try:
        yield
    finally:
        reset_current_organization_id(token)


def resolve_organization_id(
    explicit: Any = None, data: dict[str, Any] | None = None
) -> str | None:
    if explicit is not None and str(explicit).strip():
        return normalize_organization_id(explicit)
    if isinstance(data, dict) and str(data.get("organization_id") or "").strip():
        return normalize_organization_id(data["organization_id"])
    return current_organization_id()


def resolve_inherited_organization_id(
    explicit: Any = None,
    existing: dict[str, Any] | None = None,
) -> str | None:
    """Resolve a resource tenant while rejecting tenant changes across contexts."""
    current = current_organization_id()
    requested = normalize_organization_id(explicit)
    inherited = normalize_organization_id((existing or {}).get("organization_id"))
    if current and requested and requested != current:
        raise PermissionError("Resource foi informado para outra organization.")
    if requested and inherited and requested != inherited:
        raise PermissionError("Resource não pode mudar de organization.")
    return requested or current or inherited


def resolve_http_organization_id(headers: Any, query: Any = None) -> str | None:
    """Resolve the tenant from the ACTIS header or organization_id query parameter."""
    header_value = None
    if hasattr(headers, "get_all"):
        values = headers.get_all("X-Actis-Organization") or []
        header_value = next((str(value).strip() for value in values if str(value).strip()), None)
    else:
        header_value = normalize_organization_id(headers.get("X-Actis-Organization"))

    query_value = None
    if isinstance(query, dict):
        raw = query.get("organization_id")
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        query_value = normalize_organization_id(raw)
    elif query:
        query_value = normalize_organization_id(query)

    if header_value and query_value and header_value != query_value:
        raise PermissionError("Header e query informam organizations diferentes.")
    return header_value or query_value


def require_organization_id(organization_id: Any, resource: str = "resource") -> str:
    value = normalize_organization_id(organization_id)
    if not value:
        raise ValueError(f"organization_id é obrigatório para {resource}.")
    return value


def entity_organization_id(
    entity: dict[str, Any] | None, *, company_is_tenant: bool = True
) -> str | None:
    """Return the tenant ID carried by a resource.

    Legacy company records use ``company_id`` as their own identity, while most
    other legacy resources use it as an organization alias. Callers managing
    company records pass ``company_is_tenant=False``.
    """
    if not isinstance(entity, dict):
        return None
    organization_id = normalize_organization_id(entity.get("organization_id"))
    if organization_id:
        return organization_id
    if company_is_tenant:
        return normalize_organization_id(entity.get("company_id"))
    return None


def organization_matches(
    entity: dict[str, Any] | None,
    organization_id: Any,
    *,
    company_is_tenant: bool = True,
) -> bool:
    expected = normalize_organization_id(organization_id)
    if not expected:
        return True
    return entity_organization_id(entity, company_is_tenant=company_is_tenant) == expected


def assert_organization_access(
    entity: dict[str, Any] | None,
    organization_id: Any = None,
    *,
    resource: str = "resource",
    company_is_tenant: bool = True,
    allow_unscoped: bool = False,
) -> str:
    expected = require_organization_id(
        organization_id if organization_id is not None else current_organization_id(),
        resource,
    )
    actual = entity_organization_id(entity, company_is_tenant=company_is_tenant)
    if actual == expected or (allow_unscoped and not actual):
        return expected
    raise PermissionError(f"{resource} pertence a outra organization.")


def filter_by_organization(
    items: list[dict[str, Any]],
    organization_id: Any = None,
    *,
    include_unscoped: bool = False,
    include_global: bool = False,
    company_is_tenant: bool = True,
) -> list[dict[str, Any]]:
    expected = normalize_organization_id(
        organization_id if organization_id is not None else current_organization_id()
    )
    if not expected:
        return list(items)
    result = []
    for item in items:
        actual = entity_organization_id(item, company_is_tenant=company_is_tenant)
        is_global = bool(
            item.get("builtin")
            or item.get("id") == "general"
            or (item.get("scope_type") == "global" and not actual)
        )
        if actual == expected or (include_unscoped and not actual) or (include_global and is_global):
            result.append(item)
    return result


def require_same_organization(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    *,
    resource: str = "resources",
    company_is_tenant: bool = True,
) -> str:
    left_id = entity_organization_id(left, company_is_tenant=company_is_tenant)
    right_id = entity_organization_id(right, company_is_tenant=company_is_tenant)
    if not left_id or not right_id:
        raise ValueError(f"{resource} precisam possuir organization_id.")
    if left_id != right_id:
        raise PermissionError(f"{resource} pertencem a organizations diferentes.")
    return left_id


def tenant_id(entity: dict[str, Any] | None, *, company_is_tenant: bool = True) -> str | None:
    """Alias with the shorter name used by integrations."""
    return entity_organization_id(entity, company_is_tenant=company_is_tenant)
