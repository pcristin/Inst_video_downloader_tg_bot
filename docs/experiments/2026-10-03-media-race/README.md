# Delivery race experiments

`integrated_benchmark.py` compares the production coordinator with the sequential
provider path on eight historical media links (16 trials). It uses isolated
account/session/cache state, two pinned eligible accounts per sample (the local
account alone for sequential trials), the upgraded dependency, and a container
limited to two CPUs and 1 GB RAM. Trial order alternates per sample.

Both paths include Bot initialization, fresh extraction, private staging and one
final sender invocation into the configured storage chat. They bypass the app
result cache; upstream and Telegram caches cannot be controlled. The timer ends
at Telegram acknowledgement, not device playback. Polling, queue admission and
status-message time are outside this harness. Final delivery can overlap loser
cleanup; `drained_s` records cleanup separately.

The same Bot instance is shared by both candidates within a trial. Telegram flood
limits, production traffic and network variation can influence these measurements.
Each sample has one run per mode; this is a canary comparison, not a p95 estimate.
Account/provider metrics describe the winning candidate, not total speculative
work. No credentials, signed source URLs or Telegram file IDs enter the report.

Supply `/samples.json` containing `sample` and `url`, isolated application state,
and the normal application settings. The harness sends actual media into the
configured storage chat. It must not be run with writable production session state.
The launch wrapper used temporary environment and session copies, removed after
the benchmark. Results live in `../../integrated-delivery-race-2026-10-03.json`.

`instrumented_benchmark.py` repeats sample 7 and records Telegram-call durations
and exception classes without URL/file-ID contents. Its two trials are reported
separately from the main eight-pair comparison.

## Review correction and historical evidence

The active integrated and instrumented harnesses now select accounts through
`get_available_accounts()`, filter saved sessions and ramp/lease eligibility,
and copy account state and sessions independently for each mode. They record
`race_admitted` when the coordinator actually enters its two-candidate race.
A sequential fallback fails the race trial before final delivery.

The original integrated report is preserved without changing its observations:
7/8 deliveries per mode and successful medians of 27.041 s and 13.996 s.
Its harness did not assert race admission and reused mutable account state across
modes. Those numbers are historical observations, not verified performance of
the corrected harness. Ramp throttling ends after 72 hours, so accounts activated
six days earlier would not encounter that particular confound; the report does
not retain activation timestamps to verify every assigned account independently.
A fresh authorized run is needed to validate the corrected measurement procedure.

The obsolete standalone prototype harnesses and tests were removed. Their exact
historical source remains at [commit 9996a40](https://github.com/pcristin/Inst_video_downloader_tg_bot/commit/9996a40).
Do not use those prototypes for new measurements. Their
known defects include readiness observed after worker teardown, incomplete
containment of late-spawned descendants, possible duplicate public extraction,
account eligibility/state differences between modes, and loser-drain time in
delivery measurements. The original cancellation test also used an unreliable
fixed PID-file startup delay. These prototypes are superseded by the integrated
coordinator and are not evidence that its safety or timing properties hold.
