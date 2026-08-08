import os

# Application Settings
APP_NAME = "Vedritam School Stock Ledger Management System"
VERSION = "3.0.0"

# Security Settings
SECRET_KEY = os.getenv("SECRET_KEY", "vedritam_super_secret_key_2026_!@#")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 720

# Signup Settings
SIGNUP_ENABLED = True
# Self sign-up always lands on the lowest privilege level.
SIGNUP_DEFAULT_ROLE = "user"
MIN_PASSWORD_LENGTH = 8

# --- Security policy ---------------------------------------------------------
PASSWORD_REQUIRE_COMPLEXITY = True   # upper + lower + digit
MAX_FAILED_ATTEMPTS = 5              # before temporary lockout
TWOFA_STEPUP_AFTER_FAILURES = 3      # failed passwords that force a 2FA challenge
LOCKOUT_MINUTES = 15
SESSION_IDLE_MINUTES = 30            # idle session timeout
TWOFA_ENFORCED_ROLES = ("super_admin",)  # roles allowed/expected to enrol in 2FA

# Reporting
LOW_STOCK_THRESHOLD = 50

# Database Paths (CSV Files)
# Every data file lives inside the "data" folder next to the code, so the
# application code and the stored data are cleanly separated.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Per-school data lives in data/schools/school_<id>/ (one ledger file each).
SCHOOL_DATA_DIR = os.path.join(DATA_DIR, "schools")
os.makedirs(SCHOOL_DATA_DIR, exist_ok=True)


def data_path(filename: str) -> str:
    """Path inside the data folder. Legacy files still sitting next to the
    code are moved in automatically the first time they are referenced."""
    target = os.path.join(DATA_DIR, filename)
    legacy = os.path.join(BASE_DIR, filename)
    if not os.path.exists(target) and os.path.exists(legacy):
        try:
            os.replace(legacy, target)
        except OSError:
            return legacy
    return target


USERS_CSV = data_path("users.csv")
SCHOOLS_CSV = data_path("schools.csv")
LEDGER_CSV = data_path("ledger.csv")          # legacy combined ledger (migrated per school)
AUDIT_CSV = data_path("audit.csv")
AI_SETTINGS_JSON = data_path("ai_settings.json")

# Master book / notebook / stationery catalog and vendor master
CATALOG_CSV = data_path("catalog.csv")
VENDORS_CSV = data_path("vendors.csv")

# Standards used across the ledger (PRE KG .. XII plus OTHERS)
STANDARDS = [
    "PRE KG", "LKG", "UKG", "I", "II", "III", "IV", "V",
    "VI", "VII", "VIII", "IX", "X", "XI", "XII", "OTHERS",
]
ARTICLE_CATEGORIES = ["TB", "NB", "STATIONERY", "INHOUSE"]


# --- Procurement, finance & catalog stores ----------------------------------
PURCHASE_REQUESTS_CSV = data_path("purchase_requests.csv")
PURCHASE_ORDERS_CSV = data_path("purchase_orders.csv")
GRN_CSV = data_path("grns.csv")
INVOICES_CSV = data_path("invoices.csv")
PAYMENTS_CSV = data_path("payments.csv")
CREDIT_NOTES_CSV = data_path("credit_notes.csv")
DEBIT_NOTES_CSV = data_path("debit_notes.csv")
BUDGETS_CSV = data_path("budgets.csv")

# Ledger module extensions
VENDOR_RETURNS_CSV = data_path("vendor_returns.csv")
LEDGER_FIELDS_JSON = data_path("ledger_fields.json")

def academic_years(count: int = 6):
    """Rolling academic-year list (2025-2026, 2026-2027 ...)."""
    import datetime
    y = datetime.date.today().year
    start = y - 2 if datetime.date.today().month >= 4 else y - 3
    return [f"{start + i}-{start + i + 1}" for i in range(count)]

RETURN_REASONS = ["Damaged", "Excess Supply", "Wrong Title", "Wrong Edition",
                  "Print Defect", "Late Delivery", "Not Required", "Other"]
LEDGER_FIELD_TYPES = ["text", "number", "decimal", "date"]

# Dropdown option sets shared by the UI and the API
GST_RATES = [0, 5, 12, 18, 28]
DISCOUNT_OPTIONS = [0, 5, 10, 15, 20, 25]
PAYMENT_TERMS = ["Immediate", "Net 15", "Net 30", "Net 45", "Net 60", "Advance"]
PAYMENT_MODES = ["Cash", "Cheque", "NEFT", "RTGS", "UPI", "Card", "Adjustment"]
PAYMENT_STATUSES = ["Pending", "Partial", "Paid"]
PO_STATUSES = ["Draft", "Approved", "Sent", "Partially Received", "Received", "Cancelled"]
REQUEST_STATUSES = ["Pending", "Approved", "Rejected", "Ordered"]
LANGUAGES = ["English", "Hindi", "Tamil", "Sanskrit", "French", "Telugu", "Malayalam", "Kannada", "Other"]
EDITIONS = ["2024", "2025", "2026", "Revised", "Latest"]
LOW_STOCK_LIMIT = 20   # closing balance at/below this is "Low Stock"

# Default stationery & office supplies seeded into the catalog
STATIONERY_ITEMS = [
    "Pens", "Pencils", "Erasers", "Sharpeners", "Scale", "Geometry Box", "Crayons",
    "Sketch Pens", "Colour Pencils", "Glue Stick", "Glue Bottle", "Chart Paper",
    "Craft Paper", "A4 Sheets", "Brown Covers", "Labels", "White Board Marker",
    "Duster", "Stapler", "Staple Pins", "Punch Machine", "Tape", "Scissors",
    "Files", "Registers", "Attendance Register", "Chalk", "Whiteboard",
    "Printer Paper", "Toner", "Ink Cartridge",
]

# Distribution and Transfers stores
DISTRIBUTIONS_CSV = data_path("distributions.csv")
TRANSFERS_CSV = data_path("transfers.csv")

# Messaging, notifications and security stores
CONVERSATIONS_CSV = data_path("conversations.csv")
MESSAGES_CSV = data_path("messages.csv")
RECEIPTS_CSV = data_path("receipts.csv")
E2EE_KEYS_CSV = data_path("e2ee_keys.csv")
NOTIFICATIONS_CSV = data_path("notifications.csv")
LOGIN_HISTORY_CSV = data_path("login_history.csv")
TYPING_JSON = data_path("typing.json")
TWOFA_JSON = data_path("twofa.json")

# Attachment storage (messages: images, PDFs, documents)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB
ALLOWED_UPLOAD_TYPES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
    ".csv", ".xlsx", ".xls", ".doc", ".docx", ".txt",
    ".enc",   # end-to-end encrypted attachment blob (server cannot read it)
}
TYPING_TTL_SECONDS = 6

# --- Performance -------------------------------------------------------------
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200
CACHE_TTL_SECONDS = 15

# Allowed Frontend Files (Security: Prevent Directory Traversal)
ALLOWED_STATIC_FILES = {
    "index.html",
    "vendors.html",
    "inventory.html",
    "vmodule.js",
    "modules.css",
    "dashboard.html",
    "schools.html",
    "ledger.html",
    "reports.html",
    "settings.html",
    "users.html",
    "activity.html",
    "distribution.html",
    "transfers.html",
    "todo.html",
    "messages.html",
    "security.html",
    "messages.js",
    "login.html",
    "style.css",
    "theme.css",
    "intro.css",
    "intro.js",
    "welcome.css",
    "welcome.js",
    "dashboard-ui.js",
    "shell.js",
    "app.js",
    "ledger.js",
    "ledger.css",

    "todo.js",
    "e2ee.js",
    "chatbot.js",
    "chatkit.js",
    "calls.js",
    "avatars.js",
    "sounds.js",
    "logo.png",
    "catalog.csv",
}
