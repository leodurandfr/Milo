# PodcastSeries
PodcastSeries               

[🧑‍💻Taddy API](https://taddy.org/developers)/[📻Podcast API](https://taddy.org/developers/podcast-api)/


PodcastSeries
=============

GraphQL Type for a Podcast.

```
" Taddy's unique identifier (an uuid) "
uuid: ID

" Date when the podcast was published (Epoch time in seconds) "
datePublished: Int

" The name (title) for a podcast "
name: String

" The description for a podcast "
description(
  " (Optional) Option to remove the html tags from the description or leave the description as is (which may include html tags). Default is false (leave description as is)."
  shouldStripHtmlTags: Boolean
): String

" Extract all links from within the description. " 
descriptionLinks: [String]

" The cover art for a podcast "
imageUrl: String

" itunesId for the podcast "
itunesId: Int

" A hash of all podcast details. It may be useful for you to save this property in your database and compare it to know if any podcast details have updated since the last time you checked "
hash: String

" A hash of all episode details. It may be useful for you to save this property in your database and compare it to know if there are any new or updated episodes since the last time you checked "
childrenHash: String

" A list of episodes for this podcast "
episodes(
  " (Optional) Returns episodes based on SortOrder. Default is LATEST (newest episodes first), another option is OLDEST (oldest episodes first), and another option is SEARCH (pass in the property searchTerm) to filter for episodes by title or description. "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000 "
  page: Int,

  " (Optional) Return up to this number of episodes. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int,

  " (Optional) Only to be used when sortOrder is SEARCH. Filters through the title & description of episodes for the searchTerm "
  searchTerm: String,

" (Optional) The option to show episodes that were once on the RSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedEpisodes: Boolean,
): [PodcastEpisode]

" The number of episodes for this podcast "
totalEpisodesCount(
  " (Optional) Option to include episodes that were once on the RSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedEpisodes: Boolean
): Int

" A podcast can belong to multiple genres but they are listed in order of importance. Limit of 5 genres per podcast"
genres: [Genre]

" Additional info from itunes on the podcast "
itunesInfo: iTunesInfo

" Podcast type (serial or episodic) "
seriesType: PodcastSeriesType

" Language spoken on the podcast "
language: Language

" Podcast's Content Type (Is the podcast primarily an Audio or Video Podcast) "
contentType: PodcastContentType

" Boolean for if the podcast contain's explicit content "
isExplicitContent: Boolean

" Copyright details for the podcast "
copyright: String

" The podcast's website "
websiteUrl: String

" Url for the podcast's RSS feed "
rssUrl: String

" Name to use for contacting the owner of this podcast feed "
rssOwnerName: String

" Email to use for contacting the owner of this podcast feed "
rssOwnerPublicEmail: String

" Name of the Podcast creator (the podcast creator and the owner of the podcast feed can be different)"
authorName: String

" Details on how often the RSS feed is checked for new episodes "
feedRefreshDetails: FeedRefreshDetails

" Whether the podcast is being automatically transcribed by our API "
taddyTranscribeStatus: PodcastSeriesTranscriptionStatus

" The popularity of the podcast. ex) TOP_200, TOP_1000 etc "
popularityRank: PopularityRank

" People listed on the podcast including thier roles (Hosts, Guests, etc) "
persons: [Person]

" If the podcast is finished / complete "
isCompleted: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```


#### 

[](#de4dd2298a614c3e9ee8e71fb56690e5 "Referenced types in this document:")Referenced types in this document:

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[PodcastSeriesType](https://taddy.org/developers/podcast-api/podcast-series-type)

[PodcastContentType](https://taddy.org/developers/podcast-api/podcast-content-type)

[PodcastSeriesTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastseries-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[SortOrder](https://taddy.org/developers/podcast-api/sort-order)

[iTunesInfo](https://taddy.org/developers/podcast-api/itunesinfo)

[FeedRefreshDetails](https://taddy.org/developers/podcast-api/feed-refresh-details)