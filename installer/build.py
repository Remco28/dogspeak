"""Build Dogspeak's Windows installer. Runs on Windows (GitHub Actions does it per release).

    uv run python installer/build.py [--out build] [--no-installer]

1. Copies a standalone CPython 3.12 (the one uv manages; it is relocatable)
   into build/Dogspeak/python, without its tests and Tk.
2. Installs exactly the locked dependencies (uv.lock, with hashes) into it.
3. Adds Dogspeak's code, icons, licences and the model files (fetch_models.py
   must have run), and compiles everything to bytecode.
4. Checks that the bundle imports and starts its pieces.
5. Compiles installer/dogspeak.iss with Inno Setup into
   build/Dogspeak-<version>-Setup.exe.
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
APP_FILES = ["dogspeak.py", "upstream.json", "LICENSE", "README.md"]
APP_DIRS = ["dogspeak_app", "vendor", "models", "data/icons"]
# Parts of CPython Dogspeak never uses; about 40 MB.
PYTHON_TRIM = ["Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "tcl",
               "Lib/site-packages/pip"]


def version(root=ROOT):
    text = (Path(root) / "dogspeak_app" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def run(*command, **kwargs):
    print("+", " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


def output(*command):
    return subprocess.run([str(c) for c in command], check=True, capture_output=True,
                          text=True).stdout.strip()


def copy_app(bundle, root=ROOT):
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.part")
    for name in APP_FILES:
        shutil.copy2(root / name, bundle / name)
    for name in APP_DIRS:
        source = root / name
        if not source.exists():
            raise SystemExit(f"{source} is missing; run fetch_models.py first" if name == "models"
                             else f"{source} is missing")
        shutil.copytree(source, bundle / name, ignore=ignore)


def write_icon(bundle, root=ROOT):
    spec = importlib.util.spec_from_file_location("windows_install", root / "windows_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pngs = {size: (root / "data/icons/hicolor" / f"{size}x{size}" / "apps"
                   / f"{module.APP_ID}.png").read_bytes() for size in module.ICON_SIZES}
    (bundle / "dogspeak.ico").write_bytes(module.build_ico(pngs))


def bundle_python(bundle):
    found = Path(output("uv", "python", "find", "--managed-python", "3.12"))
    home = found.parent
    target = bundle / "python"
    shutil.copytree(home, target, ignore=shutil.ignore_patterns("__pycache__"))
    for part in PYTHON_TRIM:
        shutil.rmtree(target / part, ignore_errors=True)
    for leftover in target.glob("Lib/site-packages/pip-*.dist-info"):
        shutil.rmtree(leftover)
    for marker in target.glob("Lib/EXTERNALLY-MANAGED"):
        marker.unlink()  # this copy is Dogspeak's own, not uv's
    return target / "python.exe"


def install_dependencies(python, out):
    requirements = out / "requirements.txt"
    run("uv", "export", "--frozen", "--no-dev", "--no-emit-project",
        "--format", "requirements-txt", "-o", requirements, cwd=ROOT)
    # Hashes from uv.lock pin every file; the CPU build of torch lives on PyTorch's index.
    run("uv", "pip", "install", "--python", python, "--no-deps", "--compile-bytecode",
        "--extra-index-url", TORCH_INDEX, "--index-strategy", "unsafe-best-match",
        "-r", requirements)


def smoke_test(python, bundle):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1")
    run(python, "-c", "import torch, transformers, sounddevice, keyring, soundfile, soxr, "
        "constriction, anthropic, PySide6.QtWidgets; import dogspeak_app.qt_app, dogspeak; "
        "from dogspeak_app import paths; assert paths.venv_python().name == 'python.exe', "
        "paths.venv_python(); print('bundle imports ok')", cwd=bundle, env=env)


def find_iscc():
    for candidate in (os.environ.get("ISCC"), shutil.which("iscc"),
                      r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                      r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Inno Setup 6 (ISCC.exe) not found; install it or set ISCC")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(ROOT / "build"))
    parser.add_argument("--no-installer", action="store_true", help="stop after the bundle")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("The Windows installer is built on Windows.")
    out = Path(args.out).resolve()
    bundle = out / "Dogspeak"
    shutil.rmtree(bundle, ignore_errors=True)
    bundle.mkdir(parents=True)
    python = bundle_python(bundle)
    install_dependencies(python, out)
    copy_app(bundle)
    write_icon(bundle)
    run(python, "-m", "compileall", "-q", "-j", "0", bundle / "dogspeak_app", bundle / "vendor",
        bundle / "dogspeak.py")
    smoke_test(python, bundle)
    if not args.no_installer:
        run(find_iscc(), f"/DAppVersion={version()}", f"/DBundle={bundle}", f"/O{out}",
            ROOT / "installer" / "dogspeak.iss")
        print("Built", out / f"Dogspeak-{version()}-Setup.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
