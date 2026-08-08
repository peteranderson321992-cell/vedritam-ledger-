# procurement.py
# Procurement & Finance data layer:
#   Requirement -> Purchase Request -> Approval -> Purchase Order -> Vendor
#   -> Goods Received (GRN) -> Invoice -> Payment -> Inventory updated
#
# Storage follows the same CSV conventions as the rest of the system.

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from config import (PURCHASE_REQUESTS_CSV, PURCHASE_ORDERS_CSV, GRN_CSV,
                    INVOICES_CSV, PAYMENTS_CSV, CREDIT_NOTES_CSV,
                    DEBIT_NOTES_CSV, BUDGETS_CSV)
from utils import atomic_csv_write, current_timestamp
import database

REQUEST_HEADERS = [
    "id", "created_time", "school_id", "standard", "category", "item",
    "quantity", "remarks", "status", "requested_by", "decided_by",
    "decided_at", "decision_remarks", "po_id",
]
PO_HEADERS = [
    "id", "po_number", "po_date", "expected_date", "school_id", "vendorId",
    "vendor", "vendorContact", "vendorGst", "items_json", "subtotal",
    "gstAmount", "discountAmount", "total", "status", "request_id",
    "remarks", "created_by", "created_time",
]
GRN_HEADERS = [
    "id", "grn_number", "grn_date", "po_id", "school_id", "vendorId", "vendor",
    "items_json", "total_quantity", "remarks", "received_by", "created_time",
]
INVOICE_HEADERS = [
    "id", "invoice_number", "invoice_date", "due_date", "po_id", "grn_id",
    "school_id", "vendorId", "vendor", "amount", "gstAmount", "total",
    "paid_amount", "status", "remarks", "created_by", "created_time",
]
PAYMENT_HEADERS = [
    "id", "payment_date", "invoice_id", "school_id", "vendorId", "vendor",
    "amount", "mode", "reference", "remarks", "created_by", "created_time",
]
NOTE_HEADERS = [
    "id", "note_date", "invoice_id", "school_id", "vendorId", "vendor",
    "amount", "reason", "created_by", "created_time",
]
BUDGET_HEADERS = [
    "id", "school_id", "academic_year", "category", "allocated", "remarks",
    "created_by", "created_time",
]

STORES = [
    (PURCHASE_REQUESTS_CSV, REQUEST_HEADERS),
    (PURCHASE_ORDERS_CSV, PO_HEADERS),
    (GRN_CSV, GRN_HEADERS),
    (INVOICES_CSV, INVOICE_HEADERS),
    (PAYMENTS_CSV, PAYMENT_HEADERS),
    (CREDIT_NOTES_CSV, NOTE_HEADERS),
    (DEBIT_NOTES_CSV, NOTE_HEADERS),
    (BUDGETS_CSV, BUDGET_HEADERS),
]


def init_stores():
    for path, headers in STORES:
        if not os.path.exists(path):
            atomic_csv_write(path, headers, [])
        else:
            database.migrate_csv_headers(path, headers)


# --- helpers -----------------------------------------------------------------
def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    return int(round(_num(value)))


def _read(path: str, headers: List[str]) -> List[Dict]:
    if not os.path.exists(path):
        atomic_csv_write(path, headers, [])
        return []
    return database.read_csv(path)


def _scope(rows: List[Dict], school_id: str = "", allowed_ids: Optional[List[str]] = None) -> List[Dict]:
    if school_id:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id)]
    if allowed_ids is not None:
        wanted = {str(i) for i in allowed_ids}
        rows = [r for r in rows if str(r.get("school_id")) in wanted]
    return rows


def _next_number(rows: List[Dict], field: str, prefix: str) -> str:
    nums = []
    for r in rows:
        val = str(r.get(field, ""))
        tail = val.split("-")[-1]
        if tail.isdigit():
            nums.append(int(tail))
    return "%s-%04d" % (prefix, (max(nums) + 1) if nums else 1)


def _items(raw) -> List[Dict]:
    """Normalises the line items of a PO / GRN."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (ValueError, TypeError):
            raw = []
    out = []
    for it in (raw or []):
        title = str(it.get("item") or it.get("bookName") or it.get("title") or "").strip()
        if not title:
            continue
        qty = _int(it.get("quantity"))
        rate = _num(it.get("rate") or it.get("approvedRate"))
        gst = _num(it.get("gst") or it.get("gstPercent"))
        disc = _num(it.get("discountPercent"))
        base = qty * rate
        disc_amt = round(base * disc / 100.0, 2)
        gst_amt = round((base - disc_amt) * gst / 100.0, 2)
        out.append({
            "item": title,
            "category": str(it.get("category", "") or "").upper(),
            "subject": str(it.get("subject", "") or ""),
            "publication": str(it.get("publication", "") or ""),
            "standard": str(it.get("standard", "") or ""),
            "quantity": qty,
            "rate": rate,
            "gst": gst,
            "discountPercent": disc,
            "baseAmount": round(base, 2),
            "discountAmount": disc_amt,
            "gstAmount": gst_amt,
            "amount": round(base - disc_amt + gst_amt, 2),
        })
    return out


def _totals(items: List[Dict]) -> Dict[str, float]:
    return {
        "subtotal": round(sum(i["baseAmount"] for i in items), 2),
        "discountAmount": round(sum(i["discountAmount"] for i in items), 2),
        "gstAmount": round(sum(i["gstAmount"] for i in items), 2),
        "total": round(sum(i["amount"] for i in items), 2),
    }


# ============================================================================
# 1. PURCHASE REQUESTS  (Requirement -> Purchase Request -> Approval)
# ============================================================================
def list_requests(school_id: str = "", allowed_ids=None, status: str = "") -> List[Dict]:
    rows = _scope(_read(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS), school_id, allowed_ids)
    if status:
        rows = [r for r in rows if str(r.get("status", "")).lower() == status.lower()]
    return list(reversed(rows))


def create_request(data: Dict, username: str) -> Dict:
    rows = _read(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS)
    if _int(data.get("quantity")) <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if not str(data.get("item", "")).strip():
        raise ValueError("Resource is required.")
    record = {
        "id": "PR_" + uuid.uuid4().hex[:10],
        "created_time": current_timestamp(),
        "school_id": str(data.get("school_id", "")),
        "standard": str(data.get("standard", "")),
        "category": str(data.get("category", "")).upper(),
        "item": str(data.get("item", "")).strip(),
        "quantity": str(_int(data.get("quantity"))),
        "remarks": str(data.get("remarks", "")),
        "status": "Pending",
        "requested_by": username,
        "decided_by": "", "decided_at": "", "decision_remarks": "", "po_id": "",
    }
    rows.append(record)
    atomic_csv_write(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS, rows)
    return record


def decide_request(request_id: str, status: str, username: str, remarks: str = "") -> Dict:
    if status not in ("Approved", "Rejected"):
        raise ValueError("Status must be Approved or Rejected.")
    rows = _read(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS)
    for r in rows:
        if str(r.get("id")) == str(request_id):
            if str(r.get("status")) != "Pending":
                raise ValueError("This request has already been decided.")
            r["status"] = status
            r["decided_by"] = username
            r["decided_at"] = current_timestamp()
            r["decision_remarks"] = remarks
            atomic_csv_write(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS, rows)
            return r
    raise ValueError("Purchase request not found.")


# ============================================================================
# 2. PURCHASE ORDERS
# ============================================================================
def list_orders(school_id: str = "", allowed_ids=None, status: str = "",
                vendor_id: str = "") -> List[Dict]:
    rows = _scope(_read(PURCHASE_ORDERS_CSV, PO_HEADERS), school_id, allowed_ids)
    if status:
        rows = [r for r in rows if str(r.get("status", "")).lower() == status.lower()]
    if vendor_id:
        rows = [r for r in rows if str(r.get("vendorId")) == str(vendor_id)]
    return list(reversed(rows))


def get_order(po_id: str) -> Optional[Dict]:
    for r in _read(PURCHASE_ORDERS_CSV, PO_HEADERS):
        if str(r.get("id")) == str(po_id):
            return r
    return None


def create_order(data: Dict, username: str) -> Dict:
    rows = _read(PURCHASE_ORDERS_CSV, PO_HEADERS)
    items = _items(data.get("items"))
    if not items:
        raise ValueError("At least one line item is required.")
    vendor = database.get_vendor(str(data.get("vendorId", ""))) or {}
    totals = _totals(items)
    record = {
        "id": "PO_" + uuid.uuid4().hex[:10],
        "po_number": _next_number(rows, "po_number", "PO"),
        "po_date": str(data.get("po_date", "")) or current_timestamp()[:10],
        "expected_date": str(data.get("expected_date", "")),
        "school_id": str(data.get("school_id", "")),
        "vendorId": str(data.get("vendorId", "")),
        "vendor": vendor.get("name", "") or str(data.get("vendor", "")),
        "vendorContact": vendor.get("contact", ""),
        "vendorGst": vendor.get("gst", ""),
        "items_json": json.dumps(items),
        "subtotal": str(totals["subtotal"]),
        "gstAmount": str(totals["gstAmount"]),
        "discountAmount": str(totals["discountAmount"]),
        "total": str(totals["total"]),
        "status": str(data.get("status", "")) or "Draft",
        "request_id": str(data.get("request_id", "")),
        "remarks": str(data.get("remarks", "")),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(PURCHASE_ORDERS_CSV, PO_HEADERS, rows)

    if record["request_id"]:
        reqs = _read(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS)
        for r in reqs:
            if str(r.get("id")) == record["request_id"]:
                r["status"] = "Ordered"
                r["po_id"] = record["id"]
        atomic_csv_write(PURCHASE_REQUESTS_CSV, REQUEST_HEADERS, reqs)
    return record


def set_order_status(po_id: str, status: str) -> Dict:
    rows = _read(PURCHASE_ORDERS_CSV, PO_HEADERS)
    for r in rows:
        if str(r.get("id")) == str(po_id):
            r["status"] = status
            atomic_csv_write(PURCHASE_ORDERS_CSV, PO_HEADERS, rows)
            return r
    raise ValueError("Purchase order not found.")


# ============================================================================
# 3. GOODS RECEIVED NOTES  (updates inventory)
# ============================================================================
def list_grns(school_id: str = "", allowed_ids=None, po_id: str = "") -> List[Dict]:
    rows = _scope(_read(GRN_CSV, GRN_HEADERS), school_id, allowed_ids)
    if po_id:
        rows = [r for r in rows if str(r.get("po_id")) == str(po_id)]
    return list(reversed(rows))


def _post_to_inventory(school_id: str, vendor_id: str, vendor: str, items: List[Dict],
                       reference: str, username: str):
    """Goods received are added to the school ledger so stock stays live."""
    if not school_id:
        return
    ledger = database.read_ledger(school_id)
    for it in items:
        name = it["item"].strip().upper()
        target = None
        for row in ledger:
            if (str(row.get("bookName", "")).strip().upper() == name
                    and str(row.get("school_id")) == str(school_id)):
                target = row
                break
        if target is None:
            target = {h: "" for h in database.LEDGER_HEADERS}
            target.update({
                "id": "L_" + uuid.uuid4().hex[:10],
                "school_id": str(school_id),
                "class_id": "",
                "standard": database.normalize_standard(it.get("standard", "")),
                "bookName": it["item"],
                "category": it.get("category", ""),
                "subject": it.get("subject", ""),
                "publication": it.get("publication", ""),
                "openingBalance": "0", "purchased": "0",
                "distributed": "0", "returned": "0",
                "created_by": username, "created_time": current_timestamp(),
            })
            ledger.append(target)
        target["vendorId"] = vendor_id or target.get("vendorId", "")
        target["vendor"] = vendor or target.get("vendor", "")
        target["invoiceRef"] = reference or target.get("invoiceRef", "")
        target["approvedRate"] = str(it.get("rate") or target.get("approvedRate") or 0)
        target["purchased"] = str(_int(target.get("purchased")) + _int(it.get("quantity")))
        target["closingBalance"] = str(
            _int(target.get("openingBalance")) + _int(target.get("purchased"))
            - _int(target.get("distributed")) - _int(target.get("returned")))
        target["modified_by"] = username
        target["modified_time"] = current_timestamp()
    database.write_ledger(school_id, ledger)


def create_grn(data: Dict, username: str) -> Dict:
    rows = _read(GRN_CSV, GRN_HEADERS)
    po = get_order(str(data.get("po_id", ""))) if data.get("po_id") else None
    items = _items(data.get("items") or (po.get("items_json") if po else []))
    if not items:
        raise ValueError("At least one received line item is required.")
    school_id = str(data.get("school_id", "")) or (po.get("school_id") if po else "")
    vendor_id = str(data.get("vendorId", "")) or (po.get("vendorId") if po else "")
    vendor = (po or {}).get("vendor", "") or str(data.get("vendor", ""))
    record = {
        "id": "GRN_" + uuid.uuid4().hex[:10],
        "grn_number": _next_number(rows, "grn_number", "GRN"),
        "grn_date": str(data.get("grn_date", "")) or current_timestamp()[:10],
        "po_id": str(data.get("po_id", "")),
        "school_id": school_id,
        "vendorId": vendor_id,
        "vendor": vendor,
        "items_json": json.dumps(items),
        "total_quantity": str(sum(i["quantity"] for i in items)),
        "remarks": str(data.get("remarks", "")),
        "received_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(GRN_CSV, GRN_HEADERS, rows)

    # Inventory updated — the final step of the procurement workflow.
    _post_to_inventory(school_id, vendor_id, vendor, items,
                       record["grn_number"], username)

    if po:
        ordered = sum(i["quantity"] for i in _items(po.get("items_json")))
        received = sum(_int(g.get("total_quantity")) for g in _read(GRN_CSV, GRN_HEADERS)
                       if str(g.get("po_id")) == str(po.get("id")))
        set_order_status(po["id"], "Received" if received >= ordered else "Partially Received")
    return record


# ============================================================================
# 4. SUPPLIER INVOICES
# ============================================================================
def _invoice_status(total: float, paid: float) -> str:
    if paid <= 0:
        return "Pending"
    if paid + 0.01 < total:
        return "Partial"
    return "Paid"


def list_invoices(school_id: str = "", allowed_ids=None, status: str = "",
                  vendor_id: str = "") -> List[Dict]:
    rows = _scope(_read(INVOICES_CSV, INVOICE_HEADERS), school_id, allowed_ids)
    if status:
        rows = [r for r in rows if str(r.get("status", "")).lower() == status.lower()]
    if vendor_id:
        rows = [r for r in rows if str(r.get("vendorId")) == str(vendor_id)]
    for r in rows:
        r["outstanding"] = round(_num(r.get("total")) - _num(r.get("paid_amount")), 2)
    return list(reversed(rows))


def create_invoice(data: Dict, username: str) -> Dict:
    rows = _read(INVOICES_CSV, INVOICE_HEADERS)
    po = get_order(str(data.get("po_id", ""))) if data.get("po_id") else None
    amount = _num(data.get("amount"))
    if not amount and po:
        # Taxable value of the order = subtotal less any discount.
        amount = round(_num(po.get("subtotal")) - _num(po.get("discountAmount")), 2)
    gst = _num(data.get("gstAmount")) or _num((po or {}).get("gstAmount"))
    total = _num(data.get("total")) or round(amount + gst, 2)
    if total <= 0:
        raise ValueError("Invoice total must be greater than zero.")
    vendor_id = str(data.get("vendorId", "")) or (po or {}).get("vendorId", "")
    vendor = database.get_vendor(vendor_id) or {}
    record = {
        "id": "INV_" + uuid.uuid4().hex[:10],
        "invoice_number": str(data.get("invoice_number", "")).strip() or _next_number(rows, "invoice_number", "INV"),
        "invoice_date": str(data.get("invoice_date", "")) or current_timestamp()[:10],
        "due_date": str(data.get("due_date", "")),
        "po_id": str(data.get("po_id", "")),
        "grn_id": str(data.get("grn_id", "")),
        "school_id": str(data.get("school_id", "")) or (po or {}).get("school_id", ""),
        "vendorId": vendor_id,
        "vendor": vendor.get("name", "") or (po or {}).get("vendor", "") or str(data.get("vendor", "")),
        "amount": str(round(amount, 2)),
        "gstAmount": str(round(gst, 2)),
        "total": str(round(total, 2)),
        "paid_amount": "0",
        "status": "Pending",
        "remarks": str(data.get("remarks", "")),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(INVOICES_CSV, INVOICE_HEADERS, rows)
    return record


def _recalc_invoice(invoice_id: str):
    rows = _read(INVOICES_CSV, INVOICE_HEADERS)
    paid = sum(_num(p.get("amount")) for p in _read(PAYMENTS_CSV, PAYMENT_HEADERS)
               if str(p.get("invoice_id")) == str(invoice_id))
    credits = sum(_num(n.get("amount")) for n in _read(CREDIT_NOTES_CSV, NOTE_HEADERS)
                  if str(n.get("invoice_id")) == str(invoice_id))
    debits = sum(_num(n.get("amount")) for n in _read(DEBIT_NOTES_CSV, NOTE_HEADERS)
                 if str(n.get("invoice_id")) == str(invoice_id))
    for r in rows:
        if str(r.get("id")) == str(invoice_id):
            total = _num(r.get("total")) + debits - credits
            r["paid_amount"] = str(round(paid, 2))
            r["status"] = _invoice_status(total, paid)
            atomic_csv_write(INVOICES_CSV, INVOICE_HEADERS, rows)
            return r
    return None


# ============================================================================
# 5. PAYMENTS, CREDIT & DEBIT NOTES
# ============================================================================
def list_payments(school_id: str = "", allowed_ids=None, vendor_id: str = "") -> List[Dict]:
    rows = _scope(_read(PAYMENTS_CSV, PAYMENT_HEADERS), school_id, allowed_ids)
    if vendor_id:
        rows = [r for r in rows if str(r.get("vendorId")) == str(vendor_id)]
    return list(reversed(rows))


def create_payment(data: Dict, username: str) -> Dict:
    amount = _num(data.get("amount"))
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    invoice_id = str(data.get("invoice_id", ""))
    invoice = next((i for i in _read(INVOICES_CSV, INVOICE_HEADERS)
                    if str(i.get("id")) == invoice_id), None)
    if invoice_id and not invoice:
        raise ValueError("Invoice not found.")
    if invoice:
        outstanding = _num(invoice.get("total")) - _num(invoice.get("paid_amount"))
        if amount - outstanding > 0.01:
            raise ValueError("Payment exceeds the outstanding amount (%.2f)." % outstanding)
    rows = _read(PAYMENTS_CSV, PAYMENT_HEADERS)
    record = {
        "id": "PAY_" + uuid.uuid4().hex[:10],
        "payment_date": str(data.get("payment_date", "")) or current_timestamp()[:10],
        "invoice_id": invoice_id,
        "school_id": str(data.get("school_id", "")) or (invoice or {}).get("school_id", ""),
        "vendorId": str(data.get("vendorId", "")) or (invoice or {}).get("vendorId", ""),
        "vendor": (invoice or {}).get("vendor", "") or str(data.get("vendor", "")),
        "amount": str(round(amount, 2)),
        "mode": str(data.get("mode", "")) or "NEFT",
        "reference": str(data.get("reference", "")),
        "remarks": str(data.get("remarks", "")),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(PAYMENTS_CSV, PAYMENT_HEADERS, rows)
    if invoice_id:
        _recalc_invoice(invoice_id)
    return record


def list_notes(kind: str, school_id: str = "", allowed_ids=None) -> List[Dict]:
    path = CREDIT_NOTES_CSV if kind == "credit" else DEBIT_NOTES_CSV
    return list(reversed(_scope(_read(path, NOTE_HEADERS), school_id, allowed_ids)))


def create_note(kind: str, data: Dict, username: str) -> Dict:
    path = CREDIT_NOTES_CSV if kind == "credit" else DEBIT_NOTES_CSV
    amount = _num(data.get("amount"))
    if amount <= 0:
        raise ValueError("Note amount must be greater than zero.")
    rows = _read(path, NOTE_HEADERS)
    invoice = next((i for i in _read(INVOICES_CSV, INVOICE_HEADERS)
                    if str(i.get("id")) == str(data.get("invoice_id", ""))), None)
    record = {
        "id": ("CN_" if kind == "credit" else "DN_") + uuid.uuid4().hex[:10],
        "note_date": str(data.get("note_date", "")) or current_timestamp()[:10],
        "invoice_id": str(data.get("invoice_id", "")),
        "school_id": str(data.get("school_id", "")) or (invoice or {}).get("school_id", ""),
        "vendorId": str(data.get("vendorId", "")) or (invoice or {}).get("vendorId", ""),
        "vendor": (invoice or {}).get("vendor", "") or str(data.get("vendor", "")),
        "amount": str(round(amount, 2)),
        "reason": str(data.get("reason", "")),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(path, NOTE_HEADERS, rows)
    if record["invoice_id"]:
        _recalc_invoice(record["invoice_id"])
    return record


# ============================================================================
# 6. BUDGETS
# ============================================================================
def list_budgets(school_id: str = "", allowed_ids=None) -> List[Dict]:
    rows = _scope(_read(BUDGETS_CSV, BUDGET_HEADERS), school_id, allowed_ids)
    invoices = _scope(_read(INVOICES_CSV, INVOICE_HEADERS), school_id, allowed_ids)
    out = []
    for b in rows:
        spent = sum(_num(i.get("total")) for i in invoices
                    if str(i.get("school_id")) == str(b.get("school_id")))
        allocated = _num(b.get("allocated"))
        row = dict(b)
        row["utilized"] = round(spent, 2)
        row["remaining"] = round(allocated - spent, 2)
        row["utilization_percent"] = round((spent / allocated * 100.0), 1) if allocated else 0.0
        out.append(row)
    return out


def create_budget(data: Dict, username: str) -> Dict:
    allocated = _num(data.get("allocated"))
    if allocated <= 0:
        raise ValueError("Allocated budget must be greater than zero.")
    rows = _read(BUDGETS_CSV, BUDGET_HEADERS)
    record = {
        "id": "BUD_" + uuid.uuid4().hex[:10],
        "school_id": str(data.get("school_id", "")),
        "academic_year": str(data.get("academic_year", "")),
        "category": str(data.get("category", "")).upper(),
        "allocated": str(round(allocated, 2)),
        "remarks": str(data.get("remarks", "")),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(BUDGETS_CSV, BUDGET_HEADERS, rows)
    return record


def delete_budget(budget_id: str) -> bool:
    rows = _read(BUDGETS_CSV, BUDGET_HEADERS)
    kept = [r for r in rows if str(r.get("id")) != str(budget_id)]
    if len(kept) == len(rows):
        return False
    atomic_csv_write(BUDGETS_CSV, BUDGET_HEADERS, kept)
    return True


# ============================================================================
# 7. VENDOR LEDGER, OUTSTANDING & FINANCE SUMMARY
# ============================================================================
def vendor_ledger(vendor_id: str, school_id: str = "", allowed_ids=None) -> Dict[str, Any]:
    invoices = [i for i in list_invoices(school_id, allowed_ids)
                if str(i.get("vendorId")) == str(vendor_id)]
    payments = [p for p in list_payments(school_id, allowed_ids)
                if str(p.get("vendorId")) == str(vendor_id)]
    credits = [n for n in list_notes("credit", school_id, allowed_ids)
               if str(n.get("vendorId")) == str(vendor_id)]
    debits = [n for n in list_notes("debit", school_id, allowed_ids)
              if str(n.get("vendorId")) == str(vendor_id)]

    entries = []
    for i in invoices:
        entries.append({"date": i.get("invoice_date"), "type": "Invoice",
                        "reference": i.get("invoice_number"), "debit": 0.0,
                        "credit": _num(i.get("total"))})
    for p in payments:
        entries.append({"date": p.get("payment_date"), "type": "Payment",
                        "reference": p.get("reference") or p.get("id"),
                        "debit": _num(p.get("amount")), "credit": 0.0})
    for n in credits:
        entries.append({"date": n.get("note_date"), "type": "Credit Note",
                        "reference": n.get("id"), "debit": _num(n.get("amount")), "credit": 0.0})
    for n in debits:
        entries.append({"date": n.get("note_date"), "type": "Debit Note",
                        "reference": n.get("id"), "debit": 0.0, "credit": _num(n.get("amount"))})
    entries.sort(key=lambda e: str(e.get("date") or ""))
    running = 0.0
    for e in entries:
        running += e["credit"] - e["debit"]
        e["balance"] = round(running, 2)
    vendor = database.get_vendor(vendor_id) or {}
    return {
        "vendor": vendor,
        "entries": entries,
        "billed": round(sum(_num(i.get("total")) for i in invoices), 2),
        "paid": round(sum(_num(p.get("amount")) for p in payments), 2),
        "outstanding": round(running, 2),
    }


def outstanding_payments(school_id: str = "", allowed_ids=None) -> List[Dict]:
    rows = [i for i in list_invoices(school_id, allowed_ids)
            if str(i.get("status")) != "Paid"]
    rows.sort(key=lambda r: str(r.get("due_date") or r.get("invoice_date") or ""))
    return rows


def finance_summary(school_id: str = "", allowed_ids=None) -> Dict[str, Any]:
    invoices = list_invoices(school_id, allowed_ids)
    payments = list_payments(school_id, allowed_ids)
    orders = list_orders(school_id, allowed_ids)
    budgets = list_budgets(school_id, allowed_ids)
    billed = sum(_num(i.get("total")) for i in invoices)
    paid = sum(_num(p.get("amount")) for p in payments)
    allocated = sum(_num(b.get("allocated")) for b in budgets)

    by_vendor: Dict[str, Dict[str, Any]] = {}
    for i in invoices:
        key = str(i.get("vendorId") or i.get("vendor") or "Unknown")
        v = by_vendor.setdefault(key, {"vendorId": i.get("vendorId"),
                                       "vendor": i.get("vendor") or key,
                                       "billed": 0.0, "paid": 0.0, "invoices": 0})
        v["billed"] += _num(i.get("total"))
        v["paid"] += _num(i.get("paid_amount"))
        v["invoices"] += 1
    top_vendors = sorted(by_vendor.values(), key=lambda v: v["billed"], reverse=True)[:10]
    for v in top_vendors:
        v["billed"] = round(v["billed"], 2)
        v["paid"] = round(v["paid"], 2)
        v["outstanding"] = round(v["billed"] - v["paid"], 2)

    monthly: Dict[str, float] = {}
    for i in invoices:
        month = str(i.get("invoice_date") or "")[:7]
        if month:
            monthly[month] = round(monthly.get(month, 0.0) + _num(i.get("total")), 2)

    return {
        "totalBilled": round(billed, 2),
        "totalPaid": round(paid, 2),
        "outstanding": round(billed - paid, 2),
        "pendingInvoices": len([i for i in invoices if i.get("status") == "Pending"]),
        "partialInvoices": len([i for i in invoices if i.get("status") == "Partial"]),
        "paidInvoices": len([i for i in invoices if i.get("status") == "Paid"]),
        "openOrders": len([o for o in orders if o.get("status") in ("Draft", "Approved", "Sent", "Partially Received")]),
        "pendingRequests": len(list_requests(school_id, allowed_ids, status="Pending")),
        "budgetAllocated": round(allocated, 2),
        "budgetUtilized": round(billed, 2),
        "budgetUtilization": round((billed / allocated * 100.0), 1) if allocated else 0.0,
        "topVendors": top_vendors,
        "monthlyProcurement": [{"month": m, "amount": monthly[m]} for m in sorted(monthly)],
    }
