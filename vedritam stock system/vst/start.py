"""
Vedritam Stock System — one-click launcher.

Run:  python start.py

It installs the required packages the first time, starts the API + web server
on http://127.0.0.1:8000 and opens your browser there.

IMPORTANT: always use the browser address http://127.0.0.1:8000 .
Opening index.html by double-clicking it makes the browser load the page from
disk (file://...), so the login/sign-up requests have no server to talk to and
you get "Failed to fetch".
"""
import importlib
import subprocess
import sys
import threading
import webbrowser
import os
import traceback
import getpass

# On hosted platforms (Render etc.) the server must listen on 0.0.0.0 and on
# the port supplied through $PORT, otherwise the platform cannot reach it.
_HOSTED = bool(os.getenv("RENDER") or os.getenv("PORT"))
HOST = os.getenv("HOST") or ("0.0.0.0" if _HOSTED else "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))


def ensure_deps():
    missing = []
    for mod, pkg in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("jwt", "pyjwt")):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Installing missing packages:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


def _pause_on_windows():
    # A double-clicked .py file otherwise closes the console immediately when
    # startup fails, hiding the real error from the user.
    if os.name == "nt":
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, KeyboardInterrupt):
            pass


def _prepare_first_run():
    # Fresh installations use the temporary default administrator password
    # requested for this build. The password can be changed from the Security
    # page after signing in.
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    users_csv = os.path.join(data_dir, "users.csv")
    if os.path.exists(users_csv) or os.getenv("BOOTSTRAP_ADMIN_PASSWORD"):
        return
    os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
    print("\nFirst run: default Super Admin account is admin / admin123")
    print("You can change the password after signing in from Security > Change Password.\n")


def main():
    try:
        if not _HOSTED:
            ensure_deps()
        _prepare_first_run()
        import uvicorn
        url = f"http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}"
        print(f"\nVedritam Stock System running at {url}\nPress Ctrl+C to stop.\n")
        # Open the browser only after the server has successfully entered its
        # running state.  Uvicorn startup failures therefore do not look like
        # a mysterious browser/console crash.
        def open_browser():
            try:
                webbrowser.open(url)
            except Exception:
                pass
        # Never try to open a browser on hosted platforms such as Render.
        if not _HOSTED and HOST in ("127.0.0.1", "localhost"):
            threading.Timer(1.5, open_browser).start()
        uvicorn.run("app:app", host=HOST, port=PORT, proxy_headers=True, forwarded_allow_ips="*")
    except KeyboardInterrupt:
        print("\nVedritam stopped.")
    except Exception as exc:
        print("\nVedritam could not start:")
        print(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("\nCheck the message above, correct the configuration, and start again.")
        _pause_on_windows()
        raise


if __name__ == "__main__":
    main()
