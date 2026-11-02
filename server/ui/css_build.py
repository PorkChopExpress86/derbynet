"""Builds `ui/static/ui/app.css` with the Tailwind standalone CLI (#40).

The CLI is one pinned binary, checked against its release checksum. No Node,
no npm, and no network at build time: `fetch` downloads the binary once into
the user's cache, and `build` then runs offline. Nothing runs at request time,
because the built file is committed and served as a static file.

This module needs only the standard library, so a bare Python with no Django
can run it: `python -I ui/css_build.py [fetch|build|check]` from `server/`.
`manage.py build_css` runs the same steps.
"""

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

TAILWIND_VERSION = "4.1.11"
# From the release's sha256sums.txt.
SHA256 = {
    "linux-arm64": "0409aa4222969f47fa6f4160fe5387e79bf7269e7afe0e8b22f7532c98e1d314",
    "linux-arm64-musl": "bbb75c8802861546925bba34bb1e53deca6ab9f33ab95a40e0ca7f10380ab744",  # noqa: E501
    "linux-x64": "64805b84af4292e043ea6f86d242f191c0ac75359c1a498455dfe6c642afdbab",
    "linux-x64-musl": "0fb5f1a24dc5237914b9a4375fa0e7520a99da28aa62c36a00c42c34b3df1146",  # noqa: E501
    "macos-arm64": "f5984b9c005c3e67841c33906c7a7c92e85e405f61e029e9bb62e880dd662e79",
    "macos-x64": "76e27326506d10d50e65b751795f0537f9304ecb100abe835ec138c41774f38c",
    "windows-x64.exe": "6631a41de25a96eb8506f07b07ab56192df117a29849c7d7a995bd343329e900",  # noqa: E501
}
RELEASE_URL = (
    "https://github.com/tailwindlabs/tailwindcss/releases/download"
    "/v{version}/tailwindcss-{platform}"
)

UI_DIR = Path(__file__).resolve().parent
INPUT = UI_DIR / "tailwind" / "app.css"
OUTPUT = UI_DIR / "static" / "ui" / "app.css"


OUT_OF_DATE = "{} is out of date. Run `manage.py build_css` and commit it."


class BuildError(Exception):
    pass


def _platform() -> str:
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch.get(machine)
    systems = {"linux": "linux", "darwin": "macos", "win32": "windows"}
    system = systems.get(sys.platform)
    if arch is None or system is None:
        raise BuildError(f"No Tailwind CLI build for {sys.platform} on {machine}.")
    if system == "windows":
        return f"windows-{arch}.exe"
    if system == "linux" and any(Path("/lib").glob("ld-musl-*")):
        # A musl system, such as Alpine.
        return f"linux-{arch}-musl"
    return f"{system}-{arch}"


def binary_path(platform_name: str | None = None) -> Path:
    """`DERBYNET_TAILWIND` names a binary already on disk. Otherwise the
    binary lives in the user's cache, named by version and platform."""
    if override := os.environ.get("DERBYNET_TAILWIND"):
        return Path(override)
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    name = f"tailwindcss-{TAILWIND_VERSION}-{platform_name or _platform()}"
    return cache / "derbynet" / name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, platform_name: str) -> None:
    actual = _sha256(path)
    if actual != SHA256[platform_name]:
        raise BuildError(
            f"{path} is not Tailwind CLI {TAILWIND_VERSION} for {platform_name}: "
            f"sha256 {actual}, expected {SHA256[platform_name]}. "
            "Delete it and fetch again."
        )


def fetch() -> Path:
    """Download the pinned binary unless it is already in the cache, and
    check its checksum either way."""
    platform_name = _platform()
    path = binary_path(platform_name)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        url = RELEASE_URL.format(version=TAILWIND_VERSION, platform=platform_name)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    shutil.copyfileobj(response, temp)
                temp.close()
                verify(Path(temp.name), platform_name)
                os.chmod(temp.name, 0o755)
                os.replace(temp.name, path)
            finally:
                if os.path.exists(temp.name):
                    os.unlink(temp.name)
    verify(path, platform_name)
    return path


def build(output: Path = OUTPUT) -> Path:
    """Write the minified CSS with the cached binary. Never downloads."""
    platform_name = _platform()
    path = binary_path(platform_name)
    if not path.exists():
        raise BuildError(
            f"Tailwind CLI {TAILWIND_VERSION} is not at {path}. Run "
            "`manage.py build_css --fetch` once with network access, or set "
            "DERBYNET_TAILWIND to the binary."
        )
    if not os.environ.get("DERBYNET_TAILWIND"):
        verify(path, platform_name)
    result = subprocess.run(
        [str(path), "--input", str(INPUT), "--output", str(output), "--minify"],
        cwd=UI_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BuildError(f"Tailwind CLI failed:\n{result.stderr}")
    return output


def check() -> bool:
    """True when the committed CSS is what a build writes now."""
    with tempfile.TemporaryDirectory() as temp:
        fresh = build(Path(temp) / "app.css")
        return OUTPUT.exists() and fresh.read_bytes() == OUTPUT.read_bytes()


def main(argv: list[str]) -> int:
    step = argv[0] if argv else "build"
    try:
        if step == "fetch":
            print(fetch())
        elif step == "build":
            print(f"wrote {build()}")
        elif step == "check":
            if not check():
                print(OUT_OF_DATE.format(OUTPUT))
                return 1
            print(f"{OUTPUT} is up to date.")
        else:
            print("usage: css_build.py [fetch|build|check]", file=sys.stderr)
            return 2
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
