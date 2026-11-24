from __future__ import annotations

import unittest
from datetime import datetime

from client.v2.time_text import format_message_time, format_time_range


class TimeTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 24, 19, 30).timestamp()

    def _stamp(self, year: int, month: int, day: int, hour: int, minute: int) -> float:
        return datetime(year, month, day, hour, minute).timestamp()

    def test_message_time_uses_relative_day_labels(self) -> None:
        self.assertEqual(
            format_message_time(self._stamp(2026, 11, 24, 19, 23), self.now),
            "今天 19:23",
        )
        self.assertEqual(
            format_message_time(self._stamp(2026, 11, 23, 8, 5), self.now),
            "昨天 08:05",
        )
        self.assertEqual(
            format_message_time(self._stamp(2026, 11, 22, 23, 59), self.now),
            "前天 23:59",
        )
        self.assertEqual(
            format_message_time(self._stamp(2026, 11, 21, 12, 0), self.now),
            "11月21日 12:00",
        )
        self.assertEqual(
            format_message_time(self._stamp(2025, 11, 20, 7, 0), self.now),
            "2025年11月20日 07:00",
        )

    def test_range_label_keeps_dates_when_crossing_midnight(self) -> None:
        self.assertEqual(
            format_time_range(
                self._stamp(2026, 11, 24, 14, 0),
                self._stamp(2026, 11, 24, 18, 0),
                self.now,
            ),
            "今天 14:00–18:00",
        )
        self.assertEqual(
            format_time_range(
                self._stamp(2026, 11, 23, 23, 0),
                self._stamp(2026, 11, 24, 1, 0),
                self.now,
            ),
            "昨天 23:00–今天 01:00",
        )
        single = self._stamp(2026, 11, 24, 19, 23)
        self.assertEqual(format_time_range(single, single, self.now), "今天 19:23")


if __name__ == "__main__":
    unittest.main()
