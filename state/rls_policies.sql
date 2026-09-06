-- Single-instance Row Level Security policies for Job Search Copilot

-- === Block 1: Turn RLS on, and make it apply to EVERYONE, including the ===
-- === table owner (e.g. the service_role connection your own backend uses) ===
--
-- "enable row level security" turns RLS on for the table at all.
-- "force row level security" closes a Postgres default gap: normally RLS
-- policies are silently SKIPPED for the table's owner / a superuser-like
-- role. Since Supabase's SUPABASE_SERVICE_ROLE_KEY typically connects with
-- that kind of elevated privilege, "force" makes sure even YOUR OWN backend
-- code is still subject to these policies -- this is the defense-in-depth
-- NFR9 asks for: a bug in your own code (e.g. a missing WHERE clause)
-- still can't leak or overwrite rows across owners.

-- RLS is required here because Supabase authenticates every caller as one
-- of a few generic roles (anon/authenticated) with no built-in way to
-- grant access to one specific user or partner at the database level —
-- RLS policies are the only mechanism available to restrict access to
-- "this row" or "this partner" rather than "this whole table."
alter table public.roles enable row level security;
alter table public.roles force row level security;
alter table public.application_status_history enable row level security;
alter table public.application_status_history force row level security;
alter table public.contacts enable row level security;
alter table public.contacts force row level security;
alter table public.skill_gap_findings enable row level security;
alter table public.skill_gap_findings force row level security;
alter table public.digests enable row level security;
alter table public.digests force row level security;
alter table public.digest_roles enable row level security;
alter table public.digest_roles force row level security;

-- === Block 2: Grants -- WHICH OPERATIONS are even allowed at all ===
--
-- Grants and RLS policies are two separate locks. Grants decide which
-- operations (select/insert/update/delete) a role may attempt in the
-- first place; RLS policies (Block 3) then decide WHICH ROWS that
-- operation applies to. If an operation was never granted, it's blocked
-- before any RLS policy is even evaluated for it.
--
-- "anon" = unauthenticated caller (e.g. only holds the public anon key).
-- "authenticated" = a real logged-in user.
--
-- Revoke everything first, then grant back explicitly -- this makes the
-- set of allowed operations obvious to read, rather than relying on
-- whatever Postgres/Supabase defaults happen to be.
revoke all on table public.roles from anon, authenticated;
revoke all on table public.application_status_history from anon, authenticated;
revoke all on table public.contacts from anon, authenticated;
revoke all on table public.skill_gap_findings from anon, authenticated;
revoke all on table public.digests from anon, authenticated;
revoke all on table public.digest_roles from anon, authenticated;

-- Note: only "authenticated" gets any grant at all -- "anon" is granted
-- nothing on any of these tables, so an unauthenticated request can't
-- touch them regardless of what any RLS policy below might say.
grant select, insert, update, delete on table public.roles to authenticated;
grant select, insert, update, delete on table public.application_status_history
    to authenticated;
grant select, insert, update, delete on table public.contacts to authenticated;
grant select, insert, update, delete on table public.skill_gap_findings
    to authenticated;
grant select, insert, update, delete on table public.digests to authenticated;
grant select, insert, update, delete on table public.digest_roles to authenticated;

-- === Block 3: RLS policies -- WHICH ROWS each operation can see/affect ===
--
-- auth.uid() = Supabase's built-in function returning the current
-- logged-in user's ID (or null if no real session).
--
-- Each policy below checks TWO things together:
--   1. auth.uid() is not null  -> someone is actually, genuinely logged in
--      (not just nominally holding the "authenticated" role).
--   2. auth.uid() = owner_id   -> AND this specific row belongs to them.
-- Both must be true for a row to be visible/affected. This is a real
-- per-row filter: it compares each row's own owner_id against who's asking.
--
-- Four policies per table (select/insert/update/delete) repeat the same
-- "only your own rows" rule for every operation. insert uses "with check"
-- instead of "using" because there's no existing row to filter -- only the
-- new row being written, which must satisfy the same ownership rule.

create policy roles_select_own on public.roles
    for select to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy roles_insert_own on public.roles
    for insert to authenticated
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy roles_update_own on public.roles
    for update to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id)
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy roles_delete_own on public.roles
    for delete to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);

create policy application_status_history_select_own
    on public.application_status_history
    for select to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy application_status_history_insert_own
    on public.application_status_history
    for insert to authenticated
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy application_status_history_update_own
    on public.application_status_history
    for update to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id)
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy application_status_history_delete_own
    on public.application_status_history
    for delete to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);

create policy contacts_select_own on public.contacts
    for select to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy contacts_insert_own on public.contacts
    for insert to authenticated
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy contacts_update_own on public.contacts
    for update to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id)
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy contacts_delete_own on public.contacts
    for delete to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);

create policy skill_gap_findings_select_own on public.skill_gap_findings
    for select to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy skill_gap_findings_insert_own on public.skill_gap_findings
    for insert to authenticated
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy skill_gap_findings_update_own on public.skill_gap_findings
    for update to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id)
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy skill_gap_findings_delete_own on public.skill_gap_findings
    for delete to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);

create policy digests_select_own on public.digests
    for select to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy digests_insert_own on public.digests
    for insert to authenticated
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy digests_update_own on public.digests
    for update to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id)
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy digests_delete_own on public.digests
    for delete to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);

create policy digest_roles_select_own on public.digest_roles
    for select to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy digest_roles_insert_own on public.digest_roles
    for insert to authenticated
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy digest_roles_update_own on public.digest_roles
    for update to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id)
    with check ((select auth.uid()) is not null and (select auth.uid()) = owner_id);
create policy digest_roles_delete_own on public.digest_roles
    for delete to authenticated
    using ((select auth.uid()) is not null and (select auth.uid()) = owner_id);