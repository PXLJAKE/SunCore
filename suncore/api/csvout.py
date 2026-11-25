# SPDX-License-Identifier: Apache-2.0
"""CSV for every history table (plan WP4, decision 7): UTF-8, ``,`` separator, ``.`` decimal,
ISO 8601 times with the site's UTC offset; units are part of the column names (``_w``, ``_wh``,
``_pct``); money columns come with a ``currency`` column. Empty cell = not known."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Final

from fastapi.responses import Response

MEDIA_TYPE: Final = "text/csv; charset=utf-8"


def cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(round(value, 4))
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def csv_response(
    filename: str, header: Sequence[str], rows: Iterable[Sequence[object]]
) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([cell(v) for v in row])
    return Response(
        content=buffer.getvalue().encode("utf-8"),
        media_type=MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
