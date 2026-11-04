"""Builds the browser timer's generated files for Django (#50), as the Ant
targets `timer.php`, `timer-profiles` and `timer-in-browser` of `build.xml`
build them for PHP.

- `django_template()` turns `templates/timer.html` into the Django template
  `/timer.php` renders. The template is committed; `check_template()` fails
  when it no longer matches its source, and CI runs it.
- `copy_sources()` copies `timer/derbynet-timer/src/js/*.js` and its CSS into
  `ui/static/timer/`, at the paths the page loads them from.
- `profiles_js()` runs `org.jeffpiazza.derby.profiles.AllProfiles` and fills
  `templates/timer-profiles.js`. It compiles the timer's Java sources with
  `javac`, with no Ant and no jar, or runs both in a JDK container when the
  host has no JDK.

The copied scripts and `profiles.js` are not committed (`.gitignore`). The ui
CI job runs `manage.py build_timer` before the browser tests. No Django image
exists yet; the image ticket (#58) has to run it too, so nothing runs Java at
request time.
The legacy files the page also loads, jQuery, `mobile.js`, `modal.js`,
`ajax-setup.js`, `global.css`, `mobile.css` and the images, are committed
copies under `ui/static/timer/`, so the page keeps working once `website/`
is gone (#4).
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent
REPO = UI_DIR.parent.parent
SOURCE_HTML = REPO / "templates" / "timer.html"
PROFILES_TEMPLATE = REPO / "templates" / "timer-profiles.js"
TIMER_SRC = REPO / "timer" / "derbynet-timer" / "src"
JAVA_SRC = REPO / "timer" / "src"
JAVA_LIBS = [
    REPO / "lib" / name
    for name in ("jssc.jar", "Java-WebSocket-1.5.2.jar", "json-20210307.jar")
]

ASSETS = UI_DIR / "static" / "timer"
TEMPLATE = UI_DIR / "templates" / "ui" / "timer" / "browser.html"
PROFILES = ASSETS / "js" / "timer" / "profiles.js"

# A JDK image for a host with no `javac`, pinned by digest. Override with
# DERBYNET_JDK_IMAGE.
JDK_IMAGE = (
    "eclipse-temurin:21-jdk@sha256:"
    "ebfbb18d11ff163ee83b21258acf12248af48d22a0fdfdc68fa1a1516c05976a"
)

OUT_OF_DATE = (
    "{} does not match templates/timer.html. Run `manage.py build_timer` and commit it."
)

PHP_BLOCK = re.compile(r"@PHP_START@.*?@PHP_END@", re.S)
ELECTRON_BLOCK = re.compile(r"\n?@ELECTRON_ONLY_START@.*?@ELECTRON_ONLY_END@", re.S)
SETTINGS_BUTTON = re.compile(r"[ \t]*<img id=\"timer-settings-button\".*?/>\n", re.S)

HEADER = (
    "{# Generated from templates/timer.html by ui/timer_build.py (#50). "
    "Do not edit: run `manage.py build_timer`. #}"
)
# Sent to the unsupported-browser page before any timer script runs, so a
# browser without Web Serial never shows a broken timer. #50's scope asks
# for it; PHP shows its own dialog over the page. No #64 or decision id yet.
SERIAL_GUARD = (
    '<script type="text/javascript">\n'
    "if (!('serial' in navigator)) "
    "{ window.location.replace('{{ no_serial_url|escapejs }}'); }\n"
    "</script>\n"
)
# D53: only a session with CONTROL_RACE gets the Timer settings button, as
# `timer.settings` needs CONTROL_RACE.
SETTINGS_IF = "{% if can_set_timer %}\n"
SETTINGS_ENDIF = "{% endif %}\n"


class BuildError(Exception):
    pass


def django_template(source: str) -> str:
    """`templates/timer.html` as a Django template: the PHP block and the
    Electron-only blocks removed, the version tokens filled from `version`,
    the Timer settings button shown only with `can_set_timer`, and a guard
    that sends a browser without Web Serial to `no_serial_url`."""
    if any(mark in source for mark in ("{{", "{%", "{#")):
        raise BuildError("templates/timer.html holds Django template syntax")
    html = PHP_BLOCK.sub("", source, count=1)
    html = ELECTRON_BLOCK.sub("", html)
    for token in ("@PHP_START@", "@PHP_END@", "@ELECTRON_ONLY_START@"):
        if token in html:
            raise BuildError(f"{token} is not where the build expects it")
    html = (
        html.replace('"@BRANCH@"', '"{{ version.branch|escapejs }}"')
        .replace("@REV_COUNT@", "{{ version.revision }}")
        .replace('"@BUILD_DATE@"', '"{{ version.date|escapejs }}"')
    )
    if unfilled := re.search(r"@[A-Z_]+@", html):
        raise BuildError(f"unfilled token {unfilled.group()}")
    button = SETTINGS_BUTTON.search(html)
    if button is None:
        raise BuildError("templates/timer.html has no #timer-settings-button")
    html = (
        html[: button.start()]
        + SETTINGS_IF
        + button.group()
        + SETTINGS_ENDIF
        + html[button.end() :]
    )
    if "<head>\n" not in html:
        raise BuildError("templates/timer.html has no <head>")
    html = html.replace("<head>\n", "<head>\n" + SERIAL_GUARD, 1)
    return HEADER + html.lstrip("\n")


def write_template() -> Path:
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE.write_text(django_template(SOURCE_HTML.read_text()))
    return TEMPLATE


def check_template() -> bool:
    return TEMPLATE.exists() and TEMPLATE.read_text() == django_template(
        SOURCE_HTML.read_text()
    )


def copy_sources() -> list[Path]:
    """`timer-in-browser`: the scripts into `js/timer/`, the CSS into `css/`."""
    written = []
    for folder, pattern, into in (("js", "*.js", "js/timer"), ("css", "*.css", "css")):
        target = ASSETS / into
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted((TIMER_SRC / folder).glob(pattern)):
            shutil.copyfile(path, target / path.name)
            written.append(target / path.name)
    return written


def _run(argv, **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, check=True, **kwargs
        )
    except FileNotFoundError as e:
        raise BuildError(f"{argv[0]} is not installed") from e
    except subprocess.CalledProcessError as e:
        raise BuildError(f"{' '.join(map(str, argv[:3]))} failed:\n{e.stderr}") from e


# Compiles the timer's Java sources into a temporary folder and prints the
# profiles. Run on the host, or in the JDK container with the checkout at /w.
_JAVA_SCRIPT = """set -e
out=$(mktemp -d)
find "$1/timer/src" -name '*.java' > "$out/sources"
javac -nowarn -encoding UTF-8 -d "$out" -cp "$2" @"$out/sources" >&2
java -cp "$out:$2" org.jeffpiazza.derby.profiles.AllProfiles
"""


def all_profiles() -> str:
    """The JSON that `AllProfiles` prints."""
    if shutil.which("javac") and shutil.which("java"):
        classpath = os.pathsep.join(str(p) for p in JAVA_LIBS)
        return _run(["sh", "-c", _JAVA_SCRIPT, "sh", str(REPO), classpath]).stdout
    if not shutil.which("docker"):
        raise BuildError("Generating profiles.js needs a JDK (javac) or docker.")
    image = os.environ.get("DERBYNET_JDK_IMAGE") or JDK_IMAGE
    classpath = ":".join(f"/w/lib/{p.name}" for p in JAVA_LIBS)
    return _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{REPO}:/w:ro",
            "-e",
            "LC_ALL=C.UTF-8",
            image,
            "sh",
            "-c",
            _JAVA_SCRIPT,
            "sh",
            "/w",
            classpath,
        ]
    ).stdout


def profiles_js(profiles_json: str | None = None) -> Path:
    """`timer-profiles`: `templates/timer-profiles.js` with `@PROFILES@`
    filled, as Ant's `replacetokens` fills it."""
    profiles = profiles_json if profiles_json is not None else all_profiles()
    if not profiles.strip().startswith("["):
        raise BuildError(f"AllProfiles printed no JSON array: {profiles[:200]!r}")
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    text = PROFILES_TEMPLATE.read_text().replace("@PROFILES@", profiles.strip())
    with tempfile.NamedTemporaryFile(
        "w", dir=PROFILES.parent, delete=False, suffix=".tmp"
    ) as out:
        out.write(text)
    Path(out.name).replace(PROFILES)
    return PROFILES


def built() -> bool:
    """The copied scripts and `profiles.js` are there."""
    return PROFILES.exists() and (ASSETS / "js" / "timer" / "main.js").exists()
