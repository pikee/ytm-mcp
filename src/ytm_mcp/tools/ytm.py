from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..config import Settings
from ..errors import YTMError, YTMServerError
from ..models import compact_entity, compact_template
from ..router import AccountRouter


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
KINDS = {"tag": ("tags", "tag"), "trigger": ("triggers", "trigger"), "variable": ("variables", "variable")}


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, YTMError):
        return ToolError(str(exc))
    if isinstance(exc, ValueError):
        return ToolError(str(exc))
    return ToolError("Unexpected internal error")


async def _page(
    router: AccountRouter,
    container_id: int,
    kind: Literal["tag", "trigger", "variable"],
    *,
    offset: int,
    limit: int,
    name_filter: str,
) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("offset must be >= 0 and limit must be between 1 and 200")
    plural, _ = KINDS[kind]
    payload = await router.get(
        container_id,
        f"container/{container_id}/{plural}",
        {"offset": offset, "per_page": limit, "name_filter": name_filter},
    )
    raw = payload.get(plural, [])
    if not isinstance(raw, list):
        raise YTMServerError(f"API response field {plural!r} is not a list", "invalid_response")
    total = payload.get("total", len(raw))
    next_offset = offset + len(raw) if offset + len(raw) < total else None
    return {
        "container_id": container_id,
        "items": [compact_entity(item, kind) for item in raw if isinstance(item, dict)],
        "total": total,
        "next_offset": next_offset,
    }


def register_tools(mcp: MCPServer, router: AccountRouter, settings: Settings) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_containers() -> dict[str, Any]:
        """List configured container IDs that are accessible through any configured Yandex account."""
        async def inspect(container_id: int) -> dict[str, Any]:
            try:
                account = await router.account_for(container_id)
                return {"container_id": container_id, "account": account.name, "accessible": True}
            except YTMError as exc:
                return {"container_id": container_id, "accessible": False, "error": exc.error_type}

        containers = await asyncio.gather(*(inspect(value) for value in settings.container_ids))
        return {
            "containers": containers,
            "total": len(containers),
            "note": "The official YTM API has no container-list endpoint; configure YTM_CONTAINER_IDS for discovery.",
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_tags(container_id: int, offset: int = 0, limit: int = 100, name_filter: str = "") -> dict[str, Any]:
        """Get one compact page of tags from a container."""
        try:
            return await _page(router, container_id, "tag", offset=offset, limit=limit, name_filter=name_filter)
        except Exception as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_tag(container_id: int, tag_id: int) -> dict[str, Any]:
        """Get compact details for one tag."""
        try:
            payload = await router.get(container_id, f"container/{container_id}/tag/{tag_id}")
            entity = payload.get("tag")
            if not isinstance(entity, dict):
                raise YTMServerError("API response field 'tag' is not an object", "invalid_response")
            return {"container_id": container_id, "tag": compact_entity(entity, "tag", detailed=True)}
        except (Exception, KeyError) as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def list_triggers(container_id: int, offset: int = 0, limit: int = 100, name_filter: str = "") -> dict[str, Any]:
        """Get one compact page of triggers from a container."""
        try:
            return await _page(router, container_id, "trigger", offset=offset, limit=limit, name_filter=name_filter)
        except Exception as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_trigger(container_id: int, trigger_id: int) -> dict[str, Any]:
        """Get compact details for one trigger."""
        try:
            payload = await router.get(container_id, f"container/{container_id}/trigger/{trigger_id}")
            entity = payload.get("trigger")
            if not isinstance(entity, dict):
                raise YTMServerError("API response field 'trigger' is not an object", "invalid_response")
            return {"container_id": container_id, "trigger": compact_entity(entity, "trigger", detailed=True)}
        except (Exception, KeyError) as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def list_variables(container_id: int, offset: int = 0, limit: int = 100, name_filter: str = "") -> dict[str, Any]:
        """Get one compact page of variables from a container."""
        try:
            return await _page(router, container_id, "variable", offset=offset, limit=limit, name_filter=name_filter)
        except Exception as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_variable(container_id: int, variable_id: int) -> dict[str, Any]:
        """Get compact details for one variable."""
        try:
            payload = await router.get(container_id, f"container/{container_id}/variable/{variable_id}")
            entity = payload.get("variable")
            if not isinstance(entity, dict):
                raise YTMServerError("API response field 'variable' is not an object", "invalid_response")
            return {"container_id": container_id, "variable": compact_entity(entity, "variable", detailed=True)}
        except (Exception, KeyError) as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def list_templates(container_id: int | None = None, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """List compact container templates, or public gallery templates when container_id is omitted."""
        try:
            if offset < 0 or not 1 <= limit <= 200:
                raise ValueError("offset must be >= 0 and limit must be between 1 and 200")
            if container_id is None:
                payload = await router.get_global("templates/gallery", {"offset": offset, "per_page": limit})
                raw = payload.get("templates", [])
                if not isinstance(raw, list):
                    raise YTMServerError("API templates field is not a list", "invalid_response")
                page = raw
                total = payload.get("total", len(raw))
            else:
                payload = await router.get(container_id, f"container/{container_id}/container_templates")
                raw = payload.get("templates", payload.get("container_templates", []))
                if not isinstance(raw, list):
                    raise YTMServerError("API templates field is not a list", "invalid_response")
                total = len(raw)
                page = raw[offset : offset + limit]
            return {
                "scope": "gallery" if container_id is None else "container",
                "container_id": container_id,
                "templates": [compact_template(item) for item in page if isinstance(item, dict)],
                "total": total,
                "next_offset": offset + len(page) if offset + len(page) < total else None,
            }
        except Exception as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def search_entities(
        container_id: int,
        query: str,
        entity_types: list[Literal["tag", "trigger", "variable"]] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Case-insensitive search across compact container entities."""
        try:
            if not query.strip():
                raise ValueError("query must not be empty")
            if not 1 <= limit <= 200:
                raise ValueError("limit must be between 1 and 200")
            kinds = entity_types or ["tag", "trigger", "variable"]
            tasks = [
                router.list_all(container_id, f"container/{container_id}/{KINDS[kind][0]}", KINDS[kind][0])
                for kind in kinds
            ]
            groups = await asyncio.gather(*tasks)
            needle = query.casefold()
            matches: list[dict[str, Any]] = []
            for kind, group in zip(kinds, groups, strict=True):
                for item in group:
                    compact = compact_entity(item, kind)
                    haystack = " ".join(str(value) for value in compact.values()).casefold()
                    if needle in haystack:
                        matches.append({"entity_type": kind, **compact})
                        if len(matches) >= limit:
                            return {"container_id": container_id, "query": query, "matches": matches, "truncated": True}
            return {"container_id": container_id, "query": query, "matches": matches, "truncated": False}
        except Exception as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_container_structure(container_id: int, max_entities: int = 500) -> dict[str, Any]:
        """Return a compact tag-trigger-variable graph suitable for model analysis."""
        try:
            if not 1 <= max_entities <= 2000:
                raise ValueError("max_entities must be between 1 and 2000")
            tags, triggers, variables = await asyncio.gather(
                router.list_all(container_id, f"container/{container_id}/tags", "tags"),
                router.list_all(container_id, f"container/{container_id}/triggers", "triggers"),
                router.list_all(container_id, f"container/{container_id}/variables", "variables"),
            )
            compact_tags = [compact_entity(item, "tag") for item in tags]
            compact_triggers = [compact_entity(item, "trigger") for item in triggers]
            compact_variables = [compact_entity(item, "variable") for item in variables]
            combined = [
                *(dict(entity_type="tag", **item) for item in compact_tags),
                *(dict(entity_type="trigger", **item) for item in compact_triggers),
                *(dict(entity_type="variable", **item) for item in compact_variables),
            ]
            return {
                "container_id": container_id,
                "account": router.cached_account_name(container_id),
                "counts": {"tags": len(tags), "triggers": len(triggers), "variables": len(variables)},
                "entities": combined[:max_entities],
                "truncated": len(combined) > max_entities,
            }
        except Exception as exc:
            raise _tool_error(exc) from exc
