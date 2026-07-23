# Instagram Public Fallback Audio Preservation Design

## Problem

The public Instagram yt-dlp fallback can return a playable but silent video. The two inline deliveries inspected on 2026-07-23 at 20:09 and 20:15 UTC each contained one H.264 video stream and no audio stream.

yt-dlp exposed separate DASH tracks for both reels: VP9 video-only formats and an M4A audio-only format. `InstagramClient._public_ytdlp_source()` currently ranks video formats only by dimensions and bitrate. It therefore selects the largest video-only track and ignores the available audio track. The later iOS normalizer correctly converts VP9 to H.264, but it cannot restore audio that was never downloaded.

## Goals

- Preserve the best available public video quality and its available audio.
- Never accept a merged result when yt-dlp advertised audio but the output has no audio stream.
- Fall through to the existing authenticated account path when public recovery cannot produce a valid audiovisual file.
- Preserve current photo, carousel, timeout, and iOS-normalization behavior.
- Prevent previously cached silent inline Telegram files from being reused after deployment.

## Non-goals

- Reworking the fast extractor or authenticated Instagram client.
- Re-encoding media during public acquisition; the existing normalizer remains responsible for iOS-compatible video encoding.
- Repairing Telegram messages that were already delivered.
- Changing database schemas or retention policies.

## Design

### Public source selection

Replace the single-source public selector result with a small value object that describes:

- the selected visual source URL and extension;
- whether that source is a video or thumbnail;
- an optional audio-only source URL.

For video entries, select the highest-quality video candidate using the existing dimensions, filesize, and bitrate ordering. If that candidate declares `acodec == "none"`, also select the best audio-only candidate (`vcodec == "none"` and `acodec != "none"`) by bitrate and filesize. A video format that already carries audio requires no separate audio source. Photo-only entries continue to use the largest thumbnail.

### Download and merge

Download visual and audio sources into per-entry temporary files inside the requested output directory. When a separate audio source exists, invoke ffmpeg to create the final `public_<index>.mp4` with explicit stream mapping:

```text
-map 0:v:0 -map 1:a:0 -c copy -movflags +faststart
```

Stream copy retains source quality and avoids a second video encode. The existing media normalizer subsequently converts VP9 video to H.264/yuv420p while preserving or converting the audio to AAC as needed.

When the selected visual source already contains audio, keep the current direct-download behavior. Photos remain direct downloads.

### Validation and fallback

If yt-dlp advertised a separate audio track, public recovery succeeds for that entry only when:

- both source downloads complete;
- ffmpeg exits successfully and produces a non-empty final file; and
- ffprobe confirms that the final file contains both video and audio streams.

On download, merge, or validation failure, remove temporary/partial files and omit that entry. If no entries succeed, return `None`; `VideoDownloader` will then use the existing account-based fallback. Public recovery must not return the original video-only track when an advertised audio track failed to merge.

As defense in depth, the media normalizer will reject a normalized candidate if its source probe contained audio but its output probe does not. It will then preserve the original source rather than introducing a second audio-loss path.

### Inline cache invalidation

Inline media cache keys are permanent and the two affected URLs already point to silent Telegram file IDs. Add a cache-format version to Instagram inline cache keys. The version change makes old Instagram cache rows unreachable without deleting state and causes the first request for each URL after deployment to download and store corrected media. Other providers retain their existing cache namespace.

## Error handling and observability

- Log public merge failures at info level with the exception class and entry index, without media URLs or credentials.
- Log an explicit normalization warning when a source audio stream would be lost.
- Preserve existing public-fallback timeouts and the account-fallback path.
- Always remove audio, video, and partial merge artifacts that are not returned as successful media.

## Testing

Implementation will follow test-driven development:

1. A selector test with one audio-only DASH format and several video-only DASH formats must return the highest-quality video plus the best audio.
2. A public recovery test must show that separate downloaded tracks are merged into the returned file and that the video-only source is not returned directly.
3. Failure tests must show that ffmpeg or audio validation failure returns no public video and cleans partial artifacts.
4. A media-normalizer test must reject output that loses audio present in its source.
5. An inline-delivery test must show that the versioned Instagram cache key bypasses an old silent-cache namespace while non-Instagram cache keys remain stable.
6. The existing Instagram metadata, downloader-flow, media-normalizer, inline-delivery, and full pytest suites must remain green.

Container verification will also generate or download disposable separate audio/video fixtures, run the production merge and normalization path, and use ffprobe to assert that the final MP4 contains H.264 video and AAC audio.

## Deployment result

After deployment, new public Instagram fallback downloads will include available audio. Previously cached silent Instagram inline entries will be bypassed automatically. Already-sent silent Telegram messages will remain unchanged.
