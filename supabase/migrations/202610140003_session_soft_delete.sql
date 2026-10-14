-- Run after the audio persistence and session costs migrations.
begin;

alter table public.audio_sessions
    add column if not exists deleted_at timestamptz;

create index if not exists audio_sessions_active_history
    on public.audio_sessions(created_at desc, id desc) where deleted_at is null;

-- Deletion changes only its timestamp; normal persistence updates keep working.
create or replace function public.guard_session_configuration()
returns trigger language plpgsql set search_path = '' as $$
begin
    if TG_OP = 'UPDATE' then
        if OLD.deleted_at is not null and NEW.deleted_at is distinct from OLD.deleted_at then
            raise exception 'A session deletion timestamp is immutable.';
        end if;
        if OLD.deleted_at is null and NEW.deleted_at is not null then
            if (to_jsonb(NEW) - 'deleted_at') is distinct from (to_jsonb(OLD) - 'deleted_at') then
                raise exception 'Soft deletion may update only deleted_at.';
            end if;
            return NEW;
        end if;
        if NEW.configuration_snapshot is distinct from OLD.configuration_snapshot or
           NEW.configuration_id is distinct from OLD.configuration_id or
           NEW.configuration_revision is distinct from OLD.configuration_revision then
            raise exception 'A session configuration snapshot is immutable.';
        end if;
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

commit;
