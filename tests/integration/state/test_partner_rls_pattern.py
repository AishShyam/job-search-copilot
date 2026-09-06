"""Integration tests for the partner-scoped RLS pattern (FR16, schema-level).

Covers ``state/partner_shared_mode_rls.sql``: a standalone, not-applied-by-
default policy pattern that would, under ``mode: shared``, give a partner
instance read-only access to ``contacts`` and ``skill_gap_findings`` only.

Two things are proven here:

* With the pattern applied, the ``partner_readonly`` role reads every row
  of those two tables (not a per-owner subset) and is denied everything
  else.
* Without the pattern's SQL file applied -- a database carrying only
  S0-02's ``state/schema.sql`` + ``state/rls_policies.sql`` -- no
  partner/cross-instance access exists at all.
"""

from __future__ import annotations

import os  # reads SUPABASE_TEST_DB_URL from the environment, never hardcoded
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg import Connection
from psycopg.errors import InsufficientPrivilege  # raised when a grant is missing

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "state" / "schema.sql"
RLS_PATH = REPO_ROOT / "state" / "rls_policies.sql"
PARTNER_RLS_PATH = REPO_ROOT / "state" / "partner_shared_mode_rls.sql"
TEST_DB_URL_ENV = "SUPABASE_TEST_DB_URL"

PARTNER_ROLE = "partner_readonly"
# The only two tables a partner instance is ever meant to reach (FR16).
PARTNER_READABLE_TABLES = ("contacts", "skill_gap_findings")
# Everything else in the S0-02 schema -- a partner must not touch these.
PARTNER_FORBIDDEN_TABLES = (
    "roles",
    "application_status_history",
    "digests",
    "digest_roles",
)

# Two owners, seeded with one row each in every partner-readable table, so
# "sees all rows" is a meaningful assertion (2 rows, not a filtered 1).
OWNER_A = UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = UUID("22222222-2222-4222-8222-222222222222")
ROLE_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ROLE_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def baseline_database() -> Iterator[Connection]:
    """Apply ONLY S0-02's schema + RLS -- the partner pattern stays absent.

    Generator fixture (setup before ``yield``, cleanup after). Module-scoped
    because applying two migrations and seeding is expensive; every test in
    this file shares the one connection and transaction, which is rolled
    back at teardown so nothing persists in the test project.
    """
    database_url = os.getenv(TEST_DB_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DB_URL_ENV} is not set")

    connection = psycopg.connect(database_url)
    try:
        with connection.cursor() as cursor:
            existing = cursor.execute(
                """
                select tablename
                from pg_catalog.pg_tables
                where schemaname = 'public'
                """,
            ).fetchall()
            if existing:
                pytest.fail(
                    f"{TEST_DB_URL_ENV} must point to a clean test database; "
                    f"found existing tables: {existing}"
                )

            cursor.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            cursor.execute(RLS_PATH.read_text(encoding="utf-8"))
            _seed_two_owners(cursor)

        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def partner_database(baseline_database: Connection) -> Iterator[Connection]:
    """``baseline_database`` plus the partner pattern, applied in a savepoint.

    Applying it here -- inside the test's own setup, never merged into the
    shared schema files -- is the whole point of the pattern: it proves the
    policy is expressible in Postgres RLS without shipping it to every
    instance. The savepoint is rolled back after each test, so the role and
    its policies never outlive the test that needs them.
    """
    connection = baseline_database
    connection.execute("savepoint partner_pattern")
    with connection.cursor() as cursor:
        cursor.execute(PARTNER_RLS_PATH.read_text(encoding="utf-8"))
    try:
        yield connection
    finally:
        connection.execute("rollback to savepoint partner_pattern")
        connection.execute("release savepoint partner_pattern")


def _seed_two_owners(cursor: psycopg.Cursor) -> None:
    """Create two owners, each with one row in every partner-readable table."""
    # auth.users is Supabase's internal auth table; insert fake users
    # directly so the owner_id foreign keys resolve.
    cursor.execute(
        """
        insert into auth.users
            (id, instance_id, aud, role, email, created_at, updated_at)
        values
            (%s, '00000000-0000-0000-0000-000000000000', 'authenticated',
             'authenticated', 'owner-a@example.test', now(), now()),
            (%s, '00000000-0000-0000-0000-000000000000', 'authenticated',
             'authenticated', 'owner-b@example.test', now(), now())
        """,
        (OWNER_A, OWNER_B),
    )
    for role_id, owner_id, company in (
        (ROLE_A, OWNER_A, "Alpha Co"),
        (ROLE_B, OWNER_B, "Beta Co"),
    ):
        cursor.execute(
            """
            insert into public.roles
                (id, owner_id, source, listing_url,
                 company, title, location, description)
            values
                (%s, %s, 'greenhouse', 'https://example.test/jobs/1',
                 %s, 'Engineer', 'Remote', 'Example description')
            """,
            (role_id, owner_id, company),
        )
        cursor.execute(
            """
            insert into public.contacts (owner_id, name, company)
            values (%s, %s, %s)
            """,
            (owner_id, f"Contact at {company}", company),
        )
        cursor.execute(
            """
            insert into public.skill_gap_findings
                (owner_id, role_id, skill, finding_type, evidence)
            values (%s, %s, 'Python', 'missing', 'Required by role')
            """,
            (owner_id, role_id),
        )


def _run_as_partner(
    connection: Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> list[tuple]:
    """Execute one statement as ``partner_readonly``, then roll it back.

    Mirrors ``test_schema.py``'s ``_execute_as`` helper: a savepoint scopes
    both the simulated role and any writes to this single call.
    """
    connection.execute("savepoint partner_op")
    error: Exception | None = None
    rows: list[tuple] = []
    try:
        # `set local role` is reverted by the savepoint rollback below,
        # exactly as in test_schema.py.
        connection.execute(f"set local role {PARTNER_ROLE}")
        result = connection.execute(statement, parameters)
        if result.description is not None:
            rows = result.fetchall()
    except Exception as exc:  # noqa: BLE001 -- re-raised below after cleanup
        error = exc
    finally:
        connection.execute("rollback to savepoint partner_op")
        connection.execute("release savepoint partner_op")

    if error is not None:
        raise error
    return rows


@pytest.mark.parametrize("table", PARTNER_READABLE_TABLES)
def test_partner_pattern__partner_selects_shared_table__sees_every_owners_rows(
    partner_database: Connection, table: str
) -> None:
    # One row per owner was seeded. `using (true)` means the partner sees
    # BOTH -- proving this is table-scoped access, not a per-owner subset.
    rows = _run_as_partner(partner_database, f"select owner_id from public.{table}")

    assert sorted(row[0] for row in rows) == sorted([OWNER_A, OWNER_B])


@pytest.mark.parametrize("table", PARTNER_FORBIDDEN_TABLES)
def test_partner_pattern__partner_selects_other_table__is_denied(
    partner_database: Connection, table: str
) -> None:
    # partner_readonly has no SELECT grant on any table outside the two
    # shared ones -> blocked before RLS is even evaluated.
    with pytest.raises(InsufficientPrivilege):
        _run_as_partner(partner_database, f"select * from public.{table}")


@pytest.mark.parametrize(
    ("statement", "parameters"),
    (
        (
            "insert into public.contacts (owner_id, name, company) "
            "values (%s, 'Mallory', 'Evil Co')",
            (OWNER_A,),
        ),
        ("update public.contacts set name = 'overwritten'", ()),
        ("delete from public.contacts", ()),
    ),
    ids=["insert", "update", "delete"],
)
def test_partner_pattern__partner_writes_shared_table__is_denied(
    partner_database: Connection,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    # The grant is SELECT only -- every write verb is rejected even on the
    # two tables the partner can read.
    with pytest.raises(InsufficientPrivilege):
        _run_as_partner(partner_database, statement, parameters)


def test_partner_pattern__applied__creates_only_two_scoped_select_policies(
    partner_database: Connection,
) -> None:
    # The pattern must add exactly two policies, both SELECT, both on the
    # shared tables, both targeting only the partner role.
    rows = partner_database.execute(
        """
        select tablename, cmd, roles
        from pg_catalog.pg_policies
        where schemaname = 'public' and policyname like '%partner%'
        order by tablename
        """
    ).fetchall()

    assert [(table, cmd) for table, cmd, _ in rows] == [
        ("contacts", "SELECT"),
        ("skill_gap_findings", "SELECT"),
    ]
    assert all(roles == [PARTNER_ROLE] for _, _, roles in rows)


def test_partner_pattern__not_applied__partner_role_does_not_exist(
    baseline_database: Connection,
) -> None:
    # On a database carrying only S0-02's schema + RLS, the partner role
    # was never created.
    row = baseline_database.execute(
        "select 1 from pg_catalog.pg_roles where rolname = %s",
        (PARTNER_ROLE,),
    ).fetchone()

    assert row is None


def test_partner_pattern__not_applied__no_cross_instance_policy_exists(
    baseline_database: Connection,
) -> None:
    # Every S0-02 policy targets `authenticated` and only `authenticated`;
    # there is no partner/cross-instance grantee anywhere in the applied
    # schema until this pattern's file is explicitly run.
    rows = baseline_database.execute(
        """
        select policyname, roles
        from pg_catalog.pg_policies
        where schemaname = 'public'
        """
    ).fetchall()

    assert rows  # sanity: S0-02's own policies are present
    assert all(roles == ["authenticated"] for _, roles in rows)
    assert all("partner" not in policyname for policyname, _ in rows)
