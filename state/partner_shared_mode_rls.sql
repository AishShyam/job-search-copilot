-- Partner-scoped Row Level Security pattern for Opt-In Shared Mode (FR16).
--
-- STATUS: DEFINED BUT NOT APPLIED BY DEFAULT.
--
-- This file is deliberately NOT part of the always-on schema. Nothing in
-- state/schema.sql or state/rls_policies.sql references it, and a normal
-- instance never runs it. It exists to prove -- at the schema level, in
-- Phase 0 -- that the partner-scoped access pattern FR16 needs is
-- expressible in Postgres RLS. The FR16 feature build (the `mode: shared`
-- config flag, the partner-instance setup flow, read-only key
-- provisioning) is scheduled after Phase 0 and is out of scope for the
-- task that added this file (S0-03).
--
-- WHAT IT DOES
-- It creates one dedicated Postgres role, `partner_readonly`, and grants
-- it SELECT -- and only SELECT -- on exactly two tables: `contacts` and
-- `skill_gap_findings`. Every other table is left with no grant for this
-- role, so a partner connection is blocked from them before RLS is even
-- consulted (and S0-02's `force row level security` means there is no
-- policy that would let it in regardless).
--
-- The SELECT policies use `using (true)`. This is intentionally NOT
-- per-row ownership filtering: a partner that has been granted access
-- sees ALL rows in those two tables, not a per-owner subset. "Scoping"
-- here means table scoping (two tables, read-only) enforced by the role,
-- not row scoping. S0-02's row-level `auth.uid() = owner_id` policies are
-- untouched by this file and remain the only owner-filtering mechanism.
--
-- HOW `mode: shared` WOULD ACTIVATE IT LATER
-- When the FR16 feature is built, an instance with `mode: shared` in
-- config/profile.yaml and a non-empty `partners` list would:
--   1. apply this file to its own Supabase project once, creating the
--      `partner_readonly` role and its two policies;
--   2. issue each partner a connection/key that assumes `partner_readonly`
--      -- the same mechanism Supabase already uses to map the anon key to
--      the `anon` role and a user JWT to `authenticated`;
--   3. the partner instance stores that key in its own `.env` under the
--      name given by its profile's `partners[].readonly_key_env`.
-- Until all of that exists this file changes nothing, because it is not
-- applied.
--
-- Re-running this file is safe: the role is created only if absent, and
-- every policy is dropped-if-exists before being recreated.

do $$
begin
    if not exists (
        select 1 from pg_catalog.pg_roles where rolname = 'partner_readonly'
    ) then
        create role partner_readonly nologin;
    end if;

    -- In a real Supabase project PostgREST connects as `authenticator` and
    -- SET ROLEs to the request's role; mirror how anon/authenticated are
    -- wired to it. Absent in a bare Postgres, so guard on existence.
    if exists (
        select 1 from pg_catalog.pg_roles where rolname = 'authenticator'
    ) then
        execute 'grant partner_readonly to authenticator';
    end if;
end
$$;

-- So whoever applies this migration can also delegate the role onward.
grant partner_readonly to current_user;

grant usage on schema public to partner_readonly;

-- Read-only, and only these two tables.
grant select on table public.contacts to partner_readonly;
grant select on table public.skill_gap_findings to partner_readonly;

-- Defensive and explicit: no other table is reachable by this role, even
-- if a future default-privilege change would otherwise grant something.
revoke all on table public.roles from partner_readonly;
revoke all on table public.application_status_history from partner_readonly;
revoke all on table public.digests from partner_readonly;
revoke all on table public.digest_roles from partner_readonly;

drop policy if exists contacts_partner_readonly on public.contacts;
drop policy if exists skill_gap_findings_partner_readonly on public.skill_gap_findings;

-- `using (true)`: near-blanket within the table. A granted partner sees
-- every row in these two tables -- not a filtered subset.
create policy contacts_partner_readonly on public.contacts
    for select to partner_readonly
    using (true);
create policy skill_gap_findings_partner_readonly on public.skill_gap_findings
    for select to partner_readonly
    using (true);
