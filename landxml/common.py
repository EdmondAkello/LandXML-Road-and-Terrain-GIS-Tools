"""Namespace tolerant helpers for the published LandXML element vocabulary."""

from __future__ import annotations


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace_map(element) -> dict[str, str]:
    """Return an XPath prefix mapping for the document's actual namespace."""
    tag = element.tag
    uri = tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
    return {"l": uri}


def child(element, name: str):
    return next((item for item in element if local_name(item.tag) == name), None)


def descendants(element, name: str):
    return (
        item
        for item in element.iter()
        if item is not element and local_name(item.tag) == name
    )


def first_descendant(element, name: str):
    return next(descendants(element, name), None)


def numbers(text: str | None, count: int, label: str) -> tuple[float, ...]:
    parts = (text or "").split()
    if len(parts) != count:
        raise ValueError(
            f"{label} requires {count} numeric coordinates; found {len(parts)}"
        )
    try:
        return tuple(float(value) for value in parts)
    except ValueError as exc:
        raise ValueError(f"{label} contains a nonnumeric coordinate") from exc
