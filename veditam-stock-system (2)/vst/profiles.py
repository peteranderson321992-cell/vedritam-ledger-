"""profiles.py — profile photos, presence and live-call signalling.

Everything here is deliberately file based (JSON next to the CSV stores) so it
matches the rest of the system and needs no extra services.

  * Profile photos  : one small JPEG data-URL per account, stored on the
                      server so the same picture shows up on every page and
                      on every device (header, settings, messages, groups).
  * Group photos    : the same thing for a conversation.
  * Presence        : "last online" for the group / member panels.
  * Call signalling : a tiny mailbox the browsers poll to exchange WebRTC
                      offers, answers and ICE candidates for voice calls,
                      video calls and screen sharing.
"""

import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from config import DATA_DIR, data_path
from utils import current_timestamp

AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

PRESENCE_JSON = data_path("presence.json")
CALLS_JSON = data_path("calls.json")

MAX_AVATAR_CHARS = 400_000          # ~300 KB of image data
PRESENCE_ONLINE_SECONDS = 75        # polled every ~20 s by the browser
SIGNAL_TTL_SECONDS = 90
CALL_TTL_SECONDS = 120


# ---------------------------------------------------------------------------
# small json helpers
# ---------------------------------------------------------------------------
def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _slug(name: str) -> str:
    """Filesystem-safe key. Never trusts caller input."""
    return re.sub(r"[^a-z0-9_.-]", "_", str(name or "").strip().lower())[:80]


# ---------------------------------------------------------------------------
# profile photos
# ---------------------------------------------------------------------------
def _avatar_file(kind: str, key: str) -> str:
    return os.path.join(AVATAR_DIR, "%s_%s.txt" % (kind, _slug(key)))


def _read_avatar(kind: str, key: str) -> str:
    if not _slug(key):
        return ""
    try:
        with open(_avatar_file(kind, key), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write_avatar(kind: str, key: str, data_url: str) -> str:
    value = (data_url or "").strip()
    if not value.startswith("data:image/"):
        raise ValueError("The photo must be an image.")
    if len(value) > MAX_AVATAR_CHARS:
        raise ValueError("That photo is too large — please pick a smaller image.")
    os.makedirs(AVATAR_DIR, exist_ok=True)
    with open(_avatar_file(kind, key), "w", encoding="utf-8") as fh:
        fh.write(value)
    return value


def _delete_avatar(kind: str, key: str) -> None:
    try:
        os.remove(_avatar_file(kind, key))
    except OSError:
        pass


def get_user_avatar(username: str) -> str:
    return _read_avatar("u", username)


def set_user_avatar(username: str, data_url: str) -> str:
    return _write_avatar("u", username, data_url)


def clear_user_avatar(username: str) -> None:
    _delete_avatar("u", username)


def get_conversation_avatar(conversation_id: str) -> str:
    return _read_avatar("c", conversation_id)


def set_conversation_avatar(conversation_id: str, data_url: str) -> str:
    return _write_avatar("c", conversation_id, data_url)


def clear_conversation_avatar(conversation_id: str) -> None:
    _delete_avatar("c", conversation_id)


def avatars_for(usernames: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in usernames or []:
        photo = get_user_avatar(name)
        if photo:
            out[name] = photo
    return out


# ---------------------------------------------------------------------------
# presence / last online
# ---------------------------------------------------------------------------
def touch(username: str) -> None:
    name = (username or "").strip()
    if not name:
        return
    data = _load(PRESENCE_JSON)
    row = data.get(name.lower()) or {}
    now = time.time()
    # Throttled: one write a minute per account keeps the file quiet.
    if now - float(row.get("epoch") or 0) < 60:
        return
    data[name.lower()] = {"username": name, "epoch": now, "at": current_timestamp()}
    _save(PRESENCE_JSON, data)


def presence(usernames: List[str]) -> Dict[str, Dict[str, Any]]:
    data = _load(PRESENCE_JSON)
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    for name in usernames or []:
        row = data.get((name or "").strip().lower()) or {}
        epoch = float(row.get("epoch") or 0)
        out[name] = {
            "online": bool(epoch) and (now - epoch) < PRESENCE_ONLINE_SECONDS,
            "last_seen": row.get("at", ""),
            "seconds_ago": int(now - epoch) if epoch else None,
        }
    return out


# ---------------------------------------------------------------------------
# call signalling (WebRTC mailbox)
# ---------------------------------------------------------------------------
def _calls() -> Dict[str, Any]:
    data = _load(CALLS_JSON)
    data.setdefault("rooms", {})
    data.setdefault("mail", {})
    return data


def _member_entry(value: Any, fallback_name: str) -> Dict[str, Any]:
    """Members used to be stored as ``name -> timestamp``. They are now stored
    as ``lowercase name -> {"name": <as typed>, "at": <timestamp>}`` so that the
    same person signing in as "Asha" and "asha" is never counted twice. This
    reads either shape."""
    if isinstance(value, dict):
        return {"name": value.get("name") or fallback_name, "at": float(value.get("at") or 0)}
    try:
        return {"name": fallback_name, "at": float(value)}
    except (TypeError, ValueError):
        return {"name": fallback_name, "at": 0.0}


def _normalise_members(room: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for raw_key, value in (room.get("members") or {}).items():
        key = (raw_key or "").strip().lower()
        if not key:
            continue
        entry = _member_entry(value, raw_key)
        # If both casings are present, keep the most recent heartbeat.
        if key not in out or entry["at"] > out[key]["at"]:
            out[key] = entry
    return out


def _prune(data: Dict[str, Any]) -> None:
    now = time.time()
    for cid in list(data["rooms"].keys()):
        room = data["rooms"][cid]
        members = {k: v for k, v in _normalise_members(room).items()
                   if now - v["at"] < CALL_TTL_SECONDS}
        if not members:
            data["rooms"].pop(cid, None)
        else:
            room["members"] = members
    for user in list(data["mail"].keys()):
        kept = [m for m in data["mail"][user] if now - float(m.get("epoch") or 0) < SIGNAL_TTL_SECONDS]
        if kept:
            data["mail"][user] = kept
        else:
            data["mail"].pop(user, None)


def _post(data: Dict[str, Any], to: str, payload: Dict[str, Any]) -> None:
    box = data["mail"].setdefault((to or "").strip().lower(), [])
    payload = dict(payload)
    payload.setdefault("id", uuid.uuid4().hex[:12])
    payload["epoch"] = time.time()
    box.append(payload)
    del box[:-80]


def join_call(conversation_id: str, username: str, mode: str,
              members: List[str]) -> Dict[str, Any]:
    """Marks the caller as being in the room and rings everyone else."""
    data = _calls()
    _prune(data)
    cid = str(conversation_id)
    room = data["rooms"].setdefault(cid, {
        "id": cid, "mode": mode or "audio", "host": username,
        "started": current_timestamp(), "members": {},
    })
    room["members"] = _normalise_members(room)
    key = (username or "").strip().lower()
    fresh = key not in room["members"]
    if mode in ("audio", "video"):
        room["mode"] = mode
    if not room.get("host"):
        room["host"] = username
    room["members"][key] = {"name": username, "at": time.time()}
    if fresh:
        # Only ring the people who are not already sitting in the room, so a
        # rejoin after a dropped connection does not make everyone's phone
        # start ringing a second time.
        in_room = set(room["members"].keys())
        for other in members or []:
            other_key = (other or "").strip().lower()
            if not other_key or other_key == key or other_key in in_room:
                continue
            _post(data, other, {"kind": "ring", "conversation_id": cid,
                                "from": username, "mode": room["mode"]})
    _save(CALLS_JSON, data)
    return room_state(cid)


def leave_call(conversation_id: str, username: str) -> None:
    data = _calls()
    _prune(data)
    room = data["rooms"].get(str(conversation_id))
    if room:
        room["members"] = _normalise_members(room)
        room["members"].pop((username or "").strip().lower(), None)
        for entry in list(room["members"].values()):
            _post(data, entry["name"], {"kind": "bye", "conversation_id": str(conversation_id),
                                        "from": username})
        if not room["members"]:
            data["rooms"].pop(str(conversation_id), None)
    _save(CALLS_JSON, data)


def room_state(conversation_id: str) -> Dict[str, Any]:
    data = _calls()
    _prune(data)
    room = data["rooms"].get(str(conversation_id))
    if not room:
        return {"active": False, "members": [], "mode": "", "host": ""}
    return {
        "active": True,
        "mode": room.get("mode", "audio"),
        "host": room.get("host", ""),
        "started": room.get("started", ""),
        "members": sorted((e["name"] for e in _normalise_members(room).values()),
                          key=lambda n: n.lower()),
    }


def send_signal(conversation_id: str, sender: str, to: str, kind: str,
                payload: Any) -> None:
    data = _calls()
    _prune(data)
    _post(data, to, {"kind": kind, "conversation_id": str(conversation_id),
                     "from": sender, "payload": payload})
    _save(CALLS_JSON, data)


def drain_signals(username: str) -> List[Dict[str, Any]]:
    data = _calls()
    _prune(data)
    key = (username or "").strip().lower()
    items = data["mail"].pop(key, [])
    _save(CALLS_JSON, data)
    return items


def heartbeat(conversation_id: str, username: str) -> Optional[Dict[str, Any]]:
    data = _calls()
    _prune(data)
    room = data["rooms"].get(str(conversation_id))
    if not room:
        return None
    room["members"] = _normalise_members(room)
    key = (username or "").strip().lower()
    # A heartbeat must never *add* somebody: after they hang up (or are pruned
    # for going quiet) a late heartbeat used to resurrect them as a ghost
    # participant that nobody could remove.
    if key not in room["members"]:
        return None
    room["members"][key]["at"] = time.time()
    _save(CALLS_JSON, data)
    return room_state(conversation_id)
