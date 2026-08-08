"""Reset the super admin login back to admin / admin123.

Run:  python reset_admin.py
Use this if you ever get locked out of the 'admin' account.
"""
import csv, os
from config import BASE_DIR
from utils import hash_password, current_timestamp

USERS = os.path.join(BASE_DIR, "data", "users.csv")
FIELDS = ["username", "password_hash", "role", "fullName", "email",
          "school_id", "lastLogin", "status", "created_time", "created_by"]


def main():
    rows = []
    if os.path.exists(USERS):
        with open(USERS, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    admin = next((r for r in rows if r.get("username") == "admin"), None)
    if admin is None:
        admin = {k: "" for k in FIELDS}
        admin.update(username="admin", fullName="admin",
                     created_time=current_timestamp(), created_by="system")
        rows.insert(0, admin)

    admin["password_hash"] = hash_password("admin123")
    admin["role"] = "super_admin"
    admin["status"] = "Active"

    with open(USERS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print("Super admin reset. Username: admin   Password: admin123")


if __name__ == "__main__":
    main()
