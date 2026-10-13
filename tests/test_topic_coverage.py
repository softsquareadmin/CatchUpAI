import unittest
from html.parser import HTMLParser

from catchupai.topic_coverage import coverage_status, topic_coverage_html, topic_coverage_score


def topic(label, statuses):
    definition = {"id": label, "label": label, "criteria": [f"Criterion {i}" for i in range(len(statuses))]}
    result = {"criteria": {str(i): {"status": status, "evidence": "An answer." if status != "unanswered" else "",
                                  "missing": "More detail." if status != "covered" else ""}
                           for i, status in enumerate(statuses)},
              "asked": True, "summary": "An answer summary.", "suggested_question": "What else?"}
    return definition, result


class Scores(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.topic_scores = []
        self.reading_score = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "span" and dict(attrs).get("class") == "coverage-topic-score":
            self.reading_score = True

    def handle_endtag(self, tag):
        if tag == "span":
            self.reading_score = False

    def handle_data(self, data):
        if self.reading_score:
            self.topic_scores.append(data)


class TopicCoverageTests(unittest.TestCase):
    def test_incomplete_topics_reflect_how_many_criteria_have_answers(self):
        for statuses, expected in [(["covered"] * 4 + ["unanswered"], 80),
                                   (["covered"] * 4 + ["partial"], 90),
                                   (["covered", "partial"], 75),
                                   (["covered"] + ["unanswered"] * 3, 25),
                                   (["covered"] * 2 + ["unanswered"], 67)]:
            with self.subTest(statuses=statuses):
                definition, result = topic("A topic", statuses)
                report = {"topic_coverage": {definition["id"]: result}}
                self.assertEqual(coverage_status(result), "partial")
                self.assertEqual(topic_coverage_score([definition], report), expected)
                for show_heading in (True, False):
                    html = topic_coverage_html([definition], report, show_heading=show_heading)
                    self.assertEqual(Scores(html).topic_scores, [f"{expected}%"])
                    self.assertIn(f'style="width:{expected}%"', html)
                    self.assertIn(f'--coverage:{expected}%', html)

    def test_overall_score_averages_topics_with_different_numbers_of_criteria(self):
        first, first_result = topic("First", ["covered"] * 4 + ["unanswered"])
        second, second_result = topic("Second", ["covered"])
        report = {"topic_coverage": {"First": first_result, "Second": second_result}}
        self.assertEqual(topic_coverage_score([first, second], report), 90)
        self.assertEqual(Scores(topic_coverage_html([first, second], report)).topic_scores, ["80%", "100%"])

    def test_empty_pending_and_unanswered_topics_do_not_inflate_coverage(self):
        definition, result = topic("First", ["unanswered"] * 2)
        self.assertEqual(topic_coverage_score([], {}), 0)
        self.assertEqual(topic_coverage_html([], {}), "")
        self.assertEqual(topic_coverage_score([definition], {}), 0)
        pending_html = topic_coverage_html([definition], {})
        self.assertEqual(Scores(pending_html).topic_scores, [])
        self.assertIn("Awaiting analysis", pending_html)
        self.assertEqual(Scores(topic_coverage_html([definition], {"topic_coverage": {"First": result}})).topic_scores, ["0%"])
        second, second_result = topic("Second", ["covered"])
        self.assertEqual(topic_coverage_score([definition, second], {"topic_coverage": {"Second": second_result}}), 50)


if __name__ == "__main__":
    unittest.main()
