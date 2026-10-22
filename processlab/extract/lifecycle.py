"""XES lifecycle vocabulary — defined once, used by every extractor.

A row in an XES log is **not a task**. It is a state change of a task, so a duration can only
be recovered by pairing the row where someone picked the task up with the row where they put it
down. BPI 2017 stores each work item as several such rows:

    schedule   the task was queued for someone; nobody has touched it yet
    start      someone picked it up                      -> OPENS an interval
    resume     someone picked it up again                -> OPENS an interval
    suspend    they put it down (the common ending here) -> CLOSES an interval
    complete   they finished it                          -> CLOSES an interval
    withdraw   cancelled before any work happened        -> no interval
    ate_abort  abandoned                                 -> no interval

Two consequences worth stating, because both are easy to get wrong:

* `suspend` is a real ending, not an error. In BPI 2017, W_ items end in `suspend` 215,402
  times against only 41,862 `complete`s. Treating `complete` as the only ending would discard
  most of the measured work in the log.
* One activity can yield several intervals. A task started, suspended, and resumed days later by
  a different person is two separate stretches of work, and is kept as two rows.

Pairing is first-in-first-out per activity name within a case: the earliest unclosed opening is
matched to the next closing. A closing with nothing open before it is a stray row and is
skipped rather than turned into a zero-length interval.
"""

OPENS = ("start", "resume")
CLOSES = ("complete", "suspend")
#: Present in the log, but never a unit of work: queued, cancelled, or abandoned.
IGNORED = ("schedule", "withdraw", "ate_abort")
