"""Current-session cost summaries and a themed Streamlit cost dashboard."""

import csv
import io
from decimal import Decimal
from html import escape

import streamlit as st

from .frontend import icon, panel_heading
from .notifications import notify_once
from .usage import PRICE_DATE, PRICE_SOURCE
from .analysis_timing import audio_checkpoint


CATEGORIES = {
    "audio_input": ("Input audio", "Audio billed at the regular input rate."),
    "audio_cached": ("Cached input audio", "Reused audio billed at the cached rate."),
    "text_input": ("Text instructions", "Instructions, report schema and other text context."),
    "text_cached": ("Cached text", "Reused text billed at the cached rate."),
    "report": ("Report generation", "Generated report output, excluding separately reported reasoning."),
    "reasoning": ("Reasoning", "Reasoning output, when reported separately by the model."),
    "image_input": ("Input images", "Image input billed at the regular rate."),
    "image_cached": ("Cached images", "Reused image input."),
    "audio_output": ("Audio output", "Generated audio output."),
}
CORE_CATEGORIES = tuple(CATEGORIES)[:6]


def money(value):
    """Keep small audio-analysis costs visible without rounding them to zero."""
    if value is None:
        return "Unavailable"
    amount = Decimal(value)
    if amount == 0:
        return "$0.000000"
    if amount < Decimal("0.000001"):
        return "< $0.000001"
    return f"${amount:,.6f}"


def duration(seconds):
    minutes, seconds = divmod(max(0, int(seconds or 0)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def summarize_records(records):
    """Aggregate disjoint charges; unavailable reasoning stays in report output."""
    categories = {key: {"tokens": 0, "cost": Decimal(0), "rates": set(), "included": False}
                  for key in CATEGORIES}
    total = savings = Decimal(0)
    priced = unpriced = 0
    for record in records:
        if record.get("cost_usd") is None:
            unpriced += 1
            continue
        priced += 1
        total += Decimal(record["cost_usd"])
        breakdown = record["breakdown"]
        for key, item in breakdown.items():
            category = categories[key]
            category["rates"].add(item["rate"])
            if item["tokens"] is None:
                category["included"] = True
            else:
                category["tokens"] += item["tokens"]
                category["cost"] += Decimal(item["cost_usd"])
        rates = record.get("rates_usd_per_million") or {}
        for modality in ("audio", "text", "image"):
            cached = breakdown.get(f"{modality}_cached")
            regular_rate = rates.get(f"{modality}_input", rates.get("input"))
            if cached and regular_rate is not None:
                savings += Decimal(cached["tokens"]) * (Decimal(regular_rate) - Decimal(cached["rate"])) / Decimal(1_000_000)
    return {"categories": categories, "total": total, "savings": savings,
            "priced": priced, "unpriced": unpriced, "responses": len(records)}


def analysis_groups(records, uploaded=False, analysis_interval=None):
    """Validation retries belong to the same interval; an upload has one total."""
    groups = {}
    for record in records:
        metadata = dict(record.get("analysis") or {})
        interval = metadata.get("analysis_interval") or analysis_interval
        if not uploaded and interval and isinstance(metadata.get("timestamp"), (int, float)):
            # Older sessions stored request counters. Display them on the same timeline.
            checkpoint = audio_checkpoint(metadata["timestamp"], interval, is_final=metadata.get("is_final", False))
            metadata.update(checkpoint=checkpoint, analysis_interval=interval,
                            interval_start=max(0, (checkpoint - 1) * interval), interval_end=checkpoint * interval)
        key = ("upload" if uploaded else
               "final_report" if metadata.get("analysis_interval") and metadata.get("is_final") else
               f"interval_{metadata['checkpoint']}" if metadata.get("analysis_interval") else
               record.get("purpose") or record["response_id"])
        if key not in groups:
            groups[key] = {"key": key, "analysis": metadata, "records": []}
        groups[key]["records"].append(record)
    ordered = sorted(groups.values(), key=lambda group: (group["analysis"].get("checkpoint", float("inf")),
                                                        group["analysis"].get("is_final", False)))
    for index, group in enumerate(ordered, 1):
        metadata = group["analysis"]
        group["label"] = ("Uploaded audio" if uploaded else "Final report" if metadata.get("is_final")
                          else "Before first interval" if metadata.get("analysis_interval") and metadata.get("checkpoint") == 0
                          else f"Interval {metadata['checkpoint']}" if metadata.get("checkpoint") is not None
                          else f"Other response {index}")
        group["summary"] = summarize_records(group["records"])
    return ordered


def costs_csv(groups):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["Analysis", "Audio position", "Interval start", "Interval end", "API responses", "Unpriced responses", "Report repair calls",
                     *[f"{CATEGORIES[key][0]} (USD)" for key in CATEGORIES],
                     "Observed total (USD)", "Reasoning included in report generation"])
    for group in groups:
        summary = group["summary"]
        categories = summary["categories"]
        metadata = group["analysis"]
        timed = not metadata.get("is_final") and metadata.get("analysis_interval")
        writer.writerow([group["label"], duration(metadata.get("timestamp")),
                         duration(metadata["interval_start"]) if timed else "",
                         duration(metadata["interval_end"]) if timed else "", summary["responses"],
                         summary["unpriced"],
                         sum(record.get("analysis", {}).get("operation") == "report_repair" for record in group["records"]),
                         *["" if (categories[key]["included"] and not categories[key]["tokens"]) or not summary["priced"]
                           else format(categories[key]["cost"], "f") for key in CATEGORIES],
                         format(summary["total"], "f") if summary["priced"] else "",
                         categories["reasoning"]["included"]])
    return buffer.getvalue()


def _table(headers, rows, caption, total_row=""):
    headings = "".join(f'<th scope="col">{escape(label)}</th>' for label in headers)
    return (f'<div class="cost-table-scroll" tabindex="0" aria-label="{escape(caption)}">'
            f'<table class="cost-table"><caption class="cost-sr-only">{escape(caption)}</caption>'
            f'<thead><tr>{headings}</tr></thead><tbody>{"".join(rows)}</tbody>'
            f'{"<tfoot>" + total_row + "</tfoot>" if total_row else ""}</table></div>')


def _breakdown_html(summary):
    rows = []
    for key, category in summary["categories"].items():
        if key not in CORE_CATEGORIES and not category["tokens"]:
            continue
        label, description = CATEGORIES[key]
        if key == "report" and summary["categories"]["reasoning"]["included"]:
            description = "Includes reasoning for responses without a separate reasoning count."
        tokens = f'{category["tokens"]:,}'
        cost = money(category["cost"])
        if category["included"]:
            tokens = f'{tokens} reported' if category["tokens"] else "Not reported"
            cost = f'{cost} reported' if category["tokens"] else "Included in report generation"
        if not summary["priced"]:
            tokens, cost = "Unavailable", "Unavailable"
        rates = category["rates"]
        rate = f'${Decimal(next(iter(rates))):g}' if len(rates) == 1 else "Mixed rates" if rates else "—"
        rows.append(f'<tr><th scope="row"><strong>{escape(label)}</strong><small>{escape(description)}</small></th>'
                    f'<td>{escape(tokens)}</td><td>{escape(rate)}</td><td>{escape(cost)}</td></tr>')
    total = money(summary["total"]) if summary["priced"] else "Unavailable"
    label = "Observed total" if summary["unpriced"] else "Total estimated cost"
    footer = f'<tr><th scope="row" colspan="3">{label}</th><td>{escape(total)}</td></tr>'
    return _table(["Cost category", "Tokens", "USD / 1M tokens", "Estimated cost"], rows, "Cost breakdown", footer)


def _history_html(groups, uploaded):
    keys = [key for key in CATEGORIES if key in CORE_CATEGORIES or
            any(group["summary"]["categories"][key]["tokens"] for group in groups)]
    rows = []
    cumulative = Decimal(0)
    has_unknown = False
    for group in groups:
        summary = group["summary"]
        cumulative += summary["total"]
        has_unknown |= bool(summary["unpriced"])
        attempts = summary["responses"]
        repairs = sum(record.get("analysis", {}).get("operation") == "report_repair" for record in group["records"])
        extra = (f"{attempts} responses · {repairs} report repair" if repairs else
                 f"{attempts} responses · includes retries" if attempts > 1 else "1 response")
        metadata = group["analysis"]
        window = (f"{duration(metadata['interval_start'])} – {duration(metadata['interval_end'])} · "
                  if metadata.get("analysis_interval") and metadata.get("checkpoint") and not metadata.get("is_final") else "")
        position = duration(metadata["timestamp"]) if "timestamp" in metadata else "—"
        cells = []
        for key in keys:
            category = summary["categories"][key]
            value = money(category["cost"]) if summary["priced"] else "Unavailable"
            if category["included"]:
                value = f"{value} + included" if category["tokens"] else "Included in output"
            cells.append(f'<td>{escape(value)}</td>')
        total = money(summary["total"]) if summary["priced"] else "Unavailable"
        if summary["unpriced"] and summary["priced"]:
            total += " + unknown"
        cells.append(f'<td class="cost-table-total">{escape(total)}</td>')
        if not uploaded:
            cells.append(f'<td>{escape(money(cumulative))}{" + unknown" if has_unknown else ""}</td>')
        final_class = ' class="cost-final-row"' if metadata.get("is_final") else ""
        rows.append(f'<tr{final_class}><th scope="row"><strong>{escape(group["label"])}</strong>'
                    f'<small>{escape(window + extra)}</small></th><td>{position}</td>{"".join(cells)}</tr>')
    headers = ["Analysis", "Audio position", *[CATEGORIES[key][0] for key in keys], "Total"]
    if not uploaded:
        headers.append("Running total")
    return _table(headers, rows, "Analysis costs" if uploaded else "Interval costs")


def render_cost_estimation(snapshot=None, *, active=False, uploaded=False, source_name=None, analysis_interval=60, final_report=False):
    records = snapshot["records"] if snapshot else []
    summary = summarize_records(records)
    groups = analysis_groups(records, uploaded, analysis_interval)
    complete = snapshot is None or snapshot["complete"]
    status = "Session ended" if records else "No analysis yet"
    if final_report and complete:
        status = "Final estimate"
    if active:
        status = "Analyzing audio" if uploaded else "Live · updates automatically"
    if not complete:
        status = "Partial estimate · live" if active else "Partial estimate"
    source = source_name or ("Live recording" if snapshot else "Current audio analysis")
    context = "One analysis for the entire recording" if uploaded else f"Live analysis every {duration(analysis_interval)}"
    badge_class = "partial" if not complete else "live" if active else ""
    st.markdown(f'<div class="cost-session"><div><strong>{escape(source)}</strong><span>{context}</span></div>'
                f'<span class="cost-status {badge_class}" role="status">{status}</span></div>', unsafe_allow_html=True)
    total_label = "Estimated cost so far" if active else "Final estimated cost" if final_report and complete else "Estimated cost"
    if not complete:
        total_label = "Observed estimated cost"
    total = money(summary["total"]) if summary["priced"] or not records else "Unavailable"
    cards = [(total_label, total, "USD · current session", "primary"),
             ("Audio analyses", str(len(groups)), f'{len(records)} API responses · repairs and retries included', ""),
             ("Saved through caching", money(summary["savings"]) if summary["priced"] or not records else "Unavailable", "Compared with regular input rates", "")]
    st.markdown('<div class="cost-metrics">' + "".join(
        f'<div class="cost-metric {kind}"><span>{escape(label)}</span><strong>{escape(value)}</strong>'
        f'<small>{escape(note)}</small></div>' for label, value, note, kind in cards) + '</div>', unsafe_allow_html=True)
    if not complete:
        notify_once("Some response usage or pricing is unavailable. Amounts shown cover known costs only; the full session cost may be higher.",
                    key="incomplete_cost", scope=snapshot["session_id"])
    if not records:
        title = "Waiting for the first analysis" if active else "Your audio costs will appear here"
        body = ("Costs update when each analysis finishes. The final report is added when recording ends."
                if active and not uploaded else "The cost will appear after the complete recording has been analyzed."
                if active else "Record a conversation or analyze an uploaded file to see its token usage and estimated cost.")
        st.markdown(f'<div class="cost-empty"><span class="icon-tile">{icon("chart")}</span>'
                    f'<h3>{title}</h3><p>{body}</p></div>', unsafe_allow_html=True)
        return
    with st.container(key="cost_history"):
        heading, download = st.columns([3, 1], vertical_alignment="center")
        with heading:
            panel_heading("Recording cost" if uploaded else "Interval costs", "list", strong=True)
        with download:
            st.download_button("Download costs", costs_csv(groups), file_name="CatchUpAI-costs.csv", mime="text/csv",
                               icon=":material/download:", key="download_costs", on_click="ignore", use_container_width=True)
        st.markdown(_history_html(groups, uploaded), unsafe_allow_html=True)
        if not uploaded:
            st.caption("Intervals follow fixed recording-time boundaries. Retries and reconnection attempts stay in the same time interval; audio position shows when analysis was requested. Charges include earlier conversation context. The final report is added to the running total.")
    with st.container(key="cost_breakdown"):
        title, selector = st.columns([2, 1.3], vertical_alignment="center")
        with title:
            panel_heading("Cost breakdown", "chart", strong=True)
        selected_summary = summary
        with selector:
            if not uploaded:
                options = ["session", *[group["key"] for group in groups]]
                labels = {"session": "Entire session", **{group["key"]: f'{group["label"]} · {duration(group["analysis"].get("timestamp"))}' for group in groups}}
                selection_key = f'cost_scope_{snapshot["session_id"]}'
                if selection_key in st.session_state and st.session_state[selection_key] not in options:
                    del st.session_state[selection_key]
                selected = st.selectbox("Breakdown for", options, format_func=labels.get, key=selection_key,
                                        label_visibility="collapsed")
                if selected != "session":
                    selected_summary = next(group["summary"] for group in groups if group["key"] == selected)
        st.markdown(_breakdown_html(selected_summary), unsafe_allow_html=True)
        st.caption("Text instructions include all input text context. Cached tokens are charged separately from regular input. Reasoning is counted once in the total.")
    dates = sorted({record.get("pricing_date") or "unavailable" for record in records})
    models = sorted({record["model"] for record in records})
    st.markdown(f'<div class="cost-footnote">Estimates in USD · {escape(", ".join(models))} · '
                f'Rates dated {escape(", ".join(dates))} · <a href="{PRICE_SOURCE}" target="_blank" rel="noopener noreferrer">Pricing reference</a>'
                '<br>Based on reported token usage. Taxes, discounts, credits and hosting are excluded.</div>', unsafe_allow_html=True)
