# PodcastSeriesTranscriptionStatus,c
PodcastSeriesTranscriptionStatus,c               

[🧑‍💻Taddy API](https://taddy.org/developers)/[📻Podcast API](https://taddy.org/developers/podcast-api)/


`PodcastSeriesTranscriptionStatus`
==================================

Useful to check if Taddy is automatically generating transcripts and chapters for this podcast

```
enum PodcastSeriesTranscriptionStatus {
  TRANSCRIBING
  NOT_TRANSCRIBING
  CREATOR_ASKED_NOT_TO_TRANSCRIBE
}
```


`TRANSCRIBING` - Taddy API Business Users get access to automatically generated transcripts and chapters for episodes of this podcast.

`NOT_TRANSCRIBING` - We do not automatically generate transcripts or chapters.

`CREATOR_ASKED_NOT_TO_TRANSCRIBE` - A creator can contact us and asks us not to transcribe their episodes.

Please see [transcripts](https://taddy.org/developers/podcast-api/episode-transcripts) for additional context.