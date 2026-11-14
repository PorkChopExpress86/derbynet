"""The guides under `/docs/` (#61): the build, the pages, their images, the
help link, and the old `quickref.pdf` URL.

Anyone reads them, before the first event too (D92). `/quickref.pdf` answers
301 to the quick reference (D94, D20).
"""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from ui.docs import build
from ui.docs.build import GITHUB, GUIDES, link_target

pytestmark = pytest.mark.django_db

DOCS = build.SOURCE


@pytest.fixture
def built(settings, tmp_path):
    """The repository's guides, built into a folder of their own."""
    settings.DOCS_ROOT = tmp_path / "docs"
    build.build(settings.DOCS_ROOT)
    return settings.DOCS_ROOT


def test_every_guide_in_docs_is_listed_and_every_listed_guide_exists():
    on_disk = {p.stem for p in DOCS.glob("*.md")}
    assert set(GUIDES) == on_disk
    assert len(GUIDES) == len(set(GUIDES))


def test_the_build_writes_each_guide_with_its_title_and_the_images(built):
    index = json.loads((built / "index.json").read_text())
    assert [g["slug"] for g in index["guides"]] == list(GUIDES)
    for guide in index["guides"]:
        page = json.loads((built / f"{guide['slug']}.json").read_text())
        first = (DOCS / f"{guide['slug']}.md").read_text().splitlines()[0]
        assert page["title"] == guide["title"] == first.removeprefix("# ")
        assert page["html"].startswith("<h1")
    # Each guide's images are in docs/img/<guide>/.
    on_disk = sorted(
        p.relative_to(DOCS / "img").as_posix() for p in (DOCS / "img").glob("*/*")
    )
    assert index["images"] == on_disk
    assert all((built / "img" / name).is_file() for name in on_disk)


def test_the_command_builds_into_the_folder_it_is_given(tmp_path):
    out = tmp_path / "out"
    call_command("build_docs", out=out, stdout=StringIO())
    assert sorted(p.name for p in out.glob("*.json")) == sorted(
        [*(f"{slug}.json" for slug in GUIDES), "index.json"]
    )


def test_a_guide_without_its_title_is_refused(tmp_path):
    with pytest.raises(build.BuildError):
        build.render("No title here.\n")


@pytest.mark.parametrize(
    ("href", "target"),
    [
        ("install-docker.md", "../install-docker/"),
        ("install-docker.md#start-derbynet", "../install-docker/#start-derbynet"),
        ("img/live-streaming/a.jpg", "../img/live-streaming/a.jpg"),
        ("../server/README.md", f"{GITHUB}server/README.md"),
        ("../server/README.md#set-up", f"{GITHUB}server/README.md#set-up"),
        ("#part", "#part"),
        ("https://example.org/", "https://example.org/"),
        ("mailto:a@example.org", "mailto:a@example.org"),
        ("/timer.php", "/timer.php"),
        # Not a guide: a Markdown file elsewhere is a repository file.
        ("unknown.md", f"{GITHUB}docs/unknown.md"),
    ],
)
def test_links_are_relative_to_the_page_so_any_url_prefix_works(href, target):
    assert link_target(href) == target


def test_the_rendered_links_and_images_follow_the_rule():
    _, html = build.render(
        "# T\n\nSee [x](install-docker.md#start-derbynet).\n\n![p](img/a/b.png)\n"
    )
    assert 'href="../install-docker/#start-derbynet"' in html
    assert 'src="../img/a/b.png"' in html


@pytest.mark.parametrize("role", ["anonymous", "RaceCrew", "RaceCoordinator"])
def test_anyone_reads_a_guide(login, built, role):
    """D92: a guide needs no login, refining D5 as D70 did."""
    response = login(role).get("/docs/install-docker/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "<h1" in page and "Install DerbyNet with Docker" in page
    assert 'data-doc="install-docker"' in page
    assert "ui/docs.css" in page


def test_a_visitor_reads_the_guides_before_the_first_event(client, built):
    """A new install has no event yet, and its phones need the install and
    certificate guides then (D92)."""
    from core.models import Event

    assert not Event.objects.exists()
    for path in ("/docs/", "/docs/quick-reference/", "/docs/secure-contexts/"):
        assert client.get(path).status_code == 200, path


def test_the_index_lists_every_guide(client, event, built):
    page = client.get("/docs/").content.decode()
    for slug in GUIDES:
        assert f'href="/docs/{slug}/"' in page


def test_a_guide_without_the_trailing_slash_moves_to_it(client, event, built):
    response = client.get("/docs/quick-reference")
    assert response.status_code == 301
    assert response["Location"] == "/docs/quick-reference/"


def test_an_unknown_guide_is_404(client, event, built):
    assert client.get("/docs/no-such-guide/").status_code == 404


def test_unbuilt_a_guide_is_404_and_the_index_says_how_to_build(
    client, event, settings
):
    settings.DOCS_ROOT = Path("/nonexistent/docs")
    assert client.get("/docs/quick-reference/").status_code == 404
    response = client.get("/docs/")
    assert response.status_code == 200
    assert "Run `manage.py build_docs`." in response.content.decode()


def test_an_image_is_served_and_only_a_built_image(client, event, built):
    name = json.loads((built / "index.json").read_text())["images"][0]
    response = client.get(f"/docs/img/{name}")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert b"".join(response.streaming_content) == (DOCS / "img" / name).read_bytes()
    assert client.get("/docs/img/no-such.png").status_code == 404
    assert client.get("/docs/img/../index.json").status_code == 404
    assert client.get("/docs/img/%2e%2e/index.json").status_code == 404


def test_the_help_link_opens_the_quick_reference(login):
    """The phone's More drawer and the desktop sidebar both hold it (D94)."""
    page = login("RaceCrew").get("/").content.decode()
    assert page.count('href="/docs/quick-reference/"') == 2
    assert "quickref.pdf" not in page


@pytest.mark.parametrize("prefix", ["", "/derbynet"])
def test_the_old_quickref_pdf_moves_to_the_quick_reference(client, event, prefix):
    """D94: `quickref.pdf`, which PHP's help icon opened
    (`templates/banner.inc`), answers 301 with its query kept (D20)."""
    response = client.get(f"{prefix}/quickref.pdf")
    assert response.status_code == 301
    assert response["Location"] == "/docs/quick-reference/"
    response = client.get(f"{prefix}/quickref.pdf?x=1")
    assert response["Location"] == "/docs/quick-reference/?x=1"


@pytest.mark.parametrize(
    ("text", "line"),
    [
        # install-docker.md at 565e0c84: text after a closing fence left the
        # block open until the next fence, so Upgrade became code.
        ("# T\n\n```sh\nrun\n``` Then more.\n\n## Upgrade\n\n```sh\npull\n```\n", 5),
        # A fence with no closing line is text, backticks and all.
        ("# T\n\n```sh\nrun\n", 3),
        # Text after the only closing fence: the renderer makes inline code.
        ("# T\n\n```sh\nrun\n``` Then more.\n", 3),
        # dev-timer-protocol.md and photo-stand.md at 565e0c84: a fence
        # indented under a list item is no fence to the renderer, so the
        # JSON became one inline code span and the list restarted.
        ("# T\n\n1. Ask:\n\n   ```json\n   {}\n   ```\n\n2. Then.\n", 5),
        ("# T\n\n~~~\nrun\n~~~ Then more.\n\n~~~\npull\n~~~\n", 5),
    ],
)
def test_a_fence_the_renderer_does_not_take_is_refused(text, line):
    with pytest.raises(build.BuildError, match=rf"line {line}: .*not a fence"):
        build.render(text)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (
            "# T\n\n```sh\nrun\n```\n\nThen more.\n",
            '<code class="language-sh">run\n</code>',
        ),
        # Backticks in an inline code span.
        ("# T\n\nType ```` ``` ```` to open a fence.\n", "<code>```</code>"),
        ("# T\n\n```` ``` ```` opens a fence.\n", "<code>```</code> opens"),
        # A four-backtick fence that shows a three-backtick one.
        ("# T\n\n````md\n```sh\nrun\n```\n````\n", "```sh\nrun\n```\n</code>"),
        # A tilde fence that holds backticks.
        ("# T\n\n~~~md\n```sh\nrun\n```\n~~~\n", "```sh\nrun\n```\n</code>"),
        # A closing fence with spaces after it.
        ("# T\n\n```\nrun\n```   \n\nThen.\n", "<code>run\n</code>"),
        # An indented code block that shows a fence.
        (
            "# T\n\nText.\n\n    ```sh\n    run\n    ```\n",
            "<code>```sh\nrun\n```\n</code>",
        ),
        # An indented fence line inside a fenced block.
        (
            "# T\n\n```md\n  ```sh\n  run\n  ```\n```\n",
            "  ```sh\n  run\n  ```\n</code>",
        ),
        # Windows line endings.
        (
            "# T\r\n\r\n```sh\r\nrun\r\n```\r\n",
            '<code class="language-sh">run\n</code>',
        ),
    ],
)
def test_a_fence_the_renderer_takes_renders(text, code):
    _, html = build.render(text)
    assert code in html


def test_the_section_after_a_closed_fence_is_a_section():
    _, html = build.render("# T\n\n```sh\nrun\n```\n\n## Upgrade\n")
    assert '<h2 id="upgrade">Upgrade</h2>' in html


def test_a_code_block_and_a_table_take_the_focus_to_scroll():
    _, html = build.render("# T\n\n```\ncode\n```\n\n| a |\n|---|\n| b |\n")
    assert '<pre tabindex="0">' in html
    assert '<table tabindex="0">' in html


def test_a_file_the_build_did_not_list_is_not_served(client, event, built):
    """The image route serves only names in the build's index, never a path
    joined from the URL."""
    (built / "img" / "planted.png").write_bytes(b"not listed")
    assert (built / "img" / "planted.png").is_file()
    assert client.get("/docs/img/planted.png").status_code == 404


def test_a_slug_that_names_no_guide_never_reads_a_build_file(client, event, built):
    """`index.json` sits beside the guides' files; `/docs/index/` is no guide."""
    assert (built / "index.json").is_file()
    assert client.get("/docs/index/").status_code == 404
