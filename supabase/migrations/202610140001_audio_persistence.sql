-- Run this file in the project's Supabase SQL Editor.
-- Private, shared application data accessed only by the Python server.
begin;

create table if not exists public.configurations (
    id uuid primary key default gen_random_uuid(),
    name text not null check (length(btrim(name)) > 0),
    normalized_name text not null unique,
    config jsonb not null check (jsonb_typeof(config) = 'object'),
    schema_version integer not null default 1,
    revision integer not null default 1 check (revision > 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);

create table if not exists public.audio_sessions (
    id uuid primary key default gen_random_uuid(),
    configuration_id uuid references public.configurations(id) on delete restrict,
    configuration_snapshot jsonb not null check (jsonb_typeof(configuration_snapshot) = 'object'),
    configuration_revision integer,
    title text not null,
    source_type text not null check (source_type in ('live', 'upload')),
    client_session_id text unique,
    status text not null check (status in ('recording', 'processing', 'completed',
        'completed_with_errors', 'failed', 'cancelled', 'interrupted')),
    audio_storage_status text not null default 'pending'
        check (audio_storage_status in ('pending', 'uploading', 'stored', 'failed')),
    audio_bucket text not null default 'session-audio',
    audio_path text,
    original_filename text,
    audio_mime_type text,
    audio_size_bytes bigint check (audio_size_bytes >= 0),
    duration_seconds numeric check (duration_seconds >= 0),
    started_at timestamptz not null default now(),
    ended_at timestamptz,
    error_summary text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (audio_storage_status <> 'stored' or audio_path is not null)
);

create table if not exists public.session_reports (
    id uuid primary key default gen_random_uuid(),
    audio_session_id uuid not null references public.audio_sessions(id) on delete cascade,
    report_type text not null check (report_type in ('intermediate', 'final')),
    checkpoint integer not null check (checkpoint >= 0),
    audio_position_seconds numeric not null check (audio_position_seconds >= 0),
    report jsonb not null check (jsonb_typeof(report) = 'object'),
    result_metadata jsonb not null default '{}'::jsonb,
    schema_version integer not null default 1,
    generated_at timestamptz not null,
    created_at timestamptz not null default now(),
    unique (audio_session_id, report_type, checkpoint)
);

create unique index if not exists session_reports_one_final
    on public.session_reports(audio_session_id) where report_type = 'final';
create index if not exists audio_sessions_configuration on public.audio_sessions(configuration_id);
create index if not exists audio_sessions_history on public.audio_sessions(created_at desc);
create index if not exists session_reports_history
    on public.session_reports(audio_session_id, audio_position_seconds);

create or replace function public.guard_configuration_changes()
returns trigger language plpgsql set search_path = '' as $$
begin
    if TG_OP = 'DELETE' then
        raise exception 'Configurations must be soft-deleted using deleted_at.';
    end if;
    if OLD.deleted_at is not null then
        if NEW is distinct from OLD then
            raise exception 'Deleted configurations cannot be changed.';
        end if;
        return NEW;
    end if;
    if NEW.deleted_at is not null then
        if (to_jsonb(NEW) - 'deleted_at') is distinct from (to_jsonb(OLD) - 'deleted_at') then
            raise exception 'Soft deletion may update only deleted_at.';
        end if;
        return NEW;
    end if;
    NEW.updated_at := now();
    NEW.revision := OLD.revision + 1;
    return NEW;
end;
$$;
drop trigger if exists configurations_guard on public.configurations;
create trigger configurations_guard before update or delete on public.configurations
    for each row execute function public.guard_configuration_changes();

create or replace function public.guard_session_configuration()
returns trigger language plpgsql set search_path = '' as $$
begin
    if TG_OP = 'UPDATE' and (
        NEW.configuration_snapshot is distinct from OLD.configuration_snapshot or
        NEW.configuration_id is distinct from OLD.configuration_id or
        NEW.configuration_revision is distinct from OLD.configuration_revision
    ) then
        raise exception 'A session configuration snapshot is immutable.';
    end if;
    if TG_OP = 'INSERT' and NEW.configuration_id is not null then
        perform 1 from public.configurations where id = NEW.configuration_id and deleted_at is null for share;
        if not FOUND then
            raise exception 'Select an active configuration or start with an unsaved configuration.';
        end if;
    end if;
    if NEW.status = 'completed' then
        if NEW.audio_storage_status <> 'stored' or not exists (
            select 1 from public.session_reports where audio_session_id = NEW.id and report_type = 'final'
        ) then
            raise exception 'Completion requires stored audio and a final report.';
        end if;
    end if;
    NEW.updated_at := now();
    return NEW;
end;
$$;
drop trigger if exists audio_sessions_guard on public.audio_sessions;
create trigger audio_sessions_guard before insert or update on public.audio_sessions
    for each row execute function public.guard_session_configuration();

alter table public.configurations enable row level security;
alter table public.audio_sessions enable row level security;
alter table public.session_reports enable row level security;
revoke all on public.configurations, public.audio_sessions, public.session_reports from anon, authenticated;
grant select, insert, update on public.configurations to service_role;
revoke delete, truncate on public.configurations from service_role;
grant select, insert, update, delete on public.audio_sessions to service_role;
grant select, insert on public.session_reports to service_role;
revoke update, delete, truncate on public.session_reports from service_role;
revoke all on function public.guard_configuration_changes() from public, anon, authenticated;
revoke all on function public.guard_session_configuration() from public, anon, authenticated;

insert into storage.buckets (id, name, public)
values ('session-audio', 'session-audio', false)
on conflict (id) do update set public = false;

notify pgrst, 'reload schema';
commit;
