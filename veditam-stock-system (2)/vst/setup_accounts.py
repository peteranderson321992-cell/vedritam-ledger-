"""
Vedritam — bulk account setup.

Creates one school record + one login account per school, so you can hand each
school a username and password. The single 'admin' account keeps full control:
only the admin can add users, reset passwords, approve requests, and read the
activity log.

Usage
-----
1. Put one school name per line in schools_list.txt (a starter file is included).
2. Run:  python setup_accounts.py
3. Give each school the credentials printed in school_logins.csv.

Re-running is safe: existing schools/users are skipped, never overwritten.
"""
import csv
import os
import secrets
import string
import re

import database
from config import BASE_DIR

LIST_FILE = os.path.join(BASE_DIR, "schools_list.txt")
OUT_FILE = os.path.join(BASE_DIR, "data", "school_logins.csv")
ALPHABET = string.ascii_lowercase + string.digits


def make_password(n: int = 10) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def make_username(name: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "", name.lower())[:14] or "school"
    candidate, i = base, 1
    while candidate in taken:
        i += 1
        candidate = f"{base}{i}"
    taken.add(candidate)
    return candidate


def main():
    database.init_db()

    if not os.path.exists(LIST_FILE):
        print(f"Create {LIST_FILE} first (one school name per line).")
        return

    names = [ln.strip() for ln in open(LIST_FILE, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    if not names:
        print("schools_list.txt is empty.")
        return

    existing_schools = {s["name"].strip().lower(): s for s in database.get_all_schools()}
    taken = {u["username"].lower() for u in database.get_all_users()}
    created = []

    for idx, name in enumerate(names, start=1):
        key = name.lower()
        if key in existing_schools:
            school = existing_schools[key]
        else:
            school = database.add_school(name, f"SCH-{idx:03d}", "")
            existing_schools[key] = school
            print(f"school created: {name}")

        username = make_username(name, taken)
        password = make_password()
        try:
            database.create_user(
                username=username,
                password=password,
                role="school",
                full_name=name,
                email="",
                school_id=str(school["id"]),
                status="Active",
                created_by="setup_accounts",
            )
        except ValueError as e:
            print(f"skipped user for {name}: {e}")
            continue
        created.append({"school": name, "school_id": school["id"], "username": username, "password": password})
        print(f"  login: {username} / {password}")

    if created:
        write_header = not os.path.exists(OUT_FILE)
        with open(OUT_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["school", "school_id", "username", "password"])
            if write_header:
                w.writeheader()
            w.writerows(created)
        print(f"\n{len(created)} account(s) written to {OUT_FILE} — share them, then delete the file.")
    else:
        print("\nNothing new to create.")


if __name__ == "__main__":
    main()
