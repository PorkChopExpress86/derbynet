"""`snapshot.get` and `snapshot.put`: the whole event as one file (#36).

PHP lets anyone download a snapshot (S5), writes the photo files named in an
uploaded one wherever its basename points (S6), and builds SQL from its table
and column names (S7). Both need `SET_UP` here, and `put` goes through the
importer, which knows its own table and column names and keeps a photo inside
the event's photo folder.

Other differences, in `DIVERGENCES`: the replaced event gets a new event
folder, so the folder keys PHP preserves belong to the new event; the file has
no `Events` or `EventLaneDetails` tables because Django keeps no timer event
log; the reply is built whole, not streamed.
"""

from accounts.permissions import Perm
from legacy_api.registry import LegacyContext, action, query
from legacy_api.xml import php_escape
from transfer import snapshot

DIVERGENCES = (
    "S5: needs SET_UP; PHP lets anyone download a snapshot.",
    "S6: a photo name is kept inside the event's photo folder.",
    "S7: an unknown table or column in an upload is skipped, never run as SQL.",
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
