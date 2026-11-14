"""The guides in `docs/` as the pages under `/docs/` (#61).

`manage.py build_docs` renders each guide's Markdown to HTML with the Python
`markdown` package, writes one `<slug>.json` per guide (its title and its
HTML) and `index.json` into `settings.DOCS_ROOT`, and copies `docs/img/`
beside them. The Docker image runs it at build time (server/docker/Dockerfile),
so a request only reads files and no page needs the network (#61's Scope).

`GUIDES` is the fixed list the build renders and the views serve: a URL names
a guide by its slug, and never a file path. `ui/tests/test_docs.py` fails a
guide in `docs/` that the list leaves out, and a slug with no guide.

Links are rewritten so they work under any URL prefix (`DERBYNET_URL_PREFIX`)
with no setting: a page is served at `/docs/<slug>/`, so `other.md#part`
becomes `../other/#part` and `img/x.png` becomes `../img/x.png`. A relative
link to another repository file, such as `../server/README.md`, goes to that
file on GitHub, since the image ships only `docs/`.
"""

import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "docs"
IMAGES = "img"
# The repository's files, for a link to one outside docs/.
GITHUB = "https://github.com/PorkChopExpress86/derbynet/blob/master/"

# Every guide, in the order the index lists them. PR 2 of #61 adds
# running-a-race, timer-operation, photo-upload and balloting (D91, D93).
GUIDES: tuple[str, ...] = (
    "quick-reference",
    "install-docker",
    "secure-contexts",
    "roles-and-passwords",
    "database",
    "live-streaming",
    "photo-stand",
    "dev-timer-protocol",
    "dev-replay-protocol",
    "developer-guide",
)

INDEX = "index.json"
EXTENSIONS = ["tables", "fenced_code", "toc", "sane_lists"]
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


class BuildError(Exception):
    pass


def link_target(href: str, page_dir: Path = SOURCE) -> str:
    """The URL a relative link of a guide becomes on its `/docs/<slug>/` page.
    An absolute URL, a scheme (`mailto:`), a root path and a fragment stay."""
    if not href or SCHEME.match(href) or href.startswith(("/", "#")):
        return href
    parts = urlsplit(href)
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    path = parts.path
    if path.endswith(".md") and "/" not in path and path[:-3] in GUIDES:
        return f"../{path[:-3]}/{fragment}"
    if path.startswith(f"{IMAGES}/"):
        return f"../{path}{fragment}"
    target = (page_dir / path).resolve()
    try:
        relative = target.relative_to(REPO)
    except ValueError:
        return href
    return f"{GITHUB}{relative.as_posix()}{fragment}"


# A code block or a table scrolls sideways in its own box on a phone
# (`ui/static/ui/docs.css`), so it takes the keyboard focus to scroll by keys
# (axe's scrollable-region-focusable). A fenced code block reaches the HTML
# as stashed raw HTML, after the tree is built, so the tags are rewritten in
# the HTML itself.
SCROLLERS = re.compile(r"<(pre|table)>")


class _Links(Treeprocessor):
    def run(self, root: Element) -> None:
        for element in root.iter():
            for attribute in ("href", "src"):
                if (value := element.get(attribute)) is not None:
                    element.set(attribute, link_target(value))


class _LinksExtension(Extension):
    def extendMarkdown(self, md):  # noqa: N802 (the markdown package's name)
        md.treeprocessors.register(_Links(md), "derbynet_links", 0)


def render(text: str) -> tuple[str, str]:
    """(title, HTML) of one guide's Markdown. The title is its first `# `
    heading, which every guide starts with."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    if not first.startswith("# "):
        raise BuildError(f"a guide starts with its `# ` title, not {first!r}")
    html = markdown.markdown(text, extensions=[*EXTENSIONS, _LinksExtension()])
    html = SCROLLERS.sub(r'<\1 tabindex="0">', html)
    return first[2:].strip(), html


def anchors(text: str) -> list[str]:
    """The ids the build gives a guide's headings, in order: what a `#part`
    link to the guide must name. `server/docs/adr/check-paths.sh --docs`
    reads them from here, so the check and the pages share one rule (the
    `toc` extension's: `## Roles - passwords` is `roles-passwords`, and a
    second `## Same` is `same_1`)."""
    md = markdown.Markdown(extensions=EXTENSIONS)
    md.convert(text)
    ids: list[str] = []

    def walk(tokens):
        for token in tokens:
            ids.append(token["id"])
            walk(token["children"])

    walk(md.toc_tokens)
    return ids


def build(out: Path, source: Path = SOURCE) -> list[Path]:
    """Render every guide in `GUIDES` from `source` into `out`, and copy its
    images. Returns the files written."""
    out.mkdir(parents=True, exist_ok=True)
    written, index = [], []
    for slug in GUIDES:
        path = source / f"{slug}.md"
        if not path.is_file():
            raise BuildError(f"{path} is missing")
        title, html = render(path.read_text(encoding="utf-8"))
        target = out / f"{slug}.json"
        target.write_text(json.dumps({"title": title, "html": html}), encoding="utf-8")
        written.append(target)
        index.append({"slug": slug, "title": title})
    images = out / IMAGES
    shutil.rmtree(images, ignore_errors=True)
    names = []
    if (source / IMAGES).is_dir():
        shutil.copytree(source / IMAGES, images)
        names = sorted(
            p.relative_to(images).as_posix() for p in images.rglob("*") if p.is_file()
        )
    (out / INDEX).write_text(
        json.dumps({"guides": index, "images": names}), encoding="utf-8"
    )
    written.append(out / INDEX)
    return written


if __name__ == "__main__":
    # `build.py --anchors FILE...` prints `<resolved FILE>\t<id>` for each
    # heading, for check-paths.sh. It needs only the markdown package.
    import sys

    if sys.argv[1:2] != ["--anchors"]:
        sys.exit("usage: build.py --anchors FILE...")
    for name in sys.argv[2:]:
        path = Path(name).resolve()
        for anchor in anchors(path.read_text(encoding="utf-8")):
            print(f"{path}\t{anchor}")
