# modules.py
# Data layer for the Distribution and Transfers features.

import csv
import json
import os
import uuid
from typing import List, Dict, Any, Optional

from config import DISTRIBUTIONS_CSV, TRANSFERS_CSV
from utils import atomic_csv_write, current_timestamp
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
    if status not in ("Approved", "Rejected", "Completed"):
        raise ValueError("Invalid status.")
    rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
    tgt = next((r for r in rows if r["id"] == transfer_id), None)
    if not tgt:
        raise ValueError("Transfer not found.")
    tgt["status"] = status
    tgt["approved_by"] = username
    tgt["approved_at"] = current_timestamp()
    if remarks:
        tgt["decision_remarks"] = remarks
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
