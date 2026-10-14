-- Run after the migration. Fixtures are rolled back.
begin;
do $$
declare config_id uuid; session_id uuid; before_row jsonb; after_row jsonb;
begin
    insert into public.configurations(name,normalized_name,config,updated_at)
        values('SQL fixture','sql fixture','{}','2000-01-01') returning id into config_id;
    insert into public.audio_sessions(configuration_id,configuration_snapshot,title,source_type,status)
        values(config_id,'{}','SQL session','live','recording') returning id into session_id;
    select to_jsonb(c) into before_row from public.configurations c where id=config_id;
    update public.configurations set deleted_at=clock_timestamp() where id=config_id;
    select to_jsonb(c) into after_row from public.configurations c where id=config_id;
    if before_row is distinct from (after_row - 'deleted_at') || jsonb_build_object('deleted_at',null) then
        raise exception 'Deletion changed fields other than deleted_at';
    end if;
    if not exists(select 1 from public.audio_sessions where id=session_id and configuration_id=config_id) then
        raise exception 'Deletion lost the session link';
    end if;
    begin
        delete from public.configurations where id=config_id;
        raise exception 'Hard delete was permitted' using errcode='XX000';
    exception when raise_exception then null; end;
    begin
        update public.configurations set name='changed' where id=config_id;
        raise exception 'Deleted row edit permitted' using errcode='XX000';
    exception when raise_exception then null; end;
    begin
        insert into public.audio_sessions(configuration_id,configuration_snapshot,title,source_type,status)
            values(config_id,'{}','stale','live','recording');
        raise exception 'Stale config permitted' using errcode='XX000';
    exception when raise_exception then null; end;
    begin
        update public.audio_sessions set configuration_snapshot='{"changed":true}' where id=session_id;
        raise exception 'Snapshot change permitted' using errcode='XX000';
    exception when raise_exception then null; end;
    begin
        update public.audio_sessions set status='completed' where id=session_id;
        raise exception 'Incomplete session marked complete' using errcode='XX000';
    exception when raise_exception then null; end;
    insert into public.session_reports(audio_session_id,report_type,checkpoint,audio_position_seconds,report,generated_at)
        values(session_id,'intermediate',1,60,'{}',now()),(session_id,'final',1,65,'{}',now());
    begin
        insert into public.session_reports(audio_session_id,report_type,checkpoint,audio_position_seconds,report,generated_at)
            values(session_id,'final',2,65,'{}',now());
        raise exception 'Duplicate final permitted' using errcode='XX000';
    exception when unique_violation then null; end;
    update public.audio_sessions set audio_storage_status='stored',audio_path='fixture/audio.wav',status='completed'
        where id=session_id;
    if has_table_privilege('anon','public.configurations','SELECT') or
       has_table_privilege('authenticated','public.audio_sessions','INSERT') then
        raise exception 'Unexpected browser access';
    end if;
    if has_table_privilege('service_role','public.configurations','DELETE') or
       has_table_privilege('service_role','public.session_reports','UPDATE') then
        raise exception 'Unexpected privileged destructive access';
    end if;
end $$;
rollback;
