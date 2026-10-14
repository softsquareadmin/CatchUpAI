-- Run AFTER 202610140001_audio_persistence.sql. Existing sessions/data are retained.
begin;

create table if not exists public.session_costs (
    id uuid primary key default gen_random_uuid(),
    audio_session_id uuid not null references public.audio_sessions(id) on delete cascade,
    event_type text not null check (event_type in ('api_response', 'usage_gap')),
    api text,
    response_id text,
    model text,
    purpose text,
    response_status text,
    input_tokens bigint check (input_tokens >= 0),
    output_tokens bigint check (output_tokens >= 0),
    total_tokens bigint check (total_tokens >= 0),
    usage jsonb,
    analysis jsonb not null default '{}'::jsonb,
    breakdown jsonb,
    cost_usd numeric(20,10) check (cost_usd >= 0),
    rates_usd_per_million jsonb,
    pricing_date date,
    pricing_source text,
    reason text,
    logged_at timestamptz not null,
    created_at timestamptz not null default now(),
    unique (api, response_id),
    check (
        (event_type = 'api_response' and api is not null and response_id is not null and model is not null)
        or (event_type = 'usage_gap' and api is null and response_id is null and cost_usd is null and reason is not null)
    )
);

create index if not exists session_costs_session_history
    on public.session_costs(audio_session_id, logged_at);

alter table public.session_costs enable row level security;
revoke all on public.session_costs from anon, authenticated;
revoke all on public.session_costs from service_role;
grant select, insert on public.session_costs to service_role;

comment on table public.session_costs is
    'Immutable provider response costs and missing-usage events. USD values are estimates; NULL means unknown.';

notify pgrst, 'reload schema';
commit;

-- Example: retrieve costs for a session (replace the UUID).
-- select * from public.session_costs
-- where audio_session_id = 'YOUR-SESSION-UUID'
-- order by logged_at;

-- Totals include sessions with no captured usage and flag partial estimates.
-- select s.id as audio_session_id,
--        count(c.id) filter (where c.event_type = 'api_response') as api_responses,
--        coalesce(sum(c.input_tokens), 0) as known_input_tokens,
--        coalesce(sum(c.output_tokens), 0) as known_output_tokens,
--        coalesce(sum(c.cost_usd), 0) as observed_cost_usd,
--        count(c.id) filter (where c.event_type = 'api_response' and c.cost_usd is null) as unpriced_responses,
--        count(c.id) filter (where c.event_type = 'usage_gap') as usage_gaps,
--        (count(c.id) filter (where c.event_type = 'api_response') > 0
--         and count(c.id) filter (where c.event_type = 'usage_gap'
--              or (c.event_type = 'api_response' and c.cost_usd is null)) = 0) as cost_complete
-- from public.audio_sessions s
-- left join public.session_costs c on c.audio_session_id = s.id
-- group by s.id;
