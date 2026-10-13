import copy
import json
import unittest
from unittest.mock import Mock, patch

from catchupai.models import default_config
from catchupai.prompts import build_analysis_instructions
from catchupai.realtime_client import RealtimeConversationAnalyzer
from catchupai.report_schema import TOOL_NAME, build_report_schema, normalize_report_structure, validate_report
from catchupai.usage import UsageTracker


WEAKNESS = "topic_1588f43cc2be40818784009c4276ea73"
ETHICS = "topic_d8dd6cb8e85f4073a6af0b76ca4ceff3"


def configuration():
    config = default_config()
    config["conversation"]["topics_to_cover"] = [
        {"id": WEAKNESS, "label": "Weakness and improvement", "criteria": ["A professional weakness", "Concrete improvement steps"]},
        {"id": ETHICS, "label": "Ethical dilemmas", "criteria": ["An ethical dilemma", "Action and outcome"]},
    ]
    config["report"]["sections"] = [
        {"id": "notes", "label": "Notes", "instruction": "Summarize the conversation.", "type": "text", "required": True},
    ]
    return config


def valid_report():
    return {"notes": "The candidate discussed time management.", "topic_coverage": {
        WEAKNESS: {"criteria": {
            "0": {"status": "covered", "evidence": "I used to underestimate how long tasks take.", "missing": ""},
            "1": {"status": "partial", "evidence": "I started tracking my time.", "missing": "How tracking changed estimates."},
        }, "asked": True, "summary": "The candidate tracks time to improve estimates.",
            "suggested_question": "How did tracking time change your estimates?"},
        ETHICS: {"criteria": {
            "0": {"status": "unanswered", "evidence": "", "missing": "An ethical dilemma."},
            "1": {"status": "unanswered", "evidence": "", "missing": "Action taken and the outcome."},
        }, "asked": False, "summary": "", "suggested_question": "Describe an ethical dilemma and how you resolved it."},
    }}


def misplaced_report():
    report = valid_report()
    first = report["topic_coverage"][WEAKNESS]
    for key in ("asked", "summary", "suggested_question"):
        first["criteria"][key] = first.pop(key)
    first[ETHICS] = report["topic_coverage"].pop(ETHICS)
    return report


class ReportStructureTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.schema = build_report_schema(self.config["report"]["sections"], self.config["conversation"]["topics_to_cover"])

    def test_screenshot_nesting_errors_are_recovered_without_changing_findings(self):
        report = misplaced_report()
        original = copy.deepcopy(report)
        with self.assertRaisesRegex(ValueError, "Additional properties are not allowed"):
            validate_report(report, self.schema)
        recovered = normalize_report_structure(report, self.schema)
        self.assertEqual(recovered, valid_report())
        self.assertEqual(report, original)
        self.assertIs(validate_report(recovered, self.schema), recovered)

    def test_topic_nested_inside_criteria_is_relocated(self):
        report = valid_report()
        report["topic_coverage"][WEAKNESS]["criteria"][ETHICS] = report["topic_coverage"].pop(ETHICS)
        self.assertEqual(validate_report(normalize_report_structure(report, self.schema), self.schema), valid_report())

    def test_valid_reports_and_non_topic_reports_are_unchanged(self):
        report = valid_report()
        self.assertEqual(normalize_report_structure(report, self.schema), report)
        plain_schema = build_report_schema(self.config["report"]["sections"])
        plain = {"notes": "A short conversation."}
        self.assertEqual(normalize_report_structure(plain, plain_schema), plain)

    def test_duplicate_topic_values_are_rejected_even_when_identical(self):
        report = valid_report()
        report["topic_coverage"][WEAKNESS][ETHICS] = copy.deepcopy(report["topic_coverage"][ETHICS])
        recovered = normalize_report_structure(report, self.schema)
        self.assertEqual(recovered, report)
        with self.assertRaises(ValueError):
            validate_report(recovered, self.schema)

    def test_conflicting_metadata_is_rejected(self):
        report = misplaced_report()
        report["topic_coverage"][WEAKNESS]["asked"] = False
        recovered = normalize_report_structure(report, self.schema)
        self.assertEqual(recovered, report)
        with self.assertRaises(ValueError):
            validate_report(recovered, self.schema)

    def test_missing_topics_criteria_and_metadata_are_not_fabricated(self):
        for defect in ("topic", "criterion", "metadata"):
            with self.subTest(defect=defect):
                report = valid_report()
                if defect == "topic":
                    del report["topic_coverage"][ETHICS]
                elif defect == "criterion":
                    del report["topic_coverage"][WEAKNESS]["criteria"]["1"]
                else:
                    del report["topic_coverage"][WEAKNESS]["asked"]
                with self.assertRaises(ValueError):
                    validate_report(normalize_report_structure(report, self.schema), self.schema)

    def test_evidence_validation_still_runs_after_relocation(self):
        report = misplaced_report()
        report["topic_coverage"][WEAKNESS]["criteria"]["0"]["evidence"] = ""
        with self.assertRaisesRegex(ValueError, "require supporting evidence"):
            validate_report(normalize_report_structure(report, self.schema), self.schema)

    def test_unknown_fields_remain_invalid(self):
        report = misplaced_report()
        report["topic_coverage"][WEAKNESS]["topic_unknown"] = {"criteria": {}}
        with self.assertRaises(ValueError):
            validate_report(normalize_report_structure(report, self.schema), self.schema)

    def test_layout_reference_matches_current_topic_ids_and_criterion_counts(self):
        for is_final in (False, True):
            instructions = build_analysis_instructions(self.config, is_final=is_final)
            reference = instructions.split("Never copy placeholder text.\n", 1)[1].split("\nEND TOPIC COVERAGE", 1)[0]
            layout = json.loads(reference)["topic_coverage"]
            self.assertEqual(set(layout), {WEAKNESS, ETHICS})
            for topic in layout.values():
                self.assertEqual(set(topic), {"criteria", "asked", "summary", "suggested_question"})
                self.assertEqual(set(topic["criteria"]), {"0", "1"})
        plain_config = copy.deepcopy(self.config)
        plain_config["conversation"]["topics_to_cover"] = []
        self.assertNotIn("TOPIC COVERAGE LAYOUT REFERENCE", build_analysis_instructions(plain_config))


class RealtimeStructureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tracker = UsageTracker()
        self.addCleanup(patch.stopall)
        patch.object(self.tracker, "_write").start()
        patch("builtins.print").start()
        self.on_report = Mock()
        self.on_error = Mock()
        self.client = RealtimeConversationAnalyzer(configuration(), on_report=self.on_report,
                                                   on_error=self.on_error, usage_tracker=self.tracker)
        self.client.ws = Mock()
        self.client.request_analysis(timestamp=60, commit=False)

    def respond(self, report, response_id="response_1"):
        self.client._handle_event({"type": "response.created", "response": {
            "id": response_id, "metadata": {"purpose": "analysis_1"},
        }})
        self.client._handle_event({"type": "response.done", "response": {
            "id": response_id, "metadata": {"purpose": "analysis_1"}, "status": "completed",
            "usage": {"input_tokens": 0, "output_tokens": 100, "output_token_details": {"reasoning_tokens": 10}},
            "output": [{"type": "function_call", "name": TOOL_NAME, "arguments": json.dumps(report)}],
        }})

    def test_recovered_structure_delivers_report_without_extra_api_request(self):
        self.respond(misplaced_report())
        self.on_report.assert_called_once()
        self.assertEqual(self.on_report.call_args[0][0]["report"], valid_report())
        self.on_error.assert_not_called()
        self.assertEqual(self.client.ws.send.call_count, 1)
        self.assertEqual(self.client.pending_responses, 0)
        self.assertEqual(self.tracker.snapshot()["responses"], 1)

    def test_missing_evidence_still_triggers_existing_bounded_retry(self):
        invalid = misplaced_report()
        invalid["topic_coverage"][WEAKNESS]["criteria"]["0"]["evidence"] = ""
        self.respond(invalid)
        self.on_report.assert_not_called()
        self.assertEqual(self.client.ws.send.call_count, 2)
        self.assertEqual(self.client.pending_responses, 1)
        self.respond(valid_report(), response_id="response_2")
        self.on_report.assert_called_once()
        self.on_error.assert_not_called()
        self.assertEqual(self.client.pending_responses, 0)
        self.assertEqual(self.tracker.snapshot()["responses"], 2)

    def test_repeated_invalid_report_is_rejected_after_one_retry(self):
        invalid = valid_report()
        del invalid["topic_coverage"][ETHICS]
        self.respond(invalid)
        self.respond(invalid, response_id="response_2")
        self.on_report.assert_not_called()
        self.on_error.assert_called_once()
        self.assertEqual(self.client.ws.send.call_count, 2)
        self.assertEqual(self.client.pending_responses, 0)


if __name__ == "__main__":
    unittest.main()
