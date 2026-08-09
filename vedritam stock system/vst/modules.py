# modules.py
# Data layer for the Distribution and Transfers features.

import csv
import json
import os
import uuid
from typing import List, Dict, Any, Optional

from config import DISTRIBUTIONS_CSV, TRANSFERS_CSV
from utils import atomic_csv_write, current_timestamp, CSV_WRITE_LOCK
import database

# --- CSV headers -------------------------------------------------------------
DISTRIBUTION_HEADERS = [
    "id", "timestamp", "school_id", "class_id", "ledger_id", "book_name",
    "recipient", "quantity", "remarks", "created_by",
]
TRANSFER_HEADERS = [
    "id", "timestamp", "from_school_id", "to_school_id", "book_name",
    "quantity", "status", "remarks", "created_by", "approved_by", "approved_at",
    "decision_remarks",
]

def _read(path, headers):
    if not os.path.exists(path):
        atomic_csv_write(path, headers, [])
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def init_stores():
    for p, h in [
        (DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS),
        (TRANSFERS_CSV, TRANSFER_HEADERS),
    ]:
        if not os.path.exists(p):
            atomic_csv_write(p, h, [])


# ============================================================================
# DISTRIBUTION — issue books from a class ledger row to a class/teacher.
# ============================================================================
def list_distributions(school_id_filter: str = "", allowed_ids: Optional[List[str]] = None,
                       created_by: str = "") -> List[Dict]:
    """Data isolation: school scope first, then optional per-user ownership."""
    rows = _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS)
    if school_id_filter:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id_filter)]
    if allowed_ids is not None:
        wanted = {str(i) for i in allowed_ids}
        rows = [r for r in rows if str(r.get("school_id")) in wanted]
    if created_by:
        rows = [r for r in rows if str(r.get("created_by", "")).lower() == created_by.lower()]
    rows.reverse()
    return rows


def create_distribution(school_id, class_id, ledger_id, recipient, quantity, remarks, username):
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    # Locate the ledger row and increment its `distributed` counter atomically.
    ledger = database.read_ledger(school_id)
    target = next((r for r in ledger if str(r["id"]) == str(ledger_id)
                   and str(r["class_id"]) == str(class_id)), None)
    if not target:
        raise ValueError("Book row not found for this class.")
    purchased = int(target.get("purchased") or 0)
    distributed = int(target.get("distributed") or 0)
    returned = int(target.get("returned") or 0)
    balance = purchased - distributed - returned
    if qty > balance:
        raise ValueError(f"Only {balance} books available in stock.")
    target["distributed"] = str(distributed + qty)
    target["balance"] = str(balance - qty)
    target["modified_by"] = username
    target["modified_time"] = current_timestamp()
    database.write_ledger(school_id, ledger)

    rows = _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS)
    record = {
        "id": f"D_{uuid.uuid4().hex[:8]}",
        "timestamp": current_timestamp(),
        "school_id": str(school_id),
        "class_id": str(class_id),
        "ledger_id": str(ledger_id),
        "book_name": target.get("bookName", ""),
        "recipient": (recipient or "").strip(),
        "quantity": str(qty),
        "remarks": (remarks or "").strip(),
        "created_by": username,
    }
    rows.append(record)
    atomic_csv_write(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS, rows)
    return record


# ============================================================================
# TRANSFERS — move stock between schools with a request/approve workflow.
# ============================================================================
def list_transfers(school_id_filter: str = "", allowed_ids: Optional[List[str]] = None,
                   created_by: str = "") -> List[Dict]:
    rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
    if school_id_filter:
        rows = [r for r in rows
                if str(r.get("from_school_id")) == str(school_id_filter)
                or str(r.get("to_school_id")) == str(school_id_filter)]
    if allowed_ids is not None:
        wanted = {str(i) for i in allowed_ids}
        rows = [r for r in rows
                if str(r.get("from_school_id")) in wanted or str(r.get("to_school_id")) in wanted]
    if created_by:
        rows = [r for r in rows if str(r.get("created_by", "")).lower() == created_by.lower()]
    rows.reverse()
    return rows


def create_transfer(from_school_id, to_school_id, book_name, quantity, remarks, username):
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    if str(from_school_id) == str(to_school_id):
        raise ValueError("Source and destination schools must differ.")
    schools = {str(x.get("id")): x for x in database.get_all_schools()}
    if str(from_school_id) not in schools or str(to_school_id) not in schools:
        raise ValueError("Source or destination school does not exist.")
    with CSV_WRITE_LOCK:
        ledger = database.read_ledger(from_school_id)
        matches = [r for r in ledger if str(r.get("bookName","")).strip().lower() == str(book_name).strip().lower()]
        if not matches:
            raise ValueError("Item does not exist in the source school's stock.")
        available = sum(max(int(r.get("balance") or 0), 0) for r in matches)
        if qty > available:
            raise ValueError(f"Only {available} units are available in source stock.")
        rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
        record = {
        "id": f"T_{uuid.uuid4().hex[:8]}",
        "timestamp": current_timestamp(),
        "from_school_id": str(from_school_id),
        "to_school_id": str(to_school_id),
        "book_name": (book_name or "").strip(),
        "quantity": str(qty),
        "status": "Pending",
        "remarks": (remarks or "").strip(),
        "created_by": username,
        "approved_by": "",
        "approved_at": "",
        "decision_remarks": "",
        }
        rows.append(record)
        atomic_csv_write(TRANSFERS_CSV, TRANSFER_HEADERS, rows)
        return record


def get_transfer(transfer_id: str) -> Optional[Dict]:
    for r in _read(TRANSFERS_CSV, TRANSFER_HEADERS):
        if r.get("id") == transfer_id:
            return r
    return None


def set_transfer_status(transfer_id: str, status: str, username: str, remarks: str = ""):
    aliases = {"Approved": "Accepted", "Rejected": "Declined"}
    status = aliases.get(status, status)
    if status not in ("Accepted", "Declined"):
        raise ValueError("Invalid transfer decision.")
    with CSV_WRITE_LOCK:
        rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
        tgt = next((r for r in rows if r["id"] == transfer_id), None)
        if not tgt:
            raise ValueError("Transfer not found.")
        if tgt.get("status") != "Pending":
            raise ValueError("Only pending transfers can be decided.")
        if status == "Accepted":
            source_id, dest_id = str(tgt["from_school_id"]), str(tgt["to_school_id"])
            qty = int(tgt.get("quantity") or 0)
            book = str(tgt.get("book_name") or "").strip().lower()
            src = database.read_ledger(source_id)
            dst = database.read_ledger(dest_id)
            src_rows = [r for r in src if str(r.get("bookName","")).strip().lower() == book]
            available = sum(max(int(r.get("balance") or 0), 0) for r in src_rows)
            if available < qty:
                raise ValueError(f"Insufficient source stock at acceptance; only {available} available.")
            remaining = qty
            for r in src_rows:
                bal = max(int(r.get("balance") or 0), 0)
                take = min(bal, remaining)
                if take:
                    # A transfer-out is represented as an issue from the
                    # source school so the standard closing formula remains
                    # authoritative: opening + purchased - issued - returns.
                    r["balance"] = str(bal - take)
                    r["distributed"] = str(int(r.get("distributed") or 0) + take)
                    r["closingBalance"] = str(max(int(r.get("openingBalance") or 0) +
                                                     int(r.get("purchased") or 0) -
                                                     int(r.get("distributed") or 0) -
                                                     int(r.get("returned") or 0), 0))
                    r["modified_by"] = username
                    r["modified_time"] = current_timestamp()
                    remaining -= take
                if remaining <= 0: break
            if remaining:
                raise ValueError("Unable to allocate transferred quantity.")
            dst_rows = [r for r in dst if str(r.get("bookName","")).strip().lower() == book]
            if dst_rows:
                r = dst_rows[0]
            else:
                template = dict(src_rows[0])
                template["id"] = "L_" + uuid.uuid4().hex[:10]
                template["school_id"] = dest_id
                template["openingBalance"] = "0"; template["purchased"] = "0"
                template["distributed"] = "0"; template["returned"] = "0"
                template["closingBalance"] = "0"; template["balance"] = "0"
                template["created_by"] = username; template["created_time"] = current_timestamp()
                dst.append(template); r = template
            # Treat stock received by transfer as purchased into the
            # destination school so its normal closing-balance formula stays
            # consistent with the operational stock balance.
            oldbal = max(int(r.get("balance") or 0), 0)
            r["purchased"] = str(int(r.get("purchased") or 0) + qty)
            r["balance"] = str(oldbal + qty)
            r["closingBalance"] = str(max(int(r.get("openingBalance") or 0) +
                                             int(r.get("purchased") or 0) -
                                             int(r.get("distributed") or 0) -
                                             int(r.get("returned") or 0), 0))
            r["remarks"] = ((r.get("remarks") or "") + f" Transfer in {qty} from school {source_id}.").strip()
            r["modified_by"] = username; r["modified_time"] = current_timestamp()
            database.write_ledger(source_id, src)
            database.write_ledger(dest_id, dst)
            database.log_action(username, "", "TRANSFER_STOCK_MOVED", "transfer", transfer_id,
                                f"{qty} of '{tgt.get('book_name')}' moved S{source_id} -> S{dest_id}")
        tgt["status"] = status
        tgt["approved_by"] = username
        tgt["approved_at"] = current_timestamp()
        tgt["decision_remarks"] = remarks or ""
        atomic_csv_write(TRANSFERS_CSV, TRANSFER_HEADERS, rows)
        return tgt


def get_distribution(dist_id: str) -> Optional[Dict]:
    for r in _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS):
        if r.get("id") == dist_id:
            return r
    return None


def delete_distribution(dist_id: str, username: str) -> Dict:
    """Reverse a distribution: put the books back into the ledger row's balance."""
    rows = _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS)
    rec = next((r for r in rows if r.get("id") == dist_id), None)
    if not rec:
        raise ValueError("Distribution not found.")
    qty = int(rec.get("quantity") or 0)
    sid = str(rec.get("school_id", ""))
    ledger = database.read_ledger(sid) if sid else database.read_ledger()
    tgt = next((r for r in ledger if str(r["id"]) == str(rec.get("ledger_id"))), None)
    if tgt:
        distributed = max(int(tgt.get("distributed") or 0) - qty, 0)
        balance = int(tgt.get("balance") or 0) + qty
        tgt["distributed"] = str(distributed)
        tgt["balance"] = str(balance)
        tgt["modified_by"] = username
        tgt["modified_time"] = current_timestamp()
        if sid:
            database.write_ledger(sid, ledger)
        else:
            database.write_all_ledger(ledger)
    kept = [r for r in rows if r.get("id") != dist_id]
    atomic_csv_write(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS, kept)
    return rec
