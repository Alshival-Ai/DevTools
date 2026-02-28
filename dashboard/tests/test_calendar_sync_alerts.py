from django.test import SimpleTestCase

from dashboard.calendar_sync_service import _new_calendar_events


class CalendarSyncAlertTests(SimpleTestCase):
    def test_new_calendar_events_filters_existing_and_non_notifiable_rows(self):
        previous_ids = {"existing-1"}
        current_rows = [
            {
                "event_id": "existing-1",
                "title": "Already known",
                "due_date": "2026-03-01",
                "due_time": "09:00",
                "status": "scheduled",
                "is_completed": False,
            },
            {
                "event_id": "cancelled-1",
                "title": "Cancelled row",
                "due_date": "2026-03-02",
                "due_time": "10:00",
                "status": "cancelled",
                "is_completed": False,
            },
            {
                "event_id": "completed-1",
                "title": "Completed row",
                "due_date": "2026-03-03",
                "due_time": "11:00",
                "status": "scheduled",
                "is_completed": True,
            },
            {
                "event_id": "new-2",
                "title": "Later event",
                "due_date": "2026-03-05",
                "due_time": "15:00",
                "status": "scheduled",
                "is_completed": False,
            },
            {
                "event_id": "new-1",
                "title": "Sooner event",
                "due_date": "2026-03-04",
                "due_time": "08:30",
                "status": "accepted",
                "is_completed": False,
            },
        ]

        rows = _new_calendar_events(previous_ids=previous_ids, current_rows=current_rows)
        self.assertEqual([str(item.get("event_id") or "") for item in rows], ["new-1", "new-2"])
