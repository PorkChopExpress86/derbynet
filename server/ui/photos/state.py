"""What the photo management page shows (#47): the repository's badge, the
racer cards with their photo state, and the pool of unassigned photos.

Reads only. The files, the directory checks and the `lost_photo` flag come
from `media.photos.inventory()`, so no path is built here. Nothing polls:
the page lists the repository when it opens, as `photo-thumbs.php` does, and
every change answers with `photos-refresh`, which lists it again.
"""

from dataclasses import dataclass

from django.urls import get_script_prefix

from core.models import RaceSettings
from media import photos
from roster.models import Racer
from roster.services.groups import labels
from ui.checkin.state import Filters as RacerSearch
from ui.responses import car, nocase, racer_name

# The URL slug of each repository, and the registry entry of its page.
REPOS = {"head": "racer-photos", "car": "car-photos"}
REPO_LABELS = {"head": "Racers", "car": "Cars"}
REPO_NOUNS = {"head": "Racer photo", "car": "Car photo"}
# The racer list's render: RENDER_LISTVIEW of photo-thumbs.php.
THUMBNAIL = photos.RENDER_THUMBNAIL


def repo_of_page(key: str) -> str:
    return {page: repo for repo, page in REPOS.items()}[key]


def orders(event) -> tuple[tuple[str, str], ...]:
    """photo-thumbs.php's sort buttons: Name, `group_label()`, Car#."""
    group = labels(RaceSettings.objects.get(event=event)).group
    return (("name", "Name"), ("class", group), ("car", "Car"))


@dataclass(frozen=True)
class Filters:
    q: str = ""
    order: str = "name"

    @classmethod
    def from_request(cls, data) -> Filters:
        q = data.get("q", "")
        order = data.get("order", "")
        return cls(
            q=q.strip()[:100] if isinstance(q, str) else "",
            order=order if order in ("name", "class", "car") else "name",
        )


def _sort_key(order: str):
    """photo-thumbs.php's ORDER BY: `lastname, firstname`, or the car number
    or the class's sort order first."""

    def name(r):
        return (nocase(r.last_name), nocase(r.first_name), r.pk)

    if order == "car":
        return lambda r: (r.car_number, *name(r))
    if order == "class":
        return lambda r: (r.group.sort_order, *name(r))
    return name


def _url(url: str) -> str:
    return get_script_prefix() + url if url else ""


@dataclass(frozen=True)
class RacerCard:
    id: int
    car: str
    name: str
    group: str
    # The stored photo's file name, "" for none.
    photo: str = ""
    thumbnail: str = ""
    lost: bool = False

    @property
    def state(self) -> str:
        if not self.photo:
            return "none"
        return "lost" if self.lost else "assigned"


@dataclass(frozen=True)
class PoolPhoto:
    name: str
    thumbnail: str


@dataclass
class Listing:
    repo: str
    status: photos.DirectoryStatus
    cards: list[RacerCard]
    pool: list[PoolPhoto]
    total: int


def _card(racer: Racer, stored: photos.StoredPhoto | None, repo) -> RacerCard:
    common = {
        "id": racer.pk,
        "car": car(racer.car_number),
        "name": racer_name(racer.first_name, racer.last_name),
        "group": racer.group.name,
    }
    if stored is None:
        return RacerCard(**common)
    return RacerCard(
        **common,
        photo=stored.name,
        thumbnail=""
        if stored.lost
        else _url(photos.page_url(repo, stored.path, THUMBNAIL)),
        lost=stored.lost,
    )


def pool_photo(repository: photos.Repository, name: str) -> PoolPhoto:
    return PoolPhoto(name, _url(photos.page_thumbnail_url(repository, name)))


def build(event, repo: str, filters: Filters, *, without_photo=False) -> Listing:
    """The page's list. With `without_photo`, only the racers a photo can go
    to, as photo-thumbs.php's drop targets: no photo, or a lost one."""
    repository = photos.repository(event, repo)
    found = photos.inventory(event, repository)
    search = RacerSearch(q=filters.q)
    everyone = list(Racer.objects.filter(event=event).select_related("group"))
    cards = [
        _card(r, found.stored.get(r.pk), repository)
        for r in sorted(everyone, key=_sort_key(filters.order))
        if search.matches(r)
    ]
    if without_photo:
        cards = [c for c in cards if c.state != "assigned"]
    return Listing(
        repo=repo,
        status=found.status,
        cards=cards,
        pool=[pool_photo(repository, name) for name in found.unassigned],
        total=len(everyone),
    )


def card(event, repo: str, racer_id: int) -> RacerCard | None:
    repository = photos.repository(event, repo)
    racer = Racer.objects.filter(event=event, pk=racer_id).select_related("group")
    found = racer.first()
    if found is None:
        return None
    stored = photos.inventory(event, repository).stored.get(found.pk)
    return _card(found, stored, repository)
