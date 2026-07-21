"""Custom APScheduler triggers used by the synchronization service."""

from datetime import datetime, time, timedelta
from math import ceil

from apscheduler.triggers.base import BaseTrigger


class VisualScheduleTrigger(BaseTrigger):
    """Run at a fixed cadence anchored to each active window's start time."""

    _DAY_NUMBERS = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }

    def __init__(
        self,
        *,
        interval_minutes: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        active_days: list[str],
        timezone,
    ) -> None:
        self.interval = timedelta(minutes=interval_minutes)
        self.start_time = time(start_hour, start_minute)
        self.end_time = time(end_hour, end_minute)
        self.active_weekdays = {
            self._DAY_NUMBERS[day.lower()] for day in active_days
        }
        self.timezone = timezone

    def get_next_fire_time(self, previous_fire_time, now):
        local_now = self._localize(now)
        threshold = local_now
        if previous_fire_time is not None:
            previous_local = self._localize(previous_fire_time)
            threshold = max(threshold, previous_local + timedelta(microseconds=1))

        # Include yesterday because an overnight window can end today. Eight
        # forward days guarantee that every selected weekday is considered.
        first_window_date = threshold.date() - timedelta(days=1)
        best = None
        for day_offset in range(9):
            window_date = first_window_date + timedelta(days=day_offset)
            if window_date.weekday() not in self.active_weekdays:
                continue
            window_start = datetime.combine(
                window_date, self.start_time, tzinfo=self.timezone
            )
            window_end = datetime.combine(
                window_date, self.end_time, tzinfo=self.timezone
            )
            if self.end_time < self.start_time:
                window_end += timedelta(days=1)

            if window_end < threshold:
                continue

            elapsed = max(0.0, (threshold - window_start).total_seconds())
            step = max(0, ceil(elapsed / self.interval.total_seconds()))
            candidate = window_start + step * self.interval

            while candidate <= window_end:
                # The active weekday identifies the day on which the window
                # starts. An overnight window may legitimately fire next day.
                if candidate >= threshold:
                    if best is None or candidate < best:
                        best = candidate
                    break
                candidate += self.interval

        return best

    def _localize(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    def __str__(self) -> str:
        minutes = int(self.interval.total_seconds() / 60)
        days = ",".join(
            name for name, number in self._DAY_NUMBERS.items()
            if number in self.active_weekdays
        )
        return (
            f"visual[{minutes}min, {self.start_time.strftime('%H:%M')}-"
            f"{self.end_time.strftime('%H:%M')}, {days}]"
        )


class DayWiseScheduleTrigger(BaseTrigger):
    """Return the earliest next fire time from independent per-day rules."""

    def __init__(self, *, rules: list[dict], timezone) -> None:
        self.timezone = timezone
        self.rules = [dict(rule) for rule in rules if rule.get("enabled", True)]
        self.triggers = [
            VisualScheduleTrigger(
                interval_minutes=int(rule["interval_minutes"]),
                start_hour=int(rule["active_hours_start"]),
                start_minute=int(rule.get("active_minutes_start", 0)),
                end_hour=int(rule["active_hours_end"]),
                end_minute=int(rule.get("active_minutes_end", 0)),
                active_days=[str(rule["day"]).lower()],
                timezone=timezone,
            )
            for rule in self.rules
        ]

    def get_next_fire_time(self, previous_fire_time, now):
        candidates = [
            trigger.get_next_fire_time(previous_fire_time, now)
            for trigger in self.triggers
        ]
        return min((candidate for candidate in candidates if candidate is not None), default=None)

    def __str__(self) -> str:
        return "daywise[" + "; ".join(
            f"{rule['day']}={rule['interval_minutes']}min "
            f"{int(rule['active_hours_start']):02d}:{int(rule.get('active_minutes_start', 0)):02d}-"
            f"{int(rule['active_hours_end']):02d}:{int(rule.get('active_minutes_end', 0)):02d}"
            for rule in self.rules
        ) + "]"
