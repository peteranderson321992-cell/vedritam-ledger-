# api.py
from fastapi.responses import StreamingResponse
import io
import csv
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

import base64
import database
import modules
import procurement
import reporting
import utils
import messaging
import profiles
import security as security_mod
import assistant
import cache
from config import (SIGNUP_ENABLED, SIGNUP_DEFAULT_ROLE, MIN_PASSWORD_LENGTH,
                    DEFAULT_PAGE_SIZE, TWOFA_ENFORCED_ROLES)

router = APIRouter(prefix="/api/v1")
security = HTTPBearer()


# --- Identity & RBAC ---------------------------------------------------------
# Hierarchy: super_admin > admin > staff > user
# super_admin : full platform access, every school, every account. Only one.
# admin       : the schools assigned to them; sees admins, staff and users of
#               those schools and may create all three inside them.
# staff       : the schools assigned to them; sees the user accounts of those
#               schools and may create user accounts inside them.
# user        : one school; cannot create accounts.
class Identity:
    """Current identity for the caller, read fresh from users.csv on
    every request so that disabling an account or changing its role takes
    effect immediately, without waiting for the token to expire)."""

    def __init__(self, record: Dict[str, Any]):
        self.username = record.get("username", "")
        self.role = database.normalize_role(record.get("role", ""))
        self.school_id = str(record.get("school_id", "") or "")
        self.status = record.get("status", "")

    @property
    def rank(self) -> int:
        return database.role_rank(self.role)

    @property
    def is_super_admin(self) -> bool:
        return self.role == database.SUPER_ADMIN

    @property
    def is_admin(self) -> bool:
        return self.role == database.ADMIN

    @property
    def is_staff(self) -> bool:
        return self.role == database.STAFF

    @property
    def is_manager(self) -> bool:
        """Super Admin, Admin or Staff - the roles that manage a school."""
        return self.rank >= database.ROLE_RANK[database.STAFF]

    @property
    def school_ids(self) -> Optional[List[str]]:
        """Schools this identity may read. None means every school.

        super_admin : every school on the platform.
        admin/staff : only the schools assigned to them (their own school plus
                      any school that lists them under assigned_staff).
        user        : only the single school pinned on their account.
        """
        if self.is_super_admin:
            return None
        if self.is_admin or self.is_staff:
            return database.school_ids_for_staff(self.username)
        return [self.school_id] if self.school_id else []

    @property
    def scope_school_id(self) -> str:
        """Single-school filter for legacy call sites; blank when unscoped."""
        return "" if self.is_manager else self.school_id

    @property
    def visible_usernames(self) -> Optional[List[str]]:
        """Accounts whose data this identity may read.

        super_admin : everyone.
        admin       : every admin, staff and user account of their schools,
                      plus the Super Admin, plus themselves.
        staff       : every user account of their schools, plus themselves.
        user        : only their own account.
        """
        if self.is_super_admin:
            return None
        me = [self.username]
        if not (self.is_admin or self.is_staff):
            return me
        allowed_schools = {str(i) for i in (self.school_ids or [])}
        names = list(me)
        for u in database.get_all_users():
            role = database.normalize_role(u.get("role", ""))
            uname = u.get("username", "")
            if self.is_admin and role == database.SUPER_ADMIN:
                names.append(uname)      # admins and the super admin see each other
                continue
            if self.is_staff and role != database.USER:
                continue                 # staff never see staff or administrators
            if role == database.SUPER_ADMIN:
                continue
            u_school = str(u.get("school_id", "") or "")
            if allowed_schools and u_school and u_school not in allowed_schools:
                continue
            names.append(uname)
        return sorted({n for n in names if n})

    @property
    def owner_filter(self) -> str:
        """The ledger is a shared register: every role reads the same rows."""
        return ""

    def assert_school(self, school_id) -> None:
        """Data isolation gate: raises unless this school is in scope."""
        allowed = self.school_ids
        if allowed is None:
            return
        if not school_id or str(school_id) not in {str(i) for i in allowed}:
            raise HTTPException(status_code=403, detail="You do not have access to this school.")

    def assert_can_manage_schools(self) -> None:
        """Adding, editing and removing a school is a Super Admin action."""
        if not self.is_super_admin:
            raise HTTPException(status_code=403,
                                detail="Only the Super Admin can add or change schools.")

    # --- Account management rules -------------------------------------------
    @property
    def creatable_roles(self) -> List[str]:
        """Roles this identity is allowed to hand out."""
        if self.is_super_admin or self.is_admin:
            return [database.ADMIN, database.STAFF, database.USER]
        if self.is_staff:
            return [database.USER]
        return []

    def assert_can_create_role(self, role: str) -> str:
        role = database.normalize_role(role)
        allowed = self.creatable_roles
        if not allowed:
            raise HTTPException(status_code=403,
                                detail="Your account cannot create other accounts.")
        if role not in allowed:
            raise HTTPException(
                status_code=403,
                detail="You can only create these account types: "
                       + ", ".join(database.ROLE_LABELS[r] for r in allowed) + ".")
        return role

    def assert_account_school(self, school_id: str) -> str:
        """Admins and staff may only place accounts in their own schools."""
        school_id = str(school_id or "")
        if self.is_super_admin:
            return school_id
        if not school_id:
            raise HTTPException(status_code=400,
                                detail="Choose one of your assigned schools for this account.")
        self.assert_school(school_id)
        return school_id

    def assert_can_manage_user(self, target: Dict[str, Any]) -> None:
        """Gate for editing / deleting / resetting another account."""
        if self.is_super_admin:
            return
        uname = str(target.get("username", ""))
        target_role = database.normalize_role(target.get("role", ""))
        if uname.lower() == self.username.lower():
            return
        if not (self.is_admin or self.is_staff):
            raise HTTPException(status_code=403, detail="You cannot manage other accounts.")
        if database.is_protected_admin(uname) or target_role == database.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="The Super Admin account cannot be changed.")
        if database.role_rank(target_role) > self.rank:
            raise HTTPException(status_code=403,
                                detail="You cannot manage an account above your own level.")
        if self.is_staff and target_role != database.USER:
            raise HTTPException(status_code=403,
                                detail="Staff accounts can only manage user accounts.")
        visible = {n.lower() for n in (self.visible_usernames or [])}
        if uname.lower() not in visible:
            raise HTTPException(status_code=403,
                                detail="That account is not in one of your schools.")


def get_identity(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Identity:
    payload = utils.decode_access_token(credentials.credentials)
    record = database.get_user_raw(payload.get("sub"))
    if not record:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    if record.get("status") != "Active":
        raise HTTPException(status_code=403, detail=f"Account is {record.get('status', 'inactive')}. Contact your administrator.")
    username = record.get("username", "")
    # One device at a time: a newer sign-in elsewhere replaces this session.
    if not security_mod.device_session_valid(username, str(payload.get("sid", "") or "")):
        try:
            security_mod.record_login(username, "SESSION_REPLACED",
                                      detail="Signed in on another device")
        except Exception:
            pass  # audit logging must never break the response
        raise HTTPException(
            status_code=401,
            detail="This account signed in on another device. You have been signed out here.")
    # Idle session timeout: a token stays valid only while the account keeps
    # using it. Going quiet for longer than the idle window ends the session.
    if security_mod.session_expired(username):
        try:
            security_mod.end_session(username)
            security_mod.record_login(username, "SESSION_TIMEOUT", detail="Idle session expired")
        except Exception:
            pass  # audit logging must never break the response
        raise HTTPException(status_code=401, detail="Session timed out due to inactivity. Please sign in again.")
    try:
        security_mod.touch_session(username)
    except Exception:
        pass
    try:
        profiles.touch(username)   # "last online" for the people/group panels
    except Exception:
        pass
    return Identity(record)


def get_current_user(identity: Identity = Depends(get_identity)) -> str:
    return identity.username


def require_super_admin(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_super_admin:
        raise HTTPException(status_code=403, detail="Super Admin access required.")
    return identity


def require_admin(identity: Identity = Depends(get_identity)) -> Identity:
    """Super Admin or Admin."""
    if not (identity.is_super_admin or identity.is_admin):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return identity


def require_staff(identity: Identity = Depends(get_identity)) -> Identity:
    """Staff, Admin or Super Admin: management actions inside a school."""
    if not identity.is_manager:
        raise HTTPException(status_code=403, detail="Staff access required.")
    return identity


# --- Schemas -----------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str
    code: Optional[str] = None  # TOTP / backup code for 2FA accounts
class SyncRequest(BaseModel):
    schoolId: int
    classId: int = 0
    standard: str = ""
    updates: List[Dict[str, Any]] = []
    deletes: List[str] = []

class LedgerFieldCreate(BaseModel):
    label: str
    type: str = "text"


class VendorReturnCreate(BaseModel):
    schoolId: int
    classId: int = 0
    ledger_id: str = ""
    vendorId: str = ""
    vendor: str = ""
    vendorContact: str = ""
    vendorGst: str = ""
    creditNoteNo: str
    bookName: str
    subject: str = ""
    publication: str = ""
    edition: str = ""
    academicYear: str = ""
    quantity: int = 0
    returnDate: str = ""
    reason: str = ""
    remarks: str = ""


class SchoolCreate(BaseModel):
    """The School entity of the multi-school platform."""
    name: str
    code: str = ""
    location: str = ""
    logo: str = ""
    address: str = ""
    contact: str = ""
    academic_year: str = ""
    status: str = "Active"
    assigned_staff: List[str] = []
    settings: Dict[str, Any] = {}


class SchoolUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    location: Optional[str] = None
    logo: Optional[str] = None
    address: Optional[str] = None
    contact: Optional[str] = None
    academic_year: Optional[str] = None
    status: Optional[str] = None
    assigned_staff: Optional[List[str]] = None
    settings: Optional[Dict[str, Any]] = None
class ClassCreate(BaseModel): name: str; strength: int
class StrengthUpdate(BaseModel):
    strength: int


class SignupRequest(BaseModel):
    username: str
    password: str
    fullName: str = ""
    email: str = ""
    schoolName: str = ""


class UserCreate(BaseModel):
    username: str
    # Optional: administrators no longer type a password when creating an
    # account. When omitted the server generates a strong random one, which
    # the admin can replace later with "Reset Password".
    password: Optional[str] = None
    role: str = database.USER
    fullName: str = ""
    email: str = ""
    school_id: str = ""
    status: str = "Active"


class UserUpdate(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None
    fullName: Optional[str] = None
    email: Optional[str] = None
    school_id: Optional[str] = None


class PasswordSet(BaseModel):
    newPassword: str


class PasswordChange(BaseModel):
    currentPassword: str
    newPassword: str


def utils_quote(value: str) -> str:
    import urllib.parse
    return urllib.parse.quote(str(value or ""))


def utils_generate_password() -> str:
    """Strong random password used when an admin creates an account without
    typing one. It is never displayed; use Reset Password to set a known one."""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(14))
        if (any(c.islower() for c in candidate) and any(c.isupper() for c in candidate)
                and any(c.isdigit() for c in candidate)):
            return candidate


def _validate_password(pwd: str, username: str = ""):
    ok, reason = security_mod.validate_password(pwd, username)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)


def _client_ip(request: Optional[Request]) -> str:
    if not request:
        return ""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


# --- Authentication ----------------------------------------------------------
@router.post("/auth/login")
def login(request: LoginRequest, http_request: Request = None):
    ip = _client_ip(http_request)
    agent = http_request.headers.get("user-agent", "") if http_request else ""

    locked = security_mod.check_lockout(request.username)
    if locked:
        security_mod.record_login(request.username, "LOCKED", ip, agent,
                                  "Too many failed attempts")
        raise HTTPException(status_code=429,
                            detail=f"Too many failed attempts. Try again in {locked // 60 + 1} minute(s).")

    record = database.get_user_raw(request.username)
    if not record:
        security_mod.register_failure(request.username)
        security_mod.record_login(request.username, "FAILED", ip, agent, "Unknown account")
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    if not utils.verify_password(request.password, record.get('password_hash', '')):
        left = security_mod.register_failure(request.username)
        security_mod.record_login(record['username'], "FAILED", ip, agent, "Wrong password")
        database.log_action(record['username'], record.get('role', ''), "LOGIN_FAILED", "auth", record['username'], "Wrong password")
        raise HTTPException(status_code=401,
                            detail="Invalid username or password." +
                                   (f" {left} attempt(s) left before lockout." if left <= 2 else ""))
    if record.get('status') == 'Pending':
        raise HTTPException(status_code=403, detail="Your account is awaiting administrator approval.")
    if record.get('status') == 'Disabled':
        raise HTTPException(status_code=403, detail="This account has been disabled. Contact your administrator.")

    user = database.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # --- Two-factor for Super Admin -----------------------------------------
    # Always challenged when 2FA is enrolled, and additionally forced when the
    # account has just failed the password several times in a row (step-up).
    login_role = database.normalize_role(user["role"])
    enrolled = security_mod.twofa_required(user["username"])
    stepup = (login_role in TWOFA_ENFORCED_ROLES
              and security_mod.stepup_required(request.username))
    force_setup = False

    if enrolled or stepup:
        if enrolled:
            if not request.code:
                security_mod.record_login(
                    user['username'], "2FA_REQUIRED", ip, agent,
                    "Repeated failed passwords" if stepup else "")
                return {"status": "2fa_required",
                        "reason": "failed_attempts" if stepup else "enabled",
                        "message": ("Several sign-in attempts failed. Enter the 6-digit code "
                                    "from your authenticator app to continue.") if stepup else
                                   "Enter the 6-digit code from your authenticator app."}
            if not security_mod.verify_totp(user["username"], request.code):
                security_mod.register_failure(request.username)
                security_mod.record_login(user['username'], "2FA_FAILED", ip, agent, "Bad code")
                raise HTTPException(status_code=401, detail="That verification code is not valid.")
        else:
            # Not enrolled yet: let the sign-in through but force enrolment.
            force_setup = True
            security_mod.record_login(user['username'], "2FA_REQUIRED", ip, agent,
                                      "Repeated failed passwords - enrolment forced")

    security_mod.clear_failures(request.username)
    security_mod.touch_session(user["username"])
    # Single active device: this sign-in invalidates every other session.
    previous = security_mod.active_device_session(user["username"])
    session_id = security_mod.start_device_session(user["username"], agent, ip)
    if previous:
        database.log_action(user['username'], user['role'], "SESSION_REPLACED", "auth",
                            user['username'], "Signed in on a new device; other device signed out")
    security_mod.record_login(user['username'], "SUCCESS", ip, agent,
                              f"role={database.normalize_role(user['role'])}")
    database.update_user_login(user['username'])
    database.log_action(user['username'], user['role'], "LOGIN", "auth", user['username'], "Signed in")
    role = database.normalize_role(user["role"])
    return {
        "access_token": utils.create_access_token({"sub": user["username"], "role": role,
                                                   "sid": session_id}),
        "username": user["username"],
        "role": role,
        "replaced_other_device": bool(previous),
        "fullName": user.get("fullName", ""),
        "school_id": user.get("school_id", ""),
        "twofa_enabled": security_mod.twofa_required(user["username"]),
        "twofa_recommended": role in TWOFA_ENFORCED_ROLES and not security_mod.twofa_required(user["username"]),
        "twofa_setup_required": force_setup,
        "session_timeout_minutes": security_mod.get_idle_minutes(),
    }


@router.post("/auth/signup")
def signup(request: SignupRequest):
    """Public account request. The account is created as Pending — an
    administrator must approve it before the user can sign in."""
    if not SIGNUP_ENABLED:
        raise HTTPException(status_code=403, detail="Self sign-up is disabled. Ask your administrator for an account.")
    _validate_password(request.password, request.username)
    # Match the requested school name to an existing school when possible.
    school_id = ""
    requested = (request.schoolName or "").strip()
    if requested:
        match = next((s for s in database.get_all_schools() if s["name"].lower() == requested.lower()
                      or (s["code"] or "").lower() == requested.lower()), None)
        if match:
            school_id = str(match["id"])
    try:
        created = database.create_user(
            username=request.username,
            password=request.password,
            role=SIGNUP_DEFAULT_ROLE,
            full_name=request.fullName,
            email=request.email,
            school_id=school_id,
            status="Pending",
            created_by="self-signup",
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    database.log_action(created["username"], created["role"], "SIGNUP_REQUEST", "user", created["username"],
                        f"Requested access for school '{requested or 'unassigned'}'")
    return {"status": "pending",
            "message": "Account request submitted. An administrator must approve it before you can sign in."}


@router.get("/auth/me")
def me(identity: Identity = Depends(get_identity)):
    profile = database.get_user_profile(identity.username)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found.")
    return profile


@router.post("/auth/change-password")
def change_own_password(request: PasswordChange, identity: Identity = Depends(get_identity)):
    record = database.get_user_raw(identity.username)
    if not utils.verify_password(request.currentPassword, record.get('password_hash', '')):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    _validate_password(request.newPassword, identity.username)
    database.set_password(identity.username, request.newPassword)
    security_mod.record_login(identity.username, "PASSWORD_CHANGED", detail="Own password changed")
    database.log_action(identity.username, identity.role, "PASSWORD_CHANGE", "user", identity.username,
                        "Changed own password")
    return {"status": "success"}


# --- User Administration ------------------------------------------------------
# Super Admin manages every account; Staff may only view accounts inside the
# schools assigned to them.
@router.get("/users")
def list_users(identity: Identity = Depends(require_staff)):
    """Super Admin sees every account (users, staff and admins). Staff see the
    ordinary user accounts of the schools assigned to them, plus their own."""
    users = database.get_all_users()
    if identity.is_super_admin:
        return users
    visible = {n.lower() for n in (identity.visible_usernames or [])}
    return [u for u in users if str(u.get("username", "")).lower() in visible]


@router.get("/users/meta")
def users_meta(identity: Identity = Depends(get_identity)):
    """What the signed-in account is allowed to do on the Users screen."""
    return {
        "role": identity.role,
        "roleLabel": database.ROLE_LABELS.get(identity.role, "User"),
        "canCreate": bool(identity.creatable_roles),
        "creatableRoles": [{"value": r, "label": database.ROLE_LABELS[r]}
                           for r in identity.creatable_roles],
        "canDelete": identity.is_super_admin or identity.is_admin,
        "canManageSchools": identity.is_super_admin,
        "schoolLocked": not identity.is_super_admin,
        "schoolIds": identity.school_ids,
    }


@router.post("/users")
def create_user(request: UserCreate, admin: Identity = Depends(require_staff)):
    """Super Admin and Admin create Admin / Staff / User accounts; Staff create
    User accounts. Admin and Staff can only create inside their own schools."""
    role = admin.assert_can_create_role(request.role)
    school_id = admin.assert_account_school(request.school_id)
    password = (request.password or "").strip() or utils_generate_password()
    _validate_password(password, request.username)
    try:
        created = database.create_user(
            username=request.username,
            password=password,
            role=role,
            full_name=request.fullName,
            email=request.email,
            school_id=school_id,
            status=request.status,
            created_by=admin.username,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "USER_CREATE", "user", created["username"],
                        f"Created {created['role']} account (status {created['status']})")
    return {"status": "success", "data": created}


@router.put("/users/{username}")
def edit_user(username: str, request: UserUpdate, admin: Identity = Depends(require_staff)):
    target = database.get_user_raw(username)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    admin.assert_can_manage_user(target)
    school_id = request.school_id
    if request.role is not None and not admin.is_super_admin:
        # Changing a role is bounded by what the caller may hand out.
        admin.assert_can_create_role(request.role)
    if school_id is not None and not admin.is_super_admin:
        school_id = admin.assert_account_school(school_id)
    try:
        updated = database.update_user(
            username,
            role=request.role,
            status=request.status,
            full_name=request.fullName,
            email=request.email,
            school_id=school_id,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    changes = ", ".join(f"{k}={v}" for k, v in request.dict(exclude_none=True).items()) or "no changes"
    database.log_action(admin.username, admin.role, "USER_UPDATE", "user", username, changes)
    return {"status": "success", "data": updated}


@router.post("/users/{username}/password")
def reset_user_password(username: str, request: PasswordSet, admin: Identity = Depends(require_staff)):
    """Super Admin can reset any password. Admin and Staff may reset the
    passwords of the accounts they manage inside their own schools."""
    _validate_password(request.newPassword, username)
    target = database.get_user_raw(username)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    admin.assert_can_manage_user(target)
    database.set_password(username, request.newPassword)
    # A password change ends any session that account has open elsewhere.
    security_mod.end_session(username)
    database.log_action(admin.username, admin.role, "PASSWORD_RESET", "user", username,
                        "Administrator reset the password")
    return {"status": "success"}


@router.delete("/users/{username}")
def remove_user(username: str, admin: Identity = Depends(require_admin)):
    """Only the Super Admin and Admins remove accounts. The built-in Super
    Admin account can never be deleted."""
    if username.lower() == admin.username.lower():
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    target = database.get_user_raw(username)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    admin.assert_can_manage_user(target)
    try:
        database.delete_user(username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "USER_DELETE", "user", username, "Account removed")
    return {"status": "success"}


# --- Activity Log -------------------------------------------------------------
# Super Admin: every account. Staff: the user accounts in their schools plus
# their own. Ordinary users: no activity log at all.
@router.get("/audit")
def audit(limit: int = 200, offset: int = 0, page: int = 0, username: str = "",
          action: str = "", identity: Identity = Depends(require_staff)):
    """Paginated when `page`/`offset` is supplied; plain list otherwise so the
    existing Activity Log screen keeps working unchanged."""
    visible = identity.visible_usernames
    if page or offset:
        if page:
            offset = (max(1, page) - 1) * (limit or DEFAULT_PAGE_SIZE)
        payload = database.get_audit_log_page(limit=limit or DEFAULT_PAGE_SIZE,
                                              offset=offset, username=username, action=action)
        if visible is not None and isinstance(payload, dict) and isinstance(payload.get("items"), list):
            allowed = {n.lower() for n in visible}
            payload["items"] = [r for r in payload["items"]
                                if str(r.get("username", "")).lower() in allowed]
        return payload
    rows = database.get_audit_log(limit=limit, username=username, action=action)
    if visible is not None:
        allowed = {n.lower() for n in visible}
        rows = [r for r in rows if str(r.get("username", "")).lower() in allowed]
    return rows


@router.get("/audit/download")
def audit_download(identity: Identity = Depends(require_staff)):
    rows = database.get_audit_log(limit=2000)
    visible = identity.visible_usernames
    if visible is not None:
        allowed = {n.lower() for n in visible}
        rows = [r for r in rows if str(r.get("username", "")).lower() in allowed]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "User", "Role", "Action", "Entity", "Reference", "Details"])
    for r in rows:
        writer.writerow([r.get("timestamp", ""), r.get("username", ""), r.get("role", ""),
                         r.get("action", ""), r.get("entity", ""), r.get("entity_id", ""), r.get("details", "")])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="Vedritam_Activity_Log.csv"'})


# --- Global search ------------------------------------------------------------
def _hit(kind, title, subtitle, url, meta=""):
    return {"type": kind, "title": title, "subtitle": subtitle, "url": url, "meta": meta}


_SEARCH_PAGES = [
    ("Dashboard", "Overview, stock analytics and charts", "dashboard.html",
     "dashboard home overview analytics charts stats"),
    ("Schools", "Browse, add and manage schools", "schools.html",
     "schools school add school institutions branches classes"),
    ("Ledger", "Stock register entries per class", "ledger.html",
     "ledger stock register books entries purchase invoice vendor"),
    ("Distribution", "Issue books to recipients", "distribution.html",
     "distribution issue distribute books recipient"),
    ("Transfers", "Move stock between schools", "transfers.html",
     "transfers transfer stock move approve decline"),
    ("Reports", "Reports and exports", "reports.html",
     "reports report export excel csv analytics summary"),
    ("Messages", "Conversations and announcements", "messages.html",
     "messages chat conversation announcement inbox"),
    ("Users", "Accounts and roles", "users.html",
     "users accounts staff admin roles permissions people"),
    ("Activity Log", "Audit trail of every action", "activity.html",
     "activity audit log history trail actions"),
    ("Security", "Sessions, 2FA and login history", "security.html",
     "security 2fa two factor sessions login history password"),
    ("Settings", "Application and AI settings", "settings.html",
     "settings preferences configuration ai api key theme profile"),
    ("To-do", "Tasks and reminders", "todo.html", "todo tasks reminders checklist"),
]


def _matches(query: str, *values) -> bool:
    blob = " ".join(str(v or "") for v in values).lower()
    return all(term in blob for term in query)


@router.get("/search")
def global_search(q: str = "", limit: int = 40, identity: Identity = Depends(get_identity)):
    """Searches everything the caller is allowed to see: pages, schools,
    classes, users, vendors, catalog titles, ledger rows, distributions,
    transfers and the activity log. Always returns a payload; `results` is an
    empty list when nothing matched so the UI can say "No results found"."""
    raw = (q or "").strip()
    if not raw:
        return {"query": "", "count": 0, "results": [], "groups": {}}
    terms = [t for t in raw.lower().split() if t]
    results: List[Dict[str, Any]] = []

    # 1. Pages / navigation
    for title, subtitle, url, keywords in _SEARCH_PAGES:
        if _matches(terms, title, subtitle, keywords):
            results.append(_hit("Page", title, subtitle, url))

    # 2. Schools (and their classes) - scoped
    schools = database.get_all_schools(school_id_filter=identity.scope_school_id,
                                       allowed_ids=identity.school_ids)
    school_names = {}
    for s in schools:
        school_names[str(s.get("id"))] = s.get("name", "")
        if _matches(terms, s.get("name"), s.get("code"), s.get("location"),
                    s.get("address"), s.get("contact"), s.get("academic_year"),
                    s.get("status"), "school"):
            results.append(_hit("School", s.get("name", ""),
                                " - ".join([p for p in [s.get("code", ""), s.get("location", "")] if p]) or "School",
                                "schools.html?q=" + utils_quote(s.get("name", "")),
                                str(s.get("id"))))
        for c in (database.get_classes_for_school(s.get("id")) or []):
            if _matches(terms, c.get("name"), s.get("name"), "class"):
                results.append(_hit("Class", str(c.get("name", "")),
                                    "Class in " + s.get("name", ""),
                                    "ledger.html", str(s.get("id"))))

    # 3. Users - only accounts this identity may read
    try:
        visible = identity.visible_usernames
        for u in database.get_all_users():
            if visible is not None and str(u.get("username", "")).lower() not in {n.lower() for n in visible}:
                continue
            if _matches(terms, u.get("username"), u.get("fullName"), u.get("email"),
                        u.get("role"), u.get("school_name"), "user account"):
                results.append(_hit("User", u.get("fullName") or u.get("username", ""),
                                    (u.get("role", "") or "") + (" - " + u.get("school_name", "") if u.get("school_name") else ""),
                                    "users.html", u.get("username", "")))
    except Exception:
        pass

    # 4. Vendors
    for v in database.get_vendors():
        if _matches(terms, v.get("name"), v.get("vendorId"), v.get("contact"), v.get("gst"), "vendor"):
            results.append(_hit("Vendor", v.get("name", "") or v.get("vendorId", ""),
                                "Vendor" + (" - " + v.get("contact", "") if v.get("contact") else ""),
                                "ledger.html", v.get("vendorId", "")))

    # 5. Catalog titles
    for c in database.get_catalog():
        if _matches(terms, c.get("title"), c.get("subject"), c.get("publication"),
                    c.get("standard"), c.get("category"), "catalog book title"):
            results.append(_hit("Catalog", c.get("title", ""),
                                " - ".join([p for p in [c.get("standard", ""), c.get("category", ""),
                                                        c.get("publication", "")] if p]) or "Catalog item",
                                "ledger.html", c.get("standard", "")))

    # 6. Ledger rows - scoped to the schools the caller may read
    allowed_ids = identity.school_ids
    ledger_rows: List[Dict[str, Any]] = []
    if allowed_ids is None:
        ledger_rows = database.read_ledger()
    else:
        for sid in allowed_ids:
            try:
                ledger_rows.extend(database.read_ledger(sid))
            except Exception:
                continue
    for r in ledger_rows:
        if _matches(terms, r.get("bookName"), r.get("subject"), r.get("publication"),
                    r.get("vendor"), r.get("invoiceRef"), r.get("category"),
                    r.get("standard"), r.get("remarks"), "ledger stock"):
            sid = str(r.get("school_id", ""))
            results.append(_hit("Ledger", r.get("bookName", "") or r.get("subject", "") or "Ledger entry",
                                " - ".join([p for p in [school_names.get(sid, ""), r.get("standard", ""),
                                                        r.get("vendor", "")] if p]) or "Ledger entry",
                                "ledger.html", r.get("id", "")))

    # 7. Distributions
    for d in modules.list_distributions(identity.scope_school_id, allowed_ids=allowed_ids,
                                        created_by=identity.owner_filter):
        if _matches(terms, d.get("book_name"), d.get("recipient"), d.get("remarks"),
                    d.get("created_by"), "distribution"):
            results.append(_hit("Distribution", d.get("book_name", "") or "Distribution",
                                "Issued " + str(d.get("quantity", "")) + " to " + str(d.get("recipient", "")),
                                "distribution.html", d.get("id", "")))

    # 8. Transfers
    for t in modules.list_transfers(identity.scope_school_id, allowed_ids=allowed_ids,
                                    created_by=identity.owner_filter):
        if _matches(terms, t.get("book_name"), t.get("status"), t.get("remarks"),
                    t.get("created_by"), school_names.get(str(t.get("from_school_id")), ""),
                    school_names.get(str(t.get("to_school_id")), ""), "transfer"):
            results.append(_hit("Transfer", t.get("book_name", "") or "Transfer",
                                (school_names.get(str(t.get("from_school_id")), "") or "School") + " -> " +
                                (school_names.get(str(t.get("to_school_id")), "") or "School") +
                                " (" + str(t.get("status", "")) + ")",
                                "transfers.html", t.get("id", "")))

    # 9. Activity log - staff and above only
    if identity.role != database.USER:
        try:
            visible = identity.visible_usernames
            for a in database.get_audit_log(limit=500):
                if visible is not None and str(a.get("username", "")).lower() not in {n.lower() for n in visible}:
                    continue
                if _matches(terms, a.get("action"), a.get("details"), a.get("entity"),
                            a.get("username"), "activity audit"):
                    results.append(_hit("Activity", a.get("action", "") or "Activity",
                                        (a.get("details", "") or "")[:120] or (a.get("username", "") + " - " + a.get("timestamp", "")),
                                        "activity.html", a.get("timestamp", "")))
        except Exception:
            pass

    total = len(results)
    limited = results[: max(1, min(int(limit or 40), 200))]
    groups: Dict[str, int] = {}
    for r in results:
        groups[r["type"]] = groups.get(r["type"], 0) + 1
    return {"query": raw, "count": total, "returned": len(limited),
            "results": limited, "groups": groups,
            "message": "" if total else "No results found for '" + raw + "'."}


# --- Dashboard ----------------------------------------------------------------
@router.get("/dashboard")
def dashboard(school_id: str = "", date_from: str = "", date_to: str = "",
              staff: str = "", user: str = "", identity: Identity = Depends(get_identity)):
    """Live analytics computed from the real ledger, schools and audit data.
    The same aggregation powers /reports, so cards, charts and exports always
    show identical numbers."""
    data = reporting.build_report(
        school_id=school_id, date_from=date_from, date_to=date_to,
        staff=staff, user=user,
        scope_school_id=identity.scope_school_id,
        scope_school_ids=identity.school_ids,
        include_activity=not (identity.role == database.USER),
        visible_usernames=identity.visible_usernames,
    )
    data["presence"] = database.presence_snapshot()
    data["mySchoolsAdded"] = database.schools_created_by(identity.username)
    data["me"] = {"username": identity.username, "role": identity.role, "school_id": identity.school_id}
    return data


# --- Schools ------------------------------------------------------------------
@router.get("/schools")
def get_schools(identity: Identity = Depends(get_identity)):
    """Data isolation: each role only ever receives the schools in its scope."""
    return database.get_all_schools(school_id_filter=identity.scope_school_id,
                                    allowed_ids=identity.school_ids)


@router.get("/schools/{school_id}")
def get_school_detail(school_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    school = database.get_school(school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found.")
    return school


@router.post("/schools")
def create_school(request: SchoolCreate, identity: Identity = Depends(get_identity)):
    # Onboarding a school onto the platform is a Super Admin action.
    identity.assert_can_manage_schools()
    try:
        created = database.add_school(
            name=request.name, code=request.code, location=request.location,
            logo=request.logo, address=request.address, contact=request.contact,
            academic_year=request.academic_year, status=request.status,
            assigned_staff=request.assigned_staff, settings=request.settings,
        )
        database.log_action(identity.username, identity.role, "SCHOOL_CREATE", "school", created["id"], f"Added '{created['name']}'")
        return {"status": "success", "data": created}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.put("/schools/{school_id}")
def edit_school(school_id: int, request: SchoolUpdate, identity: Identity = Depends(get_identity)):
    identity.assert_can_manage_schools()
    try:
        updated = database.update_school(school_id, **request.model_dump(exclude_unset=True))
        database.log_action(identity.username, identity.role, "SCHOOL_UPDATE", "school", school_id,
                            f"Updated '{updated['name']}'")
        return {"status": "success", "data": updated}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/schools/{school_id}")
def delete_school(school_id: int, admin: Identity = Depends(get_identity)):
    admin.assert_can_manage_schools()
    try:
        database.delete_school(school_id)
        database.log_action(admin.username, admin.role, "SCHOOL_DELETE", "school", school_id, "School removed")
        return {"status": "success"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))



@router.get("/schools/{school_id}/classes")
def get_classes(school_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    return database.get_classes_for_school(school_id)


@router.post("/schools/{school_id}/classes")
def create_class(school_id: int, request: ClassCreate, admin: Identity = Depends(get_identity)):
    admin.assert_school(school_id)
    if request.strength < 0:
        raise HTTPException(status_code=400, detail="Class strength cannot be negative.")
    try:
        created = database.add_class_to_school(school_id, request.name.strip(), request.strength)
        database.log_action(admin.username, admin.role, "CLASS_CREATE", "class", f"{school_id}:{created['id']}",
                            f"Added class '{created['name']}' (strength {created['strength']})")
        return {"status": "success", "data": created}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/schools/{school_id}/classes/{class_id}")
def delete_class(school_id: int, class_id: int, admin: Identity = Depends(get_identity)):
    admin.assert_school(school_id)
    try:
        database.delete_class(school_id, class_id)
        database.log_action(admin.username, admin.role, "CLASS_DELETE", "class", f"{school_id}:{class_id}", "Class removed")
        return {"status": "success"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.put("/schools/{school_id}/classes/{class_id}/strength")
def update_strength(school_id: int, class_id: int, request: StrengthUpdate, admin: Identity = Depends(get_identity)):
    admin.assert_school(school_id)
    if request.strength < 0:
        raise HTTPException(status_code=400, detail="Class strength cannot be negative.")
    try:
        database.update_class_strength(school_id, class_id, request.strength)
        database.log_action(admin.username, admin.role, "CLASS_STRENGTH", "class", f"{school_id}:{class_id}",
                            f"Strength set to {request.strength}")
        return {"status": "success"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


# --- Catalog (master book / notebook list) --------------------------------------
@router.get("/catalog/standards")
def catalog_standards(identity: Identity = Depends(get_identity)):
    """Standards for the ledger dropdown, plus the article categories."""
    return {
        "standards": database.get_catalog_standards(),
        "categories": database.get_article_categories(),
        "publications": database.get_catalog_publications(),
    }


@router.get("/catalog")
def catalog(standard: str = "ALL", category: str = "ALL",
            identity: Identity = Depends(get_identity)):
    """Titles available for a standard. standard=ALL returns every standard."""
    return database.get_catalog(standard, category)


@router.get("/vendors")
def vendors(identity: Identity = Depends(get_identity)):
    return database.get_vendors()


# --- Ledger module: metadata, admin fields and vendor returns ------------------
@router.get("/ledger/meta/{school_id}")
def ledger_meta(school_id: int, class_id: int = 0, identity: Identity = Depends(get_identity)):
    """One call that feeds every ledger dropdown from the other modules:
    Institution Directory, Resource Catalog and Vendor Management."""
    identity.assert_school(school_id)
    allowed = identity.school_ids
    allowed_ids = None if allowed is None else [str(i) for i in allowed]
    return database.ledger_meta(school_id, class_id, allowed_ids=allowed_ids,
                                username=identity.username)


@router.get("/ledger/opening/{school_id}/{class_id}")
def ledger_opening(school_id: int, class_id: int, resource: str = "", academic_year: str = "",
                   exclude: str = "", identity: Identity = Depends(get_identity)):
    """Opening balance = previous closing balance for the same resource."""
    identity.assert_school(school_id)
    return {"openingBalance": database.previous_closing_balance(
        school_id, class_id, resource, academic_year, exclude)}


@router.get("/ledger/fields")
def ledger_fields(identity: Identity = Depends(get_identity)):
    return {"fields": database.get_ledger_custom_fields(identity.username),
            "canManage": True,
            "types": list(database.LEDGER_FIELD_TYPES)}


@router.post("/ledger/fields")
def create_ledger_field(request: LedgerFieldCreate, identity: Identity = Depends(get_identity)):
    """Any signed-in user can add an extra ledger column."""
    try:
        field = database.add_ledger_custom_field(request.label, request.type, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "LEDGER_FIELD_ADD", "ledger",
                        field["key"], f"Added ledger field '{field['label']}' ({field['type']})")
    return {"status": "success", "field": field}


@router.delete("/ledger/fields/{key}")
def remove_ledger_field(key: str, identity: Identity = Depends(get_identity)):
    """Any signed-in user may remove a ledger field, and the removal applies to
    everyone (adding a field, by contrast, only affects the creator's ledger)."""
    if not database.delete_ledger_custom_field(key):
        raise HTTPException(status_code=404, detail="Ledger field not found.")
    database.log_action(identity.username, identity.role, "LEDGER_FIELD_DELETE", "ledger", key,
                        f"Removed ledger field '{key}'")
    return {"status": "success"}


@router.get("/ledger/returns/{school_id}")
def list_vendor_returns(school_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    return database.read_vendor_returns(school_id)


@router.post("/ledger/returns")
def create_vendor_return(request: VendorReturnCreate, identity: Identity = Depends(get_identity)):
    """Return to Vendor: records the credit note and reduces the closing balance."""
    identity.assert_school(request.schoolId)
    try:
        record = database.save_vendor_return(request.schoolId, request.classId,
                                             request.model_dump(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "VENDOR_RETURN", "ledger",
                        record["id"],
                        f"Returned {record['quantity']} x {record['bookName']} to {record['vendor']} "
                        f"(credit note {record['creditNoteNo']})")
    return {"status": "success", "record": record}


# --- Ledger --------------------------------------------------------------------
@router.get("/ledger/standard/{school_id}/{standard}")
def get_ledger_for_standard(school_id: int, standard: str, identity: Identity = Depends(get_identity)):
    """Ledger rows for one standard, or every standard when standard is 'ALL'."""
    identity.assert_school(school_id)
    return {
        "standard": standard.upper(),
        "strength": database.standard_strength(school_id, standard) if standard.upper() != "ALL" else 0,
        "rows": database.get_ledger_by_standard(school_id, standard, created_by=identity.owner_filter),
    }


@router.get("/ledger/class/{school_id}/{class_id}")
def get_ledger_for_class(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    """Ledger rows for ONE class of ONE school. Each class keeps its own data."""
    identity.assert_school(school_id)
    classes = database.get_classes_for_school(school_id)
    info = next((c for c in classes if str(c["id"]) == str(class_id)), None)
    if not info:
        raise HTTPException(status_code=404, detail="Class not found.")
    locked = database.class_locked_standard(info.get("name", ""))
    return {
        "className": info.get("name", ""),
        "standard": locked or database.normalize_standard(info.get("name", "")),
        "locked": bool(locked),
        "strength": int(info.get("strength") or 0),
        "rows": database.get_ledger_records(school_id, class_id, created_by=identity.owner_filter),
    }


@router.get("/ledger/{school_id}/{class_id}")
def get_ledger(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    return database.get_ledger_records(school_id, class_id, created_by=identity.owner_filter)


@router.post("/ledger/sync")
def sync_ledger(request: SyncRequest, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    if identity.owner_filter and request.deletes:
        foreign = database.rows_not_owned_by(request.deletes, identity.username)
        if foreign:
            raise HTTPException(status_code=403, detail="You can only modify records you created.")
    try:
        changes = database.sync_ledger_records(request.schoolId, request.classId, request.updates,
                                               request.deletes, identity.username,
                                               standard=request.standard)
        summary = "; ".join(changes[:12]) or "no effective change"
        if len(changes) > 12:
            summary += f" (+{len(changes) - 12} more)"
        database.log_action(identity.username, identity.role, "LEDGER_SYNC", "ledger",
                            f"{request.schoolId}:{request.standard or request.classId}", summary)
        return {"status": "success", "synced": len(request.updates) + len(request.deletes)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Full 22-column stock-register export, matching the printed register layout.
REGISTER_EXPORT_COLUMNS = [
    ("S.No", None),
    ("Standard", "standard"),
    ("Vendor ID", "vendorId"),
    ("Vendor Name", "vendor"),
    ("Contact Number", "vendorContact"),
    ("Vendor GST No", "vendorGst"),
    ("Invoice Date", "invoiceDate"),
    ("Invoice No", "invoiceRef"),
    ("Category", "category"),
    ("Subject", "subject"),
    ("Book / Article Name", "bookName"),
    ("Publication", "publication"),
    ("Edition / Year", "edition"),
    ("Opening Balance", "openingBalance"),
    ("Qty Purchased", "purchased"),
    ("Approved Rate", "approvedRate"),
    ("Base Rate", "baseRate"),
    ("GST Amount", "gstAmount"),
    ("Discount %", "discountPercent"),
    ("Discount Amount", "discountAmount"),
    ("Total Amount", "totalAmount"),
    ("Strength", "strength"),
    ("Req. Books", "booksRequired"),
    ("Issued / Distributed", "distributed"),
    ("Returns", "returned"),
    ("Closing Balance", "closingBalance"),
    ("Remarks", "remarks"),
]


def _register_csv(records, title: str) -> io.StringIO:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"STOCK REGISTER FOR TEXT BOOKS / NOTE BOOKS - {title}"])
    writer.writerow([h for h, _ in REGISTER_EXPORT_COLUMNS])
    for i, r in enumerate(records, start=1):
        writer.writerow([i if key is None else r.get(key, "") for _, key in REGISTER_EXPORT_COLUMNS])
    output.seek(0)
    return output


@router.get("/ledger/standard/{school_id}/{standard}/download")
def download_ledger_standard_csv(school_id: int, standard: str, identity: Identity = Depends(get_identity)):
    """Register export for one standard, or all standards when standard is 'ALL'."""
    identity.assert_school(school_id)
    records = database.get_ledger_by_standard(school_id, standard, created_by=identity.owner_filter)
    school = next((s for s in database.get_all_schools() if str(s["id"]) == str(school_id)), {})
    school_name = school.get("name", f"School_{school_id}").replace(" ", "_")
    label = "All_Standards" if standard.upper() == "ALL" else standard.upper().replace(" ", "_")
    output = _register_csv(records, label.replace("_", " "))
    database.log_action(identity.username, identity.role, "LEDGER_EXPORT", "ledger",
                        f"{school_id}:{standard}", f"Downloaded register for {label}")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Register_{school_name}_{label}.csv"'}
    )


@router.get("/ledger/{school_id}/{class_id}/download")
def download_ledger_csv(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    records = database.get_ledger_records(school_id, class_id, created_by=identity.owner_filter)

    school = next((s for s in database.get_all_schools() if str(s["id"]) == str(school_id)), {})
    school_name = school.get("name", f"School_{school_id}").replace(" ", "_")

    classes = database.get_classes_for_school(school_id)
    class_info = next((c for c in classes if str(c["id"]) == str(class_id)), {})
    class_name = class_info.get("name", f"Class_{class_id}").replace(" ", "_")

    output = _register_csv(records, class_name.replace("_", " "))
    database.log_action(identity.username, identity.role, "LEDGER_EXPORT", "ledger", f"{school_id}:{class_id}",
                        f"Downloaded CSV for {class_name}")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Ledger_{school_name}_{class_name}.csv"'}
    )


# --- AI assistant: admin-managed key + permission-scoped chat ----------------
# Provider calls are proxied here so the API key stays on the server and the
# ledger context is limited to what the caller may see.
import json as _json
import urllib.request as _urlreq
import urllib.error as _urlerr


class AISettingsUpdate(BaseModel):
    apiKey: Optional[str] = ""      # blank = keep the stored key
    apiBase: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    model: str = "gemini-flash-latest"
    imageModel: str = "gemini-2.5-flash-image"


def _mask(key: str) -> str:
    if not key:
        return ""
    return (key[:3] + "..." + key[-4:]) if len(key) > 10 else "..."


@router.get("/settings/ai")
def get_ai_settings(identity: Identity = Depends(get_identity)):
    """Assistant status; provider details are admin-only and the key is never returned."""
    cfg = database.get_ai_settings()
    out = {"configured": bool(cfg["apiKey"]), "canEdit": identity.is_super_admin}
    if identity.is_super_admin:
        out.update({"apiBase": cfg["apiBase"], "model": cfg["model"],
                    "imageModel": cfg.get("imageModel", ""), "maskedKey": _mask(cfg["apiKey"])})
    return out


@router.put("/settings/ai")
def update_ai_settings(request: AISettingsUpdate, admin: Identity = Depends(require_super_admin)):
    cfg = database.save_ai_settings(request.apiKey, request.apiBase, request.model,
                                    request.imageModel)
    database.log_action(admin.username, admin.role, "AI_SETTINGS_UPDATE", "settings", "ai",
                        f"Model {cfg['model']} @ {cfg['apiBase']}")
    return {"status": "success", "configured": bool(cfg["apiKey"]),
            "apiBase": cfg["apiBase"], "model": cfg["model"],
            "imageModel": cfg.get("imageModel", ""), "maskedKey": _mask(cfg["apiKey"])}


@router.delete("/settings/ai")
def delete_ai_settings(admin: Identity = Depends(require_super_admin)):
    database.clear_ai_settings()
    database.log_action(admin.username, admin.role, "AI_SETTINGS_CLEAR", "settings", "ai", "Key removed")
    return {"status": "success", "configured": False}


@router.post("/settings/ai/test")
def test_ai_settings(admin: Identity = Depends(require_super_admin)):
    cfg = database.get_ai_settings()
    if not cfg["apiKey"]:
        raise HTTPException(status_code=400, detail="No API key saved yet.")
    req = _urlreq.Request(cfg["apiBase"].rstrip("/") + "/models",
                          headers=database.ai_auth_headers(cfg))
    try:
        with _urlreq.urlopen(req, timeout=30):
            return {"status": "success", "detail": "Connection OK."}
    except _urlerr.HTTPError as he:
        raise HTTPException(status_code=400, detail=f"Provider rejected the key (HTTP {he.code}).")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the provider: {e}")


class ChatRequest(BaseModel):
    question: str
    history: List[Dict[str, Any]] = []


@router.post("/chat")
def chat(request: ChatRequest, identity: Identity = Depends(get_identity)):
    """Tool-using assistant. Every lookup it performs is filtered by the
    caller's permission scope inside assistant.py, so one school's data can
    never surface in another school's chat."""
    question = (request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")
    if len(question) > 4000:
        raise HTTPException(status_code=400, detail="That question is too long.")
    try:
        return assistant.run(identity, question, request.history or [])
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except RuntimeError as re_:
        raise HTTPException(status_code=502, detail=str(re_))


@router.get("/ai/files")
def list_ai_files(identity: Identity = Depends(get_identity)):
    """Files the assistant has created, limited to the workspaces this
    account may see (own files; staff also see their schools' users; admins all)."""
    scope = assistant.scope_for(identity)
    return assistant._t_list_files(scope, {})


class AIUpload(BaseModel):
    filename: str
    content: str          # base64 of the file bytes (data: URLs are accepted too)


@router.post("/ai/upload")
def upload_ai_file(request: AIUpload, identity: Identity = Depends(get_identity)):
    """Attaches a file to the assistant: it is stored in the caller's own AI
    workspace, so only accounts allowed to see that workspace can read it."""
    scope = assistant.scope_for(identity)
    payload = request.content or ""
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="That file could not be read.")
    try:
        return assistant.save_upload(scope, request.filename or "upload.txt", data)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/ai/files/{owner}/{filename}")
def download_ai_file(owner: str, filename: str, request: Request, t: str = ""):
    """Downloads (and inline <img> loads) an assistant file. The token may come
    from the Authorization header or, for image tags, the ?t= query string."""
    header = request.headers.get("authorization", "")
    token = t or (header.split(" ", 1)[1] if header.lower().startswith("bearer ") else "")
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to download this file.")
    identity = get_identity(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    scope = assistant.scope_for(identity)
    try:
        path = assistant.resolve_file(scope, owner, filename)
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found.")
    from fastapi.responses import FileResponse
    database.log_action(identity.username, identity.role, "AI_FILE_DOWNLOAD", "ai_file",
                        filename, f"owner={owner}")
    return FileResponse(path, filename=filename)



# --- Distribution -------------------------------------------------------------
class DistributionCreate(BaseModel):
    schoolId: int
    classId: int
    ledgerId: str
    recipient: str
    quantity: int
    remarks: str = ""


@router.get("/distributions")
def get_distributions(identity: Identity = Depends(get_identity)):
    return modules.list_distributions(identity.scope_school_id,
                                      allowed_ids=identity.school_ids,
                                      created_by=identity.owner_filter)


@router.post("/distributions")
def post_distribution(request: DistributionCreate, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    try:
        rec = modules.create_distribution(
            request.schoolId, request.classId, request.ledgerId,
            request.recipient, request.quantity, request.remarks, identity.username
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "DISTRIBUTION_ISSUE",
                        "distribution", f"{request.schoolId}:{request.classId}:{request.ledgerId}",
                        f"Issued {request.quantity} of '{rec['book_name']}' to {rec['recipient']}")
    return {"status": "success", "data": rec}


# --- Transfers ----------------------------------------------------------------
class TransferCreate(BaseModel):
    fromSchoolId: int
    toSchoolId: int
    bookName: str
    quantity: int
    remarks: str = ""


class TransferStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = ""


def _can_decide_transfer(identity: "Identity", record: Dict[str, Any]) -> bool:
    """Who may approve / decline a transfer request.
    Super Admin always can. Any other account can when the destination school
    is inside its scope; an account with an unrestricted scope (school_ids is
    None) counts as in scope for every school."""
    if identity.is_super_admin:
        return True
    allowed = identity.school_ids
    if allowed is None:
        return True
    return str(record.get("to_school_id")) in {str(i) for i in allowed}


@router.get("/transfers")
def get_transfers(identity: Identity = Depends(get_identity)):
    rows = modules.list_transfers(identity.scope_school_id,
                                  allowed_ids=identity.school_ids,
                                  created_by=identity.owner_filter)
    for row in rows:
        row["can_decide"] = _can_decide_transfer(identity, row)
    return rows


@router.post("/transfers")
def post_transfer(request: TransferCreate, identity: Identity = Depends(get_identity)):
    # School accounts can only send from their own school.
    identity.assert_school(request.fromSchoolId)
    try:
        rec = modules.create_transfer(
            request.fromSchoolId, request.toSchoolId, request.bookName,
            request.quantity, request.remarks, identity.username
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "TRANSFER_REQUEST", "transfer", rec["id"],
                        f"{request.quantity} of '{request.bookName}' from S{request.fromSchoolId} -> S{request.toSchoolId}")
    return {"status": "success", "data": rec}


@router.put("/transfers/{transfer_id}/status")
def put_transfer_status(transfer_id: str, request: TransferStatusUpdate,
                        identity: Identity = Depends(get_identity)):
    """A Super Admin can decide any transfer; otherwise the caller must be in
    scope for the destination school holding the stock being requested."""
    existing = modules.get_transfer(transfer_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Transfer not found.")
    if not _can_decide_transfer(identity, existing):
        raise HTTPException(status_code=403, detail="Only the receiving school or a Super Admin can decide this transfer.")
    if request.status == "Rejected" and not (request.remarks or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required to reject a transfer.")
    try:
        rec = modules.set_transfer_status(transfer_id, request.status, identity.username,
                                          (request.remarks or "").strip())
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "TRANSFER_" + request.status.upper(),
                        "transfer", transfer_id, f"Transfer marked {request.status}"
                        + (f": {request.remarks}" if request.remarks else ""))
    return {"status": "success", "data": rec}


# --- Presence (file-backed heartbeat) ----------------------------------------
@router.post("/presence/ping")
def presence_ping(identity: Identity = Depends(get_identity)):
    database.record_presence(identity.username)
    return {"status": "ok"}


# --- Delete a distribution (reverses stock) -----------------------------------
@router.delete("/distributions/{dist_id}")
def delete_distribution(dist_id: str, identity: Identity = Depends(get_identity)):
    existing = modules.get_distribution(dist_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Distribution not found.")
    if not identity.is_super_admin and existing.get("created_by") != identity.username:
        identity.assert_school(existing.get("school_id"))
    try:
        modules.delete_distribution(dist_id, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "DISTRIBUTION_DELETE",
                        "distribution", dist_id, f"Reversed {existing.get('quantity')} of '{existing.get('book_name')}'")
    return {"status": "success"}



# --- Reports & Analytics ------------------------------------------------------
@router.get("/reports")
def reports(school_id: str = "", date_from: str = "", date_to: str = "",
            staff: str = "", user: str = "", identity: Identity = Depends(get_identity)):
    """Full report payload: totals, charts, per-school rollups, record rows,
    pending tasks and activity - all from one validated aggregation."""
    return reporting.build_report(
        school_id=school_id, date_from=date_from, date_to=date_to,
        staff=staff, user=user,
        scope_school_id=identity.scope_school_id,
        scope_school_ids=identity.school_ids,
        include_activity=not (identity.role == database.USER),
        visible_usernames=identity.visible_usernames,
    )


@router.get("/reports/export")
def reports_export(school_id: str = "", date_from: str = "", date_to: str = "",
                   staff: str = "", user: str = "", identity: Identity = Depends(get_identity)):
    """CSV/Excel export of exactly the rows the dashboard is showing."""
    data = reporting.build_report(
        school_id=school_id, date_from=date_from, date_to=date_to,
        staff=staff, user=user,
        scope_school_id=identity.scope_school_id,
        scope_school_ids=identity.school_ids,
        include_activity=not (identity.role == database.USER),
        visible_usernames=identity.visible_usernames,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for _, label in reporting.EXPORT_COLUMNS])
    for row in data["records"]:
        writer.writerow([row.get(key, "") for key, _ in reporting.EXPORT_COLUMNS])
    t = data["totals"]
    writer.writerow([])
    writer.writerow(["TOTALS", "", "", "", "", "", "", "", t["students"],
                     t["purchased"], t["distributed"], t["returned"], t["balance"],
                     t["required"], "", "", "", "", ""])
    writer.writerow([])
    writer.writerow(["Generated", data["generated_at"]])
    writer.writerow(["Schools", t["schools"], "Classes", t["classes"], "Records", t["records"],
                     "Users", t["users"], "Staff", t["staff"]])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="Vedritam_Report.csv"'})


# =============================================================================
# MESSAGING
# Direct messages, school groups, admin announcements, attachments,
# read receipts, typing indicators and search.
# =============================================================================
class ConversationCreate(BaseModel):
    type: str = "dm"                 # dm | group | announcement
    title: str = ""
    members: List[str] = []
    school_id: str = ""


class MembersAdd(BaseModel):
    members: List[str]


class MessageSend(BaseModel):
    body: str = ""
    attachment: Optional[Dict[str, Any]] = None   # {name, type, data(base64)}


class AnnouncementCreate(BaseModel):
    title: str
    body: str
    school_id: str = ""              # blank = every school


class PublicKeyUpload(BaseModel):
    public_jwk: str


def _assert_member(conversation: Dict[str, Any], identity: Identity) -> None:
    if conversation.get("type") == messaging.ANNOUNCEMENT:
        return
    # Private chats are end-to-end encrypted and strictly need-to-know: there
    # is deliberately no Super Admin override here.
    members = {m.lower() for m in conversation.get("members", [])}
    if identity.username.lower() not in members:
        raise HTTPException(status_code=403, detail="You are not part of this conversation.")


@router.get("/messaging/conversations")
def messaging_conversations(identity: Identity = Depends(get_identity)):
    return {"data": messaging.list_conversations(identity.username)}


@router.post("/messaging/conversations")
def messaging_create_conversation(request: ConversationCreate,
                                  identity: Identity = Depends(get_identity)):
    ctype = (request.type or "dm").lower()
    if ctype == messaging.ANNOUNCEMENT:
        if not (identity.is_super_admin or identity.is_admin):
            raise HTTPException(status_code=403,
                                detail="Only a Super Admin or an Admin can post announcements.")
        # There is only ever one announcement profile.
        return {"status": "success", "data": messaging.announcement_channel(identity.username)}
    if ctype == messaging.GROUP and not identity.is_manager:
        raise HTTPException(status_code=403,
                            detail="Only Staff, an Admin or the Super Admin can create school groups.")
    # Members must be real, active accounts.
    known = {u["username"].lower(): u["username"] for u in database.get_all_users()}
    members = []
    for m in request.members:
        if m.lower() not in known:
            raise HTTPException(status_code=400, detail=f"Unknown user '{m}'.")
        members.append(known[m.lower()])
    try:
        conv = messaging.create_conversation(ctype, request.title.strip(), members,
                                             identity.username, request.school_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    # Conversations are deliberately not recorded in the activity log.
    return {"status": "success", "data": conv}


@router.post("/messaging/conversations/{conversation_id}/members")
def messaging_add_members(conversation_id: str, request: MembersAdd,
                          identity: Identity = Depends(require_staff)):
    try:
        return {"status": "success", "data": messaging.add_members(conversation_id, request.members)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/messaging/conversations/{conversation_id}/messages")
def messaging_messages(conversation_id: str, limit: int = 50, offset: int = 0,
                       q: str = "", identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    page = messaging.list_messages(conversation_id, limit=limit, offset=offset, query=q)
    page["conversation"] = conv
    page["typing"] = messaging.who_is_typing(conversation_id, exclude=identity.username)
    return page


@router.post("/messaging/conversations/{conversation_id}/messages")
def messaging_send(conversation_id: str, request: MessageSend,
                   identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    if conv.get("type") == messaging.ANNOUNCEMENT and not (identity.is_super_admin or identity.is_admin):
        raise HTTPException(status_code=403, detail="Announcements are read-only.")

    attachment = None
    if request.attachment:
        raw = request.attachment.get("data", "")
        if "," in raw[:64]:
            raw = raw.split(",", 1)[1]     # strip data: URL prefix
        try:
            blob = base64.b64decode(raw, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Attachment could not be read.")
        try:
            attachment = messaging.save_attachment(
                request.attachment.get("name", "attachment"),
                request.attachment.get("type", ""), blob)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

    try:
        msg = messaging.send_message(conversation_id, identity.username, request.body, attachment)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {"status": "success", "data": msg}


@router.delete("/messaging/messages/{message_id}")
def messaging_delete(message_id: str, identity: Identity = Depends(get_identity)):
    try:
        messaging.delete_message(message_id, identity.username, identity.is_super_admin)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {"status": "success"}


@router.post("/messaging/conversations/{conversation_id}/read")
def messaging_mark_read(conversation_id: str, identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    return messaging.mark_read(conversation_id, identity.username)


@router.post("/messaging/conversations/{conversation_id}/typing")
def messaging_typing(conversation_id: str, identity: Identity = Depends(get_identity)):
    messaging.set_typing(conversation_id, identity.username)
    return {"status": "ok", "typing": messaging.who_is_typing(conversation_id, identity.username)}


@router.get("/messaging/conversations/{conversation_id}/typing")
def messaging_typing_state(conversation_id: str, identity: Identity = Depends(get_identity)):
    return {"typing": messaging.who_is_typing(conversation_id, identity.username)}


@router.get("/messaging/search")
def messaging_search(q: str = "", limit: int = 50, identity: Identity = Depends(get_identity)):
    return {"data": messaging.search_messages(identity.username, q, limit)}


@router.get("/messaging/unread")
def messaging_unread(identity: Identity = Depends(get_identity)):
    counts = messaging.unread_counts(identity.username)
    return {"total": sum(counts.values()), "by_conversation": counts}


@router.get("/messaging/attachments/{attachment_id}")
def messaging_attachment(attachment_id: str, identity: Identity = Depends(get_identity)):
    path = messaging.attachment_path(attachment_id)
    if not path:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    owner_msg = messaging.message_for_attachment(attachment_id)
    if not owner_msg:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    conv = messaging.get_conversation(owner_msg.get("conversation_id", ""))
    if not conv:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    _assert_member(conv, identity)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=owner_msg.get("attachment_name") or "attachment")


@router.post("/messaging/announcements")
def messaging_announce(request: AnnouncementCreate,
                       admin: Identity = Depends(require_super_admin)):
    """Post into the single Announcements profile + notify every recipient."""
    users = database.get_all_users()
    if request.school_id:
        users = [u for u in users if str(u.get("school_id", "")) == str(request.school_id)]
    recipients = [u["username"] for u in users]
    conv = messaging.announcement_channel(admin.username)
    title = request.title.strip()
    body = (title + "\n" + request.body.strip()) if title else request.body.strip()
    messaging.send_message(conv["id"], admin.username, body)
    for member in recipients:
        if member.lower() == admin.username.lower():
            continue
        messaging.push_notification(member, "announcement", title or "Announcement",
                                    request.body.strip()[:160],
                                    "messages.html?c=" + str(conv["id"]))
    # Announcements are messaging, so they are not recorded in the activity log.
    return {"status": "success", "data": conv, "recipients": len(recipients)}


# =============================================================================
# END-TO-END ENCRYPTION KEY DIRECTORY
# The server only ever stores public keys. Private keys never leave the
# browser, so message bodies are unreadable to the server and to admins.
# =============================================================================
@router.post("/messaging/keys")
def messaging_publish_key(request: PublicKeyUpload,
                          identity: Identity = Depends(get_identity)):
    if len(request.public_jwk) > 4000:
        raise HTTPException(status_code=400, detail="Key material is too large.")
    return {"status": "success",
            "data": messaging.save_public_key(identity.username, request.public_jwk)}


@router.get("/messaging/keys")
def messaging_get_keys(users: str = "", identity: Identity = Depends(get_identity)):
    names = [u.strip() for u in (users or "").split(",") if u.strip()]
    return {"data": messaging.get_public_keys(names)}


# =============================================================================
# NOTIFICATION CENTER
# Messages, reports, announcements and alerts, with mark-as-read and history.
# =============================================================================
@router.get("/notifications")
def notifications_list(unread: bool = False, type: str = "", limit: int = 30,
                       offset: int = 0, identity: Identity = Depends(get_identity)):
    return messaging.list_notifications(identity.username, unread_only=unread,
                                        ntype=type, limit=limit, offset=offset)


@router.post("/notifications/{notification_id}/read")
def notifications_read(notification_id: str, identity: Identity = Depends(get_identity)):
    try:
        messaging.mark_notification_read(notification_id, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    return {"status": "success"}


@router.post("/notifications/read-all")
def notifications_read_all(identity: Identity = Depends(get_identity)):
    return {"status": "success", "marked": messaging.mark_all_notifications_read(identity.username)}


class NotificationPush(BaseModel):
    usernames: List[str] = []
    type: str = "alert"
    title: str
    body: str = ""
    link: str = ""


@router.post("/notifications/push")
def notifications_push(request: NotificationPush,
                       admin: Identity = Depends(require_super_admin)):
    targets = request.usernames or [u["username"] for u in database.get_all_users()]
    sent = messaging.broadcast_notification(targets, request.type, request.title,
                                            request.body, request.link)
    return {"status": "success", "sent": sent}


@router.get("/notifications/history")
def notifications_history(limit: int = 100, offset: int = 0,
                          identity: Identity = Depends(get_identity)):
    return messaging.list_notifications(identity.username, limit=limit, offset=offset)


# =============================================================================
# SECURITY
# Login history, session state, password strength and Super Admin 2FA.
# =============================================================================
class TwoFactorCode(BaseModel):
    code: str


class PasswordCheck(BaseModel):
    password: str


@router.get("/security/login-history")
def security_login_history(username: str = "", result: str = "", limit: int = 100,
                           offset: int = 0, identity: Identity = Depends(get_identity)):
    """Super Admin sees everyone; everyone else sees only their own history."""
    if not identity.is_super_admin:
        username = identity.username
    return security_mod.login_history(username=username, result=result,
                                      limit=limit, offset=offset)


@router.get("/security/session")
def security_session(identity: Identity = Depends(get_identity)):
    return security_mod.session_info(identity.username)


class SessionTimeoutUpdate(BaseModel):
    minutes: int


@router.put("/security/session/timeout")
def security_session_timeout(request: SessionTimeoutUpdate,
                             admin: Identity = Depends(require_super_admin)):
    """Edit the idle session timeout (Security page)."""
    try:
        minutes = security_mod.set_idle_minutes(request.minutes)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "SESSION_TIMEOUT_UPDATE", "security",
                        admin.username, f"Idle timeout set to {minutes} minutes")
    return security_mod.session_info(admin.username)


@router.post("/security/session/heartbeat")
def security_session_heartbeat(identity: Identity = Depends(get_identity)):
    security_mod.touch_session(identity.username)
    return security_mod.session_info(identity.username)


@router.post("/security/logout")
def security_logout(identity: Identity = Depends(get_identity)):
    security_mod.end_session(identity.username)
    security_mod.record_login(identity.username, "LOGOUT")
    database.log_action(identity.username, identity.role, "LOGOUT", "auth",
                        identity.username, "Signed out")
    return {"status": "success"}


@router.post("/security/password/strength")
def security_password_strength(request: PasswordCheck,
                               identity: Identity = Depends(get_identity)):
    ok, reason = security_mod.validate_password(request.password, identity.username)
    return {"valid": ok, "reason": reason, **security_mod.password_strength(request.password)}


@router.get("/security/2fa")
def security_2fa_status(identity: Identity = Depends(get_identity)):
    status_payload = security_mod.twofa_status(identity.username)
    status_payload["required_for_role"] = identity.role in TWOFA_ENFORCED_ROLES
    return status_payload


@router.post("/security/2fa/enroll")
def security_2fa_enroll(admin: Identity = Depends(require_super_admin)):
    """Starts TOTP enrolment. The secret and backup codes are shown once."""
    data = security_mod.start_twofa_enrollment(admin.username)
    database.log_action(admin.username, admin.role, "2FA_ENROLL_START", "auth",
                        admin.username, "Started two-factor enrolment")
    return {"status": "pending", **data}


@router.post("/security/2fa/confirm")
def security_2fa_confirm(request: TwoFactorCode,
                         admin: Identity = Depends(require_super_admin)):
    if not security_mod.confirm_twofa(admin.username, request.code):
        raise HTTPException(status_code=400, detail="That verification code is not valid.")
    database.log_action(admin.username, admin.role, "2FA_ENABLED", "auth",
                        admin.username, "Two-factor authentication enabled")
    security_mod.record_login(admin.username, "2FA_ENABLED")
    return {"status": "success", "enabled": True}


@router.post("/security/2fa/disable")
def security_2fa_disable(request: TwoFactorCode,
                         admin: Identity = Depends(require_super_admin)):
    if not security_mod.verify_totp(admin.username, request.code):
        raise HTTPException(status_code=400, detail="Confirm with a valid code before disabling 2FA.")
    security_mod.disable_twofa(admin.username)
    database.log_action(admin.username, admin.role, "2FA_DISABLED", "auth",
                        admin.username, "Two-factor authentication disabled")
    return {"status": "success", "enabled": False}


# =============================================================================
# PERFORMANCE
# =============================================================================
@router.get("/system/cache")
def system_cache_stats(admin: Identity = Depends(require_super_admin)):
    return cache.stats()


@router.post("/system/cache/clear")
def system_cache_clear(admin: Identity = Depends(require_super_admin)):
    cache.clear()
    return {"status": "success"}


# =============================================================================
# CORE CONFIGURATION — Vendor Management, Resource Catalog, Inventory Status
# =============================================================================
class VendorSave(BaseModel):
    vendorId: str = ""
    name: str
    contact: str = ""
    email: str = ""
    address: str = ""
    gst: str = ""
    pan: str = ""
    bank_name: str = ""
    bank_account: str = ""
    bank_ifsc: str = ""
    payment_terms: str = ""
    status: str = "Active"


class CatalogSave(BaseModel):
    standard: str = "OTHERS"
    category: str = "STATIONERY"
    subject: str = ""
    title: str
    publication: str = ""
    default_qty_per_student: str = ""
    edition: str = ""
    academic_year: str = ""
    language: str = ""
    isbn: str = ""
    approved_rate: str = ""
    status: str = "Active"
    originalTitle: str = ""


@router.get("/options")
def dropdown_options(identity: Identity = Depends(get_identity)):
    """Every dropdown list the UI needs, in one call."""
    from config import (GST_RATES, DISCOUNT_OPTIONS, PAYMENT_TERMS, PAYMENT_MODES,
                        PAYMENT_STATUSES, PO_STATUSES, REQUEST_STATUSES,
                        LANGUAGES, EDITIONS, STATIONERY_ITEMS)
    lists = database.catalog_option_lists()
    lists.update({
        "gstRates": GST_RATES,
        "discounts": DISCOUNT_OPTIONS,
        "paymentTerms": PAYMENT_TERMS,
        "paymentModes": PAYMENT_MODES,
        "paymentStatuses": PAYMENT_STATUSES,
        "poStatuses": PO_STATUSES,
        "requestStatuses": REQUEST_STATUSES,
        "defaultLanguages": LANGUAGES,
        "defaultEditions": EDITIONS,
        "stationeryItems": STATIONERY_ITEMS,
        "vendors": database.get_vendors(),
        "schools": [{"id": s.get("id"), "name": s.get("name"), "code": s.get("code")}
                    for s in database.get_all_schools(
                        school_id_filter=identity.scope_school_id,
                        allowed_ids=identity.school_ids)],
    })
    return lists


@router.post("/vendors")
def create_vendor(request: VendorSave, identity: Identity = Depends(require_staff)):
    record = database.save_vendor(request.dict())
    database.log_action(identity.username, identity.role, "VENDOR_SAVE", "vendor",
                        record.get("vendorId", ""), f"Saved vendor '{record.get('name')}'")
    return {"status": "success", "data": record}


@router.put("/vendors/{vendor_id}")
def update_vendor(vendor_id: str, request: VendorSave,
                  identity: Identity = Depends(require_staff)):
    payload = request.dict()
    payload["vendorId"] = vendor_id
    record = database.save_vendor(payload)
    database.log_action(identity.username, identity.role, "VENDOR_UPDATE", "vendor",
                        vendor_id, f"Updated vendor '{record.get('name')}'")
    return {"status": "success", "data": record}


@router.delete("/vendors/{vendor_id}")
def remove_vendor(vendor_id: str, identity: Identity = Depends(require_super_admin)):
    if not database.delete_vendor(vendor_id):
        raise HTTPException(status_code=404, detail="Vendor not found.")
    database.log_action(identity.username, identity.role, "VENDOR_DELETE", "vendor", vendor_id)
    return {"status": "success"}


@router.post("/catalog")
def create_catalog_item(request: CatalogSave, identity: Identity = Depends(require_staff)):
    try:
        record = database.save_catalog_item(request.dict(), request.originalTitle)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "CATALOG_SAVE", "catalog",
                        record.get("title", ""), f"{record.get('category')} · {record.get('standard')}")
    return {"status": "success", "data": record}


@router.delete("/catalog")
def remove_catalog_item(title: str, category: str = "", standard: str = "",
                        identity: Identity = Depends(require_staff)):
    if not database.delete_catalog_item(title, category, standard):
        raise HTTPException(status_code=404, detail="Catalog item not found.")
    database.log_action(identity.username, identity.role, "CATALOG_DELETE", "catalog", title)
    return {"status": "success"}


@router.get("/inventory")
def inventory(school_id: str = "", status: str = "",
              identity: Identity = Depends(get_identity)):
    """Inventory Status: Available / Low Stock / Out of Stock."""
    if school_id:
        identity.assert_school(school_id)
        rows = database.inventory_snapshot(school_id)
    else:
        allowed = identity.school_ids
        rows = database.inventory_snapshot()
        if allowed is not None:
            wanted = {str(i) for i in allowed}
            rows = [r for r in rows if str(r.get("school_id")) in wanted]
    if status:
        rows = [r for r in rows if str(r.get("status", "")).lower() == status.lower()]
    return {"items": rows,
            "counts": {
                "available": len([r for r in rows if r["status"] == "Available"]),
                "low": len([r for r in rows if r["status"] == "Low Stock"]),
                "out": len([r for r in rows if r["status"] == "Out of Stock"]),
            }}


# =============================================================================
# PROCUREMENT & FINANCE
# =============================================================================
class RequestCreate(BaseModel):
    school_id: str
    standard: str = ""
    category: str = ""
    item: str
    quantity: int
    remarks: str = ""


class RequestDecision(BaseModel):
    status: str
    remarks: str = ""


class OrderCreate(BaseModel):
    school_id: str
    vendorId: str
    po_date: str = ""
    expected_date: str = ""
    items: List[Dict[str, Any]] = []
    request_id: str = ""
    remarks: str = ""
    status: str = "Draft"


class OrderStatus(BaseModel):
    status: str


class GrnCreate(BaseModel):
    po_id: str = ""
    school_id: str = ""
    vendorId: str = ""
    grn_date: str = ""
    items: List[Dict[str, Any]] = []
    remarks: str = ""


class InvoiceCreate(BaseModel):
    po_id: str = ""
    grn_id: str = ""
    school_id: str = ""
    vendorId: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    amount: float = 0
    gstAmount: float = 0
    total: float = 0
    remarks: str = ""


class PaymentCreate(BaseModel):
    invoice_id: str = ""
    school_id: str = ""
    vendorId: str = ""
    payment_date: str = ""
    amount: float
    mode: str = "NEFT"
    reference: str = ""
    remarks: str = ""


class NoteCreate(BaseModel):
    invoice_id: str = ""
    school_id: str = ""
    vendorId: str = ""
    note_date: str = ""
    amount: float
    reason: str = ""


class BudgetCreate(BaseModel):
    school_id: str
    academic_year: str = ""
    category: str = ""
    allocated: float
    remarks: str = ""


def _proc_scope(identity: "Identity", school_id: str = ""):
    if school_id:
        identity.assert_school(school_id)
        return str(school_id), None
    return identity.scope_school_id, identity.school_ids


@router.get("/procurement/requests")
def proc_requests(school_id: str = "", status: str = "",
                  identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_requests(sid, allowed, status)


@router.post("/procurement/requests")
def proc_create_request(request: RequestCreate, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.school_id)
    try:
        rec = procurement.create_request(request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "PR_CREATE", "purchase_request",
                        rec["id"], f"{rec['quantity']} x {rec['item']}")
    return {"status": "success", "data": rec}


@router.put("/procurement/requests/{request_id}")
def proc_decide_request(request_id: str, request: RequestDecision,
                        identity: Identity = Depends(require_staff)):
    try:
        rec = procurement.decide_request(request_id, request.status, identity.username, request.remarks)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "PR_" + request.status.upper(),
                        "purchase_request", request_id, request.remarks)
    return {"status": "success", "data": rec}


@router.get("/procurement/orders")
def proc_orders(school_id: str = "", status: str = "", vendor_id: str = "",
                identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_orders(sid, allowed, status, vendor_id)


@router.post("/procurement/orders")
def proc_create_order(request: OrderCreate, identity: Identity = Depends(require_staff)):
    identity.assert_school(request.school_id)
    try:
        rec = procurement.create_order(request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "PO_CREATE", "purchase_order",
                        rec["po_number"], f"Total {rec['total']} to {rec['vendor']}")
    return {"status": "success", "data": rec}


@router.put("/procurement/orders/{po_id}/status")
def proc_order_status(po_id: str, request: OrderStatus,
                      identity: Identity = Depends(require_staff)):
    try:
        rec = procurement.set_order_status(po_id, request.status)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    database.log_action(identity.username, identity.role, "PO_STATUS", "purchase_order",
                        po_id, request.status)
    return {"status": "success", "data": rec}


@router.get("/procurement/grns")
def proc_grns(school_id: str = "", po_id: str = "",
              identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_grns(sid, allowed, po_id)


@router.post("/procurement/grns")
def proc_create_grn(request: GrnCreate, identity: Identity = Depends(require_staff)):
    if request.school_id:
        identity.assert_school(request.school_id)
    try:
        rec = procurement.create_grn(request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "GRN_CREATE", "grn",
                        rec["grn_number"], f"Received {rec['total_quantity']} items")
    return {"status": "success", "data": rec}


@router.get("/procurement/invoices")
def proc_invoices(school_id: str = "", status: str = "", vendor_id: str = "",
                  identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_invoices(sid, allowed, status, vendor_id)


@router.post("/procurement/invoices")
def proc_create_invoice(request: InvoiceCreate, identity: Identity = Depends(require_staff)):
    if request.school_id:
        identity.assert_school(request.school_id)
    try:
        rec = procurement.create_invoice(request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "INVOICE_CREATE", "invoice",
                        rec["invoice_number"], f"Total {rec['total']}")
    return {"status": "success", "data": rec}


@router.get("/procurement/payments")
def proc_payments(school_id: str = "", vendor_id: str = "",
                  identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_payments(sid, allowed, vendor_id)


@router.post("/procurement/payments")
def proc_create_payment(request: PaymentCreate, identity: Identity = Depends(require_staff)):
    if request.school_id:
        identity.assert_school(request.school_id)
    try:
        rec = procurement.create_payment(request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "PAYMENT_CREATE", "payment",
                        rec["id"], f"Paid {rec['amount']} to {rec['vendor']}")
    return {"status": "success", "data": rec}


@router.get("/procurement/notes/{kind}")
def proc_notes(kind: str, school_id: str = "", identity: Identity = Depends(get_identity)):
    if kind not in ("credit", "debit"):
        raise HTTPException(status_code=400, detail="Kind must be credit or debit.")
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_notes(kind, sid, allowed)


@router.post("/procurement/notes/{kind}")
def proc_create_note(kind: str, request: NoteCreate,
                     identity: Identity = Depends(require_staff)):
    if kind not in ("credit", "debit"):
        raise HTTPException(status_code=400, detail="Kind must be credit or debit.")
    if request.school_id:
        identity.assert_school(request.school_id)
    try:
        rec = procurement.create_note(kind, request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, kind.upper() + "_NOTE", "note",
                        rec["id"], rec["reason"])
    return {"status": "success", "data": rec}


@router.get("/procurement/budgets")
def proc_budgets(school_id: str = "", identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.list_budgets(sid, allowed)


@router.post("/procurement/budgets")
def proc_create_budget(request: BudgetCreate, identity: Identity = Depends(require_staff)):
    identity.assert_school(request.school_id)
    try:
        rec = procurement.create_budget(request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "BUDGET_CREATE", "budget",
                        rec["id"], f"Allocated {rec['allocated']}")
    return {"status": "success", "data": rec}


@router.delete("/procurement/budgets/{budget_id}")
def proc_delete_budget(budget_id: str, identity: Identity = Depends(require_staff)):
    if not procurement.delete_budget(budget_id):
        raise HTTPException(status_code=404, detail="Budget not found.")
    database.log_action(identity.username, identity.role, "BUDGET_DELETE", "budget", budget_id)
    return {"status": "success"}


@router.get("/procurement/vendor-ledger/{vendor_id}")
def proc_vendor_ledger(vendor_id: str, school_id: str = "",
                       identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.vendor_ledger(vendor_id, sid, allowed)


@router.get("/procurement/outstanding")
def proc_outstanding(school_id: str = "", identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.outstanding_payments(sid, allowed)


@router.get("/procurement/summary")
def proc_summary(school_id: str = "", identity: Identity = Depends(get_identity)):
    sid, allowed = _proc_scope(identity, school_id)
    return procurement.finance_summary(sid, allowed)


# =============================================================================
# PROFILE PHOTOS, PRESENCE, GROUP DETAILS AND LIVE CALLS
# The photo an account picks in Settings is stored on the server, so it shows
# up everywhere: the header, the conversation list, and every chat bubble.
# =============================================================================
class AvatarUpload(BaseModel):
    data: str = ""                    # data:image/... URL (already resized)


class AvatarQuery(BaseModel):
    usernames: List[str] = []


class SignalSend(BaseModel):
    to: str
    kind: str
    payload: Any = None


class CallJoin(BaseModel):
    mode: str = "audio"               # audio | video


@router.get("/profile/avatar")
def profile_avatar_get(identity: Identity = Depends(get_identity)):
    return {"username": identity.username, "avatar": profiles.get_user_avatar(identity.username)}


@router.put("/profile/avatar")
def profile_avatar_put(request: AvatarUpload, identity: Identity = Depends(get_identity)):
    try:
        photo = profiles.set_user_avatar(identity.username, request.data)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {"status": "success", "avatar": photo}


@router.delete("/profile/avatar")
def profile_avatar_delete(identity: Identity = Depends(get_identity)):
    profiles.clear_user_avatar(identity.username)
    return {"status": "success"}


@router.post("/profile/avatars")
def profile_avatars(request: AvatarQuery, identity: Identity = Depends(get_identity)):
    """Bulk lookup so a chat can paint every avatar in one round trip."""
    names = [n for n in (request.usernames or []) if n][:200]
    return {"data": profiles.avatars_for(names), "presence": profiles.presence(names)}


@router.get("/messaging/conversations/{conversation_id}/details")
def messaging_conversation_details(conversation_id: str,
                                   identity: Identity = Depends(get_identity)):
    """Everything the info panel shows: members, their photo, role and
    when they were last online."""
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    members = conv.get("members") or []
    directory = {u.get("username", "").lower(): u for u in database.get_all_users()}
    seen = profiles.presence(members)
    people = []
    for name in members:
        row = directory.get(name.lower(), {})
        people.append({
            "username": name,
            "fullName": row.get("fullName", "") or name,
            "role": row.get("role", ""),
            "email": row.get("email", ""),
            "school_id": row.get("school_id", ""),
            "status": row.get("status", ""),
            "avatar": profiles.get_user_avatar(name),
            "online": seen.get(name, {}).get("online", False),
            "last_seen": seen.get(name, {}).get("last_seen", ""),
            "is_you": name.lower() == identity.username.lower(),
        })
    people.sort(key=lambda p: (not p["online"], p["username"].lower()))
    return {
        "conversation": conv,
        "avatar": profiles.get_conversation_avatar(conversation_id),
        "members": people,
        "call": profiles.room_state(conversation_id),
    }


@router.put("/messaging/conversations/{conversation_id}/avatar")
def messaging_conversation_avatar(conversation_id: str, request: AvatarUpload,
                                  identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    if conv.get("type") == messaging.DM:
        raise HTTPException(status_code=400, detail="Direct chats use the person's own photo.")
    try:
        if request.data:
            photo = profiles.set_conversation_avatar(conversation_id, request.data)
        else:
            profiles.clear_conversation_avatar(conversation_id)
            photo = ""
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {"status": "success", "avatar": photo}


# --- live calls: voice, video and screen sharing -----------------------------
# The media itself is peer-to-peer (WebRTC in the browser). The server only
# passes the small setup messages between the two browsers.
@router.post("/messaging/conversations/{conversation_id}/call/join")
def messaging_call_join(conversation_id: str, request: CallJoin,
                        identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    if conv.get("type") == messaging.ANNOUNCEMENT:
        raise HTTPException(status_code=400, detail="Announcement channels cannot host calls.")
    mode = (request.mode or "audio").lower()
    if mode not in ("audio", "video"):
        mode = "audio"
    return {"status": "success",
            "data": profiles.join_call(conversation_id, identity.username, mode,
                                       conv.get("members") or [])}


@router.post("/messaging/conversations/{conversation_id}/call/leave")
def messaging_call_leave(conversation_id: str, identity: Identity = Depends(get_identity)):
    profiles.leave_call(conversation_id, identity.username)
    return {"status": "success"}


@router.get("/messaging/conversations/{conversation_id}/call")
def messaging_call_state(conversation_id: str, identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    return {"data": profiles.heartbeat(conversation_id, identity.username)
                    or profiles.room_state(conversation_id)}


@router.post("/messaging/conversations/{conversation_id}/call/signal")
def messaging_call_signal(conversation_id: str, request: SignalSend,
                          identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    members = {m.lower() for m in (conv.get("members") or [])}
    if request.to.lower() not in members:
        raise HTTPException(status_code=400, detail="That person is not in this conversation.")
    profiles.send_signal(conversation_id, identity.username, request.to,
                         request.kind, request.payload)
    return {"status": "success"}


@router.get("/messaging/signals")
def messaging_signals(identity: Identity = Depends(get_identity)):
    """Drains this account's signalling mailbox (also delivers ring alerts)."""
    return {"data": profiles.drain_signals(identity.username)}
