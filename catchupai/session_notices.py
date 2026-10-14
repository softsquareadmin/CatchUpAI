"""Translate analysis diagnostics into short notices without exposing API output."""


def session_notice(detail, *, kind=None, metadata=None):
    if isinstance(detail, dict):
        metadata = detail
        kind = detail.get("kind", "analysis")
        text = ""
    else:
        text = str(detail).lower()
    if any(value in text for value in ("openai_api_key", "invalid_api_key", "401", "authentication")):
        kind = "credentials"
    elif any(value in text for value in ("429", "quota", "rate limit", "insufficient_quota")):
        kind = "quota"
    elif kind is None:
        if "invalid report" in text or "validation" in text or "final analysis" in text:
            kind = "report"
        elif "connection" in text or "websocket" in text:
            kind = "connection"
        elif "timed out" in text or "timeout" in text:
            kind = "timeout"
        else:
            kind = "analysis"
    if kind not in ("report", "credentials", "quota", "connection", "timeout", "analysis", "persistence", "local_storage"):
        kind = "analysis"
    checkpoint = (metadata or {}).get("checkpoint")
    return {"kind": kind, "checkpoint": checkpoint if type(checkpoint) is int and checkpoint >= 0 else None,
            "is_final": (metadata or {}).get("is_final") is True}


def notice_text(notice, *, recording=False, has_report=False, uploaded=False):
    notice = session_notice(notice)
    kind = notice["kind"]
    if kind == "persistence":
        return "Saving is delayed. Your session is retained locally and will retry automatically."
    if kind == "local_storage":
        return "Could not retain session data locally. Check disk space and keep a copy of your recording."
    if kind == "credentials":
        return "Analysis couldn't connect. Check your OpenAI API key."
    if kind == "quota":
        return "Analysis is temporarily unavailable. Check your OpenAI usage limits and try again."
    if kind == "connection" and recording:
        return "Analysis connection interrupted. Your recording continues while CatchUpAI reconnects."
    if kind == "report":
        message = "Couldn't finish the final report." if notice["is_final"] else "Couldn't update this report."
    elif kind == "timeout":
        message = "The report took too long to complete."
    else:
        message = "Analysis couldn't be completed."
    if recording:
        message += " Recording continues."
    elif uploaded:
        message += " Try analyzing the uploaded audio again."
    else:
        message += " Try analyzing the recording again."
    if has_report:
        message += " The last valid report is still available."
    return message
