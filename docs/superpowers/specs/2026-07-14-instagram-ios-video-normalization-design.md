# Instagram iOS Video Normalization Design

## Goal

Make Instagram videos delivered by the bot reliably playable in Telegram on iOS while leaving photos, X/Twitter videos, and YouTube videos unchanged.

## Context

The bot currently uploads downloaded Instagram video bytes unchanged. Telegram Web can play the reported reel, while Telegram iOS remains stuck at 0:00. The existing media probe extracts only duration, dimensions, aspect ratio, and rotation. It does not validate codec compatibility, pixel format, MP4 layout, timestamps, or full decodeability.

## Architecture

Add a focused `media_normalizer` service between successful Instagram download and caching or Telegram upload. The service inspects every Instagram video and produces an iOS-safe MP4 when possible. Other providers and photos bypass it.

Normalization is synchronous internally because FFmpeg is a subprocess, but callers invoke it in a worker thread so it cannot block Telegram's asynchronous event loop.

## Normalization Rules

For every downloaded Instagram video:

1. Probe the input container, video codec, audio codec, pixel format, dimensions, duration, and timestamps.
2. Run a full decode validation.
3. If the video is H.264 with `yuv420p` and any audio stream is AAC, remux it losslessly. The remux normalizes timestamps and moves MP4 metadata to the front with `+faststart`.
4. Otherwise, transcode video to H.264 High with `yuv420p` and audio to AAC. Preserve source resolution and frame cadence.
5. Probe and fully decode the candidate output.
6. Adopt the candidate only when all checks succeed. Refresh the media item's duration, width, and height from the normalized output.

## File Safety

FFmpeg writes to a sibling temporary MP4. The original input remains untouched until the candidate passes validation. A verified candidate becomes the media item's upload path. Normal job cleanup removes all job-directory files after delivery. Failed candidates are deleted immediately.

If FFmpeg, ffprobe, validation, or cleanup fails, the bot logs the normalization failure and continues with the original media file. Normalization must never turn a successful Instagram download into a failed request.

## Observability

Record one outcome for each Instagram video:

- `remuxed`
- `transcoded`
- `normalization_failed`

Logs include elapsed time, input/output sizes, and the compatibility reason. They do not include the source URL or credentials.

## Integration

Normalization occurs once per fresh Instagram download before the result is cached or staged for Telegram. Cache hits reuse the already-normalized Telegram file ID and do not repeat FFmpeg work. Inline and direct-chat delivery therefore share the same normalized media behavior.

## Testing

Tests cover:

- compatible H.264/AAC `yuv420p` selects lossless remux;
- incompatible video selects H.264/AAC transcoding;
- verified output replaces the media path and refreshes metadata;
- FFmpeg or validation failure preserves the original media item;
- Instagram photos bypass normalization;
- X/Twitter and YouTube downloads bypass normalization;
- normalization is invoked without blocking the async event loop;
- temporary candidates are cleaned on failure.

Focused unit tests mock subprocess boundaries. An integration test using generated FFmpeg fixtures verifies actual remuxing, transcoding, faststart output, and decodeability when FFmpeg is available.

## Success Criteria

- The previously failing class of Instagram MP4 plays in Telegram iOS after normalization.
- Compatible videos are remuxed without generation loss.
- Only incompatible videos are transcoded.
- Existing delivery continues with the original video if normalization fails.
- All existing tests continue to pass.
