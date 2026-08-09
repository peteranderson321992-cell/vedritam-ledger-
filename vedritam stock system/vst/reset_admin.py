"""Secure Super Admin recovery utility.

Usage:
  python reset_admin.py --password "<new strong password>"
or set VEDRITAM_RECOVERY_PASSWORD in the environment.

No universal/default credential is ever generated.
"""
import csv, os, argparse
from config import BASE_DIR
from utils import hash_password, current_timestamp
from security import validate_password

USERS = os.path.join(BASE_DIR, "data", "users.csv")
FIELDS = ["username", "password_hash", "role", "fullName", "email",
          "school_id", "lastLogin", "status", "created_time", "created_by"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", default=os.getenv("VEDRITAM_RECOVERY_PASSWORD", ""))
    args = ap.parse_args()
    password = args.password
    if not password:
        raise SystemExit("Provide a unique recovery password via --password or VEDRITAM_RECOVERY_PASSWORD.")
    ok, msg = validate_password(password, "admin")
    if not ok:
        raise SystemExit(msg)
    rows = []
    if os.path.exists(USERS):
        with open(USERS, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    admin = next((r for r in rows if r.get("username") == "admin"), None)
    if admin is None:
        admin = {k: "" for k in FIELDS}
        admin.update(username="admin", fullName="Super Admin",
                     created_time=current_timestamp(), created_by="secure-recovery")
        rows.insert(0, admin)
    admin["password_hash"] = hash_password(password)
    admin["role"] = "super_admin"
    admin["status"] = "Active"
    with open(USERS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    print("Super Admin recovery completed. The supplied password was not printed.")

if __name__ == "__main__":
    main()
