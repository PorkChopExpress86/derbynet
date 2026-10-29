"""`snapshot.get` and `snapshot.put`: the whole event as one file (#36).

PHP lets anyone download a snapshot (S5), writes the photo files named in an
uploaded one wherever its basename points (S6), and builds SQL from its table
and column names (S7). Both need `SET_UP` here. `put` refuses a file with a table
or column outside the export schema, or a photo name that is not a plain file
name, and changes nothing. Then it goes through the importer.

The file's `Events` and `EventLaneDetails` tables are always empty, because
Django keeps no timer event log. The reply is built whole, not streamed, and
the restored event gets a new event folder, so the folder keys PHP preserves
belong to the new event (#134).
"""

from accounts.permissions import Perm
from legacy_api.registry import LegacyContext, action, query
from legacy_api.xml import php_escape
from transfer import snapshot

DIVERGENCES = (
    "S5: needs SET_UP; PHP lets anyone download a snapshot.",
    "S6: a photo name that is not a plain file name refuses the upload.",
    "S7: an unknown table or column refuses the upload, and none is run as SQL.",
    "D57: the reply is built in memory, its Events tables are empty, and a restore"
    " is a new event with new ids and new folders.",
)


@query("snapshot.get", permission=Perm.SET_UP, divergences=DIVERGENCES)
def snapshot_get(ctx: LegacyContext) -> None:
    ctx.xml.write(
        snapshot.get(ctx.event, clean="clean" in ctx.args, photos="photos" in ctx.args)
    )


@action("snapshot.put", permission=Perm.SET_UP, divergences=DIVERGENCES)
def snapshot_put(ctx: LegacyContext) -> None:
    upload = ctx.files.get("snapshot")
    ctx.xml.start_response()
    ctx.xml.write("\n")
    name = php_escape(upload.name) if upload else ""
    ctx.xml.write(
        f'<uploaded-file name="{name}" size="{upload.size if upload else 0}">\n'
    )
    try:
        received = snapshot.put(ctx.event, upload.read() if upload else b"")
    except snapshot.PutFailure as failure:
        ctx.xml.failure(failure.code, failure.description)
        ctx.xml.write("\n")
    else:
        for table, id_column, before, count in received:
            ctx.xml.write(
                f'<table name="{table}" rows-before="{before}"'
                f' idcolumn="{id_column}">\n'
                f'  <table-summary rows-received="{count}"/>\n</table>\n'
            )
        ctx.xml.write("<success/>\n")
    ctx.xml.write("</uploaded-file>\n")
    ctx.xml.end_response()
