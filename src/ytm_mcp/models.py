from __future__ import annotations

from typing import Any


JsonObject = dict[str, Any]


def _short_text(value: Any, limit: int = 500) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + "…"


def _compact_parameters(value: Any) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    result: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact = {
            key: item.get(key) if key == "value" else _short_text(item.get(key))
            for key in ("parameter_id", "type", "required", "value")
            if item.get(key) is not None
        }
        rows = item.get("rows")
        if isinstance(rows, list):
            compact["row_count"] = len(rows)
        result.append(compact)
    return result


def compact_entity(entity: JsonObject, kind: str, *, detailed: bool = False) -> JsonObject:
    id_key = {"tag": "tag_id", "trigger": "trigger_id", "variable": "variable_id"}[kind]
    data = entity.get("data") if isinstance(entity.get("data"), dict) else {}
    result: JsonObject = {
        "id": entity.get(id_key),
        "name": entity.get("name"),
        "type": entity.get("type"),
        "status": entity.get("status"),
        "template_id": data.get("template_id"),
    }
    if kind == "tag":
        result["trigger_ids"] = entity.get("triggers", [])
        if entity.get("tag_priority") is not None:
            result["priority"] = entity.get("tag_priority")
    elif kind == "trigger":
        result["tag_ids"] = entity.get("tags", [])
        result["predicates"] = entity.get("user_predicates", [])
    if detailed:
        result.update(
            {
                "updated_at": entity.get("update_time"),
                "links_count": entity.get("links_number"),
                "parameters": _compact_parameters(data.get("user_provided_values")),
                "additional_properties": _compact_parameters(entity.get("additional_properties")),
            }
        )
        for key in ("tags_links", "triggers_links", "variables_links"):
            if key in entity:
                result[key] = entity[key]
    return {key: value for key, value in result.items() if value is not None}


def compact_template(template: JsonObject) -> JsonObject:
    version = template.get("published_version") or template.get("version")
    return {
        key: value
        for key, value in {
            "id": template.get("template_id") or template.get("id"),
            "name": template.get("name"),
            "type": template.get("type"),
            "status": template.get("status"),
            "version": version,
            "description": _short_text(template.get("description")),
            "author": template.get("author"),
            "main_page_link": template.get("main_page_link"),
        }.items()
        if value is not None
    }
