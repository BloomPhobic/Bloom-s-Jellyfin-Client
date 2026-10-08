"""Read and toggle library data: home rows, played and favourite state.

All functions are blocking and return models; the UI calls them in workers.
"""
from __future__ import annotations

from typing import Any

from .client import JellyfinClient
from .models import Item, UserData

ROW_LIMIT = 20
# Ask for what the cards draw and nothing more.
CARD_FIELDS = "PrimaryImageAspectRatio,ProductionYear,DateCreated,ParentId"
IMAGE_PARAMS = {"enableImageTypes": "Primary,Backdrop,Thumb", "imageTypeLimit": 1}

# Libraries that don't get a "Recently added" row.
NO_LATEST_TYPES = {"boxsets", "playlists", "livetv", "folders", "books", "photos", "music"}


def _items(data: Any) -> list[Item]:
    if isinstance(data, dict):
        data = data.get("Items", [])
    return [Item.from_json(x) for x in (data or []) if isinstance(x, dict)]


def _card_params(limit: int = ROW_LIMIT, **extra: Any) -> dict[str, Any]:
    p: dict[str, Any] = {"limit": limit, "fields": CARD_FIELDS, **IMAGE_PARAMS}
    p.update({k: v for k, v in extra.items() if v is not None})
    return p


# --------------------------------------------------------------------------- rows

def views(client: JellyfinClient) -> list[Item]:
    return _items(client.compat.call("views"))


def resume(client: JellyfinClient, limit: int = ROW_LIMIT) -> list[Item]:
    return _items(client.compat.call("resume", params=_card_params(limit, mediaTypes="Video")))


def next_up(client: JellyfinClient, limit: int = ROW_LIMIT) -> list[Item]:
    # enableResumable=false keeps half-watched episodes in Continue Watching only.
    params = _card_params(limit, userId=client.user_id, enableResumable=False)
    return _items(client.get("/Shows/NextUp", **params))


def latest(client: JellyfinClient, parent_id: str, limit: int = 16) -> list[Item]:
    return _items(client.compat.call("latest", params=_card_params(limit, parentId=parent_id)))


def history(client: JellyfinClient, limit: int = ROW_LIMIT) -> list[Item]:
    return _items(client.compat.call("items", params=_card_params(
        limit, recursive=True, filters="IsPlayed", sortBy="DatePlayed", sortOrder="Descending",
        includeItemTypes="Movie,Episode")))


def favourites(client: JellyfinClient, limit: int = 30) -> list[Item]:
    return _items(client.compat.call("items", params=_card_params(
        limit, recursive=True, filters="IsFavorite", sortBy="SortName", sortOrder="Ascending",
        includeItemTypes="Movie,Series,Episode")))


def wants_latest_row(view: Item) -> bool:
    return (view.collection_type or "").lower() not in NO_LATEST_TYPES


# --------------------------------------------------------------------------- toggles

def set_played(client: JellyfinClient, item_id: str, played: bool) -> UserData | None:
    data = client.compat.call("played", "POST" if played else "DELETE", item_id=item_id)
    return UserData.from_json(data) if isinstance(data, dict) else None


def set_favorite(client: JellyfinClient, item_id: str, favorite: bool) -> UserData | None:
    data = client.compat.call("favorite", "POST" if favorite else "DELETE", item_id=item_id)
    return UserData.from_json(data) if isinstance(data, dict) else None
