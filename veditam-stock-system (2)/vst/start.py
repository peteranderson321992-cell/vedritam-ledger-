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

HOST = "127.0.0.1"   # change to "0.0.0.0" to let other computers on the school LAN connect
PORT = 8000


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


def main():
    ensure_deps()
    import uvicorn
    url = f"http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}"
    print(f"\nVedritam Stock System running at {url}\nPress Ctrl+C to stop.\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
