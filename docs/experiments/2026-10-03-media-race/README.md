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
