-- Core Supabase state schema for Job Search Copilot (S0-02).
-- Apply this file before state/rls_policies.sql.

-- === roles ===
-- One row per job listing evaluated, per owner. This is the state
-- backbone (FR12) that most other tables reference back into via
-- (id, owner_id) composite foreign keys.
create table public.roles (
    id uuid primary key default gen_random_uuid(),
    -- Every table below carries owner_id the same way: FK to the Supabase
    -- auth user, cascade delete (deleting a user cleans up all their rows).
    -- This is also the column every RLS policy checks against auth.uid().
    owner_id uuid not null references auth.users (id) on delete cascade,
    source text not null check (btrim(source) <> ''),  -- which collector found this (e.g. "greenhouse")
    source_job_id text,  -- ATS-issued job ID, when the source provides one; nullable
    listing_url text not null check (btrim(listing_url) <> ''),
    company text not null check (btrim(company) <> ''),
    title text not null check (btrim(title) <> ''),
    location text not null check (btrim(location) <> ''),
    description text not null check (btrim(description) <> ''),
    posted_at timestamptz,  -- when the LISTING itself was posted (may be unknown -> nullable)
    first_seen_at timestamptz not null default now(),  -- when OUR system first observed it (for recency, not source)
    created_at timestamptz not null default now(),
    -- Composite unique -- lets other tables' foreign keys reference
    -- (id, owner_id) together, so ownership is carried through every join.
    unique (id, owner_id)
);

-- Partial unique index: enforces FR4's dedup rule (ATS job ID match = duplicate)
-- but ONLY when source_job_id is actually present -- rows without one aren't
-- constrained by this index at all.
create unique index roles_owner_source_job_id_idx
    on public.roles (owner_id, source, source_job_id)
    where source_job_id is not null;
-- Speeds up "show newest listings first" style queries.
create index roles_owner_first_seen_idx
    on public.roles (owner_id, first_seen_at desc);
-- Supports FR4's fallback dedup key (company, title, location) when no
-- ATS job ID is available; lower() makes it case-insensitive.
create index roles_owner_company_title_location_idx
    on public.roles (owner_id, lower(company), lower(title), lower(location));

-- === application_status_history ===
-- Append-only log of status changes for a role (applied, rejected,
-- interviewing, etc.) -- the audit trail behind FR12/NFR4.
create table public.application_status_history (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users (id) on delete cascade,
    role_id uuid not null,
    status text not null check (btrim(status) <> ''),
    context text,  -- optional free-text note about this status change
    occurred_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    -- Composite FK (not just role_id -> roles.id): this is what guarantees
    -- a history row's owner_id always matches its parent role's owner_id --
    -- you can't accidentally attach a history row to someone else's role.
    foreign key (role_id, owner_id)
        references public.roles (id, owner_id) on delete cascade
);

create index application_status_history_owner_role_occurred_idx
    on public.application_status_history (owner_id, role_id, occurred_at desc);

-- === contacts ===
-- People the user knows at target companies (referral contacts).
create table public.contacts (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users (id) on delete cascade,
    name text not null check (btrim(name) <> ''),
    company text not null check (btrim(company) <> ''),
    email text,  -- optional, may not be known
    -- last_touch_at/last_touch_context: NOT just passive audit logging --
    -- FR10 (Follow-Up Nudges) needs last_touch_at to compute "has enough
    -- time passed since I last reached out to nudge again?" against the
    -- outreach_days/application_with_contact_days thresholds in
    -- profile.yaml. last_touch_context gives the LLM drafting the next
    -- follow-up (draft_outreach) context on what was already said, so it
    -- doesn't repeat itself. Functional dependency, not just NFR4 audit.
    last_touch_at timestamptz,
    last_touch_context text,
    created_at timestamptz not null default now(),
    unique (id, owner_id)
);

create index contacts_owner_company_idx
    on public.contacts (owner_id, lower(company));

-- === skill_gap_findings ===
-- One row per detected skill gap for a role -- feeds FR11 (Skill-Gap
-- Synthesis), which looks for recurring patterns across these findings.
create table public.skill_gap_findings (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users (id) on delete cascade,
    role_id uuid not null,
    skill text not null check (btrim(skill) <> ''),
    finding_type text not null check (finding_type in ('missing', 'weak')),
    -- The reasoning behind the finding (NFR4 auditability) -- e.g. why the
    -- LLM concluded this skill is missing/weak, likely produced alongside
    -- the verdict by the analyze_skill_gap() agent tool.
    evidence text not null check (btrim(evidence) <> ''),
    created_at timestamptz not null default now(),
    foreign key (role_id, owner_id)
        references public.roles (id, owner_id) on delete cascade
);

create index skill_gap_findings_owner_skill_idx
    on public.skill_gap_findings (owner_id, lower(skill));
create index skill_gap_findings_owner_role_idx
    on public.skill_gap_findings (owner_id, role_id);

-- === digests ===
-- One row per user per day -- the daily digest email/dashboard batch (FR7).
create table public.digests (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users (id) on delete cascade,
    digest_date date not null,
    reviewed_at timestamptz,  -- set once every linked role is resolved (see trigger below)
    catch_up_sent_at timestamptz,  -- set if the end-of-day catch-up email was sent (FR7)
    created_at timestamptz not null default now(),
    unique (id, owner_id),
    -- Guarantees exactly one digest row per user per calendar day.
    unique (owner_id, digest_date)
);

-- === digest_roles ===
-- Join table: which roles belong to which digest, and their per-role
-- resolution (applied/dismissed). No surrogate id -- the composite PK
-- below IS the row's identity.
create table public.digest_roles (
    digest_id uuid not null,
    role_id uuid not null,
    owner_id uuid not null references auth.users (id) on delete cascade,
    -- null = not yet resolved by the user; set = user acted on it (FR7).
    resolution_status text check (
        resolution_status is null or resolution_status in ('applied', 'dismissed')
    ),
    resolved_at timestamptz,
    created_at timestamptz not null default now(),
    -- A role either is or isn't part of a given digest, once.
    primary key (digest_id, role_id),
    foreign key (digest_id, owner_id)
        references public.digests (id, owner_id) on delete cascade,
    foreign key (role_id, owner_id)
        references public.roles (id, owner_id) on delete cascade,
    -- "Set together" rule -- same pattern as LlmConfig's fallback_provider/
    -- fallback_model pairing in config/schema.py, just enforced in SQL here:
    -- resolution_status and resolved_at must both be null or both be set.
    check (
        (resolution_status is null and resolved_at is null)
        or (resolution_status is not null and resolved_at is not null)
    )
);

create index digest_roles_owner_role_idx
    on public.digest_roles (owner_id, role_id);

-- === Trigger 1: enforce_digest_review_state ===
-- Fires before a digests row's reviewed_at is set. Blocks marking a
-- digest "reviewed" while any of its linked digest_roles is still
-- unresolved (resolved_at is null) -- this is FR7's rule that a digest
-- is only reviewed once every pursued role that day is resolved.
create function public.enforce_digest_review_state()
returns trigger
language plpgsql
set search_path = ''  -- hardening: prevents search-path-based function hijacking
as $$
begin
    if new.reviewed_at is not null and exists (
        select 1
        from public.digest_roles
        where digest_id = new.id
          and owner_id = new.owner_id
          and resolved_at is null
    ) then
        raise exception 'a digest cannot be reviewed until all its roles are resolved'
            using errcode = '23514';
    end if;

    return new;
end;
$$;

create trigger digests_require_resolved_roles
before insert or update of reviewed_at on public.digests
for each row execute function public.enforce_digest_review_state();

-- === Trigger 2: prevent_unresolved_role_in_reviewed_digest ===
-- The mirror-image guard, attached to digest_roles instead of digests:
-- blocks adding a new unresolved role, or un-resolving an existing one,
-- if its parent digest has ALREADY been marked reviewed. Without this,
-- Trigger 1 alone could still be bypassed by editing digest_roles after
-- the fact, leaving the two tables inconsistent with each other.
create function public.prevent_unresolved_role_in_reviewed_digest()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.resolved_at is null and exists (
        select 1
        from public.digests
        where id = new.digest_id
          and owner_id = new.owner_id
          and reviewed_at is not null
    ) then
        raise exception 'an unresolved role cannot belong to a reviewed digest'
            using errcode = '23514';
    end if;

    return new;
end;
$$;

create trigger digest_roles_preserve_review_state
before insert or update of resolution_status, resolved_at on public.digest_roles
for each row execute function public.prevent_unresolved_role_in_reviewed_digest();

-- Both trigger functions are only ever meant to run as triggers, not be
-- called directly by any role -- revoke direct execute access entirely.
revoke all on function public.enforce_digest_review_state() from public;
revoke all on function public.prevent_unresolved_role_in_reviewed_digest() from public;