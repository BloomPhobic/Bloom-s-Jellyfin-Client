"""Which server image to show for an item, as a cache key (server-relative path).

Keys carry the image tag, so a changed image gets a new key.
"""
from __future__ import annotations

from .client import encode_params
from .models import Item

POSTER_WIDTH = 320       # ~2x a 150 px card, crisp on HiDPI
LANDSCAPE_WIDTH = 560
BACKDROP_WIDTH = 1920


def image_path(item_id: str, image_type: str, tag: str | None, max_width: int,
               index: int | None = None) -> str:
    seg = f"/Items/{item_id}/Images/{image_type}" + (f"/{index}" if index is not None else "")
    return seg + "?" + encode_params({"tag": tag, "maxWidth": max_width, "quality": 90})


def poster(item: Item, max_width: int = POSTER_WIDTH) -> str | None:
    """2:3 art. Episodes and seasons fall back to their series poster."""
    raw = item.raw
    if item.type in ("Episode", "Season") and raw.get("SeriesId") and item.series_primary_image_tag:
        if item.type == "Episode" or "Primary" not in item.image_tags:
            return image_path(raw["SeriesId"], "Primary", item.series_primary_image_tag, max_width)
    tag = item.image_tags.get("Primary")
    if tag:
        return image_path(item.id, "Primary", tag, max_width)
    if raw.get("SeriesId") and item.series_primary_image_tag:
        return image_path(raw["SeriesId"], "Primary", item.series_primary_image_tag, max_width)
    return None


def landscape(item: Item, max_width: int = LANDSCAPE_WIDTH) -> str | None:
    """16:9 art: own thumb, episode still, own backdrop, then the parent's thumb/backdrop."""
    raw, tags = item.raw, item.image_tags
    if tags.get("Thumb"):
        return image_path(item.id, "Thumb", tags["Thumb"], max_width)
    if item.type == "Episode" and tags.get("Primary"):
        return image_path(item.id, "Primary", tags["Primary"], max_width)
    if item.backdrop_image_tags:
        return image_path(item.id, "Backdrop", item.backdrop_image_tags[0], max_width, index=0)
    if raw.get("ParentThumbItemId") and raw.get("ParentThumbImageTag"):
        return image_path(raw["ParentThumbItemId"], "Thumb", raw["ParentThumbImageTag"], max_width)
    pb = raw.get("ParentBackdropImageTags") or []
    if raw.get("ParentBackdropItemId") and pb:
        return image_path(raw["ParentBackdropItemId"], "Backdrop", pb[0], max_width, index=0)
    if tags.get("Primary"):
        return image_path(item.id, "Primary", tags["Primary"], max_width)
    return None


def backdrop(item: Item, max_width: int = BACKDROP_WIDTH) -> str | None:
    raw = item.raw
    if item.backdrop_image_tags:
        return image_path(item.id, "Backdrop", item.backdrop_image_tags[0], max_width, index=0)
    pb = raw.get("ParentBackdropImageTags") or []
    if raw.get("ParentBackdropItemId") and pb:
        return image_path(raw["ParentBackdropItemId"], "Backdrop", pb[0], max_width, index=0)
    return None
