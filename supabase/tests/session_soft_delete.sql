-- Run after all three migrations. Fixtures are rolled back.
begin;

do $$
declare
    session_id uuid;
    before_row jsonb;
    after_row jsonb;
    deletion_time timestamptz;
begin
    insert into public.audio_sessions(configuration_snapshot,title,source_type,status)
        values('{}','Deletion fixture','upload','processing') returning id into session_id;
    insert into public.session_reports(audio_session_id,report_type,checkpoint,audio_position_seconds,report,generated_at)
        values(session_id,'final',1,60,'{}',now());
    update public.audio_sessions set audio_storage_status='stored',audio_path='fixture/audio.wav',status='completed'
        where id=session_id;
    select to_jsonb(s) into before_row from public.audio_sessions s where id=session_id;
    update public.audio_sessions set deleted_at=clock_timestamp() where id=session_id and deleted_at is null;
    select to_jsonb(s), s.deleted_at into after_row, deletion_time
        from public.audio_sessions s where id=session_id;
    if deletion_time is null or (before_row - 'deleted_at') is distinct from (after_row - 'deleted_at') then
        raise exception 'Session deletion must change only deleted_at';
    end if;
    update public.audio_sessions set deleted_at=clock_timestamp() where id=session_id and deleted_at is null;
    if exists(select 1 from public.audio_sessions where id=session_id and deleted_at is distinct from deletion_time) then
        raise exception 'Repeated deletion changed its timestamp';
    end if;
    if not exists(select 1 from public.session_reports where audio_session_id=session_id) then
        raise exception 'Session deletion removed reports';
    end if;
    begin
        update public.audio_sessions set deleted_at=null where id=session_id;
        raise exception 'Clearing deletion timestamp was permitted' using errcode='XX000';
    exception when raise_exception then null; end;
end $$;

rollback;
