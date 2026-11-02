"""Strict Bitrix24 crm.item.list item extraction.

Bitrix24 documents result.items as a flat array of objects. Some live
responses contain a nested array at index 0. We accept ONLY finite, nonempty
arrays entirely of object records; all identity, ordering and category checks
remain enforced by the caller. Unknown content stops processing, never skips
over records to guess an ID. No source/customer text in diagnostics.
"""
from __future__ import annotations


def item_kind(item):
    if item is None:
        return "NULL"
    if type(item) is str:
        return "TEXT"
    if type(item) is list:
        return "LIST"
    if type(item) is bool:
        return "BOOL"
    if type(item) in (int, float):
        return "NUMBER"
    if type(item) is dict:
        return "OBJECT"
    return "OTHER"


class ItemListShapeError(ValueError):
    def __init__(self, index: int, kind: str):
        self.index = index
        self.kind = kind
        super().__init__("BITRIX_LIST_ITEM_SHAPE_UNVERIFIED")


def strict_bitrix_items(result, *, maximum=50):
    """Return only a complete, verified page of objects or fail closed.

    Supports documented flat [{...}, {...}] and an extra grouping layer
    [[{...}, {...}]] when *every* member is a dict. Mixed, empty, deeper
    nesting, unbounded and non-object records are always blocked.
    """
    if not isinstance(result, dict):
        raise ValueError("BITRIX_LIST_RESULT_UNVERIFIED")
    source = result.get("items")
    if not isinstance(source, list) or len(source) > maximum:
        raise ValueError("BITRIX_LIST_ITEMS_UNVERIFIED")
    if not source:
        return []
    if all(type(entry) is dict for entry in source):
        return source
    if all(type(group) is list for group in source):
        flattened = []
        for index, group in enumerate(source):
            if not group:
                raise ItemListShapeError(index, "EMPTY")
            for child in group:
                if type(child) is not dict:
                    raise ItemListShapeError(index, item_kind(child))
                flattened.append(child)
                if len(flattened) > maximum:
                    raise ValueError("BITRIX_LIST_ITEMS_UNVERIFIED")
        return flattened
    for index, item in enumerate(source):
        if type(item) is not dict:
            raise ItemListShapeError(index, item_kind(item))
    raise ValueError("BITRIX_LIST_ITEMS_UNVERIFIED")
