# Builds the desktop backend with PyInstaller into desktop/backend-dist.
#
# Run this on the OS you are building for: PyInstaller bundles the running platform and cannot
# cross-compile, so the Windows build comes from Windows, the macOS build from macOS, and the Linux
# build from Linux. The GitHub workflow does exactly that on three runners.
#
#     python desktop/build_backend.py
#
# Set MISO_BUNDLE_CHARACTERS=1 first to pack the character folders of this working copy into a
# personal build. A build for other people leaves them out.

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(HERE, "backend-dist")
WORK = os.path.join(HERE, ".pyi-build")


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run: python -m pip install -r desktop/requirements-build.txt")
        return 2
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", DIST,
        "--workpath", WORK,
        os.path.join(HERE, "miso-backend.spec"),
    ]
    print("building the backend for " + sys.platform)
    code = subprocess.call(args, cwd=ROOT)
    if code:
        return code
    exe = "miso-backend.exe" if sys.platform.startswith("win") else "miso-backend"
    path = os.path.join(DIST, "miso-backend", exe)
    if not os.path.isfile(path):
        print("PyInstaller finished but " + path + " is missing")
        return 1
    print("backend ready: " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
