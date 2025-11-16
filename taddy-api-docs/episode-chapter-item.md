# Chapter
Chapter               

[🧑‍💻Taddy API](https://taddy.org/developers)/[📻Podcast API](https://taddy.org/developers/podcast-api)/

Chapter
=======

Get details on the chapters for an episode.

```
type Chapter {
  " The unique identifier for the chapter "
  id: ID

  " The title of the chapter "
  title: String

  " The start timecode of the chapter in milliseconds "
  startTimecode: Int
}
```
