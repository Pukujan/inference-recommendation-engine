"""11133 / HTTP 400 outcome classification (ihub/errclass.py; IRE #46 M4, #40 M0.6).

The SQL CASE expressions used by the DuckDB marts are rendered from errclass.py; here they are
evaluated in sqlite3 (standard library) against the Python rules so the two cannot drift.
"""

from __future__ import annotations

import importlib.util
import itertools
import sqlite3
import unittest
from pathlib import Path

IHUB = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline" / "ihub"
_spec = importlib.util.spec_from_file_location("ihub_errclass", IHUB / "errclass.py")
assert _spec and _spec.loader
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)


def cls(http: int | None, rail: str, code: str | None = None, ok: bool = False) -> tuple:
    t = E.error_type(not ok, http, rail, code)
    return t, E.error_class(t)


class ErrorClassTests(unittest.TestCase):
    def test_11133_code_is_upstream_on_any_rail(self) -> None:
        for rail in ("cb", "cbcn", "cx", "ali"):
            self.assertEqual(cls(400, rail, "11133"), ("upstream_reject", "upstream"))
        self.assertEqual(
            E.class_basis(True, 400, "cx", "11133", E.BASIS_CODE_INCIDENT), E.BASIS_CODE_INCIDENT
        )
        self.assertEqual(E.class_basis(True, 400, "cx", "11133", None), E.BASIS_CODE_MATCH)

    def test_uncoded_cb_400_is_presumed_11133(self) -> None:
        for rail in ("cb", "cbcn"):
            self.assertEqual(cls(400, rail), ("upstream_reject", "upstream"))
            self.assertEqual(E.class_basis(True, 400, rail, None, None), E.BASIS_RAIL_RULE)

    def test_other_client_errors_stay_excluded(self) -> None:
        self.assertEqual(cls(400, "cx"), ("client_request_error", "client"))
        self.assertEqual(cls(422, "cb"), ("client_request_error", "client"))
        self.assertEqual(cls(499, "cb"), ("client_cancelled", "client"))
        self.assertEqual(cls(401, "cb"), ("auth", "account"))
        self.assertEqual(cls(402, "zai"), ("payment_required", "account"))
        for c in ("client", "account"):
            self.assertIn(c, E.EXCLUDED_CLASSES)
        self.assertNotIn("upstream", E.EXCLUDED_CLASSES)
        self.assertEqual(E.class_basis(True, 400, "cx", None, None), E.BASIS_HTTP)

    def test_upstream_and_unknown(self) -> None:
        self.assertEqual(cls(502, "zai"), ("upstream_unavailable", "upstream"))
        self.assertEqual(cls(504, "cb"), ("timeout", "upstream"))
        self.assertEqual(cls(None, "cb"), ("unknown", "unknown"))
        self.assertEqual(cls(200, "cb", ok=True), ("ok", None))

    def test_rendered_sql_matches_python(self) -> None:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (is_error, http_status, rail, error_code, error_code_source)")
        https = [None, 200, 400, 401, 402, 403, 404, 408, 422, 429, 499, 500, 502, 503, 504, 599]
        rails = ["cb", "cbcn", "cx", "zai"]
        codes = [None, "11133", "12345"]
        srcs = [None, E.BASIS_CODE_INCIDENT]
        grid = list(itertools.product([0, 1], https, rails, codes, srcs))
        con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?)", grid)
        q = (
            f"SELECT is_error, http_status, rail, error_code, error_code_source, et, "
            f"{E.sql_error_class('et')}, {E.sql_class_basis('et')} "
            f"FROM (SELECT *, {E.sql_error_type()} AS et FROM t)"
        )
        rows = con.execute(q).fetchall()
        self.assertEqual(len(rows), len(grid))
        for is_err, http, rail, code, src, et, ec, basis in rows:
            want_t = E.error_type(bool(is_err), http, rail, code)
            self.assertEqual(et, want_t, (is_err, http, rail, code))
            self.assertEqual(ec, E.error_class(want_t))
            self.assertEqual(basis, E.class_basis(bool(is_err), http, rail, code, src))


if __name__ == "__main__":
    unittest.main()
