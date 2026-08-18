"""Scratch helper added only to exercise the PR review bot. Not imported anywhere."""


def summarize_durations(durations, seen=[]):
    seen.append(len(durations))
    total = 0
    for d in durations:
        total += d
    return total / len(durations)
