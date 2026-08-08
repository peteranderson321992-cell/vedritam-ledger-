# utils.py
import hashlib
import os
import tempfile
import time
import csv
import jwt
import cache
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from typing import Dict, List, Any
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

# --- Security & Authentication ---
def hash_password(password: str, salt: str = None) -> str:
    if not salt:
        salt = os.urandom(16).hex()
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
    return f"{salt}${pwd_hash}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        salt, _ = hashed_password.split('$')
        return hashed_password == hash_password(plain_password, salt)
    except ValueError:
        return False

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")

# --- Authoritative Business Logic ---
def calculate_balance(purchased: int, distributed: int, returned: int) -> int:
    """Balance = Purchased - (Distributed + Returned) (Returns increase available stock)"""
    return purchased - (distributed + returned)

def calculate_books_required(strength: int, purchased: int) -> int:
    """Books Required = max(Strength - Purchased, 0)"""
    return max(strength - purchased, 0)

# --- Safe Persistence ---
def _write_rows(path: str, headers: List[str], rows: List[Dict[str, Any]], mode_fd=None):
    if mode_fd is not None:
        f = os.fdopen(mode_fd, 'w', newline='', encoding='utf-8')
    else:
        f = open(path, 'w', newline='', encoding='utf-8')
    with f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def atomic_csv_write(filepath: str, headers: List[str], rows: List[Dict[str, Any]]):
    """Write a CSV safely.

    On Windows the final rename can briefly fail with WinError 5 / WinError 32
    when antivirus, Windows Search or another process still holds the target
    file open. Those failures are transient, so the replace is retried a few
    times and, as a last resort, the data is written straight to the file.
    """
    directory = os.path.dirname(filepath) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, text=True, suffix=".tmp")
    try:
        _write_rows(temp_path, headers, rows, mode_fd=fd)

        last_error = None
        for attempt in range(8):
            try:
                os.replace(temp_path, filepath)
                last_error = None
                break
            except PermissionError as e:      # WinError 5 / 32 - file busy
                last_error = e
                time.sleep(0.05 * (attempt + 1))
            except OSError as e:
                last_error = e
                time.sleep(0.05 * (attempt + 1))

        if last_error is not None:
            # Fallback: overwrite the destination in place. Slightly less
            # atomic, but it keeps the application running instead of
            # returning a 500 for every request that logs something.
            _write_rows(filepath, headers, rows)
            try:
                os.remove(temp_path)
            except OSError:
                pass

        cache.invalidate("file:" + filepath)
    except Exception as e:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise Exception(f"Failed to write CSV securely: {str(e)}")


# Application timezone. Stored timestamps are written in this zone so the
# times shown in messages, notifications and audit trails match the wall clock
# of the people using the system (server machines usually run on UTC).
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")

try:
    from zoneinfo import ZoneInfo
    _LOCAL_TZ = ZoneInfo(APP_TIMEZONE)
except Exception:
    _LOCAL_TZ = None


def local_now() -> datetime:
    """Current time in the configured application timezone."""
    now = datetime.now(timezone.utc)
    return now.astimezone(_LOCAL_TZ) if _LOCAL_TZ else now.astimezone()


def current_timestamp() -> str:
    return local_now().strftime("%Y-%m-%d %H:%M:%S")


def current_date() -> str:
    return local_now().strftime("%Y-%m-%d")