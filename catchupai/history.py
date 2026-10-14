"""Saved-session reads and report selection, independent of the recording workspace."""
import streamlit as st

from .database import SupabaseStore, canonical_id
from .models import validate_config
from .usage import saved_cost_snapshot


@st.cache_data(ttl=20, max_entries=4, show_spinner=False)
def recent_sessions():
    return SupabaseStore().request("GET", "/rest/v1/audio_sessions", params={
        "select": "id,title,status,created_at", "deleted_at": "is.null",
        "order": "created_at.desc,id.desc", "limit": 20})


def ordered_reports(rows):
    return sorted(rows, key=lambda row: (row["report_type"] == "final",
                  float(row["audio_position_seconds"]), row["checkpoint"]), reverse=True)


def report_result(row):
    if row is None:
        return None
    return {**(row.get("result_metadata") or {}), "checkpoint": row["checkpoint"],
            "generated_at": row["generated_at"], "timestamp": float(row["audio_position_seconds"]),
            "is_final": row["report_type"] == "final", "report": row["report"]}


def load_session(session_id):
    session_id = canonical_id(session_id)
    store = SupabaseStore()
    sessions = store.rows("audio_sessions", id="eq." + session_id, deleted_at="is.null")
    if not sessions:
        raise ValueError("This session is no longer available.")
    saved = sessions[0]
    validate_config(saved["configuration_snapshot"])
    configuration_name = "Custom configuration"
    if saved.get("configuration_id"):
        configurations = store.request("GET", "/rest/v1/configurations", params={
            "select": "name", "id": "eq." + saved["configuration_id"], "limit": 1})
        if configurations:
            configuration_name = configurations[0]["name"]
    reports = ordered_reports(store.rows("session_reports", audio_session_id="eq." + session_id))
    costs = store.rows("session_costs", audio_session_id="eq." + session_id, order="logged_at.asc,id.asc")
    return {"session": saved, "configuration_name": configuration_name, "reports": reports,
            "costs": saved_cost_snapshot(session_id, costs), "has_costs": bool(costs)}
