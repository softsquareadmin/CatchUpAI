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
    covered = sum(coverage_status(results[t["id"]]) == "covered" for t in topics if t["id"] in results)
    rows = []
    labels = {"covered": "Covered", "partial": "Partial", "unanswered": "Unanswered", "pending": "Awaiting analysis"}
    symbols = {"covered": "✓", "partial": "◐", "unanswered": "!", "pending": "…"}
    ordered = sorted(topics, key=lambda t: {"unanswered": 0, "partial": 1, "pending": 2, "covered": 3}[coverage_status(results[t["id"]]) if t["id"] in results else "pending"])
    for topic in ordered:
        result = results.get(topic["id"])
        status = coverage_status(result) if result else "pending"
        body = "<p>Waiting for topic analysis.</p>"
        if result:
            parts = []
            if status == "unanswered":
                parts.append(f'<p>{"Asked, no usable answer" if result["asked"] else "Not asked; no answer provided"}</p>')
            if result["summary"]:
                parts.append(f'<p><strong>Answer summary</strong><br>{escape(result["summary"])}</p>')
            if status != "covered":
                parts.append(f'<p><strong>Suggested question</strong><br>{escape(result["suggested_question"])}</p>')
            parts.append('<ul class="coverage-criteria">')
            for i, criterion in enumerate(topic["criteria"]):
                detail = result["criteria"][str(i)]
                parts.append(f'<li><strong>{escape(criterion)}</strong> — {labels[detail["status"]]}')
                if detail["evidence"]:
                    parts.append(f'<p><strong>Evidence:</strong> {escape(detail["evidence"])}</p>')
                if detail["status"] != "covered":
                    parts.append(f'<p><strong>Missing:</strong> {escape(detail["missing"])}</p>')
                parts.append('</li>')
            parts.append('</ul>')
            body = ''.join(parts)
        rows.append(f'<details class="coverage-topic {status}"><summary><span class="coverage-symbol">{symbols[status]}</span><strong>{escape(topic["label"])}</strong><span class="coverage-label">{labels[status]}</span></summary><div class="coverage-body">{body}</div></details>')
    return f'<section class="topic-coverage"><h2>Topic coverage</h2><p class="coverage-count">{covered} of {len(topics)} topics covered · Based on the conversation analyzed so far</p>{"".join(rows)}</section>'


COVERAGE_CSS = """
.topic-coverage { margin-bottom:20px; }
.topic-coverage h2 { font-size:18px; margin:0 0 6px; }
.coverage-count { color:#65748a; font-size:13px; margin:0 0 12px; }
.coverage-topic { border:1px solid #dce5f0; border-left:4px solid #8592a3; border-radius:7px; margin:8px 0; overflow:hidden; }
.coverage-topic.covered { border-left-color:#15803d; }
.coverage-topic.partial { border-left-color:#b77900; }
.coverage-topic.unanswered { border-left-color:#c53030; }
.coverage-topic summary { cursor:pointer; padding:13px 15px; background:#f8fafc; font-size:14px; }
.coverage-symbol { margin:0 10px; }
.covered .coverage-symbol,.covered .coverage-label { color:#15803d; }
.partial .coverage-symbol,.partial .coverage-label { color:#946200; }
.unanswered .coverage-symbol,.unanswered .coverage-label { color:#c53030; }
.coverage-label { float:right; margin-left:12px; font-size:12px; font-weight:600; }
.coverage-body { padding:14px 18px; font-size:14px; line-height:1.7; }
.coverage-body p { margin:0 0 12px; white-space:pre-wrap; overflow-wrap:anywhere; }
.coverage-criteria { padding-left:20px; margin-bottom:0; }
.coverage-criteria li { margin:10px 0; }
@media print { .coverage-topic > .coverage-body { display:block !important; } }
"""
