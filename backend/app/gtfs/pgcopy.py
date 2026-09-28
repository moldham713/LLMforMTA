"""Bulk-load CSV streams into Postgres with COPY."""

import csv
from typing import BinaryIO

from sqlalchemy import Connection

CHUNK_BYTES = 1 << 20
UTF8_BOM = b"\xef\xbb\xbf"


def copy_csv_raw(
    conn: Connection, stream: BinaryIO, table: str, *, temporary: bool = False
) -> list[str]:
    """COPY a CSV into a new all-text table whose columns mirror the header.

    Mirroring the header lets COPY stream the raw bytes untouched, whatever optional
    columns the publisher includes; casting to real types happens afterwards in SQL.
    The table is UNLOGGED (it's scratch), or a temp table dropped at commit.
    Returns the header column names.
    """
    header = stream.readline().removeprefix(UTF8_BOM)
    columns = [c.strip() for c in next(csv.reader([header.decode("utf-8")]))]
    quote = conn.dialect.identifier_preparer.quote_identifier
    cols_sql = ", ".join(f"{quote(c)} text" for c in columns)
    if temporary:
        conn.exec_driver_sql(f"CREATE TEMP TABLE {table} ({cols_sql}) ON COMMIT DROP")
    else:
        conn.exec_driver_sql(f"CREATE UNLOGGED TABLE {table} ({cols_sql})")

    cursor = conn.connection.driver_connection.cursor()
    with cursor.copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
        cp.write(header)
        while chunk := stream.read(CHUNK_BYTES):
            cp.write(chunk)
    return columns


def copy_rows(conn: Connection, qualified_table: str, columns: list[str], rows) -> None:
    """COPY an iterable of Python tuples; for small derived tables built in Python."""
    quote = conn.dialect.identifier_preparer.quote_identifier
    cols_sql = ", ".join(quote(c) for c in columns)
    cursor = conn.connection.driver_connection.cursor()
    with cursor.copy(f"COPY {qualified_table} ({cols_sql}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)
