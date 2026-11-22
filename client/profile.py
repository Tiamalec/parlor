"""Canonical user-profile projection shared by every V2 surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import AppState


@dataclass(frozen=True, slots=True)
class ProfileViewModel:
    user_id: int | None
    display_name: str
    agent_id: str = ""
    username: str = ""
    kind: str = "user"
    online: bool | None = None
    muted: bool = False
    global_role: str = ""
    group_role: str = ""
    avatar_version: int = 0
    avatar_mime: str | None = None
    image_data: str | None = None
    avatar_load_failed: bool = False
    available_actions: tuple[str, ...] = ()
    agent_model: str = ""
    agent_description: str = ""
    agent_personality: str = ""


def _same_user(record: dict[str, Any], user_id: int) -> bool:
    try:
        return int(record.get("id", record.get("user_id", 0)) or 0) == user_id
    except (TypeError, ValueError):
        return False


def resolve_profile(
    state: AppState,
    user_id: int | None,
    fallback: dict[str, Any] | None = None,
) -> ProfileViewModel:
    """Merge identity records by stable user ID while preserving the newest avatar."""
    fallback = dict(fallback or {})
    if fallback.get("kind") == "agent" or user_id is None:
        agent_id = str(fallback.get("id") or "")
        cached = state.agent_avatar_cache.get(agent_id, {})
        version = int(fallback.get("avatar_version", cached.get("avatar_version", 0)) or 0)
        cache_matches = int(cached.get("avatar_version", -1) or -1) == version
        return ProfileViewModel(
            user_id=None,
            display_name=str(fallback.get("name", fallback.get("username", fallback.get("display_name", "AI Agent")))),
            agent_id=agent_id,
            kind="agent",
            online=fallback.get("online", state.agent_online),
            group_role=str(fallback.get("group_role", "agent")),
            available_actions=tuple(str(item) for item in fallback.get("available_actions", [])),
            agent_model=str(fallback.get("agent_model", state.agent_model)),
            agent_description=str(fallback.get("description") or ""),
            agent_personality=str((fallback.get("card") or {}).get("personality") or "") if isinstance(fallback.get("card"), dict) else "",
            avatar_version=version,
            avatar_mime=fallback.get("avatar_mime", cached.get("avatar_mime")),
            image_data=cached.get("image_data") if cache_matches else None,
            avatar_load_failed=bool(cached.get("load_failed")) if cache_matches else False,
        )

    sources: list[dict[str, Any]] = []
    if _same_user(state.current_user, user_id):
        sources.append(state.current_user)
    sources.extend(item for item in state.users if _same_user(item, user_id))
    for members in state.members_by_group.values():
        sources.extend(item for item in members if item.get("kind") != "agent" and _same_user(item, user_id))
    sources.append(fallback)

    merged: dict[str, Any] = {"id": user_id}
    newest_avatar: dict[str, Any] = {}
    newest_version = -1
    for source in sources:
        if not source:
            continue
        merged.update({key: value for key, value in source.items() if value is not None})
        version = int(source.get("avatar_version", 0) or 0)
        if version >= newest_version:
            newest_version = version
            newest_avatar = {"avatar_version": version, "avatar_mime": source.get("avatar_mime")}
    merged.update(newest_avatar)
    version = max(0, newest_version)
    cached = state.avatar_cache.get(user_id, {})
    cache_matches = int(cached.get("avatar_version", -1) or -1) == version
    return ProfileViewModel(
        user_id=user_id,
        display_name=str(fallback.get("nickname") or merged.get("display_name") or merged.get("username") or "未知用户"),
        username=str(merged.get("username", "")),
        kind="user",
        online=merged.get("online"),
        muted=bool(merged.get("muted", False)),
        global_role=str(merged.get("global_role", merged.get("role", ""))),
        group_role=str(merged.get("group_role", "")),
        avatar_version=version,
        avatar_mime=merged.get("avatar_mime"),
        image_data=cached.get("image_data") if cache_matches else None,
        avatar_load_failed=bool(cached.get("load_failed")) if cache_matches else False,
        available_actions=tuple(str(item) for item in fallback.get("available_actions", merged.get("available_actions", []))),
    )
