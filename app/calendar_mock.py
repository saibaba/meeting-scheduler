from __future__ import annotations
import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import List, Tuple, Optional
import pytz

@dataclass
class BusyBlock:
    start: dt.datetime
    end: dt.datetime

busy = ["jeff", "mike"]

class MockCalendar:
    """
    Deterministic free/busy based on attendee name + date.
    Also stores booked events in-memory per process (fine for demo).
    """

    def is_available(self, attendee: str) -> bool:
        for b in busy:
            if b.lower() in attendee.lower():
                return False

        return True
            
        
    def suggest_alternatives(
        self,
        attendee: str,
        start: dt.datetime,
        duration_minutes: int,
        count: int = 3
    ) -> List[Tuple[dt.datetime, int]]:
        """
        Suggest next available slots in 30-min steps, same day then next day.
        """
        suggestions: List[Tuple[dt.datetime + dt.timedelta(days=1), int]] = []
        cursor = start
        for _ in range(96):  # scan up to ~2 days in 30m steps
            cursor = cursor + dt.timedelta(minutes=30)
            # keep within 9–17 for realism
            if 9 <= cursor.hour <= 16:
                suggestions.append((cursor, duration_minutes))
                if len(suggestions) >= count:
                    return suggestions
        return suggestions

    def book(self, host: str, attendee: str, subject: str, start: dt.datetime, duration_minutes: int) -> dict:
        event = {
            "id": f"1",
            "host_full_name": host,
            "attendee_full_name": attendee,
            "subject": subject,
            "start_time_iso": start.isoformat(),
            "duration_minutes": duration_minutes,
        }
        return event

