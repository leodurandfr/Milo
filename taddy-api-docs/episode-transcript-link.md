# Transcript,c Link
Transcript,c Link               

[🧑‍💻Taddy API](https://taddy.org/developers)/[📻Podcast API](https://taddy.org/developers/podcast-api)/

TranscriptLink


`Transcript`Link
================

Link details where you can download an episode’s transcript. Along with the url, it also contains additional information like file type and language, which may be useful to you.

```
type TranscriptLink {
  " The url to the transcript "
  url: String

  " Mime type of file"
  type: String
  
  " If the transcript is exclusive to Taddy API Business users and you need an API key to access it "
  isTaddyExclusive: Boolean

  " (Optional) The language of the transcript "
  language: String

  " (Optional) If the transcript has timecodes "
  hasTimecodes: Boolean
}
```


Please see [transcripts](https://taddy.org/developers/podcast-api/episode-transcripts) for additional context.