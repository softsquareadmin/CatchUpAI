import copy
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import queue

from streamlit.testing.v1 import AppTest

from catchupai.models import default_config, normalize_section_ids, record_check_section
from catchupai.record_check import comparison_items, is_record_check, record_check_html
from catchupai.report_export import build_report_document
from catchupai.usage import UsageTracker
from catchupai.report_schema import build_report_schema, validate_report
from catchupai.prompts import build_analysis_instructions


CRM = {"object": "Case", "fields": {"ReportedBy": "School staff", "ChildName": "Jill Carter", "ChildAge": "9",
       "AllegedPerpetrator": "Dave Mercer", "Relationship": "Mother's boyfriend, lives in home",
       "AllegedIncident": "Hit child in eye with a thrown cup", "InjuryLocation": "Left eye / black eye",
       "ReportDate": "This week"}, "description": "The CRM contains an allegation requiring review."}
LINES = [
    'ReportedBy (CRM: School staff): Not confirmed. No speaker identified the reporter.',
    'ChildName (CRM: Jill Carter): Mentioned by the worker. This is not independently confirmed.',
    'ChildAge (CRM: 9): Contradicted. Jill stated, "I’m 7 and I’m in second grade."',
    'AllegedPerpetrator (CRM: Dave Mercer): Partially confirmed. His identity was stated; responsibility remains disputed.',
    'Relationship (CRM: Mother\'s boyfriend, lives in home): Confirmed by the mother, who said, "Dave is my boyfriend and lives here."',
    'AllegedIncident (CRM: Hit child in eye with a thrown cup): Not confirmed and contradicted by the mother, who gave a different account.',
    'InjuryLocation (CRM: Left eye / black eye): The worker referenced the report. No speaker specified which eye.',
    'ReportDate (CRM: This week): Not confirmed. A bruise was noticed Tuesday; no report date was stated.',
    'CRM description (regular hitting with objects): Not confirmed in the conversation.',
]


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.cards = []
        self.text = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details" and "record-check-card" in attrs.get("class", ""):
            self.cards.append(attrs)
    def handle_data(self, text):
        self.text.append(text)


class RecordCheckTests(unittest.TestCase):
    def test_example_prioritizes_differences_without_reinterpreting_unknown_findings(self):
        items = comparison_items(LINES, CRM)
        self.assertEqual([item["status"] for item in items],
                         ["unconfirmed", "unconfirmed", "differs", "unconfirmed", "consistent", "differs", "unconfirmed", "unconfirmed", "unconfirmed"])
        markup = Markup(record_check_html(LINES, CRM))
        self.assertEqual(len(markup.cards), 9)
        self.assertTrue(all("open" in card for card in markup.cards[:2]))
        self.assertTrue(all("open" not in card for card in markup.cards[2:]))
        text = " ".join(markup.text)
        self.assertLess(text.index("Child Age"), text.index("Reported By"))
        self.assertIn('"I’m 7 and I’m in second grade."', text)
        self.assertIn("responsibility remains disputed", text)
        self.assertIn("record-check-quote", record_check_html(LINES, CRM))

    def test_missing_mentions_and_quoted_status_words_do_not_create_differences(self):
        lines = ['ChildAge: Not mentioned.', 'Relationship: Not confirmed.',
                 'ReportDate: The speaker said "contradicted", without discussing the report date.',
                 'ChildName: Confirmed? Further review needed.', 'ChildAge: Consistent except for one unresolved detail.']
        self.assertEqual([item["status"] for item in comparison_items(lines, CRM)],
                         ["unconfirmed"] * 5)

    def test_original_crm_values_are_used_and_disagreeing_analysis_reference_is_preserved(self):
        original = copy.deepcopy(CRM)
        item = comparison_items(['ChildAge (CRM: 8): Differs. The child stated 7.'], CRM)[0]
        self.assertEqual(item["source"], "9")
        self.assertEqual(item["reference"], "8")
        html = record_check_html(['ChildAge (CRM: 8): Differs. The child stated 7.'], CRM)
        self.assertIn("Analysis referenced CRM value: 8", html)
        self.assertEqual(CRM, original)

    def test_no_status_or_unrecognized_format_keeps_the_full_text(self):
        text = 'The record was discussed, but no field comparisons were returned.'
        item = comparison_items(text, CRM)[0]
        self.assertEqual(item["status"], "unconfirmed")
        self.assertEqual(item["body"], text)
        self.assertIn(text, record_check_html(text, CRM))

    def test_markdown_and_nested_crm_parentheses_are_presented_cleanly(self):
        item = comparison_items('- **Relationship (CRM: Parent (lives at home)): Consistent. A statement was heard.**')[0]
        self.assertEqual(item["field"], "Relationship")
        self.assertEqual(item["source"], "Parent (lives at home)")
        self.assertEqual(item["body"], "Consistent. A statement was heard.")
        bold = comparison_items(['**ChildAge** (CRM: 9): **Contradicted**. A different age was stated.'], CRM)[0]
        self.assertEqual(bold["status"], "differs")
        self.assertEqual(bold["field"], "ChildAge")
        self.assertIn("<strong>Contradicted</strong>", record_check_html(['ChildAge: **Contradicted**. A different age was stated.'], CRM))

    def test_ambiguous_source_field_names_are_not_assigned_arbitrarily(self):
        item = comparison_items(['ChildAge (CRM: 8): Not confirmed.'], {"fields": {"ChildAge": "9", "child_age": "10"}})[0]
        self.assertEqual(item["source"], "8")
        self.assertEqual(item["source_label"], "CRM value in analysis")

    def test_all_crm_and_model_html_is_escaped_including_quoted_evidence(self):
        html = record_check_html(['Field<script> (CRM: <img src=x>): Differs. "<script>alert(1)</script>"'],
                                 {"fields": {"Field<script>": "<img src=x onerror=alert(1)>"}})
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_download_uses_the_same_cards_with_all_evidence_expanded(self):
        config = default_config()
        config["conversation"]["crm_content"] = CRM
        config["report"]["sections"] = normalize_section_ids([record_check_section()])
        result = {"report": {"record_check": LINES}, "timestamp": 60, "checkpoint": 1,
                  "generated_at": datetime.now(timezone.utc).isoformat()}
        document = build_report_document(config, result)
        cards = Markup(document).cards
        self.assertEqual(len(cards), 9)
        self.assertTrue(all("open" in card for card in cards))
        self.assertIn("record-check-stats", document)

    def test_studio_uses_the_record_check_cards_with_session_crm_data(self):
        config = default_config()
        config["conversation"]["crm_content"] = CRM
        config["report"]["sections"] = normalize_section_ids([record_check_section()])
        app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=45).run()
        app.session_state.active_config = config
        app.session_state.live = SimpleNamespace(running=False, events=queue.Queue(), usage_tracker=UsageTracker())
        app.session_state.latest = {"report": {"record_check": LINES}, "timestamp": 60, "checkpoint": 1, "is_final": True,
                                    "generated_at": datetime.now(timezone.utc).isoformat()}
        app.run()
        app.radio(key="report_navigation").set_value("section:record_check").run()
        self.assertEqual(len(app.exception), 0)
        html = "\n".join(item.value for item in app.markdown)
        self.assertIn("Differences to review", html)
        self.assertIn("CRM record", html)
        self.assertIn("record-check-card differs", html)
        app.session_state.latest["report"]["record_check"] = [
            {"field": "ChildAge", "status": "differs", "explanation": "A different age was stated.", "evidence": "I’m 7."},
            {"field": "ReportedBy", "status": "unconfirmed", "explanation": "The reporter was not identified.", "evidence": ""},
        ]
        app.run()
        self.assertEqual(len(app.exception), 0)
        html = "\n".join(item.value for item in app.markdown)
        self.assertIn('<blockquote class="record-check-evidence">', html)
        self.assertIn('I’m 7.', ' '.join(Markup(html).text))
        self.assertIn('No supporting conversation quote available.', html)
        self.assertIn("Unconfirmed details", html)

    def test_non_record_report_sections_keep_the_standard_renderer(self):
        self.assertFalse(is_record_check({"id": "facts", "label": "Facts", "type": "list"}))
        self.assertFalse(is_record_check({"id": "record_check", "label": "Record Check", "type": "boolean"}))

    def test_structured_status_is_used_directly_and_evidence_is_preserved(self):
        value = [{"field": "ChildAge", "status": "differs", "explanation": "A different age was stated.",
                  "evidence": "I’m 7 and I’m in second grade."},
                 {"field": "Relationship", "status": "unconfirmed", "explanation": "Partially discussed; not established.", "evidence": ""},
                 {"field": "ReportedBy", "status": "consistent", "explanation": "The reporter was identified.", "evidence": "School staff reported it."}]
        items = comparison_items(value, CRM)
        self.assertEqual([item["status"] for item in items], ["differs", "unconfirmed", "consistent"])
        self.assertEqual(items[0]["source"], "9")
        markup = Markup(record_check_html(value, CRM))
        self.assertEqual(len(markup.cards), 3)
        self.assertIn(value[0]["evidence"], " ".join(markup.text))
        self.assertIn("record-check-evidence", record_check_html(value, CRM))
        self.assertTrue(all(any(status in card["class"] for status in ("consistent", "differs", "unconfirmed")) for card in markup.cards))

    def test_new_schema_accepts_only_explicit_three_status_comparisons(self):
        sections = normalize_section_ids([record_check_section()])
        schema = build_report_schema(sections)
        self.assertEqual(set(schema["properties"]["record_check"]["items"]["properties"]["status"]["enum"]),
                         {"consistent", "differs", "unconfirmed"})
        for status in ("consistent", "differs", "unconfirmed"):
            report = {"record_check": [{"field": "ChildAge", "status": status, "explanation": "An explanation.",
                                        "evidence": "Relevant statement." if status != "unconfirmed" else ""}]}
            self.assertEqual(validate_report(report, schema), report)
        for status in ("partial", "not_mentioned", "review", "unknown"):
            with self.assertRaises(ValueError):
                validate_report({"record_check": [{"field": "ChildAge", "status": status, "explanation": "Explanation.", "evidence": ""}]}, schema)
        with self.assertRaises(ValueError):
            validate_report({"record_check": [LINES[0]]}, schema)

    def test_comparisons_with_findings_require_evidence_and_all_entries_require_explanations(self):
        schema = build_report_schema(normalize_section_ids([record_check_section()]))
        item = {"field": "ChildAge", "status": "differs", "explanation": "Different age stated.", "evidence": "  "}
        with self.assertRaisesRegex(ValueError, "require conversation evidence"):
            validate_report({"record_check": [item]}, schema)
        item.update(status="unconfirmed", explanation=" ")
        with self.assertRaisesRegex(ValueError, "field name and explanation"):
            validate_report({"record_check": [item]}, schema)

    def test_existing_template_instructions_are_updated_for_analysis_without_mutation(self):
        config = default_config()
        config["conversation"]["crm_content"] = CRM
        config["report"]["sections"] = normalize_section_ids([record_check_section()])
        config["report"]["sections"][0]["instruction"] = "Return one line with Consistent, Differs or Not mentioned."
        original = copy.deepcopy(config)
        prompt = build_analysis_instructions(config)
        self.assertIn("status must be consistent, differs or unconfirmed", prompt)
        self.assertNotIn("Return one line with Consistent", prompt)
        self.assertEqual(config, original)

    def test_record_check_final_only_and_other_list_schemas_remain_correct(self):
        sections = normalize_section_ids([record_check_section(), {"label": "Facts", "type": "list", "instruction": "List facts.", "required": True}])
        sections[0]["final_only"] = True
        intermediate = build_report_schema(sections, is_final=False)
        self.assertNotIn("record_check", intermediate["properties"])
        self.assertEqual(intermediate["properties"]["facts"]["items"], {"type": "string"})
        self.assertEqual(build_report_schema(sections)["properties"]["record_check"]["items"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
