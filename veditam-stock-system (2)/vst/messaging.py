# messaging.py
# Messaging + Notification Center data layer.
#
# Stores (CSV, atomic writes — same convention as the rest of the system):
#   conversations.csv  direct messages, school groups and announcements
#   messages.csv       message bodies + attachment pointers
#   receipts.csv       per-user read receipts
#   notifications.csv  the Notification Center feed
#   typing.json        ephemeral typing indicators
#   uploads/           attachment blobs (image / pdf / any file)

import csv
import json
import os
import time
import uuid
from typing import List, Dict, Any, Optional

from config import (
    CONVERSATIONS_CSV, MESSAGES_CSV, RECEIPTS_CSV, NOTIFICATIONS_CSV,
    TYPING_JSON, UPLOAD_DIR, MAX_UPLOAD_BYTES, ALLOWED_UPLOAD_TYPES,
    TYPING_TTL_SECONDS, E2EE_KEYS_CSV,
)
from utils import atomic_csv_write, current_timestamp
import cache

CONVERSATION_HEADERS = [
    "id", "type", "title", "school_id", "members_json",
    "created_by", "created_time", "last_message_at", "last_message",
]
MESSAGE_HEADERS = [
    "id", "conversation_id", "sender", "body", "attachment_id",
    "attachment_name", "attachment_type", "attachment_size",
    "timestamp", "deleted",
]
RECEIPT_HEADERS = ["message_id", "conversation_id", "username", "read_at"]
NOTIFICATION_HEADERS = [
    "id", "username", "type", "title", "body", "link", "read", "timestamp",
]

DM = "dm"
GROUP = "group"
ANNOUNCEMENT = "announcement"
CONVERSATION_TYPES = (DM, GROUP, ANNOUNCEMENT)

NOTIFICATION_TYPES = ("message", "report", "announcement", "alert")

# One fixed profile that holds every announcement ever posted, like a channel.
ANNOUNCEMENT_CHANNEL_ID = "announcements"
ANNOUNCEMENT_CHANNEL_TITLE = "Announcements"

# End-to-end encryption: only public keys ever reach the server. Private keys
# live in each user's browser, so nobody on the server side - not even a
# Super Admin - can read a direct message or group chat.
E2EE_KEY_HEADERS = ["username", "public_jwk", "updated_at"]
ENCRYPTED_PREVIEW = "Encrypted message"


def is_encrypted_payload(body: str) -> bool:
    text = (body or "").strip()
    return text.startswith("{") and '"e2ee"' in text


def safe_preview(body: str, attachment_name: str = "") -> str:
    """Never let ciphertext (or an encrypted file name) leak into previews."""
    if is_encrypted_payload(body):
        return ENCRYPTED_PREVIEW
    if body:
        return body
    return "[" + (attachment_name or "attachment") + "]"


# --- storage helpers ---------------------------------------------------------
def _read(path: str, headers: List[str]) -> List[Dict[str, Any]]:
    cached = cache.get("file:" + path)
    if cached is not None:
        return [dict(r) for r in cached]
    if not os.path.exists(path):
        atomic_csv_write(path, headers, [])
        return []
    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cache.set("file:" + path, rows, ttl=10)
    return [dict(r) for r in rows]


def _write(path: str, headers: List[str], rows: List[Dict[str, Any]]) -> None:
    atomic_csv_write(path, headers, rows)
    cache.invalidate("file:" + path)


def init_stores() -> None:
    for path, headers in (
        (CONVERSATIONS_CSV, CONVERSATION_HEADERS),
        (MESSAGES_CSV, MESSAGE_HEADERS),
        (RECEIPTS_CSV, RECEIPT_HEADERS),
        (E2EE_KEYS_CSV, E2EE_KEY_HEADERS),
        (NOTIFICATIONS_CSV, NOTIFICATION_HEADERS),
    ):
        if not os.path.exists(path):
            atomic_csv_write(path, headers, [])
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    _consolidate_announcements()


def _consolidate_announcements() -> None:
    """Fold any legacy announcement threads into the one Announcements profile."""
    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    anns = [r for r in rows if r.get("type") == ANNOUNCEMENT]
    if len(anns) <= 1 and (not anns or str(anns[0]["id"]) == ANNOUNCEMENT_CHANNEL_ID):
        return
    old_ids = {str(r["id"]) for r in anns}
    keep = {
        "id": ANNOUNCEMENT_CHANNEL_ID,
        "type": ANNOUNCEMENT,
        "title": ANNOUNCEMENT_CHANNEL_TITLE,
        "school_id": "",
        "members_json": json.dumps([]),
        "created_by": anns[0].get("created_by", "system"),
        "created_time": anns[0].get("created_time") or current_timestamp(),
        "last_message_at": max([r.get("last_message_at") or "" for r in anns]) or current_timestamp(),
        "last_message": "",
    }
    rows = [r for r in rows if r.get("type") != ANNOUNCEMENT] + [keep]
    _write(CONVERSATIONS_CSV, CONVERSATION_HEADERS, rows)

    messages = _read(MESSAGES_CSV, MESSAGE_HEADERS)
    changed = False
    latest = ""
    for m in messages:
        if str(m.get("conversation_id")) in old_ids:
            m["conversation_id"] = ANNOUNCEMENT_CHANNEL_ID
            changed = True
        if str(m.get("conversation_id")) == ANNOUNCEMENT_CHANNEL_ID:
            ts = m.get("timestamp") or ""
            if ts > latest:
                latest = ts
                keep["last_message"] = (m.get("sender") or "") + ": " + safe_preview(
                    m.get("body") or "", m.get("attachment_name") or "")
    if changed:
        _write(MESSAGES_CSV, MESSAGE_HEADERS, messages)
        _write(CONVERSATIONS_CSV, CONVERSATION_HEADERS, rows)


def _members(row: Dict[str, Any]) -> List[str]:
    try:
        value = json.loads(row.get("members_json") or "[]")
        return [str(m) for m in value]
    except (ValueError, TypeError):
        return []


def _is_member(row: Dict[str, Any], username: str) -> bool:
    if row.get("type") == ANNOUNCEMENT:
        return True  # announcements are readable by every signed-in account
    return username.lower() in {m.lower() for m in _members(row)}


# ============================================================================
# CONVERSATIONS
# ============================================================================
def list_conversations(username: str) -> List[Dict[str, Any]]:
    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    mine = [r for r in rows if _is_member(r, username)]
    unread = unread_counts(username)
    out = []
    for r in mine:
        item = dict(r)
        item["members"] = _members(r)
        item["unread"] = unread.get(r["id"], 0)
        out.append(item)
    out.sort(key=lambda r: r.get("last_message_at") or "", reverse=True)
    return out


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    row = next((r for r in rows if str(r["id"]) == str(conversation_id)), None)
    if row:
        row = dict(row)
        row["members"] = _members(row)
    return row


def create_conversation(ctype: str, title: str, members: List[str],
                        creator: str, school_id: str = "") -> Dict[str, Any]:
    ctype = (ctype or DM).strip().lower()
    if ctype not in CONVERSATION_TYPES:
        raise ValueError("Unknown conversation type.")
    members = sorted({m.strip() for m in (members or []) if m and m.strip()} | {creator})
    if ctype == DM:
        if len(members) != 2:
            raise ValueError("A direct message needs exactly one other person.")
        existing = next(
            (r for r in _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
             if r.get("type") == DM
             and sorted(m.lower() for m in _members(r)) == sorted(m.lower() for m in members)),
            None,
        )
        if existing:
            out = dict(existing)
            out["members"] = _members(existing)
            return out
        title = title or " & ".join(members)
    elif not title:
        raise ValueError("A title is required for groups and announcements.")

    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    record = {
        "id": uuid.uuid4().hex[:12],
        "type": ctype,
        "title": title,
        "school_id": str(school_id or ""),
        "members_json": json.dumps(members),
        "created_by": creator,
        "created_time": current_timestamp(),
        "last_message_at": current_timestamp(),
        "last_message": "",
    }
    rows.append(record)
    _write(CONVERSATIONS_CSV, CONVERSATION_HEADERS, rows)
    out = dict(record)
    out["members"] = members
    return out


def announcement_channel(creator: str = "system") -> Dict[str, Any]:
    """Return the single announcement profile, creating it once if needed."""
    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    row = next((r for r in rows if str(r["id"]) == ANNOUNCEMENT_CHANNEL_ID
                or r.get("type") == ANNOUNCEMENT), None)
    if row:
        out = dict(row)
        out["members"] = _members(row)
        return out
    record = {
        "id": ANNOUNCEMENT_CHANNEL_ID,
        "type": ANNOUNCEMENT,
        "title": ANNOUNCEMENT_CHANNEL_TITLE,
        "school_id": "",
        "members_json": json.dumps([]),
        "created_by": creator,
        "created_time": current_timestamp(),
        "last_message_at": current_timestamp(),
        "last_message": "",
    }
    rows.append(record)
    _write(CONVERSATIONS_CSV, CONVERSATION_HEADERS, rows)
    out = dict(record)
    out["members"] = []
    return out


# ============================================================================
# END-TO-END ENCRYPTION KEY DIRECTORY (public keys only)
# ============================================================================
def save_public_key(username: str, public_jwk: str) -> Dict[str, Any]:
    rows = _read(E2EE_KEYS_CSV, E2EE_KEY_HEADERS)
    row = next((r for r in rows
                if (r.get("username") or "").lower() == username.lower()), None)
    if row:
        row["public_jwk"] = public_jwk
        row["updated_at"] = current_timestamp()
    else:
        rows.append({"username": username, "public_jwk": public_jwk,
                     "updated_at": current_timestamp()})
    _write(E2EE_KEYS_CSV, E2EE_KEY_HEADERS, rows)
    return {"username": username}


def get_public_keys(usernames: List[str]) -> Dict[str, str]:
    wanted = {u.lower() for u in usernames if u}
    out: Dict[str, str] = {}
    for r in _read(E2EE_KEYS_CSV, E2EE_KEY_HEADERS):
        name = r.get("username") or ""
        if not wanted or name.lower() in wanted:
            out[name] = r.get("public_jwk") or ""
    return out


def add_members(conversation_id: str, members: List[str]) -> Dict[str, Any]:
    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    target = next((r for r in rows if str(r["id"]) == str(conversation_id)), None)
    if not target:
        raise ValueError("Conversation not found.")
    if target.get("type") == DM:
        raise ValueError("Members cannot be added to a direct message.")
    merged = sorted(set(_members(target)) | {m for m in members if m})
    target["members_json"] = json.dumps(merged)
    _write(CONVERSATIONS_CSV, CONVERSATION_HEADERS, rows)
    out = dict(target)
    out["members"] = merged
    return out


def _touch_conversation(conversation_id: str, preview: str) -> None:
    rows = _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
    target = next((r for r in rows if str(r["id"]) == str(conversation_id)), None)
    if not target:
        return
    target["last_message_at"] = current_timestamp()
    target["last_message"] = (preview or "")[:120]
    _write(CONVERSATIONS_CSV, CONVERSATION_HEADERS, rows)


# ============================================================================
# MESSAGES
# ============================================================================
def list_messages(conversation_id: str, limit: int = 50, offset: int = 0,
                  query: str = "") -> Dict[str, Any]:
    """Paginated, newest-last. `offset` walks backwards through history."""
    rows = [r for r in _read(MESSAGES_CSV, MESSAGE_HEADERS)
            if str(r.get("conversation_id")) == str(conversation_id)
            and r.get("deleted") != "1"]
    if query:
        needle = query.lower()
        rows = [r for r in rows if needle in (r.get("body") or "").lower()
                or needle in (r.get("attachment_name") or "").lower()]
    total = len(rows)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    window = rows[max(0, total - offset - limit): total - offset] if total else []
    receipts = _receipts_for(conversation_id)
    items = []
    for r in window:
        item = dict(r)
        item["read_by"] = receipts.get(r["id"], [])
        items.append(item)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": total - offset - limit > 0,
    }


def send_message(conversation_id: str, sender: str, body: str,
                 attachment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = (body or "").strip()
    if not body and not attachment:
        raise ValueError("Write a message or attach a file.")
    if len(body) > 5000:
        raise ValueError("Message is too long (5000 characters maximum).")
    rows = _read(MESSAGES_CSV, MESSAGE_HEADERS)
    record = {
        "id": uuid.uuid4().hex[:12],
        "conversation_id": str(conversation_id),
        "sender": sender,
        "body": body,
        "attachment_id": (attachment or {}).get("id", ""),
        "attachment_name": (attachment or {}).get("name", ""),
        "attachment_type": (attachment or {}).get("type", ""),
        "attachment_size": str((attachment or {}).get("size", "")),
        "timestamp": current_timestamp(),
        "deleted": "0",
    }
    rows.append(record)
    _write(MESSAGES_CSV, MESSAGE_HEADERS, rows)
    preview = safe_preview(body, record["attachment_name"])
    _touch_conversation(conversation_id, sender + ": " + preview)
    mark_read(conversation_id, sender)  # your own message is read by you
    clear_typing(conversation_id, sender)

    conv = get_conversation(conversation_id)
    if conv:
        recipients = conv.get("members", [])
        ntype = "message"
        if conv.get("type") == ANNOUNCEMENT:
            # Announcement notifications are fanned out by the API endpoint,
            # which knows the current recipient list.
            recipients = []
        for member in recipients:
            if member.lower() == sender.lower():
                continue
            push_notification(
                member, ntype,
                (conv.get("title") or "New message"),
                sender + ": " + preview,
                "messages.html?c=" + str(conversation_id),
            )
    record["read_by"] = [sender]
    return record


def delete_message(message_id: str, username: str, is_admin: bool = False) -> None:
    # `is_admin` is intentionally ignored for private chats: an admin has no
    # more power over someone else's message than anybody else.
    rows = _read(MESSAGES_CSV, MESSAGE_HEADERS)
    target = next((r for r in rows if str(r["id"]) == str(message_id)), None)
    if not target:
        raise ValueError("Message not found.")
    if str(target.get("sender", "")).lower() != username.lower():
        raise ValueError("You can only delete your own messages.")
    target["deleted"] = "1"
    _write(MESSAGES_CSV, MESSAGE_HEADERS, rows)


def search_messages(username: str, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Full-text search across every conversation the user belongs to."""
    query = (query or "").strip().lower()
    if len(query) < 2:
        return []
    convs = {c["id"]: c for c in list_conversations(username)}
    hits = []
    for r in reversed(_read(MESSAGES_CSV, MESSAGE_HEADERS)):
        if r.get("deleted") == "1" or r.get("conversation_id") not in convs:
            continue
        if query in (r.get("body") or "").lower() or query in (r.get("attachment_name") or "").lower():
            item = dict(r)
            item["conversation_title"] = convs[r["conversation_id"]].get("title", "")
            item["conversation_type"] = convs[r["conversation_id"]].get("type", "")
            hits.append(item)
        if len(hits) >= limit:
            break
    return hits


# ============================================================================
# READ RECEIPTS
# ============================================================================
def _receipts_for(conversation_id: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for r in _read(RECEIPTS_CSV, RECEIPT_HEADERS):
        if str(r.get("conversation_id")) != str(conversation_id):
            continue
        out.setdefault(r["message_id"], []).append(r.get("username", ""))
    return out


def mark_read(conversation_id: str, username: str) -> Dict[str, Any]:
    rows = _read(RECEIPTS_CSV, RECEIPT_HEADERS)
    seen = {(r["message_id"], (r.get("username") or "").lower()) for r in rows}
    added = 0
    for m in _read(MESSAGES_CSV, MESSAGE_HEADERS):
        if str(m.get("conversation_id")) != str(conversation_id) or m.get("deleted") == "1":
            continue
        if (m["id"], username.lower()) in seen:
            continue
        rows.append({
            "message_id": m["id"],
            "conversation_id": str(conversation_id),
            "username": username,
            "read_at": current_timestamp(),
        })
        added += 1
    if added:
        _write(RECEIPTS_CSV, RECEIPT_HEADERS, rows)
    return {"marked": added}


def unread_counts(username: str) -> Dict[str, int]:
    """Unread messages per conversation, counted only for conversations the
    user actually belongs to (announcements included), never their own
    messages, and never a deleted one. Receipts are matched case-insensitively
    so a differently-cased login cannot inflate the badge."""
    name = (username or "").strip().lower()
    if not name:
        return {}
    my_convs = {str(r["id"]) for r in _read(CONVERSATIONS_CSV, CONVERSATION_HEADERS)
                if _is_member(r, username)}
    read_ids = {r["message_id"] for r in _read(RECEIPTS_CSV, RECEIPT_HEADERS)
                if (r.get("username") or "").strip().lower() == name}
    counts: Dict[str, int] = {}
    seen_ids = set()
    for m in _read(MESSAGES_CSV, MESSAGE_HEADERS):
        cid = str(m.get("conversation_id") or "")
        mid = m.get("id")
        if cid not in my_convs or not mid or mid in seen_ids:
            continue
        seen_ids.add(mid)
        if m.get("deleted") == "1":
            continue
        if (m.get("sender") or "").strip().lower() == name:
            continue
        if mid in read_ids:
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return counts


# ============================================================================
# TYPING INDICATOR (ephemeral, TTL based)
# ============================================================================
def _load_typing() -> Dict[str, Dict[str, float]]:
    try:
        with open(TYPING_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_typing(data: Dict[str, Dict[str, float]]) -> None:
    tmp = TYPING_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, TYPING_JSON)


def set_typing(conversation_id: str, username: str) -> None:
    data = _load_typing()
    data.setdefault(str(conversation_id), {})[username] = time.time()
    _save_typing(data)


def clear_typing(conversation_id: str, username: str) -> None:
    data = _load_typing()
    if str(conversation_id) in data:
        data[str(conversation_id)].pop(username, None)
        _save_typing(data)


def who_is_typing(conversation_id: str, exclude: str = "") -> List[str]:
    data = _load_typing().get(str(conversation_id), {})
    now = time.time()
    return [u for u, ts in data.items()
            if now - float(ts) < TYPING_TTL_SECONDS and u.lower() != exclude.lower()]


# ============================================================================
# ATTACHMENTS
# ============================================================================
def save_attachment(filename: str, content_type: str, blob: bytes) -> Dict[str, Any]:
    if len(blob) > MAX_UPLOAD_BYTES:
        raise ValueError("File is larger than the %d MB limit." % (MAX_UPLOAD_BYTES // (1024 * 1024)))
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_TYPES:
        raise ValueError("File type '%s' is not allowed." % (ext or "unknown"))
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    attachment_id = uuid.uuid4().hex
    # The stored name never uses caller input: no traversal is possible.
    with open(os.path.join(UPLOAD_DIR, attachment_id + ext), "wb") as f:
        f.write(blob)
    return {
        "id": attachment_id + ext,
        "name": os.path.basename(filename or "attachment")[:120],
        "type": content_type or "application/octet-stream",
        "size": len(blob),
    }


def attachment_path(attachment_id: str) -> Optional[str]:
    safe = os.path.basename(attachment_id or "")
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        return None
    path = os.path.join(UPLOAD_DIR, safe)
    return path if os.path.exists(path) else None


def message_for_attachment(attachment_id: str) -> Optional[Dict[str, Any]]:
    return next((m for m in _read(MESSAGES_CSV, MESSAGE_HEADERS)
                 if m.get("attachment_id") == attachment_id and m.get("deleted") != "1"), None)


# ============================================================================
# NOTIFICATION CENTER
# ============================================================================
def push_notification(username: str, ntype: str, title: str, body: str = "",
                      link: str = "") -> Dict[str, Any]:
    ntype = ntype if ntype in NOTIFICATION_TYPES else "alert"
    rows = _read(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS)
    record = {
        "id": uuid.uuid4().hex[:12],
        "username": username,
        "type": ntype,
        "title": (title or "")[:160],
        "body": (body or "")[:400],
        "link": link or "",
        "read": "0",
        "timestamp": current_timestamp(),
    }
    rows.append(record)
    # History cap: keep the newest 500 notifications per account.
    mine = [r for r in rows if (r.get("username") or "").lower() == username.lower()]
    if len(mine) > 500:
        drop = {r["id"] for r in mine[:len(mine) - 500]}
        rows = [r for r in rows if r["id"] not in drop]
    _write(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS, rows)
    return record


def broadcast_notification(usernames: List[str], ntype: str, title: str,
                           body: str = "", link: str = "") -> int:
    count = 0
    for u in usernames:
        push_notification(u, ntype, title, body, link)
        count += 1
    return count


def list_notifications(username: str, unread_only: bool = False, ntype: str = "",
                       limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    rows = [r for r in _read(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS)
            if (r.get("username") or "").lower() == username.lower()]
    if unread_only:
        rows = [r for r in rows if r.get("read") != "1"]
    if ntype:
        rows = [r for r in rows if r.get("type") == ntype]
    rows.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    total = len(rows)
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    return {
        "items": rows[offset:offset + limit],
        "total": total,
        "unread": sum(1 for r in rows if r.get("read") != "1"),
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def mark_notification_read(notification_id: str, username: str) -> None:
    rows = _read(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS)
    target = next((r for r in rows
                   if r["id"] == notification_id
                   and (r.get("username") or "").lower() == username.lower()), None)
    if not target:
        raise ValueError("Notification not found.")
    target["read"] = "1"
    _write(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS, rows)


def mark_all_notifications_read(username: str) -> int:
    rows = _read(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS)
    changed = 0
    for r in rows:
        if (r.get("username") or "").lower() == username.lower() and r.get("read") != "1":
            r["read"] = "1"
            changed += 1
    if changed:
        _write(NOTIFICATIONS_CSV, NOTIFICATION_HEADERS, rows)
    return changed
