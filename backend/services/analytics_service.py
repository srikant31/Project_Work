"""UTC cohort definitions and explicit safe serialization for admin analytics."""

from datetime import UTC, datetime, timedelta

from backend.repositories.analytics_repository import AnalyticsRepository
from backend.utils.date_ranges import utc_bounds


DEFINITIONS = [
    "All calendar dates use UTC; start and end dates are inclusive.",
    "Question totals use user-message time. Response, outcome, latency and rating totals use assistant-message time.",
    "Positive/negative counts are current conversation-owner votes on responses in the period, not feedback submission events or vote history.",
    "Today is UTC today; this week starts Monday UTC. Both counts intersect the selected range.",
    "Average response time includes only known nonnegative recorded latencies; timed_response_count is its denominator. Missing timing is not zero.",
    "Unsupported means insufficient_context=true, not a calibrated model confidence score. Unknown outcomes are reported separately.",
    "No useful retrieval counts only the explicit grounding reason no_eligible_sources. Missing legacy metadata is not classified as retrieval failure.",
    "Document usage counts distinct retained response/document-version pairs, including historical offline sources; it is not downloads or number of chunks.",
    "Frequent questions group lowercase text with collapsed whitespace, not semantic similarity. Lists show at most 10 rows each.",
    "Daily activity shows at most the last 31 calendar days within the selected range; totals may span a longer period.",
    "Unsupported examples use only valid explicit question links; an unknown legacy question remains null.",
    "This report covers saved messages only. Context previews, retrieval-lab searches and failed/unconfigured generation requests are not saved questions.",
]


def _utc(value):
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


class AnalyticsService:
    def __init__(self, session_factory, clock=None):
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def report(self, date_from=None, date_to=None):
        lower, upper = utc_bounds(date_from, date_to)
        now = self.clock().astimezone(UTC)
        today = now.date()
        day_start, day_end = utc_bounds(today, today)
        week_start, _ = utc_bounds(today - timedelta(days=today.weekday()), None)

        def intersect_count(repository, start, end):
            start = max(start, lower) if lower is not None else start
            end = min(end, upper) if upper is not None else end
            return 0 if start >= end else repository.question_count(start, end)

        daily_to = date_to or max(today, date_from or today)
        daily_from = max(daily_to - timedelta(days=min(30, daily_to.toordinal() - 1)), date_from or datetime.min.date())
        daily_lower, daily_upper = utc_bounds(daily_from, daily_to)
        with self.session_factory() as session:
            repository = AnalyticsRepository(session)
            summary = {"total_questions": repository.question_count(lower, upper),
                       "questions_today": intersect_count(repository, day_start, day_end),
                       "questions_this_week": intersect_count(repository, week_start, day_end),
                       **repository.response_summary(lower, upper)}
            value = summary["average_response_time_ms"]
            summary["average_response_time_ms"] = float(value) if value is not None else None
            question_days, response_days = repository.daily(daily_lower, daily_upper)
            questions = {row.day: row.questions for row in question_days}
            responses = {row.day: row for row in response_days}
            daily = []
            for offset in range((daily_to - daily_from).days + 1):
                day = daily_from + timedelta(days=offset)
                row = responses.get(day)
                daily.append({"date": day.isoformat(), "questions": questions.get(day, 0),
                              "responses": row.responses if row else 0,
                              "positive_feedback": row.positive_feedback if row else 0,
                              "negative_feedback": row.negative_feedback if row else 0})
            frequent = [{"question": row.question, "count": row.count}
                        for row in repository.frequent_questions(lower, upper)]
            usage = [{"doc_id": str(row.doc_id), "doc_name": row.doc_name, "version": row.version,
                      "category_name": row.category_name, "responses": row.responses}
                     for row in repository.document_usage(lower, upper)]
            unsupported = [{"review_id": str(row.review_id), "conversation_id": str(row.conversation_id),
                            "user_email": row.user_email, "question": row.question, "answer": row.answer,
                            "created_at": _utc(row.created_at), "reason": row.reason}
                           for row in repository.unsupported_questions(lower, upper)]
        return {"range": {"date_from": date_from.isoformat() if date_from else None,
                          "date_to": date_to.isoformat() if date_to else None,
                          "timezone": "UTC", "generated_at": _utc(now),
                          "daily_from": daily_from.isoformat(), "daily_to": daily_to.isoformat()},
                "summary": summary, "daily_activity": daily, "frequent_questions": frequent,
                "document_usage": usage, "unsupported_questions": unsupported,
                "definitions": list(DEFINITIONS)}
