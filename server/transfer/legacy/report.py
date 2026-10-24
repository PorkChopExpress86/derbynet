"""What an import read, wrote, dropped, and changed."""

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class Kind(StrEnum):
    """Every kind of item the import report lists."""

    DROPPED_ROW = "dropped-row"
    CHANGED_VALUE = "changed-value"
    UNSCOPED_AWARD = "unscoped-award"
    DUPLICATE_KEY = "duplicate-key"
    UNKNOWN_KEY = "unknown-key"
    BAD_VALUE = "bad-value"
    DROPPED_KEY = "dropped-key"
    CONSTITUENT_MISMATCH = "constituent-mismatch"
    LOCAL_KIOSK_PAGE = "local-kiosk-page"
    UNPARSED_ACTION = "unparsed-action"
    PHOTO_PATH = "photo-path"
    MISSING_PHOTO = "missing-photo"
    SYMLINK = "symlink"
    AMBIGUOUS_TIME = "ambiguous-time"


@dataclass(frozen=True)
class Finding:
    kind: Kind
    table: str
    legacy_id: object
    detail: str


# Legacy tables the importer reads only for other tables, or not at all (#12).
NOT_IMPORTED = {
    "awardtypes": "Site-level AwardType rows are seeded; awards keep their type id.",
    "events": "Not imported (D15). Read only for check-in times.",
    "eventlanedetails": "Not imported (D15).",
    "checkinaudit": "Dropped (#12).",
    "messagequeue": "Not imported (#14 out of scope).",
    "timersettings": "Not imported (#14 out of scope).",
    "raceinfo": "Split into the event's settings and state rows.",
}


@dataclass
class Report:
    source: dict
    event: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    photos: dict = field(default_factory=dict)
    invariants: dict = field(default_factory=dict)
    outcome: str = "rolled back"

    def add(self, kind, table, legacy_id, detail):
        self.findings.append(Finding(Kind(kind), table, legacy_id, detail))

    def count(self, table, source, imported):
        self.counts[table] = {"source": source, "imported": imported}

    def as_json(self):
        return {
            "source": self.source,
            "event": self.event,
            "outcome": self.outcome,
            "counts": self.counts,
            "not_imported": {
                table: reason
                for table, reason in NOT_IMPORTED.items()
                if table in self.source.get("tables", ())
            },
            "photos": self.photos,
            "findings": [asdict(finding) for finding in self.findings],
            "invariants": self.invariants,
        }

    def summary(self):
        lines = [
            f"{self.source['path']} ({self.source['kind']}, schema"
            f" {self.source['version']}): {self.outcome}"
        ]
        if self.event:
            lines.append(f"event {self.event['name']!r} (slug {self.event['slug']})")
        lines.append("rows imported of rows read:")
        for table, count in sorted(self.counts.items()):
            lines.append(f"  {table}: {count['imported']} of {count['source']}")
        if self.photos:
            lines.append(
                f"photos: {self.photos['paths']} paths, {self.photos['files_copied']}"
                " files copied"
            )
        by_kind = Counter(finding.kind for finding in self.findings)
        if by_kind:
            lines.append("items dropped or changed:")
            for kind, n in sorted(by_kind.items()):
                lines.append(f"  {kind}: {n}")
                for finding in [f for f in self.findings if f.kind == kind][:5]:
                    where = finding.table
                    if finding.legacy_id is not None:
                        where += f" {finding.legacy_id}"
                    lines.append(f"    {where}: {finding.detail}")
                if n > 5:
                    lines.append(f"    ... {n - 5} more in the JSON report")
        if self.invariants:
            lines.append("invariant violations:")
            for tag, rows in sorted(self.invariants.items()):
                lines.append(f"  {tag}: {len(rows)}")
        else:
            lines.append("check_invariants: no violations")
        return "\n".join(lines)
