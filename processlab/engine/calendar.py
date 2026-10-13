"""UTC weekly calendars. Activities pause outside working hours; no multitasking."""
import math

DAY = 86400


def next_work(timestamp: float, calendar: list) -> float:
    day = math.floor(timestamp / DAY)
    for offset in range(8):
        current = day + offset
        shift = calendar[(current + 3) % 7]
        if shift:
            start, end = current * DAY + shift[0], current * DAY + shift[1]
            if timestamp < end:
                return max(timestamp, start)
    raise ValueError("Resource has no working hours.")


def finish_work(timestamp: float, duration: float, calendar: list) -> tuple[float, float]:
    if duration < 0 or not math.isfinite(duration):
        raise ValueError("Processing duration must be finite and nonnegative.")
    start = current = next_work(timestamp, calendar)
    remaining = duration
    for _ in range(3660):
        if remaining <= 1e-7:
            return start, current
        day = math.floor(current / DAY)
        end = day * DAY + calendar[(day + 3) % 7][1]
        used = min(remaining, end - current)
        current += used
        remaining -= used
        if remaining > 1e-7:
            current = next_work(current, calendar)
    raise ValueError("An activity exceeds ten years of working time; check durations and schedules.")


def working_between(start: float, end: float, calendar: list) -> float:
    if end <= start:
        return 0.0
    first, last = math.floor(start / DAY), math.floor(end / DAY)
    if first == last:
        shift = calendar[(first + 3) % 7]
        return max(0, min(end, first * DAY + shift[1]) - max(start, first * DAY + shift[0])) if shift else 0.0
    first_shift, last_shift = calendar[(first + 3) % 7], calendar[(last + 3) % 7]
    total = max(0, first * DAY + first_shift[1] - max(start, first * DAY + first_shift[0])) if first_shift else 0.0
    if last_shift:
        total += max(0, min(end, last * DAY + last_shift[1]) - (last * DAY + last_shift[0]))
    full_days = max(0, last - first - 1)
    week = sum(s[1] - s[0] for s in calendar if s)
    total += (full_days // 7) * week
    for i in range(full_days % 7):
        shift = calendar[(first + 1 + i + 3) % 7]
        if shift:
            total += shift[1] - shift[0]
    return total
