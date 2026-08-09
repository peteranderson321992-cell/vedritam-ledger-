# reporting.py
"""Single source of truth for every number shown in the dashboard, the
reports page, charts and exports.

Design rules (see PRD "Reports & Analytics Accuracy"):
  * one aggregation function -> every surface (cards, charts, tables, exports)
    reads from the same payload, so they can never disagree;
  * records are de-duplicated by id;
  * rows pointing at deleted schools / deleted classes are dropped, so removed
    data never inflates a total;
  * derived values (balance, books required) are recomputed with the
    authoritative business logic before they are summed;
  * filters (school, date range, staff, user) are applied once, up front.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

import database
from config import SCHOOLS_CSV, USERS_CSV, LOW_STOCK_THRESHOLD
from utils import calculate_balance, calculate_books_required, current_timestamp, current_date

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _num(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except (ValueError, TypeError):
        return 0


def _day(value: str) -> str:
    """The YYYY-MM-DD part of a timestamp, or '' when unusable."""
    s = str(value or "").strip()
    return s[:10] if len(s) >= 10 else ""


def _record_day(row: Dict[str, Any]) -> str:
    return _day(row.get("modified_time")) or _day(row.get("created_time"))


def _load_schools() -> List[Dict[str, Any]]:
    """Schools with their classes parsed, de-duplicated by id."""
    seen, out = set(), []
    for s in database.read_csv(SCHOOLS_CSV):
        sid = str(s.get("id", "")).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        try:
            classes = json.loads(s.get("classes_json") or "[]")
        except (ValueError, TypeError):
            classes = []
        classes = [c for c in classes if isinstance(c, dict)]
        out.append({
            "id": sid,
            "name": s.get("name", "") or f"School {sid}",
            "code": s.get("code", "") or "",
            "location": s.get("location", "") or "",
            "classes": classes,
        })
    return out


def _load_users() -> List[Dict[str, Any]]:
    seen, out = set(), []
    for u in database.read_csv(USERS_CSV):
        name = str(u.get("username", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(u)
    return out


def build_report(school_id: str = "", date_from: str = "", date_to: str = "",
                 staff: str = "", user: str = "", scope_school_id: str = "",
                 scope_school_ids: List[str] | None = None,
                 include_activity: bool = True,
                 visible_usernames: List[str] | None = None) -> Dict[str, Any]:
    """Compute every statistic for the given filters.

    scope_school_id   -- hard single-school scope (a 'user' account).
    scope_school_ids  -- hard whitelist of schools (a 'staff' account);
                         None means every school (Super Admin).
    include_activity  -- False strips the recent-activity feed entirely.
    visible_usernames -- accounts whose activity/people figures may be shown;
                         None means every account (Super Admin).
    """
    schools = _load_schools()

    # Security scope first, then the user's dropdown selection.
    if scope_school_id:
        schools = [s for s in schools if s["id"] == str(scope_school_id)]
    if scope_school_ids is not None:
        wanted_scope = {str(i) for i in scope_school_ids}
        schools = [s for s in schools if s["id"] in wanted_scope]
    selected = str(school_id or "").strip()
    if selected and selected.lower() != "all":
        schools = [s for s in schools if s["id"] == selected]

    school_by_id = {s["id"]: s for s in schools}
    class_names = {(s["id"], str(c.get("id"))): str(c.get("name", ""))
                   for s in schools for c in s["classes"]}

    date_from = _day(date_from)
    date_to = _day(date_to)
    staff_f = str(staff or "").strip().lower()
    user_f = str(user or "").strip().lower()

    records: List[Dict[str, Any]] = []
    seen_ids = set()
    orphan_rows = 0
    corrected_rows = 0

    for row in database.read_ledger():
        rid = str(row.get("id", "")).strip()
        if not rid or rid in seen_ids:
            continue                      # de-duplicate
        seen_ids.add(rid)

        sid = str(row.get("school_id", "")).strip()
        cid = str(row.get("class_id", "")).strip()
        school = school_by_id.get(sid)
        if school is None:
            continue                      # other school / out of scope
        if (sid, cid) not in class_names:
            orphan_rows += 1              # class was deleted -> never counted
            continue

        day = _record_day(row)
        if date_from and (not day or day < date_from):
            continue
        if date_to and (not day or day > date_to):
            continue

        created_by = str(row.get("created_by", "") or "").strip()
        modified_by = str(row.get("modified_by", "") or "").strip()
        if staff_f and staff_f not in (created_by.lower(), modified_by.lower()):
            continue
        if user_f and user_f not in (created_by.lower(), modified_by.lower()):
            continue

        strength = _num(row.get("strength"))
        purchased = _num(row.get("purchased"))
        distributed = _num(row.get("distributed"))
        returned = _num(row.get("returned"))
        balance = calculate_balance(purchased, distributed, returned)
        required = calculate_books_required(strength, purchased)
        if balance != _num(row.get("balance")) or required != _num(row.get("booksRequired")):
            corrected_rows += 1           # stored value was stale -> recomputed

        records.append({
            "id": rid,
            "school_id": sid,
            "school": school["name"],
            "class_id": cid,
            "class": class_names[(sid, cid)],
            "bookName": row.get("bookName", ""),
            "subject": (row.get("subject") or "").strip() or "Unspecified",
            "publication": row.get("publication", ""),
            "vendor": row.get("vendor", ""),
            "category": (row.get("category") or "").strip() or "Uncategorized",
            "invoiceRef": row.get("invoiceRef", ""),
            "strength": strength,
            "purchased": purchased,
            "distributed": distributed,
            "returned": returned,
            "balance": balance,
            "booksRequired": required,
            "remarks": row.get("remarks", ""),
            "created_by": created_by,
            "created_time": row.get("created_time", ""),
            "modified_by": modified_by,
            "modified_time": row.get("modified_time", ""),
            "day": day,
        })

    # ---- totals -------------------------------------------------------------
    total_purchased = sum(r["purchased"] for r in records)
    total_distributed = sum(r["distributed"] for r in records)
    total_returned = sum(r["returned"] for r in records)
    total_balance = sum(r["balance"] for r in records)
    total_required = sum(r["booksRequired"] for r in records)
    low_stock = [r for r in records if r["balance"] < LOW_STOCK_THRESHOLD]

    # Classes counted from the (already scoped) schools, never from the ledger.
    all_classes = [{"school_id": s["id"], "school": s["name"],
                    "id": str(c.get("id")), "name": str(c.get("name", "")),
                    "strength": _num(c.get("strength"))}
                   for s in schools for c in s["classes"]]
    total_students = sum(c["strength"] for c in all_classes)

    # ---- people -------------------------------------------------------------
    all_users = _load_users()
    scoped_users = all_users
    if scope_school_id or (selected and selected.lower() != "all"):
        wanted = str(scope_school_id or selected)
        scoped_users = [u for u in all_users
                        if str(u.get("school_id", "") or "") == wanted or database.normalize_role(u.get("role")) == database.SUPER_ADMIN]
    if scope_school_ids is not None:
        wanted_ids = {str(i) for i in scope_school_ids}
        scoped_users = [u for u in scoped_users
                        if str(u.get("school_id", "") or "") in wanted_ids]
    if visible_usernames is not None:
        allowed_names = {str(n).lower() for n in visible_usernames}
        scoped_users = [u for u in scoped_users
                        if str(u.get("username", "")).lower() in allowed_names]
    staff_users = [u for u in scoped_users if database.normalize_role(u.get("role")) == database.STAFF]
    active_users = [u for u in scoped_users if (u.get("status") or "Active") == "Active"]

    # ---- charts (derived from the very same `records` list) ------------------
    def group(key: str, value: str = "purchased") -> List[Dict[str, Any]]:
        agg: Dict[str, int] = {}
        for r in records:
            agg[str(r[key])] = agg.get(str(r[key]), 0) + r[value]
        return [{"label": k, "value": v}
                for k, v in sorted(agg.items(), key=lambda kv: kv[1], reverse=True)]

    # monthly trend over the months actually present in the filtered data,
    # falling back to the last 6 calendar months when there is none.
    month_keys = sorted({r["day"][:7] for r in records if r["day"]})
    if not month_keys and not (date_from or date_to):
        now = datetime.now()
        y, m, tmp = now.year, now.month, []
        for _ in range(6):
            tmp.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        month_keys = list(reversed(tmp))
    month_keys = month_keys[-12:]
    buckets = {k: {"issued": 0, "received": 0} for k in month_keys}
    for r in records:
        k = r["day"][:7]
        if k in buckets:
            buckets[k]["issued"] += r["distributed"]
            buckets[k]["received"] += r["purchased"]
    monthly = {
        "labels": [f"{_MONTH_NAMES[int(k[5:7]) - 1]} {k[:4]}" for k in month_keys],
        "issued": [buckets[k]["issued"] for k in month_keys],
        "received": [buckets[k]["received"] for k in month_keys],
    }

    per_school = []
    for s in schools:
        rows = [r for r in records if r["school_id"] == s["id"]]
        per_school.append({
            "id": _num(s["id"]),
            "name": s["name"],
            "code": s["code"],
            "classes": len(s["classes"]),
            "students": sum(_num(c.get("strength")) for c in s["classes"]),
            "records": len(rows),
            "purchased": sum(r["purchased"] for r in rows),
            "issued": sum(r["distributed"] for r in rows),
            "returned": sum(r["returned"] for r in rows),
            "balance": sum(r["balance"] for r in rows),
            "required": sum(r["booksRequired"] for r in rows),
        })
    per_school.sort(key=lambda x: x["issued"], reverse=True)

    # Task list = every row that needs attention: books still required, a
    # negative balance, or stock under the low-stock threshold.
    tasks = sorted(
        [r for r in records
         if r["booksRequired"] > 0 or r["balance"] < 0 or r["balance"] < LOW_STOCK_THRESHOLD],
        key=lambda r: (-r["booksRequired"], r["balance"]))

    today = current_date()
    added_today = sum(r["purchased"] for r in records if _day(r["created_time"]) == today)

    audit = database.get_audit_log(limit=400)
    if not include_activity:
        audit = []
    if visible_usernames is not None:
        allowed_audit = {str(n).lower() for n in visible_usernames}
        audit = [a for a in audit if str(a.get("username", "")).lower() in allowed_audit]
    if scope_school_id or (selected and selected.lower() != "all"):
        wanted = str(scope_school_id or selected)
        audit = [a for a in audit
                 if str(a.get("entity_id", "")) == wanted
                 or str(a.get("entity_id", "")).startswith(wanted + ":")
                 or a.get("entity") not in ("school", "ledger", "class")]
    if staff_f or user_f:
        who = staff_f or user_f
        audit = [a for a in audit if str(a.get("username", "")).lower() == who]
    if date_from:
        audit = [a for a in audit if _day(a.get("timestamp")) >= date_from]
    if date_to:
        audit = [a for a in audit if _day(a.get("timestamp")) <= date_to]

    tone_for = {
        "LOGIN": "blue", "LOGOUT": "blue",
        "SCHOOL_CREATE": "blue", "SCHOOL_DELETE": "red",
        "CLASS_CREATE": "blue", "CLASS_DELETE": "red",
        "LEDGER_SYNC": "green", "LEDGER_DELETE": "red",
        "USER_CREATE": "blue", "USER_DELETE": "red",
    }
    activity = [{
        "tone": tone_for.get(a.get("action", ""), "amber"),
        "text": a.get("details") or a.get("action", ""),
        "meta": f"{a.get('timestamp', '')} · {a.get('username', '')}",
        "timestamp": a.get("timestamp", ""),
        "username": a.get("username", ""),
        "action": a.get("action", ""),
    } for a in audit[:25]]

    # ---- integrity check ----------------------------------------------------
    integrity = {
        "duplicatesRemoved": 0,
        "orphanRowsExcluded": orphan_rows,
        "recalculatedRows": corrected_rows,
        "balanced": total_balance == total_purchased - (total_distributed + total_returned),
    }

    # Filter dropdown options are built before any empty-range zeroing, so the
    # user can always change or clear the filters.
    option_staff = sorted({u.get("username", "") for u in staff_users if u.get("username")})
    option_users = sorted({u.get("username", "") for u in scoped_users if u.get("username")})

    # ---- empty date range ---------------------------------------------------
    # A date filter that matches nothing must read as a genuine zero everywhere,
    # not fall back to the unfiltered school / class / people counts.
    empty_range = bool((date_from or date_to) and not records)
    if empty_range:
        all_classes = []
        total_students = 0
        scoped_users = []
        staff_users = []
        active_users = []
        per_school = []
        tasks = []
        added_today = 0
        school_count = 0
    else:
        school_count = len(schools)

    return {
        "filters": {
            "school_id": selected, "date_from": date_from, "date_to": date_to,
            "staff": staff, "user": user,
            "scoped": bool(scope_school_id),
        },
        "options": {
            "schools": [{"id": s["id"], "name": s["name"], "code": s["code"]} for s in schools],
            "staff": option_staff,
            "users": option_users,
        },
        "emptyRange": empty_range,
        "totals": {
            "schools": school_count,
            "classes": len(all_classes),
            "students": total_students,
            "records": len(records),
            "purchased": total_purchased,
            "distributed": total_distributed,
            "returned": total_returned,
            "balance": total_balance,
            "required": total_required,
            "lowStock": len(low_stock),
            "users": len(scoped_users),
            "activeUsers": len(active_users),
            "staff": len(staff_users),
        },
        "kpis": {
            "books": {"value": total_purchased,
                      "delta": f"+{added_today} added today" if added_today else "no additions today"},
            "schools": {"value": school_count, "delta": f"{len(all_classes)} classes · {total_students} students"},
            "balance": {"value": total_balance,
                        "delta": f"{total_distributed} issued · {total_returned} returned"},
            "lowStock": {"value": len(low_stock), "delta": f"{total_required} books required"},
        },
        "monthly": monthly,
        "comparison": [{"name": s["name"], "value": s["issued"]} for s in per_school[:8]],
        "bySubject": group("subject")[:10],
        "byCategory": group("category")[:10],
        "bySchool": [{"label": s["name"], "value": s["purchased"]} for s in per_school],
        "overview": [
            {"label": "Purchased", "value": total_purchased},
            {"label": "Distributed", "value": total_distributed},
            {"label": "Returned", "value": total_returned},
            {"label": "Balance", "value": total_balance},
        ],
        "schools": per_school,
        "records": records,
        "tasks": tasks,
        "activity": activity,
        "showActivity": bool(include_activity),
        "integrity": integrity,
        "generated_at": current_timestamp(),
    }


EXPORT_COLUMNS = [
    ("school", "School"), ("class", "Class"), ("bookName", "Book"),
    ("subject", "Subject"), ("category", "Category"), ("publication", "Publication"),
    ("vendor", "Vendor"), ("invoiceRef", "Invoice Ref"), ("strength", "Strength"),
    ("purchased", "Purchased"), ("distributed", "Distributed"), ("returned", "Returned"),
    ("balance", "Balance"), ("booksRequired", "Books Required"), ("remarks", "Remarks"),
    ("created_by", "Created By"), ("created_time", "Created"),
    ("modified_by", "Modified By"), ("modified_time", "Modified"),
]
