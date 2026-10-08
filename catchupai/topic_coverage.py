"""Shared topic coverage calculation and portable expandable report markup."""
from html import escape


def coverage_status(result):
    statuses = [c["status"] for c in result["criteria"].values()]
    if statuses and all(s == "covered" for s in statuses):
        return "covered"
    if any(s != "unanswered" for s in statuses):
        return "partial"
    return "unanswered"


def topic_coverage_html(topics, report):
    if not topics:
        return ""
    results = report.get("topic_coverage", {})
    labels = {"covered": "Covered", "partial": "Partial", "unanswered": "Unanswered", "pending": "Awaiting analysis"}
    symbols = {"covered": "&#10003;", "partial": "&#9684;", "unanswered": "!", "pending": "&hellip;"}
    scores = {"covered": 100, "partial": 50, "unanswered": 0, "pending": 0}
    statuses = {t["id"]: coverage_status(results[t["id"]]) if results.get(t["id"]) else "pending" for t in topics}
    counts = {status: sum(s == status for s in statuses.values()) for status in labels}
    score = round(sum(scores[statuses[t["id"]]] for t in topics) / len(topics))
    stats = ''.join(
        f'<div class="coverage-stat {status}"><span class="coverage-stat-number">{counts[status]}</span><div><strong>{labels[status]}</strong><span>{description}</span></div></div>'
        for status, description in [("covered", "Topics with<br>comprehensive answers"), ("partial", "Topics with<br>incomplete answers"), ("unanswered", "Topics not yet<br>addressed")]
    )
    stats += f'<div class="coverage-stat total"><span class="coverage-stat-number">{len(topics)}</span><div><strong>Topics total</strong><span>Based on the conversation<br>analyzed so far</span></div></div>'
    pending_note = f'<p class="coverage-pending">{counts["pending"]} topic(s) awaiting analysis</p>' if counts["pending"] else ''
    rows = []
    ordered = sorted(topics, key=lambda t: {"unanswered": 0, "partial": 1, "pending": 2, "covered": 3}[statuses[t["id"]]])
    for topic in ordered:
        result = results.get(topic["id"])
        status = statuses[topic["id"]]
        body = '<p class="coverage-empty">Waiting for topic analysis.</p>'
        if result:
            cards = []
            if result["summary"]:
                cards.append(f'<div class="coverage-info"><span class="coverage-info-icon" aria-hidden="true">&#9776;</span><div><strong>Answer summary</strong><p>{escape(result["summary"])}</p></div></div>')
            if status != "covered" and result["suggested_question"]:
                cards.append(f'<div class="coverage-info"><span class="coverage-info-icon" aria-hidden="true">&#8943;</span><div><strong>Suggested follow-up question</strong><p>{escape(result["suggested_question"])}</p></div></div>')
            parts = [f'<div class="coverage-info-grid">{"".join(cards)}</div>'] if cards else []
            if status == "unanswered":
                parts.append(f'<p class="coverage-empty">{"Asked, no usable answer" if result["asked"] else "Not asked; no answer provided"}</p>')
            parts.append('<h3 class="coverage-criteria-heading">Key points and coverage</h3><ul class="coverage-criteria">')
            for i, criterion in enumerate(topic["criteria"]):
                detail = result["criteria"][str(i)]
                criterion_status = detail["status"]
                parts.append(f'<li class="coverage-criterion {criterion_status}"><span class="coverage-symbol" aria-hidden="true">{symbols[criterion_status]}</span><div><div class="coverage-criterion-title"><strong>{escape(criterion)}</strong><span class="coverage-label">{labels[criterion_status]}</span></div>')
                if detail["evidence"]:
                    parts.append(f'<p><strong>Evidence:</strong> {escape(detail["evidence"])}</p>')
                if criterion_status != "covered" and detail["missing"]:
                    parts.append(f'<p><strong>Missing:</strong> {escape(detail["missing"])}</p>')
                parts.append('</div></li>')
            parts.append('</ul>')
            body = ''.join(parts)
        percent = scores[status]
        meter = f'<span class="coverage-topic-score">{percent}%</span><span class="coverage-mini-track" aria-hidden="true"><span style="width:{percent}%"></span></span>' if status != 'pending' else ''
        rows.append(f'<details class="coverage-topic {status}"><summary><span class="coverage-chevron" aria-hidden="true"></span><span class="coverage-symbol" aria-hidden="true">{symbols[status]}</span><strong class="coverage-topic-title">{escape(topic["label"])}</strong>{meter}<span class="coverage-label">{labels[status]}</span></summary><div class="coverage-body">{body}</div></details>')
    return f'<section class="topic-coverage"><h2>Topic Coverage</h2><p class="coverage-count">Coverage score based on the conversation analyzed so far.</p><div class="coverage-dashboard"><div class="coverage-ring" style="--coverage:{score}%"><div><strong>{score}%</strong><span>Overall<br>coverage</span></div></div><div class="coverage-dashboard-main"><div class="coverage-stats">{stats}</div><div class="coverage-progress"><div class="coverage-track" role="progressbar" aria-label="Overall topic coverage" aria-valuenow="{score}" aria-valuemin="0" aria-valuemax="100"><span style="width:{score}%"></span></div><span><strong>{score}%</strong> coverage</span></div>{pending_note}</div></div><div class="coverage-topics-scroll" role="region" aria-label="Individual topic coverage" tabindex="0">{"".join(rows)}</div></section>'


COVERAGE_CSS = """
.topic-coverage { margin-bottom:20px; padding:16px; background:#fff; border:1px solid #e5edf7; border-radius:9px; color:#15285a; font-family:Arial,Helvetica,sans-serif; }
.topic-coverage * { box-sizing:border-box; }
.topic-coverage h2 { font-size:22px; line-height:1.2; letter-spacing:-.6px; padding:0; margin:0 0 4px; color:#122657; }
.topic-coverage .coverage-count { color:#6077a5; font-size:12px; margin:0; }
.coverage-dashboard { display:flex; align-items:center; gap:38px; margin:12px 12px 18px; }
.coverage-ring { width:112px; height:112px; flex-shrink:0; padding:12px; border-radius:50%; background:conic-gradient(#087bff var(--coverage),#eaf0f8 0); transform:rotate(-90deg); }
.coverage-ring > div { height:100%; border-radius:50%; background:white; display:flex; flex-direction:column; justify-content:center; align-items:center; transform:rotate(90deg); }
.coverage-ring strong { font-size:26px; line-height:1.2; }
.coverage-ring span { font-size:11px; color:#6077a5; text-align:center; margin-top:4px; }
.coverage-dashboard-main { flex:1; min-width:0; border-left:1px solid #dde6f4; padding-left:30px; }
.coverage-stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin-bottom:16px; }
.coverage-stat { display:flex; gap:16px; padding:0 24px; border-right:1px solid #e1e8f4; }
.coverage-stat:first-child { padding-left:4px; }
.coverage-stat:last-child { border:0; }
.coverage-stat-number { font-size:32px; font-weight:700; line-height:1; }
.coverage-stat > div { padding-top:8px; }
.coverage-stat strong { display:block; font-size:12px; margin-bottom:5px; }
.coverage-stat > div > span { display:block; font-size:11px; line-height:1.3; color:#6077a5; }
.coverage-stat.covered .coverage-stat-number { color:#08a353; }
.coverage-stat.partial .coverage-stat-number { color:#e18a08; }
.coverage-stat.unanswered .coverage-stat-number { color:#ef1434; }
.coverage-stat.total .coverage-stat-number { color:#3c517d; }
.coverage-progress { display:flex; align-items:center; gap:16px; font-size:12px; color:#6077a5; }
.coverage-progress > span { white-space:nowrap; }
.coverage-progress strong { color:#15285a; margin-right:4px; }
.coverage-track,.coverage-mini-track { background:#dfe7f4; border-radius:20px; overflow:hidden; }
.coverage-track { flex:1; height:13px; }
.coverage-track > span,.coverage-mini-track > span { display:block; height:100%; background:linear-gradient(90deg,#359eff,#087bff); border-radius:inherit; }
.topic-coverage .coverage-pending { font-size:11px; color:#6077a5; margin:8px 0 0; }
.coverage-topics-scroll { max-height:480px; overflow-y:auto; scrollbar-gutter:stable; padding-right:4px; }
.coverage-topics-scroll:focus-visible { outline:2px solid #087bff; outline-offset:2px; border-radius:4px; }
.coverage-topic { --status-color:#8190ab; --status-bg:#eef2f7; border:1px solid #e5edf7; border-radius:8px; margin:8px 0 0; overflow:hidden; }
.coverage-topic.covered,.coverage-criterion.covered { --status-color:#08a353; --status-bg:#dff9eb; }
.coverage-topic.partial,.coverage-criterion.partial { --status-color:#e18a08; --status-bg:#fff0d7; }
.coverage-topic.unanswered,.coverage-criterion.unanswered { --status-color:#ef1434; --status-bg:#ffe5eb; }
.coverage-topic summary { display:flex; align-items:center; gap:14px; cursor:pointer; padding:9px 12px; background:#fff; font-size:13px; list-style:none; }
.coverage-topic summary::-webkit-details-marker { display:none; }
.coverage-topic summary:focus-visible { outline:2px solid #087bff; outline-offset:-3px; }
.coverage-chevron { width:0; height:0; border-top:4px solid transparent; border-bottom:4px solid transparent; border-left:5px solid #15285a; margin:0 5px; flex-shrink:0; }
.coverage-topic[open] > summary .coverage-chevron { transform:rotate(90deg); }
.coverage-symbol { display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; width:24px; height:24px; border-radius:50%; background:var(--status-color); color:white; font-size:17px; font-weight:700; }
.coverage-topic-title { flex:1; min-width:0; overflow-wrap:anywhere; }
.coverage-label { display:inline-block; color:var(--status-color); background:var(--status-bg); padding:4px 13px; border-radius:20px; font-size:11px; font-weight:400; white-space:nowrap; }
.coverage-topic summary > .coverage-label { min-width:88px; text-align:center; }
.coverage-topic-score { font-size:11px; padding:4px 12px; background:#f0f5ff; border-radius:20px; }
.coverage-mini-track { width:76px; height:9px; flex-shrink:0; margin-left:-9px; }
.coverage-mini-track > span { background:var(--status-color); }
.coverage-body { padding:12px 14px 18px; font-size:12px; line-height:1.45; }
.coverage-info-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin-bottom:10px; }
.coverage-info { display:flex; gap:12px; padding:12px; border-radius:9px; background:linear-gradient(110deg,#edf5ff,#f2f7ff); }
.coverage-info-icon { display:flex; align-items:center; justify-content:center; width:38px; height:38px; flex-shrink:0; color:#087bff; background:#dfedff; border-radius:50%; font-size:26px; }
.coverage-info strong { color:#122657; }
.topic-coverage .coverage-body p { margin:3px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.45; color:#425d90; }
.topic-coverage .coverage-criteria-heading { font-size:13px; padding:8px 0 0; border-top:1px solid #e4ecf8; margin:0 0 8px; color:#15285a; }
.coverage-criteria { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px 24px; list-style:none; padding:0; margin:0; }
.coverage-criterion { display:flex; gap:12px; align-items:flex-start; }
.coverage-criterion .coverage-symbol { width:22px; height:22px; font-size:14px; margin-top:2px; }
.coverage-criterion > div { min-width:0; }
.coverage-criterion-title { display:flex; flex-wrap:wrap; align-items:center; gap:10px; font-size:11px; }
.coverage-criterion .coverage-label { padding:3px 12px; }
.topic-coverage .coverage-criterion p { font-size:11px; }
@media (max-width:900px) { .coverage-dashboard { gap:20px; margin-left:0; margin-right:0; } .coverage-dashboard-main { padding-left:18px; } .coverage-stat { padding:0 12px; gap:8px; } .coverage-stat-number { font-size:26px; } }
@media (max-width:640px) { .topic-coverage { padding:12px; } .coverage-dashboard { flex-direction:column; align-items:stretch; } .coverage-ring { align-self:center; } .coverage-dashboard-main { border:0; padding:0; } .coverage-stats { grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; } .coverage-stat { padding:0 8px; } .coverage-stat:nth-child(2) { border:0; } .coverage-info-grid,.coverage-criteria { grid-template-columns:1fr; } .coverage-topic summary { gap:8px; flex-wrap:wrap; } .coverage-topic-title { flex-basis:calc(100% - 70px); } .coverage-topic-score { margin-left:40px; } .coverage-mini-track { margin-left:0; } .coverage-topic summary > .coverage-label { margin-left:auto; } }
@media print { .coverage-topics-scroll { max-height:none; overflow:visible; padding-right:0; } .coverage-topic > .coverage-body { display:block !important; } .coverage-topic { break-inside:avoid; } .topic-coverage { print-color-adjust:exact; -webkit-print-color-adjust:exact; } }
"""
