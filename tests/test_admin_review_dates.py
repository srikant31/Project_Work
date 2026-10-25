"""Question-date filters use exact linked questions, not answer time or siblings."""

from datetime import datetime

import pytest

from tests.test_admin_reviews import dashboard, login, review_list


pytestmark = pytest.mark.integration


def test_question_dates_use_linked_question_not_answer_or_conversation(dashboard):
    from backend.database.models import Message

    with dashboard.factory() as session:
        for name, stamp in (("vpn-question", datetime(2026, 10, 20, 23, 59, 59, 999999)),
                            ("vpn-answer", datetime(2026, 10, 23)),
                            ("payroll-question", datetime(2026, 10, 21)),
                            ("orphan-question", datetime(2026, 10, 21, 23, 59, 59, 999999)),
                            ("legacy-question", datetime(2026, 10, 21))):
            session.get(Message, dashboard.ids[name]).created_at = stamp
        session.commit()
    login(dashboard)
    rows = review_list(dashboard, question_from="2026-10-21", question_to="2026-10-21")
    assert {row["review_id"] for row in rows["reviews"]} == {
        str(dashboard.ids["payroll-answer"]), str(dashboard.ids["orphan-question"]),
        str(dashboard.ids["legacy-question"])}
    assert all(row["question"] is not None for row in rows["reviews"])
    assert review_list(dashboard, question_from="2026-10-21", feedback="down")["total"] == 0
    assert review_list(dashboard, question_to="2026-10-20", feedback="down")["reviews"][0]["review_id"] == str(dashboard.ids["vpn-answer"])


def test_date_filter_excludes_unknown_question_and_keeps_same_review_filters(dashboard):
    login(dashboard)
    filtered = review_list(dashboard, question_from="2026-10-23", question_to="2026-10-23",
                           feedback="up", search="PAYROLL")
    assert filtered["total"] == 1
    assert filtered["reviews"][0]["review_id"] == str(dashboard.ids["payroll-answer"])
    legacy = review_list(dashboard, conversation_id=str(dashboard.ids["legacy"]), question_from="2026-10-23")
    assert legacy["total"] == 1
    assert legacy["reviews"][0]["answer"] is None
    assert review_list(dashboard, question_from="2026-10-23", feedback="down", search="payroll")["total"] == 0


@pytest.mark.parametrize("params", [
    {"question_from": "2026-10-24", "question_to": "2026-10-23"},
    {"question_from": "bad"}, {"question_to": "2026-02-30"}, {"question_to": "9999-12-31"},
    {"question_from": "9999-12-31"},
    {"question_from": "1792713600"}, {"question_from": "20261023"},
])
def test_bad_question_ranges_rejected(dashboard, params):
    login(dashboard)
    assert dashboard.client.get("/api/admin/reviews", params=params).status_code == 422
