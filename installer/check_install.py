"""Install, use and uninstall a built Dogspeak installer, and prove nothing is left.

    python installer/check_install.py build/Dogspeak-<version>-Setup.exe

Runs on Windows (CI does it for every build). It records the places the
installer may touch, installs silently, transcribes samples/jfk.flac with the
installed copy, leaves drafts and a log as a person's use would, uninstalls
silently, and compares. Expected afterwards: everything back as before, except
the drafts, which are the person's and stay (a silent uninstall never deletes
them). The script removes those test drafts at the end.
"""
import os
import subprocess
import sys
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_KEY = "{6F2B8E4D-3C1A-4B7E-9D52-8A0C4E1F7B63}_is1"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
LOCAL = Path(os.environ["LOCALAPPDATA"])
PROGRAM = LOCAL / "Programs" / "Dogspeak"
DATA = LOCAL / "Dogspeak"
MENU = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop"


def uninstall_keys():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL) as key:
        return {winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])}


def tree(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*")) if path.exists() else None


def snapshot():
    return {"uninstall keys": uninstall_keys(),
            "program": tree(PROGRAM),
            "data": tree(DATA),
            "start menu": sorted(p.name for p in MENU.glob("Dogspeak*")),
            "desktop": sorted(p.name for p in DESKTOP.glob("Dogspeak*"))}


def wait_until(condition, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1)
    raise SystemExit(f"FAIL: {what} (waited {seconds} s)")


def check(condition, what):
    print(("ok    " if condition else "FAIL  ") + what, flush=True)
    if not condition:
        raise SystemExit(f"FAIL: {what}")


def main(setup):
    setup = Path(setup).resolve()
    before = snapshot()
    check(APP_KEY not in before["uninstall keys"], "Dogspeak is not installed to begin with")

    print("Installing", setup.name, flush=True)
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    "/CURRENTUSER", "/TASKS=", f"/LOG={setup.with_suffix('.install.log')}"],
                   check=True, timeout=900)
    check((PROGRAM / "python" / "pythonw.exe").exists(), "the program is in %LOCALAPPDATA%\\Programs\\Dogspeak")
    check((MENU / "Dogspeak.lnk").exists(), "Start menu shortcut")
    check(APP_KEY in uninstall_keys(), "listed in Apps & features")
    check(not (DESKTOP / "Dogspeak.lnk").exists(), "no desktop shortcut unless asked for")

    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result = subprocess.run([str(PROGRAM / "python" / "python.exe"), str(PROGRAM / "dogspeak.py"),
                             "transcribe", str(ROOT / "samples" / "jfk.flac")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env=env, cwd=str(ROOT), timeout=600)
    print(result.stdout[-600:], result.stderr[-600:], sep="\n")
    check(result.returncode == 0 and "ask not what your country" in result.stdout,
          "the installed copy transcribes with the bundled model")

    # What using Dogspeak leaves: drafts (the person's) and logs (the program's).
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    (DATA / "logs" / "app.log").write_text("test\n", encoding="utf-8")
    drafts_existed = (DATA / "dogspeak.sqlite3").exists()
    if not drafts_existed:
        (DATA / "dogspeak.sqlite3").write_bytes(b"test drafts")

    print("Uninstalling", flush=True)
    subprocess.run([str(PROGRAM / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                    "/NORESTART"], check=True, timeout=600)
    # The uninstaller hands over to a copy of itself and returns early.
    wait_until(lambda: not PROGRAM.exists() and APP_KEY not in uninstall_keys(), 180,
               "the program folder and the Apps & features entry go")
    check((DATA / "dogspeak.sqlite3").exists(), "drafts are kept")
    check(not (DATA / "logs").exists(), "logs are removed")
    if not drafts_existed:
        (DATA / "dogspeak.sqlite3").unlink()
        if not any(DATA.iterdir()):
            DATA.rmdir()

    after = snapshot()
    for name in before:
        check(after[name] == before[name], f"{name}: as before")
    print("Install and uninstall left no trace.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
