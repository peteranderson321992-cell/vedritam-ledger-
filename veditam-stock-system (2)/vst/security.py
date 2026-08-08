# security.py
# Login history, password policy, brute-force throttling, session timeout
# bookkeeping and TOTP two-factor authentication for Super Admin accounts.

import base64
import csv
import hashlib
import hmac
import json
import os
import re
import struct
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from config import (
    LOGIN_HISTORY_CSV, TWOFA_JSON, MIN_PASSWORD_LENGTH,
    MAX_FAILED_ATTEMPTS, LOCKOUT_MINUTES, SESSION_IDLE_MINUTES,
    TWOFA_STEPUP_AFTER_FAILURES,
    PASSWORD_REQUIRE_COMPLEXITY, BASE_DIR, data_path,
)
from utils import atomic_csv_write, current_timestamp
import cache

LOGIN_HISTORY_HEADERS = [
    "id", "username", "timestamp", "result", "ip", "user_agent", "detail",
]

COMMON_PASSWORDS = {
    "password", "password1", "123456", "12345678", "123456789", "qwerty",
    "abc123", "admin", "admin123", "letmein", "welcome", "iloveyou",
    "vedritam", "school123", "changeme", "passw0rd",
}


# ============================================================================
# PASSWORD POLICY
# ============================================================================
def validate_password(password: str, username: str = "") -> Tuple[bool, str]:
    pwd = password or ""
    if len(pwd) < MIN_PASSWORD_LENGTH:
        return False, "Password must be at least %d characters long." % MIN_PASSWORD_LENGTH
    if len(pwd) > 128:
        return False, "Password must be 128 characters or fewer."
    if pwd.lower() in COMMON_PASSWORDS:
        return False, "That password is too common. Choose something harder to guess."
    if username and username.lower() in pwd.lower():
        return False, "Password must not contain your username."
    if PASSWORD_REQUIRE_COMPLEXITY:
        if not re.search(r"[A-Z]", pwd):
            return False, "Password needs at least one uppercase letter."
        if not re.search(r"[a-z]", pwd):
            return False, "Password needs at least one lowercase letter."
        if not re.search(r"\d", pwd):
            return False, "Password needs at least one number."
    return True, "ok"


def password_strength(password: str) -> Dict[str, Any]:
    pwd = password or ""
    score = 0
    if len(pwd) >= 8:
        score += 1
    if len(pwd) >= 12:
        score += 1
    if re.search(r"[A-Z]", pwd) and re.search(r"[a-z]", pwd):
        score += 1
    if re.search(r"\d", pwd):
        score += 1
    if re.search(r"[^A-Za-z0-9]", pwd):
        score += 1
    label = ["Very weak", "Weak", "Fair", "Good", "Strong", "Excellent"][min(score, 5)]
    return {"score": score, "label": label}


# ============================================================================
# LOGIN HISTORY
# ============================================================================
def _read_history() -> List[Dict[str, Any]]:
    if not os.path.exists(LOGIN_HISTORY_CSV):
        atomic_csv_write(LOGIN_HISTORY_CSV, LOGIN_HISTORY_HEADERS, [])
        return []
    with open(LOGIN_HISTORY_CSV, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def init_stores() -> None:
    if not os.path.exists(LOGIN_HISTORY_CSV):
        atomic_csv_write(LOGIN_HISTORY_CSV, LOGIN_HISTORY_HEADERS, [])


def record_login(username: str, result: str, ip: str = "", user_agent: str = "",
                 detail: str = "") -> None:
    """result: SUCCESS | FAILED | LOCKED | 2FA_REQUIRED | 2FA_FAILED | LOGOUT"""
    rows = _read_history()
    rows.append({
        "id": uuid.uuid4().hex[:12],
        "username": username or "unknown",
        "timestamp": current_timestamp(),
        "result": result,
        "ip": (ip or "")[:64],
        "user_agent": (user_agent or "")[:200],
        "detail": (detail or "")[:200],
    })
    if len(rows) > 5000:
        rows = rows[-5000:]
    atomic_csv_write(LOGIN_HISTORY_CSV, LOGIN_HISTORY_HEADERS, rows)
    cache.invalidate("login_history")


def login_history(username: str = "", result: str = "", limit: int = 100,
                  offset: int = 0) -> Dict[str, Any]:
    rows = list(reversed(_read_history()))
    if username:
        rows = [r for r in rows if (r.get("username") or "").lower() == username.lower()]
    if result:
        rows = [r for r in rows if r.get("result") == result]
    total = len(rows)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    return {"items": rows[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset,
            "has_more": offset + limit < total}


# ============================================================================
# BRUTE-FORCE THROTTLING
# ============================================================================
_ATTEMPTS: Dict[str, List[float]] = {}
_LOCKED: Dict[str, float] = {}


def check_lockout(username: str) -> Optional[int]:
    """Returns remaining lockout seconds, or None when the account is free."""
    key = (username or "").lower()
    until = _LOCKED.get(key)
    if until and until > time.time():
        return int(until - time.time())
    if until:
        _LOCKED.pop(key, None)
        _ATTEMPTS.pop(key, None)
    return None


def register_failure(username: str) -> int:
    key = (username or "").lower()
    window = time.time() - LOCKOUT_MINUTES * 60
    attempts = [t for t in _ATTEMPTS.get(key, []) if t > window]
    attempts.append(time.time())
    _ATTEMPTS[key] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        _LOCKED[key] = time.time() + LOCKOUT_MINUTES * 60
    return max(0, MAX_FAILED_ATTEMPTS - len(attempts))


def clear_failures(username: str) -> None:
    key = (username or "").lower()
    _ATTEMPTS.pop(key, None)
    _LOCKED.pop(key, None)


def recent_failures(username: str) -> int:
    """Failed password attempts still inside the lockout window."""
    key = (username or "").lower()
    window = time.time() - LOCKOUT_MINUTES * 60
    attempts = [t for t in _ATTEMPTS.get(key, []) if t > window]
    _ATTEMPTS[key] = attempts
    return len(attempts)


def stepup_required(username: str) -> bool:
    """True once an account has failed the password enough times that the next
    successful sign-in must also pass a two-factor challenge."""
    return recent_failures(username) >= TWOFA_STEPUP_AFTER_FAILURES


# ============================================================================
# SESSION TIMEOUT
# ============================================================================
_LAST_SEEN: Dict[str, float] = {}

# The idle timeout is editable from the Security page and persisted here, so it
# survives a server restart. config.SESSION_IDLE_MINUTES is only the default.
SESSION_POLICY_JSON = data_path("session_policy.json")
MIN_IDLE_MINUTES = 5
MAX_IDLE_MINUTES = 1440


def get_idle_minutes() -> int:
    try:
        with open(SESSION_POLICY_JSON, "r", encoding="utf-8") as fh:
            value = int(json.load(fh).get("idle_minutes"))
        if MIN_IDLE_MINUTES <= value <= MAX_IDLE_MINUTES:
            return value
    except Exception:
        pass
    return int(SESSION_IDLE_MINUTES)


def set_idle_minutes(minutes: int) -> int:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        raise ValueError("Enter the idle timeout as a whole number of minutes.")
    if value < MIN_IDLE_MINUTES or value > MAX_IDLE_MINUTES:
        raise ValueError(
            "Idle timeout must be between %d and %d minutes." % (MIN_IDLE_MINUTES, MAX_IDLE_MINUTES))
    tmp = SESSION_POLICY_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"idle_minutes": value}, fh)
    os.replace(tmp, SESSION_POLICY_JSON)
    return value


def touch_session(username: str) -> None:
    _LAST_SEEN[(username or "").lower()] = time.time()


def session_expired(username: str, issued_at: Optional[float] = None) -> bool:
    """Idle timeout: no request from this account for SESSION_IDLE_MINUTES."""
    key = (username or "").lower()
    last = _LAST_SEEN.get(key)
    if last is None:
        _LAST_SEEN[key] = time.time()
        return False
    return (time.time() - last) > get_idle_minutes() * 60


def end_session(username: str) -> None:
    _LAST_SEEN.pop((username or "").lower(), None)
    end_device_session(username)


# ============================================================================
# SINGLE ACTIVE DEVICE PER ACCOUNT
# One account may only be signed in on one device at a time. Signing in
# somewhere else issues a new session id and invalidates the previous one, so
# the other device is returned to the login screen on its next request.
# ============================================================================
ACTIVE_SESSIONS_JSON = data_path("active_sessions.json")


def _load_device_sessions() -> Dict[str, Any]:
    try:
        with open(ACTIVE_SESSIONS_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_device_sessions(data: Dict[str, Any]) -> None:
    tmp = ACTIVE_SESSIONS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, ACTIVE_SESSIONS_JSON)


def start_device_session(username: str, device: str = "", ip: str = "") -> str:
    """Registers this device as the only active session for the account and
    returns its session id (embedded in the access token)."""
    key = (username or "").lower()
    sid = uuid.uuid4().hex
    data = _load_device_sessions()
    data[key] = {"sid": sid, "device": device[:200], "ip": ip,
                 "started": current_timestamp()}
    _save_device_sessions(data)
    return sid


def active_device_session(username: str) -> str:
    entry = _load_device_sessions().get((username or "").lower()) or {}
    return str(entry.get("sid", "") or "")


def device_session_valid(username: str, sid: str) -> bool:
    """A token without a session id belongs to an older build: accept it once
    and let the next sign-in bind the account to a single device."""
    active = active_device_session(username)
    if not active:
        return True
    return bool(sid) and sid == active


def end_device_session(username: str) -> None:
    data = _load_device_sessions()
    if data.pop((username or "").lower(), None) is not None:
        _save_device_sessions(data)


def session_info(username: str) -> Dict[str, Any]:
    last = _LAST_SEEN.get((username or "").lower(), time.time())
    idle = int(time.time() - last)
    minutes = get_idle_minutes()
    return {
        "idle_seconds": idle,
        "timeout_minutes": minutes,
        "min_minutes": MIN_IDLE_MINUTES,
        "max_minutes": MAX_IDLE_MINUTES,
        "expires_in_seconds": max(0, minutes * 60 - idle),
    }


# ============================================================================
# TWO-FACTOR AUTHENTICATION (TOTP, RFC 6238) — required for Super Admin
# ============================================================================
def _load_2fa() -> Dict[str, Any]:
    try:
        with open(TWOFA_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_2fa(data: Dict[str, Any]) -> None:
    tmp = TWOFA_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, TWOFA_JSON)


def twofa_status(username: str) -> Dict[str, Any]:
    entry = _load_2fa().get((username or "").lower(), {})
    return {
        "enabled": bool(entry.get("enabled")),
        "pending": bool(entry.get("secret") and not entry.get("enabled")),
        "backup_codes_left": len(entry.get("backup_codes", [])),
    }


def _b32secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def start_twofa_enrollment(username: str, issuer: str = "Vedritam") -> Dict[str, Any]:
    data = _load_2fa()
    key = (username or "").lower()
    secret = _b32secret()
    backup = [uuid.uuid4().hex[:8].upper() for _ in range(8)]
    data[key] = {
        "secret": secret,
        "enabled": False,
        "backup_codes": [hashlib.sha256(c.encode()).hexdigest() for c in backup],
        "created": current_timestamp(),
    }
    _save_2fa(data)
    uri = "otpauth://totp/%s:%s?secret=%s&issuer=%s&digits=6&period=30" % (
        issuer, username, secret, issuer)
    return {"secret": secret, "otpauth_uri": uri, "backup_codes": backup}


def totp_code(secret: str, at: Optional[int] = None) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def verify_totp(username: str, code: str) -> bool:
    """Accepts the current step plus one step either side (clock drift),
    or an unused single-use backup code."""
    data = _load_2fa()
    key = (username or "").lower()
    entry = data.get(key)
    if not entry or not entry.get("secret"):
        return False
    code = (code or "").strip().replace(" ", "")
    now = time.time()
    for drift in (-1, 0, 1):
        if hmac.compare_digest(totp_code(entry["secret"], now + drift * 30), code):
            return True
    hashed = hashlib.sha256(code.upper().encode()).hexdigest()
    if hashed in entry.get("backup_codes", []):
        entry["backup_codes"].remove(hashed)  # single use
        _save_2fa(data)
        return True
    return False


def confirm_twofa(username: str, code: str) -> bool:
    if not verify_totp(username, code):
        return False
    data = _load_2fa()
    key = (username or "").lower()
    data[key]["enabled"] = True
    data[key]["confirmed"] = current_timestamp()
    _save_2fa(data)
    return True


def disable_twofa(username: str) -> None:
    data = _load_2fa()
    data.pop((username or "").lower(), None)
    _save_2fa(data)


def twofa_required(username: str) -> bool:
    return bool(_load_2fa().get((username or "").lower(), {}).get("enabled"))
