begin;
do $$
declare session_id uuid; total numeric; gap_count bigint;
begin
    insert into public.audio_sessions(configuration_snapshot,title,source_type,status)
        values('{}','Cost SQL fixture','upload','processing') returning id into session_id;
    insert into public.session_costs(audio_session_id,event_type,api,response_id,model,cost_usd,logged_at)
        values(session_id,'api_response','realtime','fixture-' || session_id,'fixture',0.001,now());
    insert into public.session_costs(audio_session_id,event_type,api,response_id,model,cost_usd,logged_at)
        values(session_id,'api_response','responses','repair-' || session_id,'fixture',0.0002,now());
    insert into public.session_costs(audio_session_id,event_type,reason,logged_at)
        values(session_id,'usage_gap','missing response',now());
    insert into public.session_costs(audio_session_id,event_type,api,response_id,model,logged_at)
        values(session_id,'api_response','realtime','unknown-' || session_id,'unknown',now());
    insert into public.session_costs(audio_session_id,event_type,api,response_id,model,cost_usd,logged_at)
        values(session_id,'api_response','realtime','fixture-' || session_id,'fixture',0.001,now())
        on conflict(api,response_id) do nothing;
    select sum(cost_usd), count(*) filter(where event_type='usage_gap') into total,gap_count
        from public.session_costs where audio_session_id=session_id;
    if total <> 0.0012 or gap_count <> 1 then
        raise exception 'Duplicate charge or gap aggregation error';
    end if;
    if not exists(select 1 from public.session_costs where audio_session_id=session_id
                  and event_type='api_response' and cost_usd is null) then
        raise exception 'Unknown costs were lost';
    end if;
    if has_table_privilege('anon','public.session_costs','SELECT') or
       has_table_privilege('authenticated','public.session_costs','INSERT') or
       has_table_privilege('service_role','public.session_costs','UPDATE') then
        raise exception 'Unexpected cost table grants';
    end if;
    delete from public.audio_sessions where id=session_id;
    if exists(select 1 from public.session_costs where audio_session_id=session_id) then
        raise exception 'Cost records did not follow session deletion';
    end if;
end $$;
rollback;
