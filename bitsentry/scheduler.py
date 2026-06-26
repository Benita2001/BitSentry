from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bitsentry.reporter import ReportGenerator


class Scheduler:
    """
    Background thread that fires report sends at fixed UTC times.

    Schedule:
      Daily   — 23:00 UTC every day
      Weekly  — 23:30 UTC every Sunday
      Monthly — 23:45 UTC on the 1st of each month
    """

    def __init__(self, reporter: "ReportGenerator", tick_seconds: int = 60):
        self._reporter = reporter
        self._tick     = tick_seconds
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fired: dict[str, str] = {}

    def _utc_key(self) -> tuple[int, int, int, int]:
        now = datetime.now(timezone.utc)
        return now.year, now.month, now.day, now.hour, now.minute  # type: ignore[return-value]

    def _should_fire(self, key: str, dt: datetime) -> bool:
        tag = f"{key}-{dt.year}-{dt.month}-{dt.day}-{dt.hour}-{dt.minute}"
        if self._last_fired.get(key) == tag:
            return False
        self._last_fired[key] = tag
        return True

    def _tick_loop(self) -> None:
        while not self._stop_evt.wait(self._tick):
            now = datetime.now(timezone.utc)
            h, m, wd, dom = now.hour, now.minute, now.weekday(), now.day

            # Daily at 23:00
            if h == 23 and m == 0 and self._should_fire("daily", now):
                print("[bitsentry-scheduler] Sending daily report…")
                try:
                    self._reporter.send_daily_report()
                except Exception as exc:
                    print(f"[bitsentry-scheduler] Daily report error: {exc}")

            # Weekly Sunday (weekday=6) at 23:30
            if h == 23 and m == 30 and wd == 6 and self._should_fire("weekly", now):
                print("[bitsentry-scheduler] Sending weekly report…")
                try:
                    self._reporter.send_weekly_report()
                except Exception as exc:
                    print(f"[bitsentry-scheduler] Weekly report error: {exc}")

            # Monthly on 1st at 23:45
            if h == 23 and m == 45 and dom == 1 and self._should_fire("monthly", now):
                print("[bitsentry-scheduler] Sending monthly report…")
                try:
                    self._reporter.send_monthly_report()
                except Exception as exc:
                    print(f"[bitsentry-scheduler] Monthly report error: {exc}")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._tick_loop, daemon=True, name="bitsentry-scheduler")
        self._thread.start()
        print(f"[bitsentry-scheduler] Started (tick={self._tick}s). "
              "Daily@23:00 Weekly@Sun23:30 Monthly@1st23:45 UTC")

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=5)
        print("[bitsentry-scheduler] Stopped.")
