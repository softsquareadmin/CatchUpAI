"""Dismissible notices that are emitted once, even during live fragment refreshes."""

import streamlit as st


def notify_once(message, *, key, scope="workspace"):
    """Remember emission so closing a toast survives reruns and tab changes.

    Scope notices to the recording or upload so new sessions can warn again.
    Streamlit owns dismissal and keyboard accessibility of the native toast.
    """
    shown = st.session_state.setdefault("_shown_notifications", set())
    identity = (scope, key)
    if identity in shown:
        return False
    st.toast(message, icon=":material/warning:", duration="infinite")
    shown.add(identity)
    return True
