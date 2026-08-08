# database.py
import csv
import json
import uuid
import os
from typing import List, Dict, Any, Optional
from utils import atomic_csv_write, hash_password, current_timestamp, current_date, calculate_balance, calculate_books_required
import cache
from config import (USERS_CSV, SCHOOLS_CSV, LEDGER_CSV, AUDIT_CSV, AI_SETTINGS_JSON,
                    CATALOG_CSV, VENDORS_CSV, STANDARDS, ARTICLE_CATEGORIES,
                    SCHOOL_DATA_DIR, STATIONERY_ITEMS, LOW_STOCK_LIMIT,
                    VENDOR_RETURNS_CSV, LEDGER_FIELDS_JSON, RETURN_REASONS,
                    LEDGER_FIELD_TYPES, GST_RATES, DISCOUNT_OPTIONS, EDITIONS,
                    academic_years)

SCHOOL_HEADERS = [
    "id", "name", "code", "location", "logo", "address", "contact",
    "academic_year", "status", "assigned_staff", "settings_json", "classes_json"
]
USER_HEADERS = [
    "username", "password_hash", "role", "fullName", "email", "school_id",
    "lastLogin", "status", "created_time", "created_by"
]
AUDIT_HEADERS = ["id", "timestamp", "username", "role", "action", "entity", "entity_id", "details"]

# The ledger mirrors the printed "STOCK REGISTER FOR TEXT BOOKS / NOTE BOOKS"
# register (22 columns) and keeps the older stock-tracking columns alongside.
LEDGER_HEADERS = [
    "id", "school_id", "class_id", "standard",
    # vendor block (cols 2-5)
    "vendorId", "vendor", "vendorContact", "vendorGst",
    # invoice block (cols 6-7)
    "invoiceDate", "invoiceRef",
    # article block (cols 8-10)
    "bookName", "category", "subject", "publication", "edition",
    # institution / academic context + extended vendor details
    "academicYear", "vendorAddress", "vendorEmail", "paymentTerms",
    # quantity block (cols 11-12)
    "openingBalance", "purchased",
    # money block (cols 13-18)
    "approvedRate", "baseRate", "gstPercent", "gstAmount", "discountPercent",
    "discountAmount", "grossAmount", "totalAmount",
    # issue block (cols 19-21)
    "distributed", "returned", "closingBalance",
    # legacy stock columns kept so existing dashboards keep working
    "strength", "booksRequired", "balance",
    "remarks", "custom_json",
    "created_by", "created_time", "modified_by", "modified_time"
]

CATALOG_HEADERS = [
    "standard", "category", "subject", "title", "publication",
    "default_qty_per_student",
    # Resource Catalog extensions
    "edition", "academic_year", "language", "isbn", "approved_rate", "status",
]
VENDOR_HEADERS = [
    "vendorId", "name", "contact", "gst",
    # Vendor Management extensions
    "email", "address", "pan", "bank_name", "bank_account", "bank_ifsc",
    "payment_terms", "status",
]

# Derived (server-computed) ledger columns the browser may never write directly.
LEDGER_DERIVED = ["balance", "booksRequired", "discountAmount", "totalAmount",
                  "closingBalance", "grossAmount", "gstAmount"]

# Vendor return (credit note) register - "Return to Vendor" section of the ledger.
VENDOR_RETURN_HEADERS = [
    "id", "school_id", "class_id", "ledger_id", "academicYear",
    "vendorId", "vendor", "vendorContact", "vendorGst",
    "creditNoteNo", "bookName", "subject", "publication", "edition",
    "quantity", "returnDate", "reason", "remarks",
    "created_by", "created_time",
]


def normalize_standard(value: str) -> str:
    """Maps free text ('Class 5-A', 'std vi', 'LKG') onto a catalog standard."""
    import re as _re
    t = _re.sub(r"\s+", " ", str(value or "")).strip().upper()
    if not t:
        return "OTHERS"
    if t in STANDARDS:
        return t
    t2 = t.replace("STANDARD", "").replace("CLASS", "").replace("STD", "").strip(" :-")
    if t2 in STANDARDS:
        return t2
    if "PRE KG" in t or "PREKG" in t or "PRE-KG" in t:
        return "PRE KG"
    if t2.startswith("LKG"):
        return "LKG"
    if t2.startswith("UKG"):
        return "UKG"
    m = _re.match(r"^(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b", t2)
    if m:
        return m.group(1)
    m = _re.search(r"\b(1[0-2]|[1-9])\b", t2)
    if m:
        roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
        return roman[int(m.group(1)) - 1]
    return "OTHERS"



# --- Per-school ledger storage ------------------------------------------------
# Every school keeps its own data folder (data/schools/school_<id>/) with its
# own ledger.csv, so one school's records can never bleed into another's.

def school_data_dir(school_id) -> str:
    path = os.path.join(SCHOOL_DATA_DIR, f"school_{school_id}")
    os.makedirs(path, exist_ok=True)
    return path


def school_ledger_path(school_id) -> str:
    return os.path.join(school_data_dir(school_id), "ledger.csv")


def read_ledger(school_id=None) -> List[Dict]:
    """Ledger rows for one school, or every school when school_id is None."""
    if school_id is not None:
        path = school_ledger_path(school_id)
        if not os.path.exists(path):
            atomic_csv_write(path, LEDGER_HEADERS, [])
            return []
        return read_csv(path)

    rows: List[Dict] = []
    if os.path.isdir(SCHOOL_DATA_DIR):
        for entry in sorted(os.listdir(SCHOOL_DATA_DIR)):
            path = os.path.join(SCHOOL_DATA_DIR, entry, "ledger.csv")
            if os.path.exists(path):
                rows.extend(read_csv(path))
    return rows


def write_ledger(school_id, rows: List[Dict]):
    atomic_csv_write(school_ledger_path(school_id), LEDGER_HEADERS, rows)


def write_all_ledger(rows: List[Dict]):
    """Rewrites every school ledger file from one combined row list."""
    grouped: Dict[str, List[Dict]] = {}
    for r in rows:
        grouped.setdefault(str(r.get("school_id", "")), []).append(r)
    known = set()
    if os.path.isdir(SCHOOL_DATA_DIR):
        for entry in os.listdir(SCHOOL_DATA_DIR):
            if entry.startswith("school_"):
                known.add(entry[len("school_"):])
    for sid in known | set(grouped.keys()):
        if sid == "":
            continue
        write_ledger(sid, grouped.get(sid, []))


def class_locked_standard(class_name: str) -> str:
    """A class whose name starts with a number (5-A, "Class 5-A", "10 B") is
    locked to that standard: only that class's data may live in its ledger.
    Names that do not start with a number (LKG, UKG, PRE KG, Others ...) are
    not locked and may hold any standard."""
    import re as _re
    t = _re.sub(r"\s+", " ", str(class_name or "")).strip().upper()
    t = _re.sub(r"^(CLASS|STD|STANDARD)\s*", "", t).strip(" :-")
    if _re.match(r"^\d", t):
        return normalize_standard(t)
    if _re.match(r"^(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b", t):
        return normalize_standard(t)
    return ""


def migrate_ledger_to_school_files():
    """One-time split of the old shared ledger.csv into per-school files."""
    if not os.path.exists(LEDGER_CSV):
        return
    rows = read_csv(LEDGER_CSV)
    if rows:
        existing = {str(r.get("id")) for r in read_ledger()}
        grouped: Dict[str, List[Dict]] = {}
        for r in rows:
            if str(r.get("id")) in existing:
                continue
            grouped.setdefault(str(r.get("school_id", "")), []).append(
                {h: r.get(h, "") for h in LEDGER_HEADERS})
        for sid, school_rows in grouped.items():
            if not sid:
                continue
            write_ledger(sid, read_ledger(sid) + school_rows)
    try:
        os.replace(LEDGER_CSV, LEDGER_CSV + ".migrated")
    except OSError:
        pass


# --- Role model -----------------------------------------------------------
# Hierarchy: super_admin > admin > staff > user
# super_admin : the single owner account. Every school, every account.
#               Cannot be deleted, disabled or demoted.
# admin       : the schools assigned to them by the Super Admin. Sees admins,
#               staff and users of those schools; may create all three.
# staff       : the schools assigned to them. Sees the user accounts of those
#               schools; may create user accounts only.
# user        : one school, read/write inside that school only. Cannot create
#               any account.
SUPER_ADMIN = "super_admin"
ADMIN = "admin"
STAFF = "staff"
USER = "user"
VALID_ROLES = (SUPER_ADMIN, ADMIN, STAFF, USER)

# Rank used for every "who may manage / see whom" decision.
ROLE_RANK = {USER: 0, STAFF: 1, ADMIN: 2, SUPER_ADMIN: 3}

ROLE_LABELS = {SUPER_ADMIN: "Super Admin", ADMIN: "Admin",
               STAFF: "Staff", USER: "User"}

# The built-in owner account. It is always the one and only Super Admin, can
# never be deleted, disabled or demoted, and no other account may hold the role.
PROTECTED_SUPER_ADMIN = "admin"


def is_protected_admin(username: str) -> bool:
    return str(username or "").strip().lower() == PROTECTED_SUPER_ADMIN


def role_rank(role: str) -> int:
    return ROLE_RANK.get(normalize_role(role), 0)


VALID_STATUSES = ("Active", "Pending", "Disabled")
SCHOOL_STATUSES = ("Active", "Inactive", "Archived")

# Legacy role names kept working so existing users.csv files still load.
ROLE_ALIASES = {
    "superadmin": SUPER_ADMIN,
    "super_admin": SUPER_ADMIN,
    "super admin": SUPER_ADMIN,
    "owner": SUPER_ADMIN,
    "admin": ADMIN,
    "administrator": ADMIN,
    "staff": STAFF,
    "school": USER,
    "user": USER,
}


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(str(role or "").strip().lower(), USER)


def init_db():
    if not os.path.exists(USERS_CSV):
        atomic_csv_write(USERS_CSV, USER_HEADERS, [{
            "username": "admin",
            "password_hash": hash_password("admin123"),
            "role": SUPER_ADMIN,
            "fullName": "System Administrator",
            "email": "",
            "school_id": "",
            "lastLogin": current_timestamp(),
            "status": "Active",
            "created_time": current_timestamp(),
            "created_by": "system",
        }])
    else:
        migrate_users_csv()

    if not os.path.exists(AUDIT_CSV):
        atomic_csv_write(AUDIT_CSV, AUDIT_HEADERS, [])

    if not os.path.exists(SCHOOLS_CSV):
        # The schools file starts empty; schools are created by a super admin.
        atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, [])
    else:
        migrate_schools_csv()

    migrate_ledger_csv()
    migrate_ledger_to_school_files()
    # Make sure every known school has its own ledger file.
    for s_row in read_csv(SCHOOLS_CSV):
        path = school_ledger_path(s_row.get("id"))
        if not os.path.exists(path):
            atomic_csv_write(path, LEDGER_HEADERS, [])

    if not os.path.exists(VENDORS_CSV):
        atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, [])
    else:
        migrate_csv_headers(VENDORS_CSV, VENDOR_HEADERS)
    if not os.path.exists(CATALOG_CSV):
        atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, [])
    else:
        migrate_csv_headers(CATALOG_CSV, CATALOG_HEADERS)
    seed_stationery_catalog()


def migrate_ledger_csv():
    """Upgrades an older ledger.csv to the full 22-column stock register,
    backfilling `standard` from the school's class name."""
    if not os.path.exists(LEDGER_CSV):
        return
    rows = read_csv(LEDGER_CSV)
    if not rows:
        return
    if all(h in rows[0] for h in LEDGER_HEADERS):
        return

    # class_id -> class name lookup, per school, for the standard backfill.
    class_names = {}
    try:
        for s in read_csv(SCHOOLS_CSV):
            for c in json.loads(s.get("classes_json", "[]") or "[]"):
                class_names[(str(s.get("id")), str(c.get("id")))] = c.get("name", "")
    except Exception:
        pass

    upgraded = []
    for r in rows:
        row = {h: r.get(h, "") for h in LEDGER_HEADERS}
        if not row.get("standard"):
            row["standard"] = normalize_standard(
                class_names.get((str(r.get("school_id")), str(r.get("class_id"))), "")
            )
        if not row.get("openingBalance"):
            row["openingBalance"] = "0"
        for money in ["approvedRate", "baseRate", "gstAmount", "discountPercent",
                      "discountAmount", "totalAmount"]:
            if not row.get(money):
                row[money] = "0"
        row["closingBalance"] = str(
            _num(row.get("openingBalance")) + _num(row.get("purchased"))
            - _num(row.get("distributed")) - _num(row.get("returned"))
        )
        upgraded.append(row)
    atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, upgraded)


def migrate_csv_headers(path: str, headers: List[str]):
    """Adds newly introduced columns to an existing CSV without losing data."""
    rows = read_csv(path)
    if rows and all(h in rows[0] for h in headers):
        return
    atomic_csv_write(path, headers, [{h: r.get(h, "") for h in headers} for r in rows])


def seed_stationery_catalog():
    """Makes sure the default Stationery & Office Supplies list exists.
    Custom items added by an administrator are never touched."""
    rows = read_csv(CATALOG_CSV)
    have = {str(r.get("title", "")).strip().upper() for r in rows
            if str(r.get("category", "")).upper() == "STATIONERY"}
    added = False
    for item in STATIONERY_ITEMS:
        if item.strip().upper() in have:
            continue
        rows.append({
            "standard": "OTHERS", "category": "STATIONERY", "subject": "",
            "title": item, "publication": "", "default_qty_per_student": "",
            "edition": "", "academic_year": "", "language": "", "isbn": "",
            "approved_rate": "", "status": "Active",
        })
        added = True
    if added:
        atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, rows)


# --- Inventory status ---------------------------------------------------------
def stock_status(closing_balance) -> str:
    """Green / orange / red inventory badge for a closing balance."""
    qty = _num(closing_balance)
    if qty <= 0:
        return "Out of Stock"
    if qty <= LOW_STOCK_LIMIT:
        return "Low Stock"
    return "Available"


def inventory_snapshot(school_id=None) -> List[Dict]:
    """One row per article with quantities and the inventory status badge."""
    agg: Dict[tuple, Dict] = {}
    for r in read_ledger(school_id):
        key = (str(r.get("school_id")), str(r.get("bookName", "")).strip().upper(),
               str(r.get("category", "")).upper())
        row = agg.setdefault(key, {
            "school_id": r.get("school_id"), "bookName": r.get("bookName", ""),
            "category": r.get("category", ""), "subject": r.get("subject", ""),
            "publication": r.get("publication", ""), "standard": r.get("standard", ""),
            "openingBalance": 0, "purchased": 0, "distributed": 0,
            "returned": 0, "closingBalance": 0,
        })
        for f in ("openingBalance", "purchased", "distributed", "returned", "closingBalance"):
            row[f] += _num(r.get(f))
    out = []
    for row in agg.values():
        row["status"] = stock_status(row["closingBalance"])
        out.append(row)
    out.sort(key=lambda r: (r["status"] != "Out of Stock", r["status"] != "Low Stock",
                            str(r["bookName"])))
    return out


# --- Catalog and vendor masters ---------------------------------------------
def get_catalog(standard: str = "", category: str = "") -> List[Dict]:
    """Master list of titles / notebook categories seeded from the school's
    Text Book & Note Book requirement sheets. standard='ALL' returns everything."""
    if not os.path.exists(CATALOG_CSV):
        return []
    rows = read_csv(CATALOG_CSV)
    std = str(standard or "").strip().upper()
    cat = str(category or "").strip().upper()
    if std and std != "ALL":
        std = normalize_standard(std)
        rows = [r for r in rows if str(r.get("standard", "")).upper() == std]
    if cat and cat != "ALL":
        rows = [r for r in rows if str(r.get("category", "")).upper() == cat]
    return rows


def get_catalog_standards() -> List[str]:
    return list(STANDARDS)


def get_article_categories() -> List[str]:
    return list(ARTICLE_CATEGORIES)


def get_catalog_publications() -> List[str]:
    pubs = {str(r.get("publication", "")).strip() for r in get_catalog()}
    return sorted(p for p in pubs if p)


def get_vendors() -> List[Dict]:
    if not os.path.exists(VENDORS_CSV):
        return []
    return read_csv(VENDORS_CSV)


def get_vendor(vendor_id: str) -> Optional[Dict]:
    key = str(vendor_id or "").strip()
    for v in get_vendors():
        if str(v.get("vendorId")) == key or str(v.get("name")) == key:
            return v
    return None


def next_vendor_id() -> str:
    nums = []
    for v in get_vendors():
        m = str(v.get("vendorId", ""))
        if m.upper().startswith("V") and m[1:].isdigit():
            nums.append(int(m[1:]))
    return "V%03d" % ((max(nums) + 1) if nums else 1)


def save_vendor(data: Dict) -> Dict:
    """Creates or updates a full vendor record (Vendor Management module)."""
    vendors = get_vendors()
    vid = str(data.get("vendorId", "")).strip() or next_vendor_id()
    record = {h: str(data.get(h, "") or "") for h in VENDOR_HEADERS}
    record["vendorId"] = vid
    record["status"] = record["status"] or "Active"
    for i, v in enumerate(vendors):
        if str(v.get("vendorId")) == vid:
            merged = dict(v)
            merged.update({k: val for k, val in record.items() if val != "" or k in ("status",)})
            vendors[i] = {h: merged.get(h, "") for h in VENDOR_HEADERS}
            atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, vendors)
            return vendors[i]
    vendors.append(record)
    atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, vendors)
    return record


def delete_vendor(vendor_id: str) -> bool:
    vendors = get_vendors()
    kept = [v for v in vendors if str(v.get("vendorId")) != str(vendor_id)]
    if len(kept) == len(vendors):
        return False
    atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, kept)
    return True


# --- Catalog write operations -------------------------------------------------
def save_catalog_item(data: Dict, original_title: str = "") -> Dict:
    """Adds or updates a catalog record (textbook, notebook, in-house or
    custom stationery item)."""
    rows = read_csv(CATALOG_CSV)
    record = {h: str(data.get(h, "") or "") for h in CATALOG_HEADERS}
    record["standard"] = normalize_standard(record["standard"] or "OTHERS")
    record["category"] = (record["category"] or "STATIONERY").upper()
    record["status"] = record["status"] or "Active"
    if not record["title"].strip():
        raise ValueError("Title is required.")
    key = (original_title or record["title"]).strip().upper()
    for i, r in enumerate(rows):
        if (str(r.get("title", "")).strip().upper() == key
                and str(r.get("category", "")).upper() == record["category"]
                and str(r.get("standard", "")).upper() == record["standard"]):
            rows[i] = record
            atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, rows)
            return record
    rows.append(record)
    atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, rows)
    return record


def delete_catalog_item(title: str, category: str = "", standard: str = "") -> bool:
    rows = read_csv(CATALOG_CSV)
    key = str(title or "").strip().upper()
    kept = [r for r in rows if not (
        str(r.get("title", "")).strip().upper() == key
        and (not category or str(r.get("category", "")).upper() == category.upper())
        and (not standard or str(r.get("standard", "")).upper() == normalize_standard(standard))
    )]
    if len(kept) == len(rows):
        return False
    atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, kept)
    return True


def catalog_option_lists() -> Dict[str, List[str]]:
    """Distinct values powering the searchable dropdowns."""
    rows = read_csv(CATALOG_CSV)
    def uniq(field):
        return sorted({str(r.get(field, "")).strip() for r in rows if str(r.get(field, "")).strip()})
    return {
        "subjects": uniq("subject"),
        "publications": uniq("publication"),
        "editions": uniq("edition"),
        "academic_years": uniq("academic_year"),
        "languages": uniq("language"),
        "categories": list(ARTICLE_CATEGORIES),
        "standards": list(STANDARDS),
    }


def upsert_vendor(vendor_id: str, name: str, contact: str = "", gst: str = ""):
    """Keeps the vendor master in step with what is typed into the ledger."""
    vendor_id = str(vendor_id or "").strip()
    name = str(name or "").strip()
    if not vendor_id and not name:
        return
    vendors = get_vendors()
    key = vendor_id or name
    for v in vendors:
        if (v.get("vendorId") or v.get("name")) == key:
            if name:
                v["name"] = name
            if contact:
                v["contact"] = contact
            if gst:
                v["gst"] = gst
            atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, vendors)
            return
    vendors.append({"vendorId": vendor_id, "name": name, "contact": contact, "gst": gst})
    atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, vendors)



def migrate_users_csv():
    """Upgrades an older users.csv (username,password_hash,role,lastLogin,status)
    to the extended schema without losing any existing accounts."""
    rows = read_csv(USERS_CSV)
    if not rows:
        return
    needs_role_fix = any(r.get("role") not in VALID_ROLES for r in rows)
    # There must always be exactly one Super Admin: the built-in owner account.
    supers = [r for r in rows if normalize_role(r.get("role", "")) == SUPER_ADMIN]
    needs_owner_fix = (len(supers) != 1
                       or not is_protected_admin(supers[0].get("username", "")))
    if all(h in rows[0] for h in USER_HEADERS) and not needs_role_fix and not needs_owner_fix:
        return
    upgraded = []
    for r in rows:
        upgraded.append({
            "username": r.get("username", ""),
            "password_hash": r.get("password_hash", ""),
            "role": normalize_role(r.get("role", "")),
            "fullName": r.get("fullName", "") or r.get("username", ""),
            "email": r.get("email", ""),
            "school_id": r.get("school_id", ""),
            "lastLogin": r.get("lastLogin", ""),
            "status": r.get("status", "Active") or "Active",
            "created_time": r.get("created_time", "") or current_timestamp(),
            "created_by": r.get("created_by", "") or "system",
        })
    # Enforce the single-Super-Admin rule: the built-in 'admin' account owns the
    # role, every other Super Admin row is downgraded to Admin.
    for r in upgraded:
        if is_protected_admin(r["username"]):
            r["role"] = SUPER_ADMIN
            r["status"] = "Active"
        elif r["role"] == SUPER_ADMIN:
            r["role"] = ADMIN
    atomic_csv_write(USERS_CSV, USER_HEADERS, upgraded)


def migrate_schools_csv():
    """Adds the multi-school columns (logo, address, contact, academic year,
    status, assigned staff, settings) to an older schools.csv."""
    rows = read_csv(SCHOOLS_CSV)
    if not rows:
        return
    if all(h in rows[0] for h in SCHOOL_HEADERS):
        return
    upgraded = []
    for r in rows:
        upgraded.append({
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "code": r.get("code", ""),
            "location": r.get("location", ""),
            "logo": r.get("logo", ""),
            "address": r.get("address", "") or r.get("location", ""),
            "contact": r.get("contact", ""),
            "academic_year": r.get("academic_year", ""),
            "status": r.get("status", "") or "Active",
            "assigned_staff": r.get("assigned_staff", ""),
            "settings_json": r.get("settings_json", "") or "{}",
            "classes_json": r.get("classes_json", "[]") or "[]",
        })
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, upgraded)


def read_csv(filepath: str) -> List[Dict[str, Any]]:
    """Cached CSV read. Every write path goes through atomic_csv_write, which
    drops the matching cache key, so callers never observe stale rows."""
    key = "file:" + filepath
    hit = cache.get(key)
    if hit is not None:
        return [dict(r) for r in hit]
    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []
    cache.set(key, rows, ttl=10)
    return [dict(r) for r in rows]


def get_audit_log_page(limit: int = 50, offset: int = 0, username: str = "",
                       action: str = "") -> Dict[str, Any]:
    """Paginated activity log — avoids shipping the whole file to the browser."""
    rows = get_audit_log(limit=100000, username=username, action=action)
    total = len(rows)
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    return {"items": rows[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset, "has_more": offset + limit < total}


# --- Audit Trail ---------------------------------------------------------
# Messaging is private. Nothing about conversations or message bodies is ever
# written to the activity log, and any legacy rows are hidden when reading.
MESSAGING_ACTIONS = {
    "CONVERSATION_CREATE", "CONVERSATION_DELETE", "MESSAGE_SEND",
    "MESSAGE_DELETE", "MESSAGE_READ", "ANNOUNCEMENT",
}
MESSAGING_ENTITIES = {"conversation", "message", "messaging", "announcement"}


def is_messaging_audit_row(row: Dict) -> bool:
    return (str(row.get("action", "")).upper() in MESSAGING_ACTIONS
            or str(row.get("entity", "")).lower() in MESSAGING_ENTITIES)


def log_action(username: str, role: str, action: str, entity: str = "", entity_id: str = "", details: str = ""):
    """Appends a single immutable-ish row to the activity log."""
    if is_messaging_audit_row({"action": action, "entity": entity}):
        return  # message activity is never logged
    try:
        rows = read_csv(AUDIT_CSV)
        rows.append({
            "id": f"A_{uuid.uuid4().hex[:10]}",
            "timestamp": current_timestamp(),
            "username": username or "anonymous",
            "role": role or "",
            "action": action,
            "entity": entity,
            "entity_id": str(entity_id),
            "details": details,
        })
        # Bound the log size so the CSV does not grow without limit.
        if len(rows) > 20000:
            rows = rows[-20000:]
        atomic_csv_write(AUDIT_CSV, AUDIT_HEADERS, rows)
    except Exception as e:
        print(f"[audit] failed to write log entry: {e}")


def get_audit_log(limit: int = 200, username: str = "", action: str = "", school_id: str = "") -> List[Dict]:
    rows = [r for r in read_csv(AUDIT_CSV) if not is_messaging_audit_row(r)]
    if username:
        rows = [r for r in rows if r.get("username", "").lower() == username.lower()]
    if action:
        rows = [r for r in rows if r.get("action", "") == action]
    if school_id:
        rows = [r for r in rows if str(r.get("entity_id", "")).startswith(str(school_id) + ":")
                or str(r.get("entity_id", "")) == str(school_id)]
    rows.reverse()  # newest first
    return rows[:max(1, min(limit, 2000))]


# --- Users Logic ---------------------------------------------------------
def _sanitize_user(u: Dict) -> Dict:
    online = {name.lower() for name in presence_snapshot().get("usernames", [])}
    return {
        "username": u.get("username", ""),
        "role": normalize_role(u.get("role", "")),
        "fullName": u.get("fullName", ""),
        "email": u.get("email", ""),
        "school_id": u.get("school_id", ""),
        "school_name": _school_name(u.get("school_id", "")),
        "lastLogin": u.get("lastLogin", ""),
        "status": u.get("status", ""),
        "online": u.get("username", "").lower() in online,
        "created_time": u.get("created_time", ""),
        "created_by": u.get("created_by", ""),
    }


def _school_name(school_id) -> str:
    if not school_id:
        return ""
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id):
            return s["name"]
    return ""


def get_user_raw(username: str) -> Optional[Dict]:
    for u in read_csv(USERS_CSV):
        if u['username'].lower() == str(username).lower():
            return u
    return None


def get_user_by_username(username: str) -> Optional[Dict]:
    """Only returns an account that is allowed to authenticate."""
    u = get_user_raw(username)
    if u and u.get('status') == 'Active':
        return u
    return None


def get_all_users() -> List[Dict]:
    return [_sanitize_user(u) for u in read_csv(USERS_CSV)]


def get_user_profile(username: str) -> Optional[Dict]:
    u = get_user_raw(username)
    return _sanitize_user(u) if u else None


def _write_users(users: List[Dict]):
    atomic_csv_write(USERS_CSV, USER_HEADERS, users)


def update_user_login(username: str):
    users = read_csv(USERS_CSV)
    for u in users:
        if u['username'] == username:
            u['lastLogin'] = current_timestamp()
    _write_users(users)


def create_user(username: str, password: str, role: str, full_name: str = "", email: str = "",
                school_id: str = "", status: str = "Active", created_by: str = "system") -> Dict:
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters long.")
    if any(c in username for c in " ,\t\n"):
        raise ValueError("Username cannot contain spaces or commas.")
    role = normalize_role(role)
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}.")
    if role == SUPER_ADMIN and not is_protected_admin(username):
        raise ValueError("Super Admin is reserved for the built-in 'admin' account. "
                         "Create this account as an Admin instead.")
    if status not in VALID_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(VALID_STATUSES)}.")
    if get_user_raw(username):
        raise ValueError(f"Username '{username}' is already taken.")
    if school_id and not _school_name(school_id):
        raise ValueError("Selected school does not exist.")

    users = read_csv(USERS_CSV)
    record = {
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "fullName": (full_name or "").strip(),
        "email": (email or "").strip(),
        "school_id": str(school_id or ""),
        "lastLogin": "",
        "status": status,
        "created_time": current_timestamp(),
        "created_by": created_by,
    }
    users.append(record)
    _write_users(users)
    return _sanitize_user(record)


def update_user(username: str, role: str = None, status: str = None, full_name: str = None,
                email: str = None, school_id: str = None) -> Dict:
    users = read_csv(USERS_CSV)
    target = next((u for u in users if u['username'].lower() == str(username).lower()), None)
    if not target:
        raise ValueError("User not found.")

    if role is not None:
        role = normalize_role(role)
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}.")
        if is_protected_admin(username) and role != SUPER_ADMIN:
            raise ValueError("The built-in 'admin' account must stay Super Admin.")
        if role == SUPER_ADMIN and not is_protected_admin(username):
            raise ValueError("Super Admin is reserved for the built-in 'admin' account.")
        if target['role'] == SUPER_ADMIN and role != SUPER_ADMIN and _count_active_admins(users) <= 1:
            raise ValueError("Cannot remove the last remaining administrator.")
        target['role'] = role
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(VALID_STATUSES)}.")
        if is_protected_admin(username) and status != 'Active':
            raise ValueError("The built-in 'admin' account cannot be disabled.")
        if target['role'] == SUPER_ADMIN and status != 'Active' and _count_active_admins(users) <= 1:
            raise ValueError("Cannot disable the last remaining administrator.")
        target['status'] = status
    if full_name is not None:
        target['fullName'] = full_name.strip()
    if email is not None:
        target['email'] = email.strip()
    if school_id is not None:
        if school_id and not _school_name(school_id):
            raise ValueError("Selected school does not exist.")
        target['school_id'] = str(school_id)

    _write_users(users)
    return _sanitize_user(target)


def _count_active_admins(users: List[Dict]) -> int:
    return len([u for u in users if normalize_role(u.get('role')) == SUPER_ADMIN and u.get('status') == 'Active'])


def set_password(username: str, new_password: str):
    users = read_csv(USERS_CSV)
    target = next((u for u in users if u['username'].lower() == str(username).lower()), None)
    if not target:
        raise ValueError("User not found.")
    target['password_hash'] = hash_password(new_password)
    _write_users(users)


def delete_user(username: str):
    users = read_csv(USERS_CSV)
    target = next((u for u in users if u['username'].lower() == str(username).lower()), None)
    if not target:
        raise ValueError("User not found.")
    if is_protected_admin(username):
        raise ValueError("The built-in 'admin' account cannot be deleted.")
    if normalize_role(target.get('role')) == SUPER_ADMIN and _count_active_admins(users) <= 1:
        raise ValueError("Cannot delete the last remaining administrator.")
    users = [u for u in users if u['username'].lower() != str(username).lower()]
    _write_users(users)


# --- Schools & Classes Logic ---------------------------------------------
def _staff_list(raw: str) -> List[str]:
    """assigned_staff is stored as a simple pipe-separated username list."""
    return [p.strip() for p in str(raw or "").split("|") if p.strip()]


def _staff_raw(names: List[str]) -> str:
    seen, out = set(), []
    for n in names:
        key = str(n or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(n).strip())
    return "|".join(out)


def _school_public(s: Dict) -> Dict:
    try:
        settings = json.loads(s.get("settings_json") or "{}")
    except Exception:
        settings = {}
    return {
        "id": int(s["id"]),
        "name": s.get("name", ""),
        "code": s.get("code", ""),
        "location": s.get("location", ""),
        "logo": s.get("logo", ""),
        "address": s.get("address", ""),
        "contact": s.get("contact", ""),
        "academic_year": s.get("academic_year", ""),
        "status": s.get("status", "") or "Active",
        "assigned_staff": _staff_list(s.get("assigned_staff", "")),
        "settings": settings if isinstance(settings, dict) else {},
    }


def get_all_schools(school_id_filter: str = "", allowed_ids: Optional[List[str]] = None) -> List[Dict]:
    """Returns schools visible to the caller.

    school_id_filter -- single-school scope (a 'user' account).
    allowed_ids      -- explicit whitelist (a 'staff' account); None means no limit.
    """
    rows = read_csv(SCHOOLS_CSV)
    if school_id_filter:
        rows = [s for s in rows if str(s["id"]) == str(school_id_filter)]
    if allowed_ids is not None:
        wanted = {str(i) for i in allowed_ids}
        rows = [s for s in rows if str(s["id"]) in wanted]
    return [_school_public(s) for s in rows]


def get_school(school_id) -> Optional[Dict]:
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id):
            return _school_public(s)
    return None


def school_ids_for_staff(username: str) -> List[str]:
    """Every school this staff account manages: the school on their user record
    plus any school that lists them in assigned_staff."""
    ids = []
    user = get_user_raw(username)
    own = str((user or {}).get("school_id", "") or "")
    if own:
        ids.append(own)
    uname = str(username or "").strip().lower()
    for s in read_csv(SCHOOLS_CSV):
        if uname in [n.lower() for n in _staff_list(s.get("assigned_staff", ""))]:
            ids.append(str(s["id"]))
    return sorted(set(ids), key=lambda v: int(v) if str(v).isdigit() else 0)


def get_classes_for_school(school_id: int) -> List[Dict]:
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id):
            return json.loads(s.get("classes_json", "[]") or "[]")
    return []


def add_school(name: str, code: str = "", location: str = "", logo: str = "", address: str = "",
               contact: str = "", academic_year: str = "", status: str = "Active",
               assigned_staff: Optional[List[str]] = None,
               settings: Optional[Dict[str, Any]] = None) -> Dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("School name is required.")
    status = (status or "Active").strip() or "Active"
    if status not in SCHOOL_STATUSES:
        raise ValueError(f"School status must be one of: {', '.join(SCHOOL_STATUSES)}.")
    schools = read_csv(SCHOOLS_CSV)
    for s in schools:
        if s["name"].strip().lower() == name.lower():
            raise ValueError(f"School '{name}' already exists.")
    new_id = str(max([int(s["id"]) for s in schools] + [0]) + 1)
    record = {
        "id": new_id,
        "name": name,
        "code": (code or "").strip(),
        "location": (location or "").strip(),
        "logo": (logo or "").strip(),
        "address": (address or "").strip(),
        "contact": (contact or "").strip(),
        "academic_year": (academic_year or "").strip(),
        "status": status,
        "assigned_staff": _staff_raw(assigned_staff or []),
        "settings_json": json.dumps(settings or {}),
        "classes_json": "[]",
    }
    schools.append(record)
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return _school_public(record)


def update_school(school_id, **fields) -> Dict:
    """Updates any School attribute. Only supplied (non-None) fields change."""
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target:
        raise ValueError("School not found.")

    name = fields.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("School name cannot be empty.")
        if any(s["name"].strip().lower() == name.lower() and str(s["id"]) != str(school_id) for s in schools):
            raise ValueError(f"School '{name}' already exists.")
        target["name"] = name

    for key in ("code", "location", "logo", "address", "contact", "academic_year"):
        if fields.get(key) is not None:
            target[key] = str(fields[key]).strip()

    if fields.get("status") is not None:
        status = str(fields["status"]).strip() or "Active"
        if status not in SCHOOL_STATUSES:
            raise ValueError(f"School status must be one of: {', '.join(SCHOOL_STATUSES)}.")
        target["status"] = status

    if fields.get("assigned_staff") is not None:
        names = fields["assigned_staff"]
        if isinstance(names, str):
            names = [n for n in names.replace(",", "|").split("|")]
        known = {u["username"].lower(): u["username"] for u in read_csv(USERS_CSV)}
        cleaned = []
        for n in names:
            key = str(n or "").strip().lower()
            if not key:
                continue
            if key not in known:
                raise ValueError(f"Staff account '{n}' does not exist.")
            cleaned.append(known[key])
        target["assigned_staff"] = _staff_raw(cleaned)

    if fields.get("settings") is not None:
        settings = fields["settings"]
        if not isinstance(settings, dict):
            raise ValueError("School settings must be an object.")
        target["settings_json"] = json.dumps(settings)

    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return _school_public(target)


def delete_school(school_id: int):
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target:
        raise ValueError("School not found.")
    if json.loads(target.get("classes_json", "[]") or "[]"):
        raise ValueError("Cannot delete school: It contains active classes. Please delete classes first to prevent orphaned records.")
    if any(str(u.get("school_id", "")) == str(school_id) for u in read_csv(USERS_CSV)):
        raise ValueError("Cannot delete school: accounts are still assigned to it. Reassign those accounts first.")
    schools = [s for s in schools if str(s["id"]) != str(school_id)]
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)


def add_class_to_school(school_id: int, name: str, strength: int) -> Dict:
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target: 
        raise ValueError("School not found.")
    classes = json.loads(target.get("classes_json", "[]"))
    if any(c["name"].lower() == name.lower() for c in classes): 
        raise ValueError("Class already exists.")
    new_class = {"id": max([int(c["id"]) for c in classes] + [0]) + 1, "name": name, "strength": strength}
    classes.append(new_class)
    target["classes_json"] = json.dumps(classes)
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return new_class

def delete_class(school_id: int, class_id: int):
    ledger = read_ledger(school_id)
    if any(str(r["class_id"]) == str(class_id) for r in ledger):
        raise ValueError("Cannot delete class: It contains existing ledger records. Preserve data integrity by clearing the ledger first.")
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if target:
        classes = json.loads(target.get("classes_json", "[]"))
        target["classes_json"] = json.dumps([c for c in classes if str(c["id"]) != str(class_id)])
        atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)

def update_class_strength(school_id: int, class_id: int, new_strength: int):
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target: 
        raise ValueError("School not found.")
    
    classes = json.loads(target.get("classes_json", "[]"))
    class_updated = False
    
    for c in classes:
        if str(c["id"]) == str(class_id):
            c["strength"] = new_strength
            class_updated = True
            break
            
    if not class_updated: 
        raise ValueError("Class not found.")
        
    target["classes_json"] = json.dumps(classes)
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)

# --- Ledger Logic --------------------------------------------------------
LEDGER_INT_FIELDS = ['purchased', 'distributed', 'returned', 'balance', 'booksRequired',
                     'strength', 'openingBalance', 'closingBalance']
LEDGER_FLOAT_FIELDS = ['approvedRate', 'baseRate', 'gstPercent', 'gstAmount', 'discountPercent',
                       'discountAmount', 'grossAmount', 'totalAmount']


def _dec(v) -> float:
    try:
        return round(float(str(v).replace(",", "").strip() or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _cast_ledger_row(r: Dict) -> Dict:
    for f in LEDGER_INT_FIELDS:
        r[f] = _num(r.get(f))
    for f in LEDGER_FLOAT_FIELDS:
        r[f] = _dec(r.get(f))
    r["standard"] = str(r.get("standard") or "OTHERS")
    return r


def get_ledger_records(school_id: int, class_id: int, created_by: str = "") -> List[Dict]:
    """created_by narrows the rows to the ones a plain 'user' account owns."""
    ledger = read_ledger(school_id)
    result = [r for r in ledger if str(r["class_id"]) == str(class_id)]
    if created_by:
        result = [r for r in result if str(r.get("created_by", "")).lower() == created_by.lower()]
    return [_cast_ledger_row(r) for r in result]


def get_ledger_by_standard(school_id: int, standard: str, created_by: str = "") -> List[Dict]:
    """Rows for one standard, or every standard when standard is 'ALL'.
    Ordered by standard so the 'All standards' view groups naturally."""
    result = read_ledger(school_id)
    std = str(standard or "").strip().upper()
    if std and std != "ALL":
        std = normalize_standard(std)
        result = [r for r in result if normalize_standard(r.get("standard", "")) == std]
    if created_by:
        result = [r for r in result if str(r.get("created_by", "")).lower() == created_by.lower()]
    result = [_cast_ledger_row(r) for r in result]
    order = {s: i for i, s in enumerate(STANDARDS)}
    result.sort(key=lambda r: (order.get(r.get("standard"), 99), str(r.get("category", "")),
                               str(r.get("bookName", ""))))
    return result


def standard_strength(school_id: int, standard: str) -> int:
    """Total student strength recorded for a standard, summed across its
    sections (Class 5-A, Class 5-B ... all map to standard V)."""
    std = normalize_standard(standard)
    total = 0
    for c in get_classes_for_school(school_id):
        if normalize_standard(c.get("name", "")) == std:
            total += _num(c.get("strength"))
    return total


def rows_not_owned_by(record_ids: List[str], username: str) -> List[str]:
    """Ledger row ids in the list that were not created by this username."""
    wanted = {str(i) for i in record_ids}
    owned_check = []
    for r in read_ledger():
        if str(r.get("id")) in wanted and str(r.get("created_by", "")).lower() != str(username).lower():
            owned_check.append(str(r.get("id")))
    return owned_check


def sync_ledger_records(school_id: int, class_id: int, updates: List[Dict], deletes: List[str],
                        username: str, standard: str = ""):
    """Persists ledger edits for ONE class of ONE school. Rows are written to
    that school's own ledger file and tagged with the class, so each class keeps
    its own data. A class named after a number (5-A) accepts only that standard."""
    target_class = next((c for c in get_classes_for_school(school_id) if str(c["id"]) == str(class_id)), None)
    if not target_class:
        raise ValueError("Class not found.")

    locked_standard = class_locked_standard(target_class.get("name", ""))
    default_standard = locked_standard or (normalize_standard(standard) if standard else "") \
        or normalize_standard(target_class.get("name", ""))
    default_class_strength = _num(target_class.get("strength"))

    ledger = read_ledger(school_id)
    delete_set = set(deletes)
    deleted_names = [r.get("bookName", r.get("id")) for r in ledger if str(r["id"]) in delete_set]
    ledger = [row for row in ledger if str(row["id"]) not in delete_set]
    update_dict = {str(u["id"]): u for u in updates}

    changed_books = []
    touched_vendors = []

    # Validate the submitted row modifications.
    def process_row(row_data, incoming_mod):
        protected = ['id', 'school_id', 'class_id', 'created_by', 'created_time'] + LEDGER_DERIVED
        for key in LEDGER_HEADERS:
            if key in incoming_mod and key not in protected:
                row_data[key] = incoming_mod[key]

        name = row_data.get('bookName') or 'Unknown'
        row_data["standard"] = normalize_standard(row_data.get("standard") or default_standard)
        if locked_standard and row_data["standard"] != locked_standard:
            raise ValueError(
                f"'{target_class.get('name')}' only accepts Class {locked_standard} data — "
                f"'{name}' is marked as {row_data['standard']}.")
        cat = str(row_data.get("category") or "").strip().upper()
        if cat and cat not in ARTICLE_CATEGORIES:
            # free-text categories are allowed, they are just not normalised
            cat = str(row_data.get("category")).strip()
        row_data["category"] = cat

        p = _num(row_data.get("purchased"))
        d = _num(row_data.get("distributed"))
        r = _num(row_data.get("returned"))
        ob = _num(row_data.get("openingBalance"))

        row_str = row_data.get("strength")
        if row_str is not None and str(row_str).strip().isdigit():
            row_strength = int(row_str)
        else:
            row_strength = default_class_strength

        if min(p, d, r, ob, row_strength) < 0:
            raise ValueError(f"Stock quantities and strength cannot be negative for '{name}'.")

        if d > ob + p:
            raise ValueError(
                f"Quantity issued ({d}) for '{name}' exceeds the available stock "
                f"({ob + p} = opening {ob} + purchased {p}).")

        balance = calculate_balance(p, d, r)
        if balance < 0:
            raise ValueError(f"Transaction rejected: Distributing {d} books when only {p+r} are available creates a negative balance for '{name}'.")

        closing = ob + p - d - r
        if closing < 0:
            raise ValueError(f"Closing balance for '{name}' would be negative: opening {ob} + purchased {p} cannot cover {d} issued and {r} returned.")

        base = _dec(row_data.get("baseRate"))
        gst_pct = _dec(row_data.get("gstPercent"))
        disc_pct = _dec(row_data.get("discountPercent"))
        if disc_pct < 0 or disc_pct > 100:
            raise ValueError(f"Discount % for '{name}' must be between 0 and 100.")
        if gst_pct < 0 or gst_pct > 100:
            raise ValueError(f"GST % for '{name}' must be between 0 and 100.")
        if base < 0:
            raise ValueError(f"Base rate for '{name}' cannot be negative.")
        if p > 0 and base <= 0:
            raise ValueError(f"Base rate for '{name}' must be greater than zero when articles are purchased.")

        # Duplicate invoice numbers are not allowed for the same vendor.
        inv = str(row_data.get("invoiceRef") or "").strip().upper()
        vend_key = (str(row_data.get("vendorId") or "").strip().upper()
                    or str(row_data.get("vendor") or "").strip().upper())
        if inv and vend_key:
            for other in ledger:
                if str(other.get("id")) == str(row_data.get("id")):
                    continue
                o_inv = str(other.get("invoiceRef") or "").strip().upper()
                o_vend = (str(other.get("vendorId") or "").strip().upper()
                          or str(other.get("vendor") or "").strip().upper())
                if o_inv == inv and o_vend == vend_key:
                    raise ValueError(
                        f"Invoice '{row_data.get('invoiceRef')}' already exists for vendor "
                        f"'{row_data.get('vendor') or vend_key}'. Duplicate invoice numbers are not allowed.")

        qty = p if p > 0 else 1
        gross_amount = round(base * qty, 2)
        gst_amount = round(gross_amount * gst_pct / 100, 2) if gst_pct else _dec(row_data.get("gstAmount"))
        discount_amount = round(gross_amount * disc_pct / 100, 2)
        # Business rule as specified: Total = Base Rate + GST Amount - Discount Amount
        total_amount = round(base + gst_amount - discount_amount, 2)

        row_data["strength"] = str(row_strength)
        row_data["openingBalance"] = str(ob)
        row_data["balance"] = str(balance)
        row_data["closingBalance"] = str(closing)
        row_data["booksRequired"] = str(calculate_books_required(row_strength, p))
        row_data["approvedRate"] = str(_dec(row_data.get("approvedRate")))
        row_data["baseRate"] = str(base)
        row_data["gstPercent"] = str(gst_pct)
        row_data["gstAmount"] = str(gst_amount)
        row_data["grossAmount"] = str(gross_amount)
        row_data["discountPercent"] = str(disc_pct)
        row_data["academicYear"] = str(row_data.get("academicYear") or "").strip()
        row_data["discountAmount"] = str(discount_amount)
        row_data["totalAmount"] = str(total_amount)
        row_data["modified_by"] = username
        row_data["modified_time"] = current_timestamp()

        if row_data.get("vendor") or row_data.get("vendorId"):
            touched_vendors.append((row_data.get("vendorId", ""), row_data.get("vendor", ""),
                                    row_data.get("vendorContact", ""), row_data.get("vendorGst", "")))

    # Apply updates to existing rows.
    for row in ledger:
        rid = str(row["id"])
        if rid in update_dict:
            process_row(row, update_dict[rid])
            changed_books.append(f"edited '{row.get('bookName', rid)}'")
            del update_dict[rid]

    # Append newly added rows.
    for uid, new_row in update_dict.items():
        if not str(uid).startswith("new_"):
            continue
        record = {h: "" for h in LEDGER_HEADERS}
        record.update({
            "id": f"L_{uuid.uuid4().hex[:8]}",
            "school_id": str(school_id),
            "class_id": str(class_id or ""),
            "created_by": username,
            "created_time": current_timestamp()
        })
        process_row(record, new_row)
        changed_books.append(f"added '{record.get('bookName', record['id'])}'")
        ledger.insert(0, record)

    write_ledger(school_id, ledger)

    for vid, vname, vcontact, vgst in touched_vendors:
        try:
            upsert_vendor(vid, vname, vcontact, vgst)
        except Exception:
            pass

    for name in deleted_names:
        changed_books.append(f"deleted '{name}'")
    return changed_books



# --- AI assistant settings (admin-managed, stored server-side) ----------------
AI_DEFAULTS = {"apiKey": "", "apiBase": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-flash-latest", "imageModel": "gemini-2.5-flash-image"}


GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


def normalize_ai_base(api_base: str, model: str = "") -> str:
    """Google's OpenAI-compatible endpoint lives under /v1beta/openai. A base that
    stops at /v1beta (or a Gemini model still pointed at the OpenAI default) makes
    Google answer 400 "Please pass a valid API key", so repair it here."""
    base = (api_base or "").strip().rstrip("/")
    model = (model or "").strip().lower()
    if model.startswith("gemini") and (not base or "api.openai.com" in base):
        return GEMINI_OPENAI_BASE
    if GEMINI_HOST in base and not base.endswith("/openai"):
        if "/openai/" in base + "/":
            return base
        base = base.split("/v1beta")[0] + "/v1beta/openai"
    return base or AI_DEFAULTS["apiBase"]


def ai_auth_headers(cfg: Dict[str, str]) -> Dict[str, str]:
    """Auth headers for the configured provider (Gemini also accepts x-goog-api-key)."""
    key = cfg.get("apiKey") or ""
    headers = {"Authorization": "Bearer " + key}
    if GEMINI_HOST in str(cfg.get("apiBase") or ""):
        headers["x-goog-api-key"] = key
    return headers


def get_ai_settings() -> Dict[str, str]:
    """Full settings including the secret key. Never return this to the browser."""
    data = dict(AI_DEFAULTS)
    try:
        with open(AI_SETTINGS_JSON, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for k in AI_DEFAULTS:
                if stored.get(k):
                    data[k] = str(stored[k])
    except Exception:
        pass
    data["apiBase"] = normalize_ai_base(data["apiBase"], data.get("model", ""))
    return data


def save_ai_settings(api_key: Optional[str], api_base: str, model: str,
                    image_model: str = "") -> Dict[str, str]:
    current = get_ai_settings()
    data = {
        # An empty apiKey means "keep the existing one" so the admin can edit
        # the base URL/model without retyping the secret.
        "apiKey": (api_key or "").strip() or current["apiKey"],
        "apiBase": normalize_ai_base(api_base, model),
        "model": (model or "").strip() or AI_DEFAULTS["model"],
        "imageModel": (image_model or "").strip() or current.get("imageModel")
                      or AI_DEFAULTS["imageModel"],
    }
    with open(AI_SETTINGS_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        os.chmod(AI_SETTINGS_JSON, 0o600)
    except Exception:
        pass
    return data


def clear_ai_settings() -> None:
    try:
        os.remove(AI_SETTINGS_JSON)
    except FileNotFoundError:
        pass


# --- Dashboard analytics -------------------------------------------------
LOW_STOCK_THRESHOLD = 50
_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _num(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def get_dashboard_stats(school_id_filter: str = "") -> Dict[str, Any]:
    """Real analytics computed from schools.csv, ledger.csv and audit.csv.
    No demo or placeholder numbers are used anywhere."""
    from datetime import datetime

    schools = read_csv(SCHOOLS_CSV)
    if school_id_filter:
        schools = [s for s in schools if str(s["id"]) == str(school_id_filter)]
    school_ids = {str(s["id"]) for s in schools}

    ledger = [r for r in read_ledger() if str(r.get("school_id")) in school_ids]

    total_purchased = sum(_num(r.get("purchased")) for r in ledger)
    total_distributed = sum(_num(r.get("distributed")) for r in ledger)
    total_returned = sum(_num(r.get("returned")) for r in ledger)
    total_balance = sum(_num(r.get("balance")) for r in ledger)
    total_required = sum(_num(r.get("booksRequired")) for r in ledger)
    low_rows = [r for r in ledger if _num(r.get("balance")) < LOW_STOCK_THRESHOLD]

    today = current_date()
    added_today = sum(_num(r.get("purchased")) for r in ledger
                      if str(r.get("created_time", "")).startswith(today))

    # ---- monthly trend (last 6 calendar months, from ledger timestamps) ----
    now = datetime.now()
    months = []
    y, m = now.year, now.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    buckets = {km: {"issued": 0, "received": 0} for km in months}
    for r in ledger:
        ts = str(r.get("modified_time") or r.get("created_time") or "")[:7]
        try:
            key = (int(ts[:4]), int(ts[5:7]))
        except ValueError:
            continue
        if key in buckets:
            buckets[key]["issued"] += _num(r.get("distributed"))
            buckets[key]["received"] += _num(r.get("purchased"))

    monthly = {
        "labels": [_MONTH_NAMES[m - 1] for (y, m) in months],
        "issued": [buckets[k]["issued"] for k in months],
        "received": [buckets[k]["received"] for k in months],
    }

    # ---- per-school rollups ----
    per_school = []
    for s in schools:
        sid = str(s["id"])
        rows = [r for r in ledger if str(r.get("school_id")) == sid]
        try:
            classes = json.loads(s.get("classes_json") or "[]")
        except (ValueError, TypeError):
            classes = []
        per_school.append({
            "id": _num(sid),
            "name": s.get("name", ""),
            "code": s.get("code", ""),
            "students": sum(_num(c.get("strength")) for c in classes),
            "purchased": sum(_num(r.get("purchased")) for r in rows),
            "issued": sum(_num(r.get("distributed")) for r in rows),
            "balance": sum(_num(r.get("balance")) for r in rows),
        })
    per_school.sort(key=lambda x: x["issued"], reverse=True)

    # ---- recent activity from the audit trail ----
    tone_for = {
        "LOGIN": "blue", "LOGOUT": "blue",
        "SCHOOL_CREATE": "blue", "SCHOOL_DELETE": "red",
        "CLASS_CREATE": "blue", "CLASS_DELETE": "red",
        "LEDGER_SYNC": "green", "LEDGER_DELETE": "red",
        "USER_CREATE": "blue", "USER_DELETE": "red",
    }
    activity = []
    for r in get_audit_log(limit=8, school_id=school_id_filter if school_id_filter else ""):
        activity.append({
            "tone": tone_for.get(r.get("action", ""), "amber"),
            "text": r.get("details") or r.get("action", ""),
            "meta": f"{r.get('timestamp', '')} · {r.get('username', '')}",
        })

    return {
        "kpis": {
            "books": {"value": total_purchased,
                      "delta": f"+{added_today} added today" if added_today else "no additions today"},
            "schools": {"value": len(schools),
                        "delta": f"{len(schools)} active"},
            "balance": {"value": total_balance,
                        "delta": f"{total_distributed} issued · {total_returned} returned"},
            "lowStock": {"value": len(low_rows),
                         "delta": f"{total_required} books required"},
        },
        "monthly": monthly,
        "comparison": [{"name": s["name"], "value": s["issued"]} for s in per_school[:5]],
        "activity": activity,
        "schools": per_school,
    }


# --- Presence (in-memory heartbeat) ------------------------------------------
_PRESENCE: Dict[str, float] = {}


def record_presence(username: str) -> None:
    import time as _t
    _PRESENCE[str(username or "").lower()] = _t.time()


def presence_snapshot(window_seconds: int = 120) -> Dict[str, Any]:
    """Return the list of usernames considered online (a heartbeat in the
    last `window_seconds`) plus the total count."""
    import time as _t
    now = _t.time()
    online = [u for u, ts in list(_PRESENCE.items()) if (now - ts) <= window_seconds]
    return {"count": len(online), "usernames": online}


def schools_created_by(username: str) -> int:
    """How many schools were added by this account (from the audit log)."""
    n = 0
    for r in read_csv(AUDIT_CSV):
        if r.get("action") == "SCHOOL_CREATE" and (r.get("username") or "").lower() == (username or "").lower():
            n += 1
    return n



# ============================================================================
# Ledger module: custom fields, vendor returns and integration metadata
# ============================================================================

RESERVED_FIELD_KEYS = set(LEDGER_HEADERS) | {"id", "custom_json"}


def get_ledger_custom_fields(username: Optional[str] = None) -> List[Dict]:
    """Extra ledger columns (data/ledger_fields.json).

    A field belongs to the account that created it: passing a username returns
    only that account's own fields (plus any legacy field saved before this
    rule existed, which has no owner). Passing None returns every field."""
    try:
        with open(LEDGER_FIELDS_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            out = []
            for f in data:
                if not isinstance(f, dict) or not f.get("key"):
                    continue
                out.append({
                    "key": str(f.get("key")),
                    "label": str(f.get("label") or f.get("key")),
                    "type": str(f.get("type") or "text"),
                    "created_by": str(f.get("created_by") or ""),
                })
            if username is not None:
                who = str(username).lower()
                out = [f for f in out
                       if not f["created_by"] or f["created_by"].lower() == who]
            return out
    except Exception:
        pass
    return []


def _write_ledger_custom_fields(fields: List[Dict]):
    with open(LEDGER_FIELDS_JSON, "w", encoding="utf-8") as fh:
        json.dump(fields, fh, indent=2)


def add_ledger_custom_field(label: str, ftype: str = "text", username: str = "") -> Dict:
    """Adds a new ledger column that only its creator sees."""
    import re as _re
    label = str(label or "").strip()
    if not label:
        raise ValueError("Field label is required.")
    if len(label) > 40:
        raise ValueError("Field label must be 40 characters or fewer.")
    ftype = str(ftype or "text").strip().lower()
    if ftype not in LEDGER_FIELD_TYPES:
        raise ValueError(f"Field type must be one of: {', '.join(LEDGER_FIELD_TYPES)}.")
    key = "cf_" + _re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if key == "cf_":
        raise ValueError("Field label must contain letters or numbers.")
    if key in RESERVED_FIELD_KEYS:
        raise ValueError("That field name is reserved.")
    fields = get_ledger_custom_fields()
    who = str(username or "").lower()
    if any(f["key"] == key and (not f["created_by"] or f["created_by"].lower() == who)
           for f in fields):
        raise ValueError(f"A ledger field named '{label}' already exists.")
    if key in {f["key"] for f in fields}:
        # another account already uses this key - give this one its own suffix
        key = key + "_" + (_re.sub(r"[^a-z0-9]+", "", who)[:12] or "own")
    if len([f for f in fields if f["created_by"].lower() == who]) >= 25:
        raise ValueError("A maximum of 25 custom ledger fields is supported.")
    record = {"key": key, "label": label, "type": ftype, "created_by": username}
    fields.append(record)
    _write_ledger_custom_fields(fields)
    return record


def delete_ledger_custom_field(key: str) -> bool:
    """Removes a field for everyone, whoever created it."""
    fields = get_ledger_custom_fields()
    kept = [f for f in fields if f["key"] != str(key)]
    if len(kept) == len(fields):
        return False
    _write_ledger_custom_fields(kept)
    return True


# --- Vendor returns (Return to Vendor) ---------------------------------------
def read_vendor_returns(school_id=None) -> List[Dict]:
    if not os.path.exists(VENDOR_RETURNS_CSV):
        atomic_csv_write(VENDOR_RETURNS_CSV, VENDOR_RETURN_HEADERS, [])
        return []
    rows = read_csv(VENDOR_RETURNS_CSV)
    if school_id is not None:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id)]
    rows.sort(key=lambda r: str(r.get("created_time", "")), reverse=True)
    return rows


def save_vendor_return(school_id, class_id, data: Dict, username: str) -> Dict:
    """Records books/items returned to a vendor and pushes the quantity into the
    matching ledger row, which reduces its closing balance through the standard
    formula (closing = opening + purchased - issued - returns)."""
    qty = _num(data.get("quantity"))
    if qty <= 0:
        raise ValueError("Number of books/items returned must be greater than zero.")
    book = str(data.get("bookName") or "").strip()
    if not book:
        raise ValueError("Resource name is required for a vendor return.")
    vendor_name = str(data.get("vendor") or "").strip()
    if not vendor_name:
        raise ValueError("Vendor name is required for a vendor return.")
    if not str(data.get("creditNoteNo") or "").strip():
        raise ValueError("Credit note number is required.")

    vendor = get_vendor(str(data.get("vendorId") or vendor_name)) or {}

    ledger = read_ledger(school_id)
    target = None
    for row in ledger:
        if str(row.get("class_id")) != str(class_id or row.get("class_id")):
            continue
        if str(row.get("bookName", "")).strip().upper() != book.upper():
            continue
        if data.get("ledger_id") and str(row.get("id")) != str(data.get("ledger_id")):
            continue
        target = row
        break
    if target is None:
        raise ValueError(f"'{book}' has no ledger row in this class, so it cannot be returned to a vendor.")

    ob = _num(target.get("openingBalance"))
    p = _num(target.get("purchased"))
    d = _num(target.get("distributed"))
    r = _num(target.get("returned")) + qty
    closing = ob + p - d - r
    if closing < 0:
        raise ValueError(
            f"Returning {qty} of '{book}' would make the closing balance negative "
            f"(available {ob + p - d - _num(target.get('returned'))}).")
    target["returned"] = str(r)
    target["closingBalance"] = str(closing)
    target["balance"] = str(calculate_balance(p, d, r))
    target["modified_by"] = username
    target["modified_time"] = current_timestamp()
    write_ledger(school_id, ledger)

    record = {h: "" for h in VENDOR_RETURN_HEADERS}
    record.update({
        "id": f"VR_{uuid.uuid4().hex[:8]}",
        "school_id": str(school_id),
        "class_id": str(class_id or ""),
        "ledger_id": str(target.get("id", "")),
        "academicYear": str(data.get("academicYear") or target.get("academicYear") or ""),
        "vendorId": str(data.get("vendorId") or vendor.get("vendorId") or ""),
        "vendor": vendor_name,
        "vendorContact": str(data.get("vendorContact") or vendor.get("contact") or ""),
        "vendorGst": str(data.get("vendorGst") or vendor.get("gst") or ""),
        "creditNoteNo": str(data.get("creditNoteNo") or "").strip(),
        "bookName": book,
        "subject": str(data.get("subject") or target.get("subject") or ""),
        "publication": str(data.get("publication") or target.get("publication") or ""),
        "edition": str(data.get("edition") or target.get("edition") or ""),
        "quantity": str(qty),
        "returnDate": str(data.get("returnDate") or "")[:10],
        "reason": str(data.get("reason") or ""),
        "remarks": str(data.get("remarks") or ""),
        "created_by": username,
        "created_time": current_timestamp(),
    })
    rows = read_vendor_returns()
    rows.insert(0, record)
    atomic_csv_write(VENDOR_RETURNS_CSV, VENDOR_RETURN_HEADERS, rows)
    return record


def previous_closing_balance(school_id, class_id, book_name: str, academic_year: str = "",
                             exclude_id: str = "") -> int:
    """Opening balance source: the latest closing balance already recorded for
    the same resource (previous academic year / previous entry)."""
    book = str(book_name or "").strip().upper()
    if not book:
        return 0
    rows = [r for r in read_ledger(school_id)
            if str(r.get("bookName", "")).strip().upper() == book
            and str(r.get("id")) != str(exclude_id)]
    if class_id:
        same_class = [r for r in rows if str(r.get("class_id")) == str(class_id)]
        rows = same_class or rows
    if academic_year:
        earlier = [r for r in rows if str(r.get("academicYear") or "") != str(academic_year)]
        rows = earlier or rows
    if not rows:
        return 0
    rows.sort(key=lambda r: str(r.get("modified_time") or r.get("created_time") or ""), reverse=True)
    return _num(rows[0].get("closingBalance"))


def ledger_meta(school_id, class_id=0, allowed_ids: Optional[List[str]] = None,
                username: Optional[str] = None) -> Dict[str, Any]:
    """Everything the Ledger screen needs from the other modules, in one call:
    Institution Directory, Resource Catalog and Vendor Management stay the single
    source of truth - the ledger never duplicates their data."""
    schools = get_all_schools(allowed_ids=allowed_ids)
    institutions = [{
        "id": str(s.get("id")),
        "name": s.get("name", ""),
        "code": s.get("code", ""),
        "academic_year": s.get("academic_year", ""),
        "classes": [{"id": c.get("id"), "name": c.get("name", ""),
                     "strength": _num(c.get("strength")),
                     "standard": normalize_standard(c.get("name", ""))}
                    for c in get_classes_for_school(s.get("id"))],
    } for s in schools]

    vendors = [{
        "vendorId": v.get("vendorId", ""), "name": v.get("name", ""),
        "contact": v.get("contact", ""), "gst": v.get("gst", ""),
        "email": v.get("email", ""), "address": v.get("address", ""),
        "payment_terms": v.get("payment_terms", ""), "status": v.get("status", "Active"),
    } for v in get_vendors() if str(v.get("status", "Active")).lower() != "inactive"]

    resources = [{
        "title": c.get("title", ""), "standard": c.get("standard", ""),
        "category": c.get("category", ""), "subject": c.get("subject", ""),
        "publication": c.get("publication", ""), "edition": c.get("edition", ""),
        "approved_rate": _dec(c.get("approved_rate")),
        "language": c.get("language", ""), "isbn": c.get("isbn", ""),
        "academic_year": c.get("academic_year", ""),
    } for c in get_catalog() if str(c.get("status", "Active")).lower() != "inactive"]

    years = academic_years()
    for r in resources:
        if r["academic_year"] and r["academic_year"] not in years:
            years.append(r["academic_year"])

    return {
        "institutions": institutions,
        "vendors": vendors,
        "resources": resources,
        "standards": list(STANDARDS),
        "categories": list(ARTICLE_CATEGORIES),
        "subjects": sorted({r["subject"] for r in resources if r["subject"]}),
        "publications": sorted({r["publication"] for r in resources if r["publication"]}),
        "editions": sorted({r["edition"] for r in resources if r["edition"]} | set(EDITIONS)),
        "academicYears": years,
        "gstRates": list(GST_RATES),
        "discountRates": list(DISCOUNT_OPTIONS),
        "returnReasons": list(RETURN_REASONS),
        "customFields": get_ledger_custom_fields(username),
        "fieldTypes": list(LEDGER_FIELD_TYPES),
    }
