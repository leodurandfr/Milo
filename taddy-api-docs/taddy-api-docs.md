# Taddy API

## What is Taddy Podcast API?

Taddy API is what we wished existed when we built our first podcast app. Our API simplifies the process of building a great podcast app by:

1. Providing an [up-to-date directory](https://taddy.org/developers/podcast-api) of 4 million podcasts and 180 million episodes, with more being added daily.
2. Constantly checking RSS feeds for new or updated content. We make sure our API is always up-to-date.
3. Blazing fast [search](https://taddy.org/developers/podcast-api/search) on all 4 million podcasts and 180 million episodes.
4. We send you a [webhook notification](https://taddy.org/developers/podcast-api/webhooks) whenever a new podcast is released, updated, or deleted.

<aside>
ðŸ’¡ Quickstart guide to our APIs:

[Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api)

[Podcast API](https://taddy.org/developers/podcast-api)

[Webcomics API](https://taddy.org/developers/comics-api)

[Creator API](https://taddy.org/developers/creator-api)

[Webhooks](https://taddy.org/developers/webhooks)

</aside>

## [Sign Up for Free âž¤](https://taddy.org/signup/developers)

## Why should you use Taddyâ€™s API?

**Save Time:** Taddyâ€™s focus is on crawling millions of feeds every day. Weâ€™ve taken the time to deal with the edge cases for missing or corrupt data and to integrate with standards like [WebSub](https://en.wikipedia.org/wiki/WebSub). It means you can focus your time on making your application great and not on maintaining an up-to-date directory of all active podcast feeds and crawling those feeds for new episodes.

**Save Money:** We have fair, affordable [pricing](https://taddy.org/developers/pricing) starting at $75/month. Even if you ignore the time or labour costs to build and maintain a service like Taddy, you could easily spend more running your own instance of Taddy. 

## Our API is trusted by apps like:

![https://podyssey.fm](https://podyssey.imgix.net/website/images/badges/podyssey_logo_horizontal.png?h=100)

https://podyssey.fm

A community of podcast lovers recommending the best podcasts & episodes

![[https://sond.com](https://sond.com/)](https://ax0.taddy.org/blog/developers/sond-logo.png)

[https://sond.com](https://sond.com/)

A sleep companion 

![[https://www.podcastkiosk.com/](https://www.podcastkiosk.com/)](https://ax0.taddy.org/general/podcast-kiosk.jpg)

[https://www.podcastkiosk.com/](https://www.podcastkiosk.com/)

A podcast player with clean design

![https://inkverse.co](https://ax0.taddy.org/inkverse/inkverse-brandmark-transparent.png)

https://inkverse.co

Webtoons Reader

![[https://www.overhaul.fm/](https://auditory.cc)](https://ax0.taddy.org/general/overhaul-fm.jpg)

[https://www.overhaul.fm/](https://auditory.cc)

Discover a new podcast

![[https://www.unitus.ai/](https://www.unitus.ai/)](https://ax0.taddy.org/general/unitus-logo.jpg)

[https://www.unitus.ai/](https://www.unitus.ai/)

Helping SDRs & AEs research faster.

## [Sign Up for Free âž¤](https://taddy.org/signup/developers)

More Links: 

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Blog](https://taddy.org/blog)

[Pricing](https://taddy.org/developers/pricing)

[Changelog](https://taddy.org/developers/changelog)

[OAuth Instructions](https://taddy.org/developers/instructions)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

[Why we Built Taddy](https://taddy.org/developers/why-we-built-taddy)

---

# Changelog

Recent updates to Taddy API. 

## Improvements to Transcripts!

September 12, 2025

- Episode transcripts are available to all paid user (Pro and Business)
- 100 Episode Transcripts/month are included on the Pro Plan
- 2000 EpisodeÂ Transcripts/month are includedÂ on the Business Plan
- We've simplified our credit system for transcripts. Previously, we had 2 types of credits for transcripts (one for transcripts that Taddy API had already generated and one for transcripts we hadn't). Now, we combined both into one.
- Weâ€™ve slightly increased the price for new ProÂ + Business users. All current paid users are grandfathered into your current pricing (thank you for being an early adopter of Taddy API).

## Marketing Updates

August 20, 2025

Check out our new Taddy API [home page](https://taddy.org/developers), [pricing](https://taddy.org/developers/pricing) page, and [Why we Built Taddy](https://taddy.org/developers/why-we-built-taddy) page. 

## Improvement to getEpisode

May 21, 2025

We added `seriesUuidForLookup` to the [getEpisode](https://taddy.org/developers/podcast-api/get-podcast-episode) query. 

This is useful because an episodeâ€™s guid is not guaranteed to be unique (two different podcasts can use the same guid for episodes in their podcast). With`seriesUuidForLookup`, if you know the podcastâ€™s uuid and an episodeâ€™s guid, you can get exactly the episode you want.

## Monthly Snapshots Added

May 1, 2025

If you pay for the one-time data export and have a Taddy Business Plan, you get access to a snapshot of all podcast data from our database on the 1st of every month. See [here](https://taddy.org/developers/podcast-api/bulk-download-podcastseries) for doc on how to access these monthly snapshots.

## 2 New Queries Added

April 22, 2025

- Added [getPopularContent](https://taddy.org/developers/podcast-api/most-popular-podcasts). It lists the most popular podcasts and allows you to filter by language and genres.
- Added [getLatestPodcastEpisodes](https://taddy.org/developers/podcast-api/latest-episodes-from-multiple-podcasts). It allows you to get the most recent episodes for multiple podcasts (ie: Makes it easy for you to build a Podcast Feed)
- Added the property `popularityRank` on PodcastSeries. ex) if a podcast is in the TOP_200, TOP_1000 podcast etc.

## New Search API

March 4, 2025

Weâ€™ve made search faster & improved the quality of search results returned. See [search](https://taddy.org/developers/podcast-api/search) for documentation. 

**Note:** The old search query (`searchForTerm`) now uses the new search engineÂ behindÂ the scenes, so you don't have to update your queries to see improved results. However, the new search query (`search`) does have some new features and improved syntax. So, try it out!

## Links from description

January 8, 2025

Added the GraphQL property `descriptionLinks` to [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries) and [PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode). This gives you an easy way to get all the URLs inside a podcastâ€™s or episodeâ€™s description.

## Even more transcripts

December 7, 2024

We made some updates to our transcription feature:

- Weâ€™ve updated the transcription model to use the latest Open AI Whisper model `large-v3-turbo`
- We stopped using pyannote-audio for speaker diarization and speaker identification. We found it hard to offer high-quality speaker diarization and identification for cheap. It resulted in users not using this feature. We try very hard not to deprecate any feature we build but we decided to stop using pyannote-audio and double-down on what people are currently using: high-quality transcripts.
- Weâ€™ve increased the number of transcripts on Taddy going from all episodes of the top 650 podcasts to all episodes from the top 2,000 podcasts in our directory. We plan to increase this even more in the next couple of months after we finish transcribing the back catalogue of these episodes.

## Updates to Queues

October 22, 2024

To ensure we are checking RSS feeds as efficiently as possible, we did an audit of feeds in our directory. 

This is the breakdown of how many podcasts are in each queue as of today:

`WEBSUB`  - 115,459

`HIGH` - 6,638

`MEDIUM` - 61,852

`REGULAR` - 2,783,065

`INACTIVE` - 1,175,194 (Podcast feed still works, but no new episodes within the last year)

`LOW` - 657,736 (Podcast feed no longer works)

## Episode Transcripts launched!

September 20, 2024

We launched episode transcripts, our most requested feature.

- [Episode transcripts](https://taddy.org/developers/podcast-api/episode-transcripts) for the most popular podcasts
- On-demand transcripts for any episode in our directory

Email `danny@taddy.org` if you have a feature request.

---

# Creator API

An API for creator information for any comic that conforms to the [**SSS specification**](https://3s-docs.org). 

All creators that upload their comics using [Taddy Ink](https://taddy.org) are automatically added to this directory, plus any creator feed that conforms to the SSS standard can also be included in this API. If you have created your own creator feed, please [add it to our directory](https://taddy.org/developers/creator-api/add-a-creator-to-taddy). 

## What can you do with the API?

- Get details on any [creator](https://taddy.org/developers/creator-api/get-creator-details).
- Blazing fast [full-text search](https://taddy.org/developers/creator-api/search) on all creators.
- Get notifications for new or updated creators through a [webhook](https://taddy.org/developers/creator-api/webhooks).
- Get details on [multiple creators](https://taddy.org/developers/creator-api/get-multiple-creators) (returns an array).
- [Add a creator](https://taddy.org/developers/creator-api/add-a-creator-to-taddy) to our directory.
- Bulk [download all creators](https://taddy.org/developers/creator-api/bulk-download-creators) from our directory.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

### API Reference:

[Creator](https://taddy.org/developers/creator-api/creator) - Details on a creator.

[CreatorContent](https://taddy.org/developers/creator-api/creator-content) - Details on every piece of content made by a creator.

[ContentRole](https://taddy.org/developers/creator-api/content-role) - Options for roles performed by the creator in making the piece of content.

[ImageVariant](https://taddy.org/developers/creator-api/imagevariant) - Options for image size / variants.

[LinkDetails](https://taddy.org/developers/creator-api/link-details) - Details on how to generate the links to a creatorâ€™s website, email, and social media.

[Country](https://taddy.org/developers/creator-api/country) - The country in which the creator is resides in or is from.

[SortOrder](https://taddy.org/developers/creator-api/sort-order) - The option to have returned items sorted by newest or oldest.

[TaddyType](https://taddy.org/developers/creator-api/taddytype) - The types of media available on Taddy.

[SearchContentType](https://taddy.org/developers/creator-api/search-content-type) - The types of media available to search for on Taddy

[SearchMatchType](https://taddy.org/developers/creator-api/search-match-by) - Choose between searching for all terms, some terms or frequency

[SearchSortOrder](https://taddy.org/developers/creator-api/search-sort-by) - The option to have search results be prioritized by exact term matching or popularity

[SearchRankingDetails](https://taddy.org/developers/creator-api/search-query-ranking-details) - Ranking details for each search result

[SearchResponseDetails](https://taddy.org/developers/creator-api/search-query-response-details) - Additional details for search request

[FeedRefreshDetails](https://taddy.org/developers/creator-api/feed-refresh-details) - Details on how often Taddy checks a feed for updates.

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Webcomics API](https://taddy.org/developers/comics-api)

[Podcast API](https://taddy.org/developers/podcast-api)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Changelog](https://taddy.org/developers/changelog)

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Add a Creator Feed to Taddyâ€™s Directory

When you add your creator feed to Taddyâ€™s Directory, we notify all supported apps that a new creator feed has been released.

## How to add a creator to Taddy

1. Use the REST endpoint: **** **https://taddy.org/feeds/add/creator** (POST)
2. Add your `X-USER-ID` and `X-API-KEY` values to the headers (see [Intro Guide](https://taddy.org/developers/intro-to-taddy-graphql-api) for instructions on how to get your API Key and User ID)

```jsx
{
	"X-USER-ID": 7,
	"X-API-KEY": "96c5007c18858e86d..."
}
```

 3. Add `sssUrl` (required) to the data being passed with the POST request.

```jsx
{
	"sssUrl": "https://example.com/feeds/sss/creator/123",
}
```

You will get back one of the following responses:

**Success:** On a successful response you get back a `status` and the `uuid` for the added creator.

```jsx
{
	"status": "success",
	"uuid":  "d682a935-ad2d-46ee-a0ac-139198b83bcc"
}
```

**Error:** When we encounter an error adding a new creator to Taddy. We send back a string with the error message.

```jsx
`Could not add SSS Feed to Taddy`
```

### Example:

```jsx
curl -X POST \
https://taddy.org/feeds/add/creator \
-H "Content-Type: application/json" \
-H "X-USER-ID: 7" \
-H "X-API-KEY: 96c5007c18858e86d..." \
-d '{ "sssUrl": "https://example.com/feeds/sss/creator/123" }'
```

**Notes:**

- Once you send a request: 1) We immediately add the creator feed to Taddyâ€™s Directory and 2) Queue up a job to parse the feed. This second step may take up to 15 mins but is usually much sooner.
- Trying to re-add a creator feed that has already been added to our directory will return a successful response and return the uuid for the already added creator feed.
- If you are run into any issues, please contact `danny@taddy.org`.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Bulk download all creators

You can easily download basic information on all creators from our directory. 

## Download Data

| **Date** | **URL** | **Size** |
| --- | --- | --- |
| Apr 08, 2024 | [https://archive.org/details/creator-2024-04-09T03-19-27.353Z.txt](https://archive.org/details/creator-2024-04-09T03-19-27.353Z.txt) | 1 MB |

## Format of File

A txt file with every new line of the file being information on a creator. Here is the information you get for every creator:

| **Property** | **Type** | **Description** |
| --- | --- | --- |
| `uuid` | Uuid | Unique id for content on Taddy |
| `name` | String | Name of creator |
| `createdAt` | Date | Date when the creator feed was added to Taddy |
| `datePublished` | Date | Date when the creator feed was published |
| `sssUrl` | String | SSS feed for the creator |
| `hash` | String | A hash of all creator details. You know if you have a different hash, the creator details have updated since the last time you checked, use our API to get up-to-date details. |
| `contentHash` | String | A hash of all content details. You know if you have a different contentHash, there is new or updated content details for the creator since the last time you checked, use our API to get up-to-date details. |

---

# ContentRole

Options for roles performed by the creator in making a piece of content. Follows format: TYPE_ROLE_SUBROLE

```jsx
enum ContentRole {
  COMICSERIES_ARTIST
  COMICSERIES_ARTIST_PENCILER
  COMICSERIES_ARTIST_INKER
  COMICSERIES_ARTIST_COLORIST
  COMICSERIES_ARTIST_LETTERER
  COMICSERIES_WRITER
  COMICSERIES_PRODUCER
  COMICSERIES_EDITOR
  COMICSERIES_TRANSLATOR
}
```

---

# Country

A list of all possible Countries. Conforms to [ISO 3166-1](https://en.wikipedia.org/wiki/ISO_3166-1)

```jsx
enum Country {
  AFGHANISTAN
  ALAND_ISLANDS
  ALBANIA
  ALGERIA
  AMERICAN_SAMOA
  ANDORRA
  ANGOLA
  ANGUILLA
  ANTARCTICA
  ANTIGUA_AND_BARBUDA
  ARGENTINA
  ARMENIA
  ARUBA
  AUSTRALIA
  AUSTRIA
  AZERBAIJAN
  BAHAMAS
  BAHRAIN
  BANGLADESH
  BARBADOS
  BELARUS
  BELGIUM
  BELIZE
  BENIN
  BERMUDA
  BHUTAN
  BOLIVIA_PLURINATIONAL_STATE_OF
  BONAIRE_SINT_EUSTATIUS_AND_SABA
  BOSNIA_AND_HERZEGOVINA
  BOTSWANA
  BOUVET_ISLAND
  BRAZIL
  BRITISH_INDIAN_OCEAN_TERRITORY_THE
  BRUNEI_DARUSSALAM
  BULGARIA
  BURKINA_FASO
  BURUNDI
  CABO_VERDE
  CAMBODIA
  CAMEROON
  CANADA
  CAYMAN_ISLANDS
  CENTRAL_AFRICAN_REPUBLIC
  CHAD
  CHILE
  CHINA
  CHRISTMAS_ISLAND
  COCOS_KEELING_ISLANDS
  COLOMBIA
  COMOROS
  CONGO
  CONGO_THE_DEMOCRATIC_REPUBLIC_OF
  COOK_ISLANDS
  COSTA_RICA
  COTE_D_IVOIRE
  CROATIA
  CUBA
  CURACAO
  CYPRUS
  CZECHIA
  DENMARK
  DJIBOUTI
  DOMINICA
  DOMINICAN_REPUBLIC
  ECUADOR
  EGYPT
  EL_SALVADOR
  EQUATORIAL_GUINEA
  ERITREA
  ESTONIA
  ESWATINI
  ETHIOPIA
  FALKLAND_ISLANDS_THE_MALVINAS
  FAROE_ISLANDS
  FIJI
  FINLAND
  FRANCE
  FRENCH_GUIANA
  FRENCH_POLYNESIA
  FRENCH_SOUTHERN_TERRITORIES
  GABON
  GAMBIA
  GEORGIA
  GERMANY
  GHANA
  GIBRALTAR
  GREECE
  GREENLAND
  GRENADA
  GUADELOUPE
  GUAM
  GUATEMALA
  GUERNSEY
  GUINEA
  GUINEA_BISSAU
  GUYANA
  HAITI
  HEARD_ISLAND_AND_MCDONALD_ISLANDS
  HOLY_SEE
  HONDURAS
  HONG_KONG
  HUNGARY
  ICELAND
  INDIA
  INDONESIA
  IRAN
  IRAQ
  IRELAND
  ISLE_OF_MAN
  ISRAEL
  ITALY
  JAMAICA
  JAPAN
  JERSEY
  JORDAN
  KAZAKHSTAN
  KENYA
  KIRIBATI
  KOREA_NORTH
  KOREA_SOUTH
  KUWAIT
  KYRGYZSTAN
  LAO_PEOPLES_DEMOCRATIC_REPUBLIC_THE
  LATVIA
  LEBANON
  LESOTHO
  LIBERIA
  LIBYA
  LIECHTENSTEIN
  LITHUANIA
  LUXEMBOURG
  MACAO
  MADAGASCAR
  MALAWI
  MALAYSIA
  MALDIVES
  MALI
  MALTA
  MARSHALL_ISLANDS
  MARTINIQUE
  MAURITANIA
  MAURITIUS
  MAYOTTE
  MEXICO
  MICRONESIA_FEDERATED_STATES
  MINOR_OUTLYING_ISLANDS_US
  MOLDOVA_THE_REPUBLIC
  MONACO
  MONGOLIA
  MONTENEGRO
  MONTSERRAT
  MOROCCO
  MOZAMBIQUE
  MYANMAR
  NAMIBIA
  NAURU
  NEPAL
  NETHERLANDS
  NEW_CALEDONIA
  NEW_ZEALAND
  NICARAGUA
  NIGER
  NIGERIA
  NIUE
  NORFOLK_ISLAND
  NORTH_MACEDONIA
  NORTHERN_MARIANA_ISLANDS
  NORWAY
  OMAN
  PAKISTAN
  PALAU
  PALESTINE_STATE
  PANAMA
  PAPUA_NEW_GUINEA
  PARAGUAY
  PERU
  PHILIPPINES
  PITCAIRN
  POLAND
  PORTUGAL
  PUERTO_RICO
  QATAR
  REUNION
  ROMANIA
  RUSSIA
  RWANDA
  SAINT_BARTHELEMY
  SAINT_HELENA_ASCENSION_AND_TRISTAN_DA_CUNHA
  SAINT_KITTS_AND_NEVIS
  SAINT_LUCIA
  SAINT_MARTIN_FRENCH_PART
  SAINT_PIERRE_AND_MIQUELON
  SAINT_VINCENT_AND_THE_GRENADINES
  SAMOA
  SAN_MARINO
  SAO_TOME_AND_PRINCIPE
  SAUDI_ARABIA
  SENEGAL
  SERBIA
  SEYCHELLES
  SIERRA_LEONE
  SINGAPORE
  SINT_MAARTEN_DUTCH_PART
  SLOVAKIA
  SLOVENIA
  SOLOMON_ISLANDS
  SOMALIA
  SOUTH_AFRICA
  SOUTH_GEORGIA_AND_THE_SOUTH_SANDWICH_ISLANDS
  SOUTH_SUDAN
  SPAIN
  SRI_LANKA
  SUDAN
  SURINAME
  SVALBARD_AND_JAN_MAYEN
  SWEDEN
  SWITZERLAND
  SYRIA
  TAIWAN
  TAJIKISTAN
  TANZANIA
  THAILAND
  TIMOR_LESTE
  TOGO
  TOKELAU
  TONGA
  TRINIDAD_AND_TOBAGO
  TUNISIA
  TURKEY
  TURKMENISTAN
  TURKS_AND_CAICOS_ISLANDS
  TUVALU
  UGANDA
  UKRAINE
  UNITED_ARAB_EMIRATES
  UNITED_KINGDOM
  UNITED_STATES_OF_AMERICA
  URUGUAY
  UZBEKISTAN
  VANUATU
  VENEZUELA
  VIETNAM
  VIRGIN_ISLANDS_BRITISH
  VIRGIN_ISLANDS_US
  WALLIS_AND_FUTUNA
  WESTERN_SAHARA
  YEMEN
  ZAMBIA
  ZIMBABWE
}
```

---

# Creator

GraphQL Type for a Creator. 

```jsx
" Unique identifier for this creator "
uuid: ID

" Date when the creator feed was published (Epoch time in seconds) "
datePublished: Int

" The name of the creator "
name: String

" A short bio on the creator "
bio: String

" The avatar image for the creator"
avatarImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" Stringified JSON details for the avatar image. Convert to JSON to use."
avatarImageAsString: String

" A hash of all creator details. It may be useful for you to save this property in your database and compare it to know if any details have updated since the last time you checked "
hash: String

" A hash of the details for all different content a creator makes. It may be useful for you to save this property in your database and compare it to know if there are any new or updated content since the last time you checked "
contentHash: String

" A list of content for this creator "
content(
  " Sort order for the results. Default is LATEST (newest first), another option is OLDEST (oldest first) "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000. "
  page: Int,

  " (Optional) Return up to this number of results. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int
): [CreatorContent]

" The total number of content from this creator "
totalContentCount: Int

" Tags for the creator "
tags: [String]

" Stringified JSON details for the links to creator's website, email, and social media. Convert to JSON to use."
linksAsString: String

" Links to creator's website, email, and social media "
links: [LinkDetails]

" The country in which the creator is resides in or is from "
country: Country

" Url for the creator's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

### Referenced types in this document:

[Country](https://taddy.org/developers/creator-api/country)

[ContentRole](https://taddy.org/developers/creator-api/content-role)

[TaddyType](https://taddy.org/developers/creator-api/taddytype)

[FeedRefreshDetails](https://taddy.org/developers/creator-api/feed-refresh-details)

---

# CreatorContent

A piece of content made by a creator. Creator feeds returns an array of content (this allows you to see every piece of content made by them and the roles they performed in making them)

```jsx
" Unique identifier for the creator "
creatorUuid: ID

" Unique identifier for the content "
contentUuid: ID

" Content type "
contentType: TaddyType

" Roles for the creator for this content "
roles: [ContentRole]
```

### Referenced types in this document:

[TaddyType](https://taddy.org/developers/creator-api/taddytype)

[ContentRole](https://taddy.org/developers/creator-api/content-role)

---

# FeedRefreshDetails

Options for how often Taddy checks a feed for updates.

```jsx
enum FeedRefreshPriority {
  WEBSUB
  HIGH
  REGULAR
  LOW
  INACTIVE
  NEVER
}
```

`WEBSUB` - Feed is checked immediately for updates (within 30 mins, usually much sooner). Taddy gets notified of a change to the SSS feed via a [WebSub](https://en.wikipedia.org/wiki/WebSub) notification.

`HIGH` - Feed is checked 4 hours

`REGULAR` - Feed is checked every day

`LOW` - Feed is checked once a week

`INACTIVE` - Feed is checked once a week

`NEVER` - Feed is no longer checked for updates. (Only used in the rare circumstance a podcast violates our [content policy](https://taddy.org/terms-of-service/content-policy)).

If a feed has been set as LOW, INACTIVE, or NEVER priority, you can check the reason why: 

```jsx
enum FeedRefreshPriorityReason {
  INACTIVE_FOR_OVER_1_YEAR
  DUPLICATE_FEED
  ERROR_PARSING_FEED
  FEED_URL_NOT_WORKING
  VIOLATES_TADDY_DISTRIBUTION_POLICY
}
```

`INACTIVE_FOR_OVER_1_YEAR` - Feed has not had any updates in the last 12 months

`DUPLICATE_FEED` - There is another feed in our database that links to the same content

`ERROR_PARSING_FEED` - Error parsing document when trying to check the feed for new updates

`FEED_URL_NOT_WORKING` - Error when trying to load the feed url (404 error, etc)

`VIOLATES_TADDY_DISTRIBUTION_POLICY` - The feed has been reviewed by a Taddy staff member and is in violation of [our distribution policy](https://taddy.org/terms-of-service/distribution-policy).

## How to check the FeedRefreshPriority for a comic

If you would like to know how often a feed is being checked for updates, check its `feedRefreshDetails` property.

```jsx
type FeedRefreshDetails {
  " Taddy's unique identifier "
  uuid: ID

  " How often a feed is refreshed "
  priority: FeedRefreshPriority

  " The reason why feed has a LOW, INACTIVE, or NEVER priority "
  priorityReason: FeedRefreshPriorityReason

  " Date when the feed was refreshed last (Epoch time in seconds) "
  dateLastRefreshed: Int

  " Websub Details (if available)"
  websubDetails: WebsubDetails
}
```

Details for the WebSub hub that notifies Taddy of any changes on the feed.

```jsx
" Websub Details "
type WebsubDetails {
  " Taddy's unique identifier "
  uuid: ID

  " The feed url for the websub "
  topicUrl: String

  " The url for the hub where you get the websub notification "
  websubHubUrl: String

  " If the websub notification is currently active "
  isVerified: Boolean
}
```

---

# Get creator details

Use getCreator to get details on a specific creator. 

## Example:

```jsx
{
  getCreator(uuid:"5a4977e8-b0da-4cc7-a516-16a0fb6973d8"){
    uuid
    name
    bio
    hash
    contentHash
  	avatarImageUrl
    tags
    country
    links{
      type
      url
    }
		content{
      hash
      creatorUuid
      contentUuid
      contentType
      roles
    }
    sssUrl
    sssOwnerName
    sssOwnerPublicEmail
    copyright
    isBlocked
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getCreator": {
      "uuid": "5a4977e8-b0da-4cc7-a516-16a0fb6973d8",
      "name": "Zachary Morris",
      "bio": null,
      "hash": "2b80ecea81312717bcbe1872aa80c52ede308993819fdbafa2027ee123ed565c",
      "contentHash": "9d982608495697f438e0e31d768fdb297d1e6984f013842b2ccc982562c343a6",
      "avatarImageUrl": "https://ax1.taddy.org/5a4977e8-b0da-4cc7-a516-16a0fb6973d8/c414e9a8-8d27-4280-9e43-a2e5445a9626/avatar-sm.webp",
      "tags": [
        "zachmorris",
        "willdrawforfood1",
        "cartoonist"
      ],
      "country": "UNITED_STATES_OF_AMERICA",
      "links": [
        {
          "type": "PATREON",
          "url": "https://patreon.com/zachmorris"
        },
        {
          "type": "TWITTER",
          "url": "https://twitter.com/toonzach"
        }
      ],
      "content": [
        {
          "hash": "f2c93ef053a62b05da1092cb39e41726da9fbbe436888b4d53dd6c9067839013",
          "creatorUuid": "5a4977e8-b0da-4cc7-a516-16a0fb6973d8",
          "contentUuid": "71113968-45a2-4c30-b770-655b57ae0de6",
          "contentType": "COMICSERIES",
          "roles": [
            "COMICSERIES_ARTIST",
            "COMICSERIES_WRITER"
          ]
        }
      ],
      "sssUrl": "https://taddy.org/feeds/sss/creator/5a4977e8-b0da-4cc7-a516-16a0fb6973d8",
      "sssOwnerName": null,
      "sssOwnerPublicEmail": null,
      "copyright": "Copyright notice available at http://3s-docs.org/creator-friendly-copyright-notice",
      "isBlocked": null
    }
  }
}
```

## Query Breakdown:

For getCreator, you can get details on any comic using one of the following properties:

```jsx
" Unique identifier for a creator "
uuid: ID

" A podcast's RSS Feed "
sssUrl: String

" The name of a creator. Note: Multiple creators can have the exact same name, in that case we always try to return the most popular creator (based on the infomation we have)"
name: String
```

The response you get back is a of [Creator](https://taddy.org/developers/creator-api/creator). That means you can return any of the following details:

```jsx
" Unique identifier for this creator "
uuid: ID

" Date when the creator feed was published (Epoch time in seconds) "
datePublished: Int

" The name of the creator "
name: String

" A short bio on the creator "
bio: String

" The avatar image for the creator"
avatarImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" Stringified JSON details for the avatar image. Convert to JSON to use."
avatarImageAsString: String

" A hash of all creator details. It may be useful for you to save this property in your database and compare it to know if any details have updated since the last time you checked "
hash: String

" A hash of the details for all different content a creator makes. It may be useful for you to save this property in your database and compare it to know if there are any new or updated content since the last time you checked "
contentHash: String

" A list of content for this creator "
content(
  " Sort order for the results. Default is LATEST (newest first), another option is OLDEST (oldest first) "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000. "
  page: Int,

  " (Optional) Return up to this number of results. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int
): [CreatorContent]

" The total number of content from this creator "
totalContentCount: Int

" Tags for the creator "
tags: [String]

" Stringified JSON details for the links to creator's website, email, and social media. Convert to JSON to use."
linksAsString: String

" Links to creator's website, email, and social media "
links: [LinkDetails]

" The country in which the creator is resides in or is from "
country: Country

" Url for the creator's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

### Referenced types in this document:

[Creator](https://taddy.org/developers/creator-api/creator)

[Country](https://taddy.org/developers/creator-api/country)

[ContentRole](https://taddy.org/developers/creator-api/content-role)

[TaddyType](https://taddy.org/developers/creator-api/taddytype)

[LinkDetails](https://taddy.org/developers/creator-api/link-details)

[FeedRefreshDetails](https://taddy.org/developers/creator-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get details on creators

Use getMultipleCreators to get details on multiple creators. There is a max limit of 25 creators per request.

## Example:

```jsx
{
  getMultipleCreators(uuids:["5a4977e8-b0da-4cc7-a516-16a0fb6973d8"]){
    uuid
    name
    bio
    hash
    contentHash
    avatarImageUrl
    tags
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getMultipleComicSeries": [
      "uuid": "5a4977e8-b0da-4cc7-a516-16a0fb6973d8",
      "name": "Zachary Morris",
      "bio": null,
      "hash": "2b80ecea81312717bcbe1872aa80c52ede308993819fdbafa2027ee123ed565c",
      "contentHash": "9d982608495697f438e0e31d768fdb297d1e6984f013842b2ccc982562c343a6",
      "avatarImageUrl": "https://ax1.taddy.org/5a4977e8-b0da-4cc7-a516-16a0fb6973d8/c414e9a8-8d27-4280-9e43-a2e5445a9626/avatar-sm.webp",
      "tags": [
        "zachmorris",
        "willdrawforfood1",
        "cartoonist"
      ],
    ]
  }
}
```

## Query Breakdown:

For getMultipleCreators, you can get details on comics by passing in an array of uuids. 

```jsx
" An Array of taddy's unique identifier (uuid). Max 25 IDs allowed "
uuids: [ID]
```

The response you get back is an array of [Creator](https://taddy.org/developers/creator-api/creator).

```jsx
" Unique identifier for this creator "
uuid: ID

" Date when the creator feed was published (Epoch time in seconds) "
datePublished: Int

" The name of the creator "
name: String

" A short bio on the creator "
bio: String

" The avatar image for the creator"
avatarImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" Stringified JSON details for the avatar image. Convert to JSON to use."
avatarImageAsString: String

" A hash of all creator details. It may be useful for you to save this property in your database and compare it to know if any details have updated since the last time you checked "
hash: String

" A hash of the details for all different content a creator makes. It may be useful for you to save this property in your database and compare it to know if there are any new or updated content since the last time you checked "
contentHash: String

" A list of content for this creator "
content(
  " Sort order for the results. Default is LATEST (newest first), another option is OLDEST (oldest first) "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000. "
  page: Int,

  " (Optional) Return up to this number of results. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int
): [CreatorContent]

" The total number of content from this creator "
totalContentCount: Int

" Tags for the creator "
tags: [String]

" Stringified JSON details for the links to creator's website, email, and social media. Convert to JSON to use."
linksAsString: String

" Links to creator's website, email, and social media "
links: [LinkDetails]

" The country in which the creator is resides in or is from "
country: Country

" Url for the creator's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

### Referenced types in this document:

[Creator](https://taddy.org/developers/creator-api/creator)

[Country](https://taddy.org/developers/creator-api/country)

[ContentRole](https://taddy.org/developers/creator-api/content-role)

[TaddyType](https://taddy.org/developers/creator-api/taddytype)

[LinkDetails](https://taddy.org/developers/creator-api/link-details)

[FeedRefreshDetails](https://taddy.org/developers/creator-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# ImageVariant

Possible Image variants available:

```jsx
enum ImageVariant {
  SMALL
  MEDIUM
  LARGE
}
```

---

# LinkDetails

Link to a creatorâ€™s website, email, or social media account. It is broken down into `type` and the `url`

```jsx
" The type of link "
type: LinkType

" The url for the link "
url: String
```

## LinkType Options

```jsx
enum LinkType {
  INSTAGRAM
  YOUTUBE
  TIKTOK
  PATREON
  KO_FI
  EMAIL
  TWITTER
  MASTODON
  FACEBOOK
  WEBSITE
  MERCH_STORE
  LINKTREE
  TWITCH
  SNAPCHAT
  REDDIT
  DISCORD
  TELEGRAM
  ETSY
  PINTEREST
  TUMBLR
  SPOTIFY
  SOUNDCLOUD
  BANDCAMP
  VIMEO
  WECHAT
  WHATSAPP
}
```

**Note**: Please read the SSS documentation for [LinkDetails](https://3s-docs.org/linkdetails) . For security reasons, we assume the base_url for most of the LinkTypes above.

---

# Search for creators

Use search to search for a creator. 

Be sure to include `filterForTypes: CREATOR` as an argument (By default  search does not include searching for creators)

## Example:

1. Searching for creators that include the term â€œZacharyâ€ in their name or bio.

```jsx
{
  search(term:"Zachary", filterForTypes:CREATOR){
    searchId
    creators{
      uuid
      sssUrl
      name
      bio
    }
  }
}
```

2. Searching for creators using the term â€œZacharyâ€ and filter only those who make content in Canada

```jsx
{
  search(term:"Zachary", filterForTypes:CREATOR, filterForCountries:UNITED_STATES_OF_AMERICA){
    searchId
    creators{
      uuid
      sssUrl
      name
      bio
    }
  }
}
```

## Query Breakdown:

For search, you can search for creators using theses properties:

```jsx
" The term you are searching for "
term: String

" (Optional) Allows for pagination. Default is 1 (ie: page 1 of the results). Max value is 20. "
page: Int

" (Optional) The number of results per page. Default is 10. Max value is 25 (ie: that max results you can return in one query in 25) "
limitPerPage: Int

" (Optional) Filter for certain types of content. Default is PODCASTSERIES. Possible values are PODCASTSERIES, PODCASTEPISODE, COMICSERIES, CREATOR "
filterForTypes: [SearchContentType]

" (Optional) Filter for only content made in certain countries "
filterForCountries: [Country]

" (Optional) Filter for only content made in certain languages "
filterForLanguages: [Language]

" (Optional) Filter for only content from certain genres "
filterForGenres: [Genre]

" (Optional) Filter for results only from certain series "
filterForSeriesUuids: [ID]

" (Optional) Filter for results that are not from certain series "
filterForNotInSeriesUuids: [ID]

" (Optional) Filter for results that are published after a certain date (Epoch time in seconds)"
filterForPublishedAfter: Int

" (Optional) Filter for only content that includes a certain tag. Tags are case sensitive and must be exact matches. This filter is only for COMICSERIES & CREATOR and will return an empty array for any other type "
filterForTags: [String]

" (Optional) Filter for only creators with a specific role in creating the content. This filter is only for CREATOR and will return an empty array for any other type "
filterForContentRoles: [ContentRole]

" (Optional) Choose how the results are sorted. Default is sort by EXACTNESS. Possible values are EXACTNESS and POPULARITY. "
sortBy: SearchSortOrder

" (Optional) Choose which results are matched as valid search results. Default is MOST_TERMS. Possible values are MOST_TERMS, ALL_TERMS, FREQUENCY. If you search has multiple terms, FREQUENCY gives more weight to the terms that appear less frequently in results "
matchBy: SearchMatchType

" (Optional) Choose to only return safe (not explicit) content or all content. Default is false (include everything, including explicit content) "
isSafeMode: Boolean
```

The response you get back includes an array of Creators that match your search term.

```jsx
" Identifier for the search query being sent "
searchId: ID!

" A list of Creator items "
creator: [Creator]

" Ranking information for each search result "
rankingDetails: [SearchRankingDetails]

" Additional information on the search results (Total # of results, pages, etc) "
responseDetails: [SearchResponseDetails]
```

### Referenced types in this document:

[Creator](https://taddy.org/developers/creator-api/creator)

[Country](https://taddy.org/developers/creator-api/country)

[ContentRole](https://taddy.org/developers/creator-api/content-role)

[TaddyType](https://taddy.org/developers/creator-api/taddytype)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# SearchContentType

The different types of content you can search for using the [`search`](https://taddy.org/developers/podcast-api/search) query on Taddy.

```jsx
enum SearchContentType {
  PODCASTSERIES
  PODCASTEPISODE
  COMICSERIES
  CREATOR
}
```

---

# SearchMatchType

Choose how your search matches contentâ€”by exact phrase, all terms, or most terms.

```jsx
enum SearchMatchType {
  EXACT_PHRASE
  MOST_TERMS
  ALL_TERMS
}
```

`EXACT_PHRASE`Â â€“ Returns results that contain the exact phrase only. This is ideal for searching names, e.g.,Â "Peter Smith", as results will only include instances where the full and exact phrase "Peter Smith" appears.

`ALL_TERMS`Â â€“ Returns results that contain all the provided search terms, regardless of the order of the terms. Exact phrase matches are ranked higher.

`MOST_TERMS`Â (Default) â€“ Returns results that contain any of the search terms provided. Exact phrase matches and multiple terms matched are ranked higher.

---

# SearchRankingDetails

Ranking details (including score) about each search result.

```jsx
type SearchRankingDetails {
  " Identifier for the search query being sent "
  id: ID!

  " The UUID of the item being returned in the search results "
  uuid: ID

  " The type of item being returned in the search results "
  type: SearchContentType

  " The ranking score for the search results from 100 (exact match) to 0 (no match) "
  rankingScore: Int
}
```

---

# SearchResponseDetails

Additional details about the search request.

```jsx
type SearchResponseDetails {
  " Identifier for the search query being sent "
  id: ID!

  " The type of item being returned in the search results "
  type: SearchContentType

  " Total number of search results returned for this type "
  totalCount: Int

  " Total number of pages of results returned for this type "
  pagesCount: Int
}
```

---

# SearchSortOrder

Choose if you want search results to be prioritized by exact term matching or popularity of the content.

```jsx
enum SearchSortOrder {
  EXACTNESS
  POPULARITY
}
```

`EXACTNESS`(Default) - Search results will prioritize exact matching based on title, description, publisher name and other relevant information. 

`POPULARITY` - This parameter is ignored for creator search, and still uses exactness.

---

# SortOrder

SortOrder gives you the ability to return different items based on what option you pass in.

```jsx
enum SortOrder {
  LATEST
  OLDEST
  SEARCH
}
```

`LATEST` - Return latest (newest) first

`OLDEST` - Return oldest first

`SEARCH` - Return only issues that match the filtered `searchTerm` property (which should be passed in whenever `SEARCH` is chosen as the SortOrder).

---

# TaddyType

The different types of media available on Taddy

```jsx
enum TaddyType {
  PODCASTSERIES
  PODCASTEPISODE
	COMICSERIES
  COMICISSUE
	CREATOR
}
```

`PODCASTSERIES` - A podcast

`PODCASTEPISODE` - An episode of a podcast

`COMICSERIES` - A comic

`COMICISSUE` - An issue (episode) of a comic

`CREATOR` - A creator (that made the podcast or comic).

---

# Webhooks

**Webhooks are an incredibly useful feature of the Taddy API.** In the background, Taddy is constantly monitoring SSS feeds to know whenever they have been updated. Whenever there is new data, we send it to you via a webhook. This means you don't have to continuously check with us for new updates, we'll push those updates to you immediately.

## Example use-cases:

- Are you **building a comic app?** Get a webhook notification immediately after new comics or creator information is updated

## Setting Up a Webhook

- Open up the [**Taddy Dashboard**](https://taddy.org/dashboard), you will see a â€œSetup a New Webhookâ€ button.
- You will have to enter in:
    1. The endpoint url where you want to receive the notifications (usually on your own server)
    2. Pick which webhook events you would like to receive.

**Note:** 

- You must be on a paid plan to add a webhook to your account.
- Webhook notifications do not count against your monthly API limits ðŸ¥³.
- The endpoint url you enter needs to be one publicly accessible over the internet. (For example, 127.0.0.1 and localhost URLs will not work, since Taddy servers will not be able to contact your local computer). If youâ€™d like to test receiving these notifications locally on your computer, [Ngrok](https://ngrok.com) provides a useful & free way to do so. It provides you a public url that is mapped to your localhost.
- Your endpoint url needs to be a  `POST` endpoint
- In the Webhooks section, one of the fields given to you is the Webhook Secret. This is an optional security feature. We add this secret as the header `X-TADDY-WEBHOOK-SECRET` to all webhook notifications sent to this webhook. Because your endpoint url is publicly accessible and if you dont share this secret with anyone else, you can be confident that any data you receive on your endpoint is from us and can be trusted.
- [Listen Up](https://heylistenup.app/) is a very simple app that receives and displays webhook events. It may be useful to you as a very easy way to see what kind of events are going to be received by your webhook endpoint.
- There is an [example project](https://github.com/taddyorg/taddy-api-example-project) which allows you to mock webhook events. It's a great way to test receiving webhook events before receiving the live events from Taddyâ€™s API.

## Webhook Events

A list of possible events for:

### Creator

| **Event** | **Description** |
| --- | --- |
| `creator.created` | Get a notification when a new creator feed has been released |
| `creator.updated` | Get a notification when creator details have been updated (e.g. name, description, avatar image, etc.) |
| `creator.deleted` | Get a notification when a comic feed has been removed from Taddy, usually at the request of the creator |
| `creator.new_content_released` | Most users won't need to subscribe to this event. When new creatorcontent have been released, you'll get this notification only once, no matter how many creatorcontent items have been added, updated, or removed. An example use case for this notification is if you're parsing the creator SSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each piece of creatorcontent that's been added or updated. |

### CreatorContent

| **Event** | **Description** |
| --- | --- |
| `creatorcontent.created` | Get a notification when the creator has released a new type of content (along with their role in making the content). ex) Creator had made a ComicSeries and had the roles COMICSERIES_ARTIST & COMICSERIES_WRITER on it. |
| `creatorcontent.updated` | Get a notification when a details around the the role they performed in creating the content are updated. |
| `creatorcontent.deleted` | Get a notification when a creator has removed a type of content from their content feed. |

## What does a webhook event look like?

A webhook event is made up of:  `uuid`, `taddyType`, `action`, `timestamp`, `data`.

### Example:

```jsx
// EXAMPLE WEBHOOK EVENT for Creator event
{
  uuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
  taddyType: 'creator',
  action: 'created',
  timestamp: 1684448992,
  data: {
    uuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    name: 'Zachary Morris',
    bio: null,
    hash: '2b80ecea81312717bcbe1872aa80c52ede308993819fdbafa2027ee123ed565c',
    contentHash: '9d982608495697f438e0e31d768fdb297d1e6984f013842b2ccc982562c343a6',
    avatarImageAsString: '{"base_url":"https://ax1.taddy.org/5a4977e8-b0da-4cc7-a516-16a0fb6973d8/c414e9a8-8d27-4280-9e43-a2e5445a9626/","avatar_sm":"avatar-sm.webp","avatar_md":"avatar-md.webp","avatar_lg":"avatar-lg.webp"}',
    tags: [ 'zachmorris', 'willdrawforfood1', 'cartoonist' ],
    country: 'UNITED_STATES_OF_AMERICA',
    linksAsString: '[{"type":"PATREON","base_url":"https://patreon.com/","value":"zachmorris"},{"type":"TWITTER","base_url":"https://twitter.com/","value":"toonzach"}]',
    sssUrl: 'https://taddy.org/feeds/sss/creator/5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    sssOwnerName: null,
    sssOwnerPublicEmail: null,
    copyright: 'Copyright notice available at http://3s-docs.org/creator-friendly-copyright-notice',
    isBlocked: null,
    totalContentCount: 1
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for CreatorContent event
{
  uuid: '3c90e534-0aba-4536-8bab-43e9dd0c4ac9',
  taddyType: 'creatorcontent',
  action: 'created',
  timestamp: 1684449090,
  data: {
    uuid: '3c90e534-0aba-4536-8bab-43e9dd0c4ac9',
    hash: 'f2c93ef053a62b05da1092cb39e41726da9fbbe436888b4d53dd6c9067839013',
    creatorUuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    contentUuid: '71113968-45a2-4c30-b770-655b57ae0de6',
    contentType: 'COMICSERIES',
    roles: [ 'COMICSERIES_ARTIST', 'COMICSERIES_WRITER' ],
    position: 0,
    contentPosition: 0
  }
}
```

---

# Intro to Taddyâ€™s API

## Getting Started with our API

To get started using our API, follow these steps:

1. Signup for a [**developer account](https://taddy.org/signup/developers).** 
2. Login to the [**dashboard**](https://taddy.org/dashboard) and get your API Key. 
3. Follow the steps in the next section to make your first API request.

[https://www.youtube.com/watch?v=kzjSjVjCMlk](https://www.youtube.com/watch?v=kzjSjVjCMlk)

## Hello World (Making your first request)

Go to [**api.taddy.org](https://api.taddy.org)** on your browser and copy & paste the below query:

```jsx
{
  getPodcastSeries(name:"The Daily"){
    uuid
    name
    itunesId
    description
    imageUrl
    totalEpisodesCount
    itunesInfo{
      uuid
      baseArtworkUrlOf(size:640)
    }
  }
}
```

**Note:** All requests to Taddy API need to be authenticated to get a successful response. Donâ€™t worry, it's easy to setup, but that means you will need to pass in your User ID and API Key in the headers. See Authentication section below for more details. 

If you are using the [api.taddy.org](http://api.taddy.org) playground there is a â€˜Headersâ€™ section at the bottom of the screen, click to open it up and add your values for the headers `X-USER-ID` and `X-API-KEY` there. (you can see it being done @ 2:28 of the [Intro to Taddy API video](https://www.youtube.com/watch?v=kzjSjVjCMlk&t=149s)).

![api-key-2.png](Intro%20to%20Taddy%E2%80%99s%20API%2044460696f04e4877997bf45ef2a992a2/api-key-2.png)

## Authentication

All requests to our API need to be authenticated. To get your API Key, log in to the [**Taddy Dashboard**](https://taddy.org/dashboard), you will see a â€œGet my API Keyâ€ button, clicking on it will reveal your API Key

![Screen Shot 2022-03-22 at 9.43.43 AM.png](https://ax0.taddy.org/blog/intro-to-taddy-api/developer-api-key.png)

You now have both your User ID and API Key and you will need to add both of them to the headers whenever you make an API request. Use the `X-USER-ID` and `X-API-KEY` keys and pass in your information as the values.

```jsx
{
	"X-USER-ID": 7,
	"X-API-KEY": "96c5007c18858e86d..."
}
```

## **Useful Docs for AI**

Using ChatGPT, Cursor, Windsurf or a similar tool to build Taddy API into your project? 

We have 2 resources that you can add into your coding IDE to give it more context about our API:

1. [**GraphQL Schema**](https://ax0.taddy.org/docs/schema.graphql) - GraphQL schema for Taddy API, including all possible queries, mutations and variables that can be passed in.
2. [**Taddy API Documentation**](https://ax0.taddy.org/docs/taddy-api-docs.md) - Full documentation for Taddy API in one big markdown file.

<aside>
ðŸ’¡ If this is your first time using GraphQL, continue reading the rest of this page to get an intro on working with GraphQL. 

If you already have some experience with GraphQL, check out our:
-  [**ðŸ“»Â Podcast API](https://taddy.org/developers/podcast-api)**  documentation, or
-  [**ðŸŽ¨ Comic API**](https://taddy.org/developers/comics-api) documentation

</aside>

## GraphQL vs REST APIs

Most developers will be familiar with REST APIs, but Taddy uses a GraphQL API. While both REST & GraphQL architectures are used to build APIs, we want to go over some of the unique benefits of GraphQL.

### Ability to write custom queries

The main difference is that with **REST** you go to multiple endpoints to get back different information on different topics. With **GraphQL** there is only 1 endpoint and you can write custom queries depending on what information you want to get back.

![Screen Shot 2022-03-22 at 3.47.47 PM.png](https://ax0.taddy.org/blog/intro-to-taddy-api/rest-vs-graphql-1.png)

### Ability to get back custom responses

The big benefit of writing custom queries is that you get to specify the information you want back in the response. For the example below the first query gets back a podcastâ€™s name and description, but the second one gets back the last 10 episodes. Practically, what this means is that depending on your business requirements, you can request the exact fields you need for your business use-case.

![Screen Shot 2022-03-21 at 12.59.23 PM.png](https://ax0.taddy.org/blog/intro-to-taddy-api/rest-vs-graphql-2.png)

## Working with a GraphQL API

When working with a GraphQL API, you can either: 

### 1. Use a GraphQL specific library (Recommended)

One big benefit of using a dedicated library built to work with GraphQL over using a standard http library is that it is built to make it even easier to work with the queries you send and the data you get back. Here are some popular GraphQL libraries for a some popular languages:

- Javascript: [GraphQL request](https://github.com/prisma-labs/graphql-request) (also check out our [**example project**](https://github.com/taddyorg/example-project)).
- Python: [Graphene](https://github.com/graphql-python/graphene)
- Ruby: [GraphQL client](https://github.com/github/graphql-client)
- iOS: [Apollo](https://www.apollographql.com/docs/ios) or [Swift GraphQL](https://github.com/maticzav/swift-graphql)
- Android: [Apollo](https://www.apollographql.com/docs/kotlin) or [GraphQL Kotlin](https://github.com/ExpediaGroup/graphql-kotlin/)

**Note:**  For a full list of libraries supported for even more languages, please see: [https://graphql.org/code/](https://graphql.org/code/)

### 2. Interact with our API using any http client

This below shows a example of a simple request to our API using curl. There are 3 important parts to the request:

- Sending a POST request to our GraphQL API Endpoint `https://api.taddy.org`
- Adding `Content-Type: application/json` , `X-USER-ID` & `X-API-KEY` in the headers.
- Sending the query you care about in the data field. You can read our [Podcast API](https://taddy.org/developers/podcast-api) docs to find all the different types of queries you can use.
    
    ```jsx
    curl -X POST \
    https://api.taddy.org \
    -H "Content-Type: application/json" \
    -H "X-USER-ID: 7" \
    -H "X-API-KEY: 96c5007c18858e86d..." \
    -d '{ "query": "{ getPodcastSeries(name:\"This American Life\") { uuid name } }" }'
    ```
    

**Note:** Currently, there are no official Taddy SDKs at the moment. However, one of the benefits of GraphQL is that types are built into it. If the language you using supports types, use a [GraphQL Specific Library](https://taddy.org/developers/intro-to-taddy-graphql-api). While not an SDK, types give you most of the practical benefits of an SDK.

## Useful Tips

### UUIDs

Every piece of content on Taddy has its own unique UUID and therefore you will be using UUIDs when interacting with our API. You can learn more about uuids on [**Wikipedia](https://en.wikipedia.org/wiki/Universally_unique_identifier).**

### Caching

We cache results from our API, which is great for API performance. Moreover, if your API request gets a cached response, it does not count against your monthly API limit ðŸ¥³.

### Pagination

Queries such as [**getPodcastSeries**](https://taddy.org/developers/podcast-api/get-podcast-series) can return multiple pages of results. For example the query below returns information on a podcast and its latest 10 episodes. By default, it returns the latest 10 episodes released but you can modify that by passing different parameters.

```jsx
// by default "episodes" returns the last 10 episodes
{
  getPodcastSeries(name:"This American Life"){
    uuid
    episodes{
      uuid
      name
      description
      audioUrl
    }
  }
}
```

```jsx
// you can also pass in parameters for "episodes". 
// This will return the exact same results as above 
// as these are the default parameters
{
  getPodcastSeries(name:"This American Life"){
    uuid
    episodes(sortOrder:LATEST, page:1, limitPerPage:10){
      uuid
      name
      description
      audioUrl
    }
  }
}
```

```jsx
// to get the 2nd page of results (the next 10 episodes)
{
  getPodcastSeries(name:"This American Life"){
    uuid
    episodes(page:2){
      uuid
      name
      description
      audioUrl
    }
  }
}
```

### Check how many API requests you have left for the month

You can use this query to check how many API requests you have left for the month. 

```jsx
{
  getTranscriptCreditsRemaining
}
```

### GraphQL Errors

A successful response will have the information you requested in the `data` key.

```jsx
{
  "data": {
    "answer": 42
  }
}
```

However, when you get back an error, you get back an `errors` array, which returns a list of all the errors. Note the two values associated with each error: the `code` (a category code for the type of error) and the `message` (a helpful message to help you troubleshoot the error).

```jsx
{
  "errors": [
    {
      "code": // An Error Code (We categorize errors using different codes)
      "message": // Helpful message to help you problem solve the error
    }
  ],
  "data": {
    "answer": null
  }
}
```

Here is an example: (123 is not a valid uuid because a valid uuid has to be 36 characters long)

![Screen Shot 2022-04-26 at 9.41.31 AM.png](https://ax0.taddy.org/blog/intro-to-taddy-api/error-code.png)

**Possible Error Codes**

- `API_KEY_INVALID` - The API Key or User ID you are using in your headers is invalid.
- `API_RATE_LIMIT_EXCEEDED` - You have exceeded your monthly quota of API requests.
- `INVALID_QUERY_OR_SYNTAX` - Your query is too complex or there is a spelling or syntax mistake somewhere in your query. Use the message value as a hint as to what can be fixed.
- `BAD_USER_INPUT` - One of the arguments you are passing in is invalid. Use the message value to get more details on what is invalid.
- `QUERY_TOO_COMPLEX` - The query you are passing in is too complex. Please simplify your query (by removing items from your query)
- `REQUIRES_USER_AUTHENTICATION` - You need to be be logged in to make that request.
- `ACCESS_NOT_ALLOWED` - You are not allowed to access this query or mutation.
- `TADDY_SERVER_ERROR` - Something is wrong on our end. We have systems in place to monitor this but also feel free to reach out to danny@taddy.org if you are getting this error.

### Example Project

Here is an [**example project**](https://github.com/taddyorg/taddy-api-example-project) in Node.js that uses Taddyâ€™s API.

<aside>
ðŸ‘‹ You just completed our Intro to Taddyâ€™s API Guide. Now you can use our API to:

Get podcast & episode information with our [ðŸ“» Podcast API](https://taddy.org/developers/podcast-api).

Get comic & issue information with our [ðŸŽ¨Â Comic API](https://taddy.org/developers/comics-api).

Get details on the creator of content with our [ðŸ§‘â€ðŸŽ¨ Creator API](https://taddy.org/developers/creator-api).

</aside>

More Links: 

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# OAuth Instructions

Most developers use Taddy API for our Podcast API. If that is your use case, you can ignore this page, Taddy OAuth is not required. Taddy OAuth is used to verify if a user has paid for the exclusive content hosted by Taddy (for webcomics). 

![](https://ax0.taddy.org/sss/oauth-flow-2.png)

A creator uses Taddy to upload their content and select which episodes are only available to paid users. Fans of the creator can then consume this content in other apps. If you are building one of those apps, you can verify if the user has paid for access to the exclusive content using OAuth.

## Getting Started

To be able to use Taddy OAuth, you must first sign up for a Developer account:

1. Sign up for a [developer account](https://taddy.org/signup/developers).
2. Login to the [dashboard](https://taddy.org/dashboard). Click on â€˜Get My API Keyâ€™.
3. Scroll down to the Taddy OAuth section. Click on â€˜registering your applicationâ€™.
4. Save your client Id and client secret details. Fill in other details for your client app like description, image, and callback url.
5. Follow the [SSS specification](https://3s-docs.org/hosting-provider)Â for using OAuth to verify if a user has paid for exclusive content. Taddy OAuth endpoints can be found [here](https://taddy.org/feeds/sss/hostingprovider/e9957105-80e4-46e3-8e82-20472b9d7512).

```bash
oauth: {
  signupUrl: 'https://taddy.org/signup/developers',
  authorizeUrl: 'https://taddy.org/fans/authorize',
  tokenUrl: 'https://taddy.org/auth/oauth2/token',
  newAccessTokenUrl: 'https://taddy.org/auth/oauth2/new_access_token',
  newRefreshTokenUrl: 'https://taddy.org/auth/oauth2/new_refresh_token',
  newContentTokenUrl: 'https://taddy.org/auth/oauth2/new_content_token',
  instructionsUrl: 'https://taddy.org/developers/instructions'
},
```

---

# Podcast API

An API for developers to get details on over 4 million podcasts, 180 million episodes, episode transcripts, webhook notifications for new and updated podcasts and more.

## What can you do with the API?

- Get details on any [podcast](https://taddy.org/developers/podcast-api/get-podcast-series) or [episode](https://taddy.org/developers/podcast-api/get-podcast-episode).
- Blazing fast [full-text search](https://taddy.org/developers/podcast-api/search) on all podcasts and episodes.
- A list of the [most popular podcasts](https://taddy.org/developers/podcast-api/most-popular-podcasts) on Taddy Podcast API, plus the [daily top charts](https://taddy.org/developers/podcast-api/get-top-charts) for podcasts and episodes.
- Get the [transcript](https://taddy.org/developers/podcast-api/episode-transcripts) for any episode.
- Return the [latest episodes](https://taddy.org/developers/podcast-api/latest-episodes-from-multiple-podcasts) for a list of podcasts (Letâ€™s you easily build a podcast feed).
- Get a [webhook notification](https://taddy.org/developers/podcast-api/webhooks) for new or updated podcast data.
- Get details on [multiple podcasts](https://taddy.org/developers/podcast-api/get-multiple-podcast-series) or [multiple episodes](https://taddy.org/developers/podcast-api/get-multiple-podcast-episodes) (returns an array).
- [Add a podcast](https://taddy.org/developers/podcast-api/add-a-podcastseries-to-taddy) to our directory.
- [Bulk download all 4+ million podcasts](https://taddy.org/developers/podcast-api/bulk-download-podcastseries) from our directory.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

## [Sign Up for Free âž¤](https://taddy.org/signup/developers)

### How to get the most out of Taddy API

[https://www.youtube.com/watch?v=kzjSjVjCMlk](https://www.youtube.com/watch?v=kzjSjVjCMlk)

### API Reference:

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries) - Information on a podcast

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode) - Information on an episode

[iTunesInfo](https://taddy.org/developers/podcast-api/itunesinfo) - Additional information from iTunes on a podcast

[Country](https://taddy.org/developers/podcast-api/country) - The country in which the podcast is made

[Language](https://taddy.org/developers/podcast-api/language) - The language spoken on the podcast

[Genre](https://taddy.org/developers/podcast-api/genre) - The genre of the podcast

[SortOrder](https://taddy.org/developers/podcast-api/sort-order) - The option to have returned items sorted by newest or oldest.

[PodcastContentType](https://taddy.org/developers/podcast-api/podcast-content-type) - Helps you distinguish between audio and video podcasts

[PodcastSeriesType](https://taddy.org/developers/podcast-api/podcast-series-type) - Helps you distinguish between episodic and serial podcasts

[PodcastEpisodeType](https://taddy.org/developers/podcast-api/podcast-episode-type) - Helps you distinguish between episodic and serial podcasts

[TaddyType](https://taddy.org/developers/podcast-api/taddytype) - The types of media available on Taddy

[TopChartsSource](https://taddy.org/developers/podcast-api/top-charts-source) - A list of platforms you can get Top Charts data from

[TopChartsType](https://taddy.org/developers/podcast-api/top-charts-type) - A list of possible categories for podcasts or episodes to be ranked in

[SearchContentType](https://taddy.org/developers/podcast-api/search-content-type) - The types of media available to search for on Taddy

[SearchMatchType](https://taddy.org/developers/podcast-api/search-match-by) - Choose between searching for all terms, some terms or frequency

[SearchSortOrder](https://taddy.org/developers/podcast-api/search-sort-by) - The option to have search results be prioritized by exact term matching or popularity

[SearchRankingDetails](https://taddy.org/developers/podcast-api/search-query-ranking-details) - Ranking details for each search result

[SearchResponseDetails](https://taddy.org/developers/podcast-api/search-query-response-details) - Additional details for search request

[FeedRefreshDetails](https://taddy.org/developers/podcast-api/feed-refresh-details) - Details on how often Taddy checks a podcast feed for updates

[Person](https://taddy.org/developers/podcast-api/person) - People listed on the podcast including their roles (Host, Guest, etc).

[PopularityRank](https://taddy.org/developers/podcast-api/popularity-rank) - Returns how popular the podcast is

[PodcastSeriesTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastseries-transcription-status) - Check if Taddy is automatically generating transcripts and chapters for this podcast

[PodcastEpisodeTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastepisode-transcription-status) - Check the status of automatically generated transcripts

[TranscriptItem](https://taddy.org/developers/podcast-api/episode-transcript-item) -  Transcript details including text, timecodes and speaker info.

[TranscriptLink](https://taddy.org/developers/podcast-api/episode-transcript-link) - Link details where you can download the transcript for an episode.

[Chapter](https://taddy.org/developers/podcast-api/episode-chapter-item) - Details on the chapters for an episode.

[ChapterLink](https://taddy.org/developers/podcast-api/episode-chapter-link) - Link details where you can download chapters for an episode.

## [Sign Up for Free âž¤](https://taddy.org/signup/developers)

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Webcomics API](https://taddy.org/developers/comics-api)

[Creator API](https://taddy.org/developers/creator-api)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Changelog](https://taddy.org/developers/changelog)

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

Blog Posts:

[The 5 Top Podcast APIs](https://taddy.org/blog/best-podcast-api-tools)

[iTunes API vs Taddy API - Which is better?](https://taddy.org/blog/itunes-vs-taddy-podcast-api)

[Listen Notes API vs Taddy API - Which is better?](https://taddy.org/blog/listen-notes-vs-taddy-podcast-api)

[PodcastIndex.org API vs Taddy - Which is better?](https://taddy.org/blog/podcastindex-vs-taddy-podcast-api)

[Should I rebuild my app on Blueskyâ€™s AT Protocol?](https://taddy.org/blog/indie-developer-weighs-pro-cons-of-rebuilding-app-on-atprotocol)

---

# Add a Podcast to Taddyâ€™s Directory

Taddyâ€™s Directory has over 4 million podcasts and we add more podcasts to our directory every day. Once you add your podcast to Taddyâ€™s Directory, we notify all the podcast players that use Taddyâ€™s API that there is a new podcast that has been released.

## How to add a Podcast to Taddy

1. Use the REST endpoint: **** **https://taddy.org/feeds/add-podcastseries** (POST)
2. Add your `X-USER-ID` and `X-API-KEY` values to the headers (see [Intro Guide](https://taddy.org/developers/intro-to-taddy-graphql-api) for instructions on how to get your API Key and User ID)

```jsx
{
	"X-USER-ID": 7,
	"X-API-KEY": "96c5007c18858e86d..."
}
```

 3. Add `rssUrl` (required) and `itunesId` (optional) values to the data being passed with the POST request.

```jsx
{
	"rssUrl": "http://feed.thisamericanlife.org/talpodcast",
	"itunesId":  201671138
}
```

You will get back one of the following responses:

**Success:** On a successful response you get back a `status` and the `uuid` for the added podcast (uuid is the distinct identifier for all content on Taddy).

```jsx
{
	"status": "success",
	"uuid":  "d682a935-ad2d-46ee-a0ac-139198b83bcc"
}
```

**Error:** When we encounter an error adding a new podcast to Taddy. We send back a string with the error message.

```jsx
`Could not add podcastseries to Taddy`
```

### Example:

```jsx
curl -X POST \
https://taddy.org/feeds/add-podcastseries \
-H "Content-Type: application/json" \
-H "X-USER-ID: 7" \
-H "X-API-KEY: 96c5007c18858e86d..." \
-d '{ "rssUrl": "http://feed.thisamericanlife.org/talpodcast", "itunesId": 201671138 }'
```

**Notes:**

- Once you send a request: 1) We immediately add the podcast to Taddyâ€™s Directory and 2) Queue up a job to parse the podcast feedâ€™s XML, including all episodes. This second step may take up to 30 mins but is usually much sooner.
- Adding the itunesId is optional, especially if you don't know it yet. We check the iTunes API every day for new podcasts and will match the rssUrl to one given from iTunes API to update the itunesId for a podcast without one.
- Trying to re-add a podcast that has already been added to our directory will return a successful response and return the uuid for the podcast series.
- If you are run into any issues, please contact `danny@taddy.org`.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Bulk download all 4 million podcasts and 180 million episodes

You can choose to integrateÂ Taddy APIÂ into your app in 2 main ways:

## **1. Use our API**

Make an API request to Taddy API to get the details of any podcast or episode. 

**Taddy API Costs:** Available for Free, Pro, and Business Taddy API users.

**Additional Setup + Maintenance:** None

**Additional Costs:** None

### **Why Choose This Option?**

This is the quick, straightforward way to get podcast data into your app. Most Taddy API users use this option.

## **2. Full Data Control**

If your use case requires more control and you need to store all podcast and episode data in your own database, Taddy makes it easy to:

- **Export podcast data from Taddy API** - A one-time export (and monthly snapshots) of all podcast and episode data from Taddyâ€™s database.
- **Keep your database in sync with our database**Â â€“ We continuously check RSS feeds for new or updated episodes and push any updates to an endpoint on your server via [webhooks](https://taddy.org/developers/podcast-api/webhooks).

When used together, the database export and webhooks allow you to keep your own up-to-date database of all podcasts and episodes. 

**Taddy API Costs:** One-time fee for data export (details below) plus Taddy API Business plan is required to receive webhook events for new or updated episodes.

**Additional Setup + Maintenance:** Yes. (You need to maintain and host your own database/infrastructure).

**Additional Costs:** Yes. (~$200 to $400 per month to host your own infrastructure). 

### **Why Choose This Option?**

This option is ideal if you want complete control over your podcast data. Start with option 1 (using Taddyâ€™s API) for simplicity, and transition to full data control if / when needed. 

## **Export data from Taddy API**:

Taddy offers aÂ **complete export**Â of our podcast data, including an export of these tables:

- podcast table (4 million podcasts)
- episodes table (180 million episodes)
- genres table (a podcast can belong to up to 5 genres)
- itunes_info table (additional information on the podcast from Apple iTunes)

ðŸ“¦Â **Full Dataset Size:**Â ~80 GB

ðŸ’°Â **One-time Fee:**Â **`$4,750 USD`**

ðŸ“ŽÂ [Download a Sample dataset](https://github.com/taddyorg/podcast-dataset-export) of 10,000 podcasts and episodes. 

If you have any further inquiries or want to purchase the full dataset, please contact **`danny@taddy.org`**

## Monthly Snapshots

If you have purchased the above one-time export and you are on the Taddy Business plan, you get access to monthly snapshots of podcast data from our database on theÂ **1st of every month**.

**To download info on what snapshot files are available:** 

Download the JSON file at https://ax3.taddy.org/db-snapshot/contents.json. You must pass in your Taddy API `X-USER-ID` and `X-API-KEY` in the headers. 

**Example:**

```jsx
curl -H "X-USER-ID: 1" -H "X-API-KEY: xyz..." \                                                                                           
https://ax3.taddy.org/db-snapshot/contents.json > ~/Downloads/contents.json
```

The JSON file lists available tables, when they were last updated, and the corresponding file names. 

**Example:**

```jsx
[
  {
    "tableName": "podcastseries",
    "updatedAt": "2025-05-01T04:00:40.000Z",
    "fileNames": [
      "podcastseries.zip"
    ]
  },
  {
    "tableName": "podcastepisode",
    "updatedAt": "2025-05-01T06:18:16.000Z",
    "fileNames": [
      "podcastepisode-part1.zip",
      "podcastepisode-part2.zip",
      "podcastepisode-part3.zip"
    ]
  },
  {
    "tableName": "genres",
    "updatedAt": "2025-05-01T03:56:52.000Z",
    "fileNames": [
      "genres.zip"
    ]
  },
  {
    "tableName": "itunes_info",
    "updatedAt": "2025-05-01T03:58:17.000Z",
    "fileNames": [
      "itunes_info.zip"
    ]
  }
]
```

**Downloading each file:** 

To download each file listed in contents.json, use this format: https://ax3.taddy.org/db-snapshot/{fileName} and pass in your Taddy API `X-USER-ID` and `X-API-KEY` in the headers. 

**Example:**

```jsx
curl -H "X-USER-ID: 1" -H "X-API-KEY: xyz..." \                                                                                           
https://ax3.taddy.org/db-snapshot/podcastseries.zip > ~/Downloads/podcastseries.zip
```

**Note: If** you downgrade from Taddy Business Plan to a lower plan, you will loose access to future monthly snapshots.

If you have any questions, please contact **`danny@taddy.org`**

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Chapter

Get details on the chapters for an episode.

```jsx
type Chapter {
  " The unique identifier for the chapter "
  id: ID

  " The title of the chapter "
  title: String

  " The start timecode of the chapter in milliseconds "
  startTimecode: Int
}
```

---

# ChapterLink

Link details where you can download chapters for an episode.

```jsx
" A url link to the chapters for an episode "
type ChapterLink {
  " The url to the chapter "
  url: String

  " Mime type of file "
  type: String

  " If the transcript is exclusive to Taddy API Business users and you need an API key to access it "
  isTaddyExclusive: Boolean
}
```

---

# Country

A list of possible Countries. Conforms to [ISO 3166-1](https://en.wikipedia.org/wiki/ISO_3166-1)

```jsx
enum Country {
  AFGHANISTAN
  ALAND_ISLANDS
  ALBANIA
  ALGERIA
  AMERICAN_SAMOA
  ANDORRA
  ANGOLA
  ANGUILLA
  ANTARCTICA
  ANTIGUA_AND_BARBUDA
  ARGENTINA
  ARMENIA
  ARUBA
  AUSTRALIA
  AUSTRIA
  AZERBAIJAN
  BAHAMAS
  BAHRAIN
  BANGLADESH
  BARBADOS
  BELARUS
  BELGIUM
  BELIZE
  BENIN
  BERMUDA
  BHUTAN
  BOLIVIA_PLURINATIONAL_STATE_OF
  BONAIRE_SINT_EUSTATIUS_AND_SABA
  BOSNIA_AND_HERZEGOVINA
  BOTSWANA
  BOUVET_ISLAND
  BRAZIL
  BRITISH_INDIAN_OCEAN_TERRITORY_THE
  BRUNEI_DARUSSALAM
  BULGARIA
  BURKINA_FASO
  BURUNDI
  CABO_VERDE
  CAMBODIA
  CAMEROON
  CANADA
  CAYMAN_ISLANDS
  CENTRAL_AFRICAN_REPUBLIC
  CHAD
  CHILE
  CHINA
  CHRISTMAS_ISLAND
  COCOS_KEELING_ISLANDS
  COLOMBIA
  COMOROS
  CONGO
  CONGO_THE_DEMOCRATIC_REPUBLIC_OF
  COOK_ISLANDS
  COSTA_RICA
  COTE_D_IVOIRE
  CROATIA
  CUBA
  CURACAO
  CYPRUS
  CZECHIA
  DENMARK
  DJIBOUTI
  DOMINICA
  DOMINICAN_REPUBLIC
  ECUADOR
  EGYPT
  EL_SALVADOR
  EQUATORIAL_GUINEA
  ERITREA
  ESTONIA
  ESWATINI
  ETHIOPIA
  FALKLAND_ISLANDS_THE_MALVINAS
  FAROE_ISLANDS
  FIJI
  FINLAND
  FRANCE
  FRENCH_GUIANA
  FRENCH_POLYNESIA
  FRENCH_SOUTHERN_TERRITORIES
  GABON
  GAMBIA
  GEORGIA
  GERMANY
  GHANA
  GIBRALTAR
  GREECE
  GREENLAND
  GRENADA
  GUADELOUPE
  GUAM
  GUATEMALA
  GUERNSEY
  GUINEA
  GUINEA_BISSAU
  GUYANA
  HAITI
  HEARD_ISLAND_AND_MCDONALD_ISLANDS
  HOLY_SEE
  HONDURAS
  HONG_KONG
  HUNGARY
  ICELAND
  INDIA
  INDONESIA
  IRAN
  IRAQ
  IRELAND
  ISLE_OF_MAN
  ISRAEL
  ITALY
  JAMAICA
  JAPAN
  JERSEY
  JORDAN
  KAZAKHSTAN
  KENYA
  KIRIBATI
  KOREA_NORTH
  KOREA_SOUTH
  KUWAIT
  KYRGYZSTAN
  LAO_PEOPLES_DEMOCRATIC_REPUBLIC_THE
  LATVIA
  LEBANON
  LESOTHO
  LIBERIA
  LIBYA
  LIECHTENSTEIN
  LITHUANIA
  LUXEMBOURG
  MACAO
  MADAGASCAR
  MALAWI
  MALAYSIA
  MALDIVES
  MALI
  MALTA
  MARSHALL_ISLANDS
  MARTINIQUE
  MAURITANIA
  MAURITIUS
  MAYOTTE
  MEXICO
  MICRONESIA_FEDERATED_STATES
  MINOR_OUTLYING_ISLANDS_US
  MOLDOVA_THE_REPUBLIC
  MONACO
  MONGOLIA
  MONTENEGRO
  MONTSERRAT
  MOROCCO
  MOZAMBIQUE
  MYANMAR
  NAMIBIA
  NAURU
  NEPAL
  NETHERLANDS
  NEW_CALEDONIA
  NEW_ZEALAND
  NICARAGUA
  NIGER
  NIGERIA
  NIUE
  NORFOLK_ISLAND
  NORTH_MACEDONIA
  NORTHERN_MARIANA_ISLANDS
  NORWAY
  OMAN
  PAKISTAN
  PALAU
  PALESTINE_STATE
  PANAMA
  PAPUA_NEW_GUINEA
  PARAGUAY
  PERU
  PHILIPPINES
  PITCAIRN
  POLAND
  PORTUGAL
  PUERTO_RICO
  QATAR
  REUNION
  ROMANIA
  RUSSIA
  RWANDA
  SAINT_BARTHELEMY
  SAINT_HELENA_ASCENSION_AND_TRISTAN_DA_CUNHA
  SAINT_KITTS_AND_NEVIS
  SAINT_LUCIA
  SAINT_MARTIN_FRENCH_PART
  SAINT_PIERRE_AND_MIQUELON
  SAINT_VINCENT_AND_THE_GRENADINES
  SAMOA
  SAN_MARINO
  SAO_TOME_AND_PRINCIPE
  SAUDI_ARABIA
  SENEGAL
  SERBIA
  SEYCHELLES
  SIERRA_LEONE
  SINGAPORE
  SINT_MAARTEN_DUTCH_PART
  SLOVAKIA
  SLOVENIA
  SOLOMON_ISLANDS
  SOMALIA
  SOUTH_AFRICA
  SOUTH_GEORGIA_AND_THE_SOUTH_SANDWICH_ISLANDS
  SOUTH_SUDAN
  SPAIN
  SRI_LANKA
  SUDAN
  SURINAME
  SVALBARD_AND_JAN_MAYEN
  SWEDEN
  SWITZERLAND
  SYRIA
  TAIWAN
  TAJIKISTAN
  TANZANIA
  THAILAND
  TIMOR_LESTE
  TOGO
  TOKELAU
  TONGA
  TRINIDAD_AND_TOBAGO
  TUNISIA
  TURKEY
  TURKMENISTAN
  TURKS_AND_CAICOS_ISLANDS
  TUVALU
  UGANDA
  UKRAINE
  UNITED_ARAB_EMIRATES
  UNITED_KINGDOM
  UNITED_STATES_OF_AMERICA
  URUGUAY
  UZBEKISTAN
  VANUATU
  VENEZUELA
  VIETNAM
  VIRGIN_ISLANDS_BRITISH
  VIRGIN_ISLANDS_US
  WALLIS_AND_FUTUNA
  WESTERN_SAHARA
  YEMEN
  ZAMBIA
  ZIMBABWE
}
```

---

# FeedRefreshDetails

Options for how often Taddy checks a podcast feed for updates.

```jsx
enum FeedRefreshPriority {
  WEBSUB
  HIGH
  MEDIUM
  REGULAR
  LOW
  INACTIVE
  NEVER
}
```

`WEBSUB` - Feed is checked immediately for updates (within 5 mins, usually much sooner). Taddy gets notified of a change to the podcast feed via a [WebSub](https://en.wikipedia.org/wiki/WebSub) notification.

`HIGH` - Feed is checked every 2 hours. This is used for very popular podcasts that do not have WebSub support

`MEDIUM` -  Feed is checked every 6 hours. This is used for popular podcasts that do not have WebSub support

`REGULAR` - Feed is checked every day. This is the most common queue for podcasts that do not support WebSub.

`INACTIVE` - Feed is checked once a week.  This is used for podcast feeds that have not been updated in over a year.

`LOW` - Feed is checked once a month. This is used for podcast feeds that throw an error.

`NEVER` - Feed is no longer checked for updates. This is only used in the rare circumstance a podcast violates our [distribution policy](https://taddy.org/terms-of-service/distribution-policy).

If a feed has been set as LOW, INACTIVE, or NEVER priority, you can check the reason why: 

```jsx
enum FeedRefreshPriorityReason {
  INACTIVE_FOR_OVER_1_YEAR
  DUPLICATE_FEED
  ERROR_PARSING_FEED
  FEED_URL_NOT_WORKING
  PRIVATE_PODCAST_FEED
  VIOLATES_TADDY_DISTRIBUTION_POLICY 
}
```

`INACTIVE_FOR_OVER_1_YEAR` - Feed has not had any updates in the last 12 months

`DUPLICATE_FEED` - There is another feed in our database that links to the same content

`ERROR_PARSING_FEED` - Error parsing document when trying to check the feed for new updates

`FEED_URL_NOT_WORKING` - Error when trying to load the feed url (404 error, etc)

`PRIVATE_PODCAST_FEED` - This is a private Patreon or Supercast feed that is not meant for the public

`VIOLATES_TADDY_DISTRIBUTION_POLICY` - The feed has been reviewed by a Taddy staff member and is in violation of [our distribution policy](https://taddy.org/terms-of-service/distribution-policy).

## How to check the FeedRefreshPriority for a podcast

If you would like to know how often a [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries) is being checked for updates, check its `feedRefreshDetails` property.

```jsx
type FeedRefreshDetails {
  " Taddy's unique identifier "
  uuid: ID

  " How often a feed is refreshed "
  priority: FeedRefreshPriority

  " The reason why feed has a LOW, INACTIVE, or NEVER priority "
  priorityReason: FeedRefreshPriorityReason

  " Date when the feed was refreshed last (Epoch time in seconds) "
  dateLastRefreshed: Int

  " Websub Details (if available)"
  websubDetails: WebsubDetails
}
```

Details for the WebSub hub that notifies Taddy of any changes for a podcast. (Only some podcasts have websub enabled and if it does not WebsubDetails will return null)

```jsx
" Websub Details "
type WebsubDetails {
  " Taddy's unique identifier "
  uuid: ID

  " The feed url for the websub "
  topicUrl: String

  " The url for the hub where you get the websub notification "
  websubHubUrl: String

  " If the websub notification is currently active "
  isVerified: Boolean
}
```

---

# Genre

Possible Genres for podcasts. Follows format: **TYPE_GENRE_SUBGENRE**.

```jsx
enum Genre {
  PODCASTSERIES_ARTS
  PODCASTSERIES_ARTS_BOOKS
  PODCASTSERIES_ARTS_DESIGN
  PODCASTSERIES_ARTS_FASHION_AND_BEAUTY
  PODCASTSERIES_ARTS_FOOD
  PODCASTSERIES_ARTS_PERFORMING_ARTS
  PODCASTSERIES_ARTS_VISUAL_ARTS
  PODCASTSERIES_BUSINESS
  PODCASTSERIES_BUSINESS_CAREERS
  PODCASTSERIES_BUSINESS_ENTREPRENEURSHIP
  PODCASTSERIES_BUSINESS_INVESTING
  PODCASTSERIES_BUSINESS_MANAGEMENT
  PODCASTSERIES_BUSINESS_MARKETING
  PODCASTSERIES_BUSINESS_NON_PROFIT
  PODCASTSERIES_COMEDY
  PODCASTSERIES_COMEDY_INTERVIEWS
  PODCASTSERIES_COMEDY_IMPROV
  PODCASTSERIES_COMEDY_STANDUP
  PODCASTSERIES_EDUCATION
  PODCASTSERIES_EDUCATION_COURSES
  PODCASTSERIES_EDUCATION_HOW_TO
  PODCASTSERIES_EDUCATION_LANGUAGE_LEARNING
  PODCASTSERIES_EDUCATION_SELF_IMPROVEMENT
  PODCASTSERIES_FICTION
  PODCASTSERIES_FICTION_COMEDY_FICTION
  PODCASTSERIES_FICTION_DRAMA
  PODCASTSERIES_FICTION_SCIENCE_FICTION
  PODCASTSERIES_GOVERNMENT
  PODCASTSERIES_HISTORY
  PODCASTSERIES_HEALTH_AND_FITNESS
  PODCASTSERIES_HEALTH_AND_FITNESS_ALTERNATIVE_HEALTH
  PODCASTSERIES_HEALTH_AND_FITNESS_FITNESS
  PODCASTSERIES_HEALTH_AND_FITNESS_MEDICINE
  PODCASTSERIES_HEALTH_AND_FITNESS_MENTAL_HEALTH
  PODCASTSERIES_HEALTH_AND_FITNESS_NUTRITION
  PODCASTSERIES_HEALTH_AND_FITNESS_SEXUALITY
  PODCASTSERIES_KIDS_AND_FAMILY
  PODCASTSERIES_KIDS_AND_FAMILY_EDUCATION_FOR_KIDS
  PODCASTSERIES_KIDS_AND_FAMILY_PARENTING
  PODCASTSERIES_KIDS_AND_FAMILY_PETS_AND_ANIMALS
  PODCASTSERIES_KIDS_AND_FAMILY_STORIES_FOR_KIDS
  PODCASTSERIES_LEISURE
  PODCASTSERIES_LEISURE_ANIMATION_AND_MANGA
  PODCASTSERIES_LEISURE_AUTOMOTIVE
  PODCASTSERIES_LEISURE_AVIATION
  PODCASTSERIES_LEISURE_CRAFTS
  PODCASTSERIES_LEISURE_GAMES
  PODCASTSERIES_LEISURE_HOBBIES
  PODCASTSERIES_LEISURE_HOME_AND_GARDEN
  PODCASTSERIES_LEISURE_VIDEO_GAMES
  PODCASTSERIES_MUSIC
  PODCASTSERIES_MUSIC_COMMENTARY
  PODCASTSERIES_MUSIC_HISTORY
  PODCASTSERIES_MUSIC_INTERVIEWS
  PODCASTSERIES_NEWS
  PODCASTSERIES_NEWS_BUSINESS
  PODCASTSERIES_NEWS_DAILY_NEWS
  PODCASTSERIES_NEWS_ENTERTAINMENT
  PODCASTSERIES_NEWS_COMMENTARY
  PODCASTSERIES_NEWS_POLITICS
  PODCASTSERIES_NEWS_SPORTS
  PODCASTSERIES_NEWS_TECH
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_BUDDHISM
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_CHRISTIANITY
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_HINDUISM
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_ISLAM
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_JUDAISM
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_RELIGION
  PODCASTSERIES_RELIGION_AND_SPIRITUALITY_SPIRITUALITY
  PODCASTSERIES_SCIENCE
  PODCASTSERIES_SCIENCE_ASTRONOMY
  PODCASTSERIES_SCIENCE_CHEMISTRY
  PODCASTSERIES_SCIENCE_EARTH_SCIENCES
  PODCASTSERIES_SCIENCE_LIFE_SCIENCES
  PODCASTSERIES_SCIENCE_MATHEMATICS
  PODCASTSERIES_SCIENCE_NATURAL_SCIENCES
  PODCASTSERIES_SCIENCE_NATURE
  PODCASTSERIES_SCIENCE_PHYSICS
  PODCASTSERIES_SCIENCE_SOCIAL_SCIENCES
  PODCASTSERIES_SOCIETY_AND_CULTURE
  PODCASTSERIES_SOCIETY_AND_CULTURE_DOCUMENTARY
  PODCASTSERIES_SOCIETY_AND_CULTURE_PERSONAL_JOURNALS
  PODCASTSERIES_SOCIETY_AND_CULTURE_PHILOSOPHY
  PODCASTSERIES_SOCIETY_AND_CULTURE_PLACES_AND_TRAVEL
  PODCASTSERIES_SOCIETY_AND_CULTURE_RELATIONSHIPS
  PODCASTSERIES_SPORTS
  PODCASTSERIES_SPORTS_BASEBALL
  PODCASTSERIES_SPORTS_BASKETBALL
  PODCASTSERIES_SPORTS_CRICKET
  PODCASTSERIES_SPORTS_FANTASY_SPORTS
  PODCASTSERIES_SPORTS_FOOTBALL
  PODCASTSERIES_SPORTS_GOLF
  PODCASTSERIES_SPORTS_HOCKEY
  PODCASTSERIES_SPORTS_RUGBY
  PODCASTSERIES_SPORTS_RUNNING
  PODCASTSERIES_SPORTS_SOCCER
  PODCASTSERIES_SPORTS_SWIMMING
  PODCASTSERIES_SPORTS_TENNIS
  PODCASTSERIES_SPORTS_VOLLEYBALL
  PODCASTSERIES_SPORTS_WILDERNESS
  PODCASTSERIES_SPORTS_WRESTLING
  PODCASTSERIES_TECHNOLOGY
  PODCASTSERIES_TRUE_CRIME
  PODCASTSERIES_TV_AND_FILM
  PODCASTSERIES_TV_AND_FILM_AFTER_SHOWS
  PODCASTSERIES_TV_AND_FILM_HISTORY
  PODCASTSERIES_TV_AND_FILM_INTERVIEWS
  PODCASTSERIES_TV_AND_FILM_FILM_REVIEWS
  PODCASTSERIES_TV_AND_FILM_TV_REVIEWS
}
```

---

# Get Episode Transcripts

You can get the transcript for any episode from our directory of 180+ million episodes. 

## Example:

1. **(Recommended)** Use the `getEpisodeTranscript` query to get the transcript, including  timecodes and speaker names (if provided). 

```jsx
{
  getEpisodeTranscript(uuid:"e03bf3ef-829e-4f47-9f02-29ac6a747b4f"){
    id
    text
    speaker
    startTimecode
    endTimecode
  }
}
```

2. **(Easiest)** If you want to get episode details along with the transcript, use `transcript` or `transcriptWithSpeakersAndTimecodes`. 

If you just need the text of the transcript, use `transcript`. If you also need the timecodes and speaker names (if provided), use `transcriptWithSpeakersAndTimecodes`.

```jsx
{
  getPodcastEpisode(uuid:"e03bf3ef-829e-4f47-9f02-29ac6a747b4f"){
    uuid
    name
    taddyTranscribeStatus
    transcript
    transcriptWithSpeakersAndTimecodes{
      id
      text
      speaker
      startTimecode
      endTimecode
    }
  }
}
```

Example 1 (Recommended) is recommended over Example 2 (Easiest) because if Taddy API doesnâ€™t already have the transcript, we generate one on-demand, which can take 10+ seconds to generate. 

This means for example 2, episode details wonâ€™t be returned until the transcript has been generated. Depending on your use-case, you may want to consider splitting your requests into two requests for a better user-experience: 1) get general episode details (fast) and 2) get the transcript for the episode (can be slow). 

3. **(Advanced)** You can use `transcriptUrls` or `transcriptUrlsWithDetails` to get the URLs where you can download a transcript yourself. This includes both the transcripts provided by podcast (if available) and the transcripts that have been automatically generated via Taddy API. 

To download a transcript provided by Taddy API via its url, you must pass in your Taddy API `X-USER-ID` and `X-API-KEY` in the headers. Here is an example using curl:

```jsx
curl -H "X-USER-ID: 1" -H "X-API-KEY: xyz..." \                                                                                           
https://ax2.taddy.org/9d874d17-fe25-4cbb-a802-ce65f7c198a1/106d1dfb-50ed-4844-8e04-31960d8767c7/transcript.vtt
```

### How Taddy API generates transcripts

Behind the scenes, Taddy API gets the transcript for an episode in 3 ways:

1. **Podcast-provided transcripts**Â - Some podcasts provide their own text transcripts (however, less than 1% of podcasts currently do this)
2. **Automatic transcription for popular podcasts**Â - We automatically generate transcripts for the most popular 5000 podcasts using an open-source transcription model running on our GPUs
3. **On-demand transcription**Â - We can transcribe any episode on-demand (It takes ~10 seconds to transcribe every ~1hr of audio)

To summarize, between transcripts provided by the podcast and Taddy API generated transcripts, you can get the transcript for any episode on Taddy API.

### Pricing

All Taddy API users (including Free users) get access to any transcript provided by the podcast themselves **(podcast-provided transcripts)**. 

Paid users ([Pro and Business users](https://taddy.org/developers/pricing)) get access to Taddy API generated transcripts **(automatic and on-demand transcriptions)**.

- 100 episode transcripts/month are included on the Pro Plan
- 2000 episodeÂ transcripts/monthÂ are includedÂ on the Business Plan
- Need additional episode transcripts? Get an additional 2000 episode transcripts/month for $100/month (5c per transcript)
- If an episode provides its own text transcript with timecodes i.e.) if a transcript is provided in WEBVTT (.vtt) or SubRip (.srt) format, we use that transcript and do not generate our own transcript. This transcript is available to all Taddy API users (Free and Paid) and does not count against your episode transcripts/month.
- You can request the same transcript multiple times in the same month and it will only count once. We keep track of all the transcripts that a user has requested for that month and reset it when your billing cycle resets every month.

### How to check if a podcast is one of the top 5000 podcasts we are automatically transcribing every episode of:

Check the `taddyTranscribeStatus` of a podcast. You are looking for the value `TRANSCRIBING`.

```jsx
{
  getPodcastSeries(name:"This American Life"){
    uuid
    name
    taddyTranscribeStatus
  }
}
```

### How to check if a transcript exists for an episode:

Check the `taddyTranscribeStatus` of an episode.

```jsx
{
  getPodcastEpisode(uuid:"e03bf3ef-829e-4f47-9f02-29ac6a747b4f"){
    uuid
    name
    taddyTranscribeStatus
  }
}
```

Here are the possible values:

`COMPLETED` - The transcript is available (This can be either be because the podcast has provided it or because we have already transcribed the episode)

`PROCESSING` -  Currently in a queue, waiting to be transcribed. Please note that there can be 10k+ episodes that are queued to be transcribed (so it does not necessarily mean it is going to be transcribed in the next couple minutes).

`NOT_TRANSCRIBING` - We do not have the episode in a queue to be transcribed.

**Please note:**  Being a paid Taddy API user allows you to get the transcript for any episode, even if it is in the `PROCESSING` or  `NOT_TRANSCRIBING` state. Keep in mind, they are generated on-demand (takes ~10 seconds to transcribe every ~1hr of audio) and not already available.

### Try our transcription feature for Free

Every Taddy API user (Free or Paid) gets access to any episode transcript where the podcast provides its own episode transcripts. 

**Build Your SaaS** is an example of a podcast that provides transcripts for its episodes. The query below gets its latest episode along with its transcript.

```jsx
{
  getPodcastSeries(uuid:"6bdfd429-f58b-427d-8072-353d478aa15f") {
    uuid
    name
    rssUrl
    episodes(limitPerPage:1){
      uuid
      name
      datePublished
      audioUrl
      duration
      taddyTranscribeStatus
      transcript
    }
  }
}
```

However, please keep in mind only 1% of podcasts provide transcripts for their episodes, which is why we built automatic transcription into Taddy API.

### Searching only for episodes that have a transcript

Our `search` query has the option to filter only for results that have transcripts available. Set `filterForHasTranscript` to true.

```jsx
{
  search(term:"Neil deGrasse Tyson", filterForTypes:PODCASTEPISODE, sortBy:POPULARITY, filterForHasTranscript:true){
    searchId
    podcastEpisodes{
      uuid
      name
      datePublished
      description
      audioUrl
      transcript
    }
  }
}
```

### See how many episode transcripts you have left for the month

You can use this query to check how many episode transcript you have left. 

```jsx
{
  getTranscriptCreditsRemaining
}
```

### Useful GraphQL properties

Here are the properties on [PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode) related to transcripts. 

```jsx
" Status of transcript (complete, processing, not transcribed) "
taddyTranscribeStatus: PodcastEpisodeTranscriptionStatus

" Downloads the transcript, parses it and returns an array of text in paragraphs. "
transcript: [String]

" Download the transcript, parses it and return an array of transcript items (which includes text, speakers and timecodes) "
transcriptWithSpeakersAndTimecodes(
  " (Optional) Style option for transcript. Default is PARAGRAPH"
  style: TranscriptItemStyle
): [TranscriptItem]

" A list of urls where you can download the transcript for this episode "
transcriptUrls: [String]

" A list of urls where you can download the transcript for this episode, including more details "
transcriptUrlsWithDetails: [TranscriptLink]
```

### Technical Details

- The most popular 5000 podcasts were picked based on the [Most Popular Podcasts](https://taddy.org/developers/podcast-api/most-popular-podcasts) query.
- We do not attempt to identify individual speakers in the transcript or infer who is speaking. If your use case requires this, you can explore speaker diarization (segmenting audio by speaker) and speaker identification (identifying the name of each speaker) and can use our transcript as input.
- For on-demand transcript, which are created as Taddy API users request them, we use a faster open-source model that is within a 1% word error rate of our standard transcription model.
- Some podcasts use dynamic ad insertion. ie) The same audio link will give different listeners different ads depending on location and other factors. This will affect the timestamps provided in transcripts as there may be different ads in the audio file we transcribed.
- We use queues to prioritize which episodes we transcribe. Newly released episodes are high priority and transcribed as soon as possible. We're also working through the entire back catalogue of the most popular 5000 podcasts, which is a lower priority.
- Transcripts automatically generated by Taddy API are Brotli encoded. This only affects you if you're accessing a transcript provided by us directly via its URL. Most libraries will automatically decode Brotli, but if you're using a library that doesn't, be sure to decode it yourself.

### Referenced types in this document:

[PodcastEpisodeTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastepisode-transcription-status)

[TranscriptItem](https://taddy.org/developers/podcast-api/episode-transcript-item)

[TranscriptLink](https://taddy.org/developers/podcast-api/episode-transcript-link)

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get details on multiple episodes

Use getMultiplePodcastEpisodes to get details on multiple episodes. There is a max limit of 25 episodes per request.

## Example:

```jsx
{
  getMultiplePodcastEpisodes(uuids:["9ef0ddbf-65c4-4bba-89af-49d6f3f11464", "1ee4859c-cf17-48c8-9edb-a501544bdb75"]){
    uuid
    hash
    name
    description
    imageUrl
    datePublished
    guid
    subtitle
    audioUrl
    videoUrl
    fileLength
    fileType
    duration
    episodeType
    seasonNumber
    episodeNumber
    websiteUrl
    isExplicitContent
    isRemoved
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getMultiplePodcastEpisodes": [
      {
        "uuid": "9ef0ddbf-65c4-4bba-89af-49d6f3f11464",
        "hash": "1327b13c10209636b14d12f486e52aeadda8af21f070d11bedd0544b0dc36706",
        "name": "The Battle for Azovstal: A Soldierâ€™s Story",
        "description": "<p>For the past two months, a group of Ukrainian fighters has been holed up in the Azovstal steel plant in the city of Mariupol, mounting a last stand against Russian forces in a critical part of eastern Ukraine.</p><p>On Monday, Ukraine finally surrendered the plant.</p><p>After the end of the determined resistance at Azovstal, we hear from Leonid Kuznetsov, a 25 year-old soldier who had been stationed inside.</p><p>Guest: <a href=\"https://www.nytimes.com/by/michael-schwirtz?smid=pc-thedaily\">Michael Schwirtz</a>, an investigative reporter for The New York Times.</p><p>Want more from The Daily? For one big idea on the news each week from our team, <a href=\"https://www.nytimes.com/newsletters/the-daily?module=inline\">subscribe to our newsletter</a>.Â </p><p>Background reading:Â </p><ul><li>Hundreds of Ukrainian soldiers who fought at the steel plant in Mariupol<a href=\"https://www.nytimes.com/2022/05/17/world/europe/ukraine-mariupol-fighters-surrender.html\"> face an uncertain future</a> in Russian custody.</li></ul><p>For more information on todayâ€™s episode, visit <a href=\"http://nytimes.com/thedaily?smid=pc-thedaily\">nytimes.com/thedaily</a>. Transcripts of each episode will be made available by the next workday.Â </p>\n",
        "imageUrl": null,
        "datePublished": 1652867700,
        "guid": "c1be4b7b-9109-42aa-8f74-3c2d0a429f59",
        "subtitle": "For the past two months, a group of Ukrainian fighters has been holed up in the Azovstal steel plant in the city of Mariupol, mounting a last stand against Russian forces in a critical part of eastern Ukraine.\n\nOn Monday, Ukraine finally surrendered the plant.\n\nAfter the end of the determined resistance at Azovstal, we hear from Leonid Kuznetsov, a 25 year-old soldier who had been stationed inside.\n\nGuest: Michael Schwirtz, an investigative reporter for The New York Times.",
        "audioUrl": "https://dts.podtrac.com/redirect.mp3/chrt.fm/track/8DB4DB/pdst.fm/e/nyt.simplecastaudio.com/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/episodes/8484d0fb-6124-49c3-9b83-e95cf558847e/audio/128/default.mp3?aid=rss_feed&awCollectionId=03d8b493-87fc-4bd1-931f-8a8e9b945d8a&awEpisodeId=8484d0fb-6124-49c3-9b83-e95cf558847e&feed=54nAGcIl",
        "videoUrl": null,
        "fileLength": 29985152,
        "fileType": "audio/mpeg",
        "duration": 1874,
        "episodeType": "FULL",
        "seasonNumber": null,
        "episodeNumber": null,
        "websiteUrl": "https://www.nytimes.com/the-daily",
        "isExplicitContent": false,
        "isRemoved": null
      },
      {
        "uuid": "1ee4859c-cf17-48c8-9edb-a501544bdb75",
        "hash": "d951733fa92da746fa2d86177f97dd253d14d3e85a5cc831be8355b96f33f239",
        "name": "The Sunday Read: â€˜I Lived the #VanLife. It Wasnâ€™t Pretty.â€™",
        "description": "<p>The Times journalist Caity Weaver was tasked by her editor to go on an adventure: With an old college friend she would spend a week in California, living out of a converted camper van, in pursuit of the aesthetic fantasy known as #VanLife.</p><p>Given the discomfort that can arise even in the plushiest of vehicles, itâ€™s a surprising trend that shows no sign of letting up. As Weaver explains, even the idea of living full time out of a vehicle has â€œbecome aspirational for a subset of millennials and Zoomers, despite the fact that, traditionally, residing in a car or van is usually an action taken as a last resort, from want of other options to protect oneself from the elements.â€</p><p>Unpacking the craze by testing it herself, Weaver offers a humorous account of the trials of not being adequately prepared, claustrophobia, long restaurant lines, the increase in traffic within the national parks, and the disappointment that occurs when an Instagram aesthetic bumps up against reality. Sometimes fantasies are too good to be true.</p><p><i><strong>This story was written by Caity Weaver and recorded by Audm</strong>. To hear more audio stories from publications like The New York Times, </i><a href=\"https://www.audm.com/?utm_source=nytmag&utm_medium=embed&utm_campaign=age_old_question_jabr\" target=\"_blank\"><i>download Audm for iPhone or Android</i></a><i>.</i></p>\n",
        "imageUrl": null,
        "datePublished": 1652608800,
        "guid": "1b640a0c-01ea-427b-a115-16de63709157",
        "subtitle": "The Times journalist Caity Weaver was tasked by her editor to go on an adventure: With an old college friend she would spend a week in California, living out of a converted camper van, in pursuit of the aesthetic fantasy known as #VanLife.\n\nGiven the discomfort that can arise even in the plushiest of vehicles, itâ€™s a surprising trend that shows no sign of letting up. As Weaver explains, even the idea of living full time out of a vehicle has â€œbecome aspirational for a subset of millennials and Zoomers, despite the fact that, traditionally, residing in a car or van is usually an action taken as a last resort, from want of other options to protect oneself from the elements.â€\n\nUnpacking the craze by testing it herself, Weaver offers a humorous account of the trials of not being adequately prepared, claustrophobia, long restaurant lines, the increase in traffic within the national parks, and the disappointment that occurs when an Instagram aesthetic bumps up against reality. Sometimes f...",
        "audioUrl": "https://dts.podtrac.com/redirect.mp3/chrt.fm/track/8DB4DB/pdst.fm/e/nyt.simplecastaudio.com/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/episodes/7f79bcdd-068e-4f78-9f2d-aaa7d0d33c36/audio/128/default.mp3?aid=rss_feed&awCollectionId=03d8b493-87fc-4bd1-931f-8a8e9b945d8a&awEpisodeId=7f79bcdd-068e-4f78-9f2d-aaa7d0d33c36&feed=54nAGcIl",
        "videoUrl": null,
        "fileLength": 31627013,
        "fileType": "audio/mpeg",
        "duration": 1976,
        "episodeType": "FULL",
        "seasonNumber": null,
        "episodeNumber": null,
        "websiteUrl": "https://www.nytimes.com/the-daily",
        "isExplicitContent": false,
        "isRemoved": null
      }
    ]
  }
}
```

## Query Input:

For getMultiplePodcastEpisodes, you can get details on episodes by passing in an array of uuids. 

```jsx
" An Array of taddy's unique identifier (uuid). Max 25 IDs allowed "
uuids: [ID]
```

## Query Response:

The response you get back is an array of [PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode).

```jsx
" Taddy's unique identifier (an uuid) "
uuid: ID

" Date when the episode was published (Epoch time in seconds) "
datePublished: Int

" The name of an episode "
name: String

" The description for a episode "
description(
  " (Optional) Option to remove the html tags from the description or leave the description as is (which might include html tags). Default is false (do not strip html tags from description)."
  shouldStripHtmlTags: Boolean
): String

" Cover Art for the episode (it may be different than podcast cover art) "
imageUrl: String

" A different hash means that details for this episode have updated since the last hash "
hash: String

" An episode's unique identifier from its RSS feed "
guid: String

" The subtitle of an episode (shorter version of episode description, limited to 255 characters long) "
subtitle: String

" Link to Audio Content "
audioUrl: String

" Link to Video Content "
videoUrl: String

" File Length of Content "
fileLength: Int

" Exact File Type of Content "
fileType: String

" Duration of Content (in seconds) "
duration: Int

" Episode Type ie) trailer, bonus or full "
episodeType: PodcastEpisodeType

" Season Number (if provided) "
seasonNumber: Int

" Episode Number (if provided) "
episodeNumber: Int

" Website Link for episode "
websiteUrl: String

" If the episode contain's explicit content "
isExplicitContent: Boolean

" If the episode has now been removed from the RSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the podcast for which this episode belongs to "
podcastSeries: PodcastSeries

" People listed on the episode including thier roles (Hosts, Guests, etc) "
persons(
  " (Experimental) Include persons we have inferred using the episode's transcript "
  includeAutoGeneratedPersons: Boolean
): [Person]

" Status of transcript (complete, processing, not transcribed) "
taddyTranscribeStatus: PodcastEpisodeTranscriptionStatus

" Downloads the transcript, parses it and returns an array of text in paragraphs. "
transcript: [String]

" Download the transcript, parses it and return an array of transcript items (which includes text, speakers and timecodes) "
transcriptWithSpeakersAndTimecodes(
  " (Optional) Style option for transcript. Default is PARAGRAPH"
  style: TranscriptItemStyle
): [TranscriptItem]

" A list of urls where you can download the transcript for this episode "
transcriptUrls: [String]

" A list of urls where you can download the transcript for this episode, including more details "
transcriptUrlsWithDetails: [TranscriptLink]

" Download and parse the chapters "
chapters: [Chapter]

" A list of urls where you can download chapter details "
chaptersUrls: [String]

" A list of urls where you can download chapter details, including more details "
chaptersUrlsWithDetails: [ChapterLink]
```

### Referenced types in this document:

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisodeType](https://taddy.org/developers/podcast-api/podcast-episode-type)

[PodcastEpisodeTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastepisode-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[TranscriptItem](https://taddy.org/developers/podcast-api/episode-transcript-item)

[TranscriptLink](https://taddy.org/developers/podcast-api/episode-transcript-link)

[ChapterLink](https://taddy.org/developers/podcast-api/episode-chapter-link)

[Chapter](https://taddy.org/developers/podcast-api/episode-chapter-item)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get details on multiple podcasts

Use getMultiplePodcastSeries to get details on multiple podcasts. There is a max limit of 25 podcasts per request.

## Example:

```jsx
{
  getMultiplePodcastSeries(uuids:["cb8d858a-3ef4-4645-8942-67e55c0927f2", "d682a935-ad2d-46ee-a0ac-139198b83bcc"]){
    uuid
    name
    itunesId
    description
    imageUrl
    itunesInfo{
      uuid
      publisherName
      baseArtworkUrlOf(size: 640)
    }
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getMultiplePodcastSeries": [
      {
        "uuid": "cb8d858a-3ef4-4645-8942-67e55c0927f2",
        "name": "The Daily",
        "itunesId": 1200361736,
        "description": "This is what the news should sound like. The biggest stories of our time, told by the best journalists in the world. Hosted by Michael Barbaro and Sabrina Tavernise. Twenty minutes a day, five days a week, ready by 6 a.m.",
        "imageUrl": "https://image.simplecastcdn.com/images/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/2cce5659-f647-4366-b318-46e4b67afcfa/3000x3000/c81936f538106550b804e7e4fe2c236319bab7fba37941a6e8f7e5c3d3048b88fc5b2182fb790f7d446bdc820406456c94287f245db89d8656c105d5511ec3de.jpeg?aid=rss_feed",
        "itunesInfo": {
          "uuid": "cb8d858a-3ef4-4645-8942-67e55c0927f2",
          "publisherName": "The New York Times",
          "baseArtworkUrlOf": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/640x640bb.png"
        }
      },
      {
        "uuid": "d682a935-ad2d-46ee-a0ac-139198b83bcc",
        "name": "This American Life",
        "itunesId": 201671138,
        "description": "This American Life is a weekly public radio show, heard by 2.2 million people on more than 500 stations. Another 2.5 million people download the weekly podcast. It is hosted by Ira Glass, produced in collaboration with Chicago Public Media, delivered to stations by PRX The Public Radio Exchange, and has won all of the major broadcasting awards.",
        "imageUrl": "https://files.thisamericanlife.org/sites/all/themes/thislife/img/tal-name-1400x1400.png",
        "itunesInfo": {
          "uuid": "d682a935-ad2d-46ee-a0ac-139198b83bcc",
          "publisherName": "This American Life",
          "baseArtworkUrlOf": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts123/v4/4e/b9/bb/4eb9bb9b-ed19-f0b7-7739-1177f1b35207/mza_8452563123961176873.png/640x640bb.png"
        }
      }
    ]
  }
}
```

## Query Input:

For getMultiplePodcastSeries, you can get details on podcasts by passing in an array of uuids. 

```jsx
" An Array of taddy's unique identifier (uuid). Max 25 IDs allowed "
uuids: [ID]
```

## Query Response:

The response you get back is an array of [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries).

```jsx
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

### Referenced types in this document:

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[SortOrder](https://taddy.org/developers/podcast-api/sort-order)

[PodcastSeriesType](https://taddy.org/developers/podcast-api/podcast-series-type)

[PodcastContentType](https://taddy.org/developers/podcast-api/podcast-content-type)

[PodcastSeriesTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastseries-transcription-status)

[PopularityRank](https://taddy.org/developers/podcast-api/popularity-rank)

[Person](https://taddy.org/developers/podcast-api/person)

[iTunesInfo](https://taddy.org/developers/podcast-api/itunesinfo)

[Language](https://taddy.org/developers/podcast-api/language)

[FeedRefreshDetails](https://taddy.org/developers/podcast-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get episode details

Use getPodcastEpisode to get details on a specific podcast episode.

### Example:

```jsx
{
  getPodcastEpisode(uuid: "eb9d1c8f-58a4-4adb-a3d3-6bca573d31a2"){
    uuid
    hash
    name
    description
    imageUrl
    datePublished
    guid
    subtitle
    audioUrl
    videoUrl
    fileLength
    fileType
    duration
    episodeType
    seasonNumber
    episodeNumber
    websiteUrl
    isExplicitContent
    isRemoved
    podcastSeries{
      uuid
      name
    }
  }
}
```

Example response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getPodcastEpisode": {
      "uuid": "eb9d1c8f-58a4-4adb-a3d3-6bca573d31a2",
      "hash": "700d5eca3bd6ea013760bfe5956a9c4ddbd7399f26c2eb2a5b0360a7e76fa448",
      "name": "Case #2 Britney",
      "description": "<p>Andrea's a writer no one reads. Then she makes a shocking discovery.</p><p> </p><p>Learn more about your ad choices. Visit <a href=\"https://podcastchoices.com/adchoices\">podcastchoices.com/adchoices</a></p>",
      "imageUrl": null,
      "datePublished": 1432958400,
      "guid": "http://www.wnyc.org/story/case-2-britney/",
      "subtitle": "The Case:Â  Andrea's a writer no one reads. Then she makes a shocking discovery. The Facts:Â  Mystery Show is produced by myself, Alex Blumberg, Melinda Shopsin and Eric Mennel. Producing help from Chris Neary and Wendy Dorr. Eli Horowitz is contributing ed",
      "audioUrl": "https://traffic.megaphone.fm/GLT5025099642.mp3?updated=1511216902",
      "videoUrl": null,
      "fileLength": null,
      "fileType": "audio/mpeg",
      "duration": 2964,
      "episodeType": "FULL",
      "seasonNumber": null,
      "episodeNumber": null,
      "websiteUrl": null,
      "isExplicitContent": false,
      "isRemoved": null,
      "podcastSeries": {
        "uuid": "8c9998d7-7114-4514-ab17-1a0ad05f73fc",
        "name": "Mystery Show"
      }
    }
  }
}
```

## Query Breakdown:

For getPodcastEpisode, you can get details on any episode using one of the following properties:

```jsx
" Taddy's unique identifier (uuid) "
uuid: ID,

" An episode's guid (from its RSS Feed). Note: guid is not guaranteed to be unique (two different podcasts can use the same guid for two different episodes). If you know the podcast series uuid, you can use seriesUuidForLookup to filter the results to only return episodes for that series "
guid: String,

" The name (title) of a episode. Note: Multiple episodes can have the exact same name. If you know the podcast series uuid, you can use seriesUuidForLookup to filter the results to only return episodes for that series "
name: String,

" If you know the podcast series uuid, you can use this to filter the results to only return episodes for that series. This is useful if you using this query and passing in guid or name and want to ensure you get the correct episode "
seriesUuidForLookup: ID,
```

The response you get back is a [PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode). That means you can get back any of the following details for a podcast episode:

```jsx
" Taddy's unique identifier (an uuid) "
uuid: ID

" Date when the episode was published (Epoch time in seconds) "
datePublished: Int

" The name of an episode "
name: String

" The description for a episode "
description(
  " (Optional) Option to remove the html tags from the description or leave the description as is (which might include html tags). Default is false (do not strip html tags from description)."
  shouldStripHtmlTags: Boolean
): String

" Extract all links from within the description. " 
descriptionLinks: [String]

" Cover Art for the episode (it may be different than podcast cover art) "
imageUrl: String

" A different hash means that details for this episode have updated since the last hash "
hash: String

" An episode's unique identifier from its RSS feed "
guid: String

" The subtitle of an episode (shorter version of episode description, limited to 255 characters long) "
subtitle: String

" Link to Audio Content "
audioUrl: String

" Link to Video Content "
videoUrl: String

" File Length of Content "
fileLength: Int

" Exact File Type of Content "
fileType: String

" Duration of Content (in seconds) "
duration: Int

" Episode Type ie) trailer, bonus or full "
episodeType: PodcastEpisodeType

" Season Number (if provided) "
seasonNumber: Int

" Episode Number (if provided) "
episodeNumber: Int

" Website Link for episode "
websiteUrl: String

" If the episode contain's explicit content "
isExplicitContent: Boolean

" If the episode has now been removed from the RSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the podcast for which this episode belongs to "
podcastSeries: PodcastSeries

" People listed on the episode including thier roles (Hosts, Guests, etc) "
persons: [Person]

" Status of transcript (complete, processing, not transcribed) "
taddyTranscribeStatus: PodcastEpisodeTranscriptionStatus

" Downloads the transcript, parses it and returns an array of text in paragraphs. "
transcript: [String]

" Download the transcript, parses it and return an array of transcript items (which includes text, speakers and timecodes) "
transcriptWithSpeakersAndTimecodes(
  " (Optional) Style option for transcript. Default is PARAGRAPH"
  style: TranscriptItemStyle
): [TranscriptItem]

" A list of urls where you can download the transcript for this episode "
transcriptUrls: [String]

" A list of urls where you can download the transcript for this episode, including more details "
transcriptUrlsWithDetails: [TranscriptLink]

" Download and parse the chapters "
chapters: [Chapter]

" A list of urls where you can download chapter details "
chaptersUrls: [String]

" A list of urls where you can download chapter details, including more details "
chaptersUrlsWithDetails: [ChapterLink]
```

### Referenced types in this document:

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisodeType](https://taddy.org/developers/podcast-api/podcast-episode-type)

[PodcastEpisodeTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastepisode-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[TranscriptItem](https://taddy.org/developers/podcast-api/episode-transcript-item)

[TranscriptLink](https://taddy.org/developers/podcast-api/episode-transcript-link)

[ChapterLink](https://taddy.org/developers/podcast-api/episode-chapter-link)

[Chapter](https://taddy.org/developers/podcast-api/episode-chapter-item)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get podcast details

Use getPodcastSeries to get details on a specific podcast. 

## Examples:

1. Get details on a podcast with the name â€œThis American Lifeâ€:

```jsx
{
  getPodcastSeries(name:"This American Life"){
    uuid
    name
    itunesId
    description
    imageUrl
    totalEpisodesCount
    itunesInfo{
      uuid
      publisherName
      baseArtworkUrlOf(size: 640)
    }
  }
}
```

Example response from this query:

```jsx
{
  "data": {
    "getPodcastSeries": {
      "uuid": "d682a935-ad2d-46ee-a0ac-139198b83bcc",
      "name": "This American Life",
      "itunesId": 201671138,
      "description": "Each week we choose a theme. Then anything can happen. This American Life is true stories that unfold like little movies for radio. Personal stories with funny moments, big feelings, and surprising plot twists. Newsy stories that try to capture what itâ€™s like to be alive right now. Itâ€™s the most popular weekly podcast in the world, and winner of the first ever Pulitzer Prize for a radio show or podcast. Hosted by Ira Glass and produced in collaboration with WBEZ Chicago.",
      "imageUrl": "https://thisamericanlife.org/sites/all/themes/thislife/img/tal-logo-3000x3000.png",
      "totalEpisodesCount": 11,
      "itunesInfo": {
        "uuid": "d682a935-ad2d-46ee-a0ac-139198b83bcc",
        "publisherName": "This American Life",
        "baseArtworkUrlOf": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts123/v4/4e/b9/bb/4eb9bb9b-ed19-f0b7-7739-1177f1b35207/mza_8452563123961176873.png/640x640bb.png"
      },
    }
  }
}
```

2. Get details on a podcast by its uuid and return the most recent 10 episodes for the podcast

```jsx
{
  getPodcastSeries(uuid:"d682a935-ad2d-46ee-a0ac-139198b83bcc"){
    uuid
    name
    itunesId
    description
    imageUrl
    totalEpisodesCount
    itunesInfo{
      uuid
      publisherName
      baseArtworkUrlOf(size: 640)
    }
    episodes{
      uuid
      name
      description
      audioUrl
    }
  }
}
```

3. Get the 2nd page of results of the most recent episodes. (Pagination)

```jsx
{
  getPodcastSeries(uuid:"d682a935-ad2d-46ee-a0ac-139198b83bcc"){
    uuid
    name
    itunesId
    description
    imageUrl
    totalEpisodesCount
    itunesInfo{
      uuid
      publisherName
      baseArtworkUrlOf(size: 640)
    }
    episodes(page:2, limitPerPage:10){
      uuid
      name
      description
      audioUrl
    }
  }
}
```

## Query Input:

For getPodcastSeries, you can get details on any podcast by passing in any one of the following:

```jsx
" Taddy's unique identifier (uuid) "
uuid: ID

" A podcast's iTunes ID "
itunesId: Int

" A podcast's RSS Feed "
rssUrl: String

" The name (title) of a podcast. Note: Multiple podcasts can have the exact same name, in that case we always try to return the most popular podcast (based on infomation we have on the podcast popularity)"
name: String
```

## Query Response:

The response you get back is a [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries). That means you can return any of the following details:

```jsx
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

### Referenced types in this document:

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[SortOrder](https://taddy.org/developers/podcast-api/sort-order)

[PodcastSeriesType](https://taddy.org/developers/podcast-api/podcast-series-type)

[PodcastContentType](https://taddy.org/developers/podcast-api/podcast-content-type)

[PodcastSeriesTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastseries-transcription-status)

[PopularityRank](https://taddy.org/developers/podcast-api/popularity-rank)

[Person](https://taddy.org/developers/podcast-api/person)

[Genre](https://taddy.org/developers/podcast-api/genre)

[iTunesInfo](https://taddy.org/developers/podcast-api/itunesinfo)

[Language](https://taddy.org/developers/podcast-api/language)

[FeedRefreshDetails](https://taddy.org/developers/podcast-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get the most popular podcasts

Use getPopularContent to get details on the most popular podcasts. Moreover, you can filter by language or genre. 

## Example

To get the most popular podcasts in English:

```jsx
{
  getPopularContent(filterByLanguage:ENGLISH){
    popularityRankId
    podcastSeries{
      uuid
      name
      description
      popularityRank
    }
  }
}
```

To get the most popular podcasts in the Business genre. 

**Note:** filterByGenres takes an array of genres so that you can use multiple genres and subgenres. Please see [Genre](https://taddy.org/developers/podcast-api/genre) for a full list of possible genres.

```jsx
{
  getPopularContent(filterByLanguage:ENGLISH, filterByGenres:[PODCASTSERIES_BUSINESS, PODCASTSERIES_NEWS_BUSINESS, PODCASTSERIES_BUSINESS_CAREERS, PODCASTSERIES_BUSINESS_INVESTING, PODCASTSERIES_BUSINESS_MARKETING, PODCASTSERIES_BUSINESS_MANAGEMENT, PODCASTSERIES_BUSINESS_NON_PROFIT, PODCASTSERIES_BUSINESS_ENTREPRENEURSHIP]){
    popularityRankId
    podcastSeries{
      uuid
      name
      description
      popularityRank
    }
  }
}
```

## Query Input:

For getPopularContent, you can get back a list of podcasts with the following input:

```jsx
" (Optional) Allows for filtering by language. "
filterByLanguage: Language

" (Optional) Allows for filtering by genres. "
filterByGenres: [Genre!]

" (Optional) Allows for pagination. Default is 1 (ie: page 1 of the results). Max value is 20. "
page: Int

" (Optional) The number of results per page. Default is 10. Max value is 25 (ie: that max results you can return in one query in 25) "
limitPerPage: Int
```

## Query Response:

The response you get back includes an array of [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries) . 

```jsx
" Identifier for the popularity query being sent (Used for caching) "
popularityRankId: ID!

" A list of PodcastSeries items "
podcastSeries: [PodcastSeries]
```

### Referenced types in this document:

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[SortOrder](https://taddy.org/developers/podcast-api/sort-order)

[PodcastSeriesType](https://taddy.org/developers/podcast-api/podcast-series-type)

[PodcastContentType](https://taddy.org/developers/podcast-api/podcast-content-type)

[PodcastSeriesTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastseries-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[iTunesInfo](https://taddy.org/developers/podcast-api/itunesinfo)

[Language](https://taddy.org/developers/podcast-api/language)

[FeedRefreshDetails](https://taddy.org/developers/podcast-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get the most recent episodes

Use getLatestPodcastEpisodes if you are building a podcast feed and want to return the most recent episodes from multiple podcasts.

## Example

```jsx
{
  getLatestPodcastEpisodes(uuids:["fc3c8bb1-1b2a-4619-a029-8866610ae292", "f8de4489-7054-4ce1-9325-87fb4a69e123", "afd89860-866c-4ceb-b4f0-c290d77dad90", "c86d311c-0744-4f6b-9e29-3317d9626a37"]){
    uuid
    name
    datePublished
    audioUrl
	}
}
```

Example response from this query:

```jsx
{
  "data": {
    "getLatestPodcastEpisodes": [
      {
        "uuid": "026c604c-1dcb-49b5-821e-22746d79f738",
        "name": "Richard III: Good Guy or Evil Putz?",
        "datePublished": 1745312400,
        "audioUrl": "https://podtrac.com/pts/redirect.mp3/pscrb.fm/rss/p/traffic.omny.fm/d/clips/e73c998e-6e60-432f-8610-ae210140c5b1/a91018a4-ea4f-4130-bf55-ae270180c327/dd66d63a-6391-4af2-ac7e-b2c6014dcd39/audio.mp3?utm_source=Podcast&in_playlist=44710ecc-10bb-48d1-93c7-ae270180c33e"
      },
      {
        "uuid": "ef8dda4d-b653-4940-b3df-811ee35215f6",
        "name": "MURDERED: Solomon Robinson",
        "datePublished": 1745218800,
        "audioUrl": "https://tracking.swap.fm/track/fxUKVg2nSMaPSHLeKNKH/pscrb.fm/rss/p/stitcher.simplecastaudio.com/ec74d48c-cbf1-4764-923e-7d584dce50fa/episodes/0cccb36f-e11a-4ffa-a8f6-a7d78105fdea/audio/128/default.mp3?aid=rss_feed&awCollectionId=ec74d48c-cbf1-4764-923e-7d584dce50fa&awEpisodeId=0cccb36f-e11a-4ffa-a8f6-a7d78105fdea&feed=qm_9xx0g"
      },
      {
        "uuid": "3ec2d79b-eb21-45e6-8d08-d788c39b37d3",
        "name": "Lore 278: Bad For Business",
        "datePublished": 1745208060,
        "audioUrl": "https://pdst.fm/e/pscrb.fm/rss/p/claritaspod.com/measure/dts.podtrac.com/redirect.mp3/mgln.ai/e/35/traffic.libsyn.com/secure/lorepodcast/Lore278b.mp3?dest-id=259905"
      },
      //returns 25 episodes in total
  }
}
```

## Query Input:

For getLatestPodcastEpisodes, you can get back the most recent episodes with the following parameterst:

```jsx
" An Array of taddy's unique identifier (uuid). Max 1000 uuids allowed "
uuids: [ID],

" An Array of urls to the RSS feeds for the podcasts. Max 1000 rssUrls allowed "
rssUrls: [String],

" Allows for pagination. The page number to return. Default is 1. Max value is 20 "
page: Int,

" Allows for pagination. The number of episodes to return per page. Default is 25. Max value is 50 "
limitPerPage: Int
```

**Notes:**

- The query supports up to 1,000 UUIDs (or rssUrls) per request.
- Under the hood this can be a complex query, and because of that, Free users are limited to only page 1 and to only return 25 episodes (the default for the query). Paid Taddy API users can get paginated responses and return up to 50 episodes back.

## Query Response:

The response you get back is a [PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode). That means you can get back any of the following details for a podcast episode:

```jsx
" Taddy's unique identifier (an uuid) "
uuid: ID

" Date when the episode was published (Epoch time in seconds) "
datePublished: Int

" The name of an episode "
name: String

" The description for a episode "
description(
  " (Optional) Option to remove the html tags from the description or leave the description as is (which might include html tags). Default is false (do not strip html tags from description)."
  shouldStripHtmlTags: Boolean
): String

" Extract all links from within the description. " 
descriptionLinks: [String]

" Cover Art for the episode (it may be different than podcast cover art) "
imageUrl: String

" A different hash means that details for this episode have updated since the last hash "
hash: String

" An episode's unique identifier from its RSS feed "
guid: String

" The subtitle of an episode (shorter version of episode description, limited to 255 characters long) "
subtitle: String

" Link to Audio Content "
audioUrl: String

" Link to Video Content "
videoUrl: String

" File Length of Content "
fileLength: Int

" Exact File Type of Content "
fileType: String

" Duration of Content (in seconds) "
duration: Int

" Episode Type ie) trailer, bonus or full "
episodeType: PodcastEpisodeType

" Season Number (if provided) "
seasonNumber: Int

" Episode Number (if provided) "
episodeNumber: Int

" Website Link for episode "
websiteUrl: String

" If the episode contain's explicit content "
isExplicitContent: Boolean

" If the episode has now been removed from the RSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the podcast for which this episode belongs to "
podcastSeries: PodcastSeries

" People listed on the episode including thier roles (Hosts, Guests, etc) "
persons: [Person]

" Status of automatically generated transcription "
taddyTranscribeStatus: PodcastEpisodeTranscriptionStatus

" Downloads the transcript, parses it and returns an array of text in paragraphs. "
transcript: [String]

" A list of urls where you can download the transcript for this episode "
transcriptUrls: [String]

" A list of urls where you can download the transcript for this episode, including more details "
transcriptUrlsWithDetails: [TranscriptLink]

" Download the transcript, parse it and return an array of transcript items (which include text, speakers and timecodes) "
transcriptWithSpeakersAndTimecodes(
  " (Optional) Style option for transcript. Default is PARAGRAPH"
  style: TranscriptItemStyle
): [TranscriptItem]

" Download and parse the chapters "
chapters: [Chapter]

" A list of urls where you can download chapter details "
chaptersUrls: [String]

" A list of urls where you can download chapter details, including more details "
chaptersUrlsWithDetails: [ChapterLink]
```

### Referenced types in this document:

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisodeType](https://taddy.org/developers/podcast-api/podcast-episode-type)

[PodcastEpisodeTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastepisode-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[TranscriptItem](https://taddy.org/developers/podcast-api/episode-transcript-item)

[TranscriptLink](https://taddy.org/developers/podcast-api/episode-transcript-link)

[ChapterLink](https://taddy.org/developers/podcast-api/episode-chapter-link)

[Chapter](https://taddy.org/developers/podcast-api/episode-chapter-item)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Language

A list of possible Languages. Conforms to [ISO 639-2](https://www.loc.gov/standards/iso639-2/php/code_list.php)

```jsx
enum Language {
  ABKHAZIAN
  AFAR
  AFRIKAANS
  AKAN
  ALBANIAN
  AMHARIC
  ARABIC
  ARAGONESE
  ARMENIAN
  ASSAMESE
  AVARIC
  AVESTAN
  AYMARA
  AZERBAIJANI
  BAMBARA
  BASHKIR
  BASQUE
  BELARUSIAN
  BENGALI
  BIHARI_LANGUAGES
  BISLAMA
  BOSNIAN
  BRETON
  BULGARIAN
  BURMESE
  CENTRAL_KHMER
  CHAMORRO
  CHECHEN
  CHICHEWA_CHEWA_NYANJA
  CHINESE
  CHURCH_SLAVONIC
  CHUVASH
  CORNISH
  CORSICAN
  CREE
  CROATIAN
  CZECH
  DANISH
  DHIVEHI_MALDIVIAN
  DUTCH_FLEMISH
  DZONGKHA
  ENGLISH
  ESPERANTO
  ESTONIAN
  EWE
  FAROESE
  FARSI
  FIJIAN
  FINNISH
  FRENCH
  FULAH
  GAELIC
  GALICIAN
  GANDA
  GEORGIAN
  GERMAN
  GIKUYU
  GREEK
  GUARANI
  GUJARATI
  HAITIAN_CREOLE
  HAUSA
  HEBREW
  HERERO
  HINDI
  HIRI_MOTU
  HUNGARIAN
  ICELANDIC
  IDO
  IGBO
  INDONESIAN
  INTERLINGUA
  INTERLINGUE_OCCIDENTAL
  INUKTITUT
  INUPIAQ
  IRISH
  ITALIAN
  JAPANESE
  JAVANESE
  KALAALLISUT_GREENLANDIC
  KANNADA
  KANURI
  KASHMIRI
  KAZAKH
  KINYARWANDA
  KOMI
  KONGO
  KOREAN
  KURDISH
  KWANYAMA
  KYRGYZ
  LAO
  LATIN
  LATVIAN
  LETZEBURGESCH
  LIMBURGISH
  LINGALA
  LITHUANIAN
  LUBA_KATANGA
  MACEDONIAN
  MALAGASY
  MALAY
  MALAYALAM
  MALTESE
  MANX
  MAORI
  MARATHI
  MARSHALLESE
  MONGOLIAN
  NAURU
  NAVAJO
  NDONGA
  NEPALI
  NORTH_NDEBELE
  NORTHERN_SAMI
  NORWEGIAN
  NORWEGIAN_BOKMAL
  NORWEGIAN_NYNORSK
  NUOSU_SICHUAN_YI
  OCCITAN
  OJIBWA
  ORIYA
  OROMO
  OSSETIAN
  PALI
  PASHTO
  POLISH
  PORTUGUESE
  PUNJABI
  QUECHUA
  ROMANIAN_MOLDOVAN
  ROMANSH
  RUNDI
  RUSSIAN
  SAMOAN
  SANGO
  SANSKRIT
  SARDINIAN
  SERBIAN
  SHONA
  SINDHI
  SINHALA
  SLOVAK
  SLOVENIAN
  SOMALI
  SOTHO
  SOUTH_NDEBELE
  SPANISH
  SUNDANESE
  SWAHILI
  SWATI
  SWEDISH
  TAGALOG
  TAHITIAN
  TAJIK
  TAMIL
  TATAR
  TELUGU
  THAI
  TIBETAN
  TIGRINYA
  TONGA
  TSONGA
  TSWANA
  TURKISH
  TURKMEN
  TWI
  UKRAINIAN
  URDU
  UYGHUR
  UZBEK
  VALENCIAN_CATALAN
  VENDA
  VIETNAMESE
  VOLAPUK
  WALLOON
  WELSH
  WESTERN_FRISIAN
  WOLOF
  XHOSA
  YIDDISH
  YORUBA
  ZHUANG
  ZULU
}
```

---

# Listen Notes API vs Taddy API - Which is better?

## TLDR - Who is the winner?

Both Listen Notes Podcast API and Taddy Podcast API share the same goal of making it easy for you to integrate podcast data into your app.

Behind the scenes, both have built the podcast infrastructure needed to provide an up-to-date directory of all podcasts and episodes, providing it to you over an API that allows you to integrate podcast data into your app from months to minutes.

There are 3 key differences between the Listen Notes and Taddy APIs:

1. **Owning the data you get back from the API**Â - As per the Listen Notes APIÂ [Terms of Service](https://www.listennotes.com/api/terms/) you are required to purchase an Enterprise license if you would like to save the information you get back from its API on your own servers and requires you to show aÂ ["Powered by Listen Notes"](https://www.listennotes.com/api/terms/)Â banner inside your app. This same restriction does not apply when using Taddy's API - you have full data ownership and can cache responses on your own servers.
2. **Transcripts**Â - Paid users of Taddy API can access the transcript for any episode. Listen Notes API provides transcripts for less than 1% of all episodes (you only get access to a transcript if it is provided by the podcast creator) and they provide another paid service called Listen411 that allows you to get the transcript for any episode. 
3. **Webhook notifications**Â - Taddy API gives you the ability to send webhook notifications to your server for new or updated podcast data. Listen Notes API does not provide this feature.

Below we go over a feature by feature comparison to help you get a summary of how to get the most out of each API and why we think Taddy's API is better value for what you get and a great alternative to the Listen Notes API.

## Feature Breakdown

| Feature | Taddy API | Listen Notes API |
| --- | --- | --- |
| Get details on a podcast | âœ…Â - See [getPodcastSeries](https://taddy.org/developers/podcast-api/get-podcast-series) | âœ… |
| Get details on an episode | âœ… - See [getPodcastEpisode](https://taddy.org/developers/podcast-api/get-podcast-episode) | âœ… |
| Search for podcasts | âœ…Â - See [search](https://taddy.org/developers/podcast-api/search) | âœ… |
| Search for episodes | âœ…Â - See [search](https://taddy.org/developers/podcast-api/search) | âœ… |
| Episode Transcripts | âœ…Â - See [getEpisodeTranscript](https://taddy.org/developers/podcast-api/episode-transcripts) | âŒ Partial (Less than 1% of episodes in Listen Notesâ€™ directory have a transcript available) |
| Save data from API on your server | âœ…Â Full ownership | âŒ Enterprise only. |
| White-label solution | âœ… Your brand | âŒÂ  "Powered by Listen Notes" Logo required unless you are on the Enterprise Plan. |
| Webhooks | âœ…Â - Get a [webhook](https://taddy.org/developers/podcast-api/webhooks) notification whenever a new podcast or episode is released. | âŒÂ You poll the API to check for new or updated episodes. |
| Type of API | GraphQL | REST |
| Price | Free -  500 API Requests per month. 

[Pro - $75/month](https://taddy.org/developers/pricing) for 100,000 API Requests per month. 

[Business - $150/month](https://taddy.org/developers/pricing) for 350,000 API Requests per month. 
 | Free -  300 API Requests per month. 

[Paid - $200/month](https://www.listennotes.com/api/pricing/) for 5,000 API Requests per month. 

[Paid - $352/month](https://www.listennotes.com/api/pricing/) for 100,000 API Requests per month. 

[Paid - $752/month](https://www.listennotes.com/api/pricing/) for 350,000 API Requests per month.  |

### Getting details on a podcast

Both APIs give you a way to get back details on a podcast.

Taddy API: See [**getPodcastSeries**](https://taddy.org/developers/podcast-api/get-podcast-series).

Listen Notes API: See [**/podcasts/id**](https://www.listennotes.com/api/docs/#get-api-v2-podcasts-id) endpoint.

### Getting details on an episode

Both APIs give you a way to get back details on an episode.

Taddy API: See [**getPodcastEpisode**](https://taddy.org/developers/podcast-api/get-podcast-episode).

Listen Notes API: See [**/episodes/id**](https://www.listennotes.com/api/docs/#get-api-v2-episodes-id) endpoint.

### Search for podcasts or episodes

Both APIs give you a way to perform a text search on millions of podcasts & episodes.

Taddy API: See [**search**](https://taddy.org/developers/podcast-api/search).

Listen Notes API: See [**/search**](https://www.listennotes.com/api/docs/#get-api-v2-search) endpoint.

### Total Number of Podcasts & Episodes available

Taddy API: 4+ million podcasts, 180+ million episodes

Listen Notes API: 4+ million podcasts, 180+ million episodes

### Episode Transcripts

Paid users of **Taddy API**Â can access the transcript for any podcast episode. 

- Pro users get 100 episode transcripts/month
- Business Users get 2000 episodes transcripts / month included in their Business Plan and can [purchase more episode transcripts](https://taddy.org/developers/pricing) if they need them.

**Listen Notes** provides transcripts for less than 1% of episodes that are in their directory (these are the transcripts that are provided by the podcast creators themselves). If you want to get the transcript for the other 99% of episodes, they offer a separate paid service, Listen411, which allows you to transcribe any episode.

For apps that need transcripts for accessibility, search, or AI features, Taddy's built-in transcript feature is a key differentiator.

### White-labelled solution

Taddy API can be used behind the scenes without your customers knowing what API you are using.

As per the Listen Notes API [Terms of Service](https://www.listennotes.com/api/terms/), you are required to add a â€œPowered by Listen Notesâ€ badge to your user interface unless you are an Enterprise User.

### Store and Cache any data you get back from the API

Taddy API allows you to save any data returned from Taddyâ€™s API. Other than the price and performance benefits for being able to cache data on your own servers, you get the added benefit of not being locked into Taddy API and can switch API providers anytime if you ever choose to do so.

The Listen Notes API [Terms of Service](https://www.listennotes.com/api/terms/) do not allow you to cache or store any data returned via the API on your server unless you are an Enterprise User.

### Hosted Audio & Podcast Artwork

Listen Notes API provides an internal redirect to the audio file (not the original audio url link) and re-uploads / hosts podcast artwork on their own image servers. For the podcast artwork, the pro of this is that developers get a consistent image server and resizing options for images but using Listen Notes audio links and podcast artwork does lock you into their API as these links stop working once you stop being a paid user.

Taddy API provides you the original audio url link and does not re-upload or host our own podcast artwork. You can choose to either use the artwork provided by the podcast or use iTunes hosted artwork (including their different sizes), you can get a link to these via our API.

### Webhooks

Taddy API gives you the ability to set up a [webhook](https://taddy.org/developers/podcast-api/webhooks) endpoint where you will receive notifications whenever there is a new or updated podcast or episode. 

This can be a critical feature depending on what app you are trying to build because it means you don't have to constantly check our API to see if a podcast has updated information, Taddy will push these updates immediately to you. A common example why this is useful is if you want to send a push notification to your users about a newly released episode. 

### **REST vs GraphQL**

Taddy API uses GraphQL, which lets you request exactly the data you need in a single query. For example, you can fetch a podcast's title, the latest 10 episodes, and only the duration and transcript for each episode, all in one request. 

Listen Notes API uses REST, which will be more familiar to most developers. With REST, you typically work with predefined endpoints that return fixed data structures, and you might need multiple API calls to gather related information.

While REST's simplicity and widespread familiarity are advantages, GraphQL eliminates over-fetching and under-fetching of data, reduces the number of network requests, and gives you complete control over the data you get back.

### Price

Taddy API: 

Free Plan - 500 API Requests / month

Pro Plan - $75/month for 100,000 API Requests. Access to episode transcripts. 

Business Plan - $150/month for 350,000 API Requests. Access to more episode transcripts & webhook notifications.

Listen Notes API: 

Free Plan - 300 API Requests / month

Pro Plan: 

$200/month for 5,000 API Requests. 

$352/month for 100,000 API Requests

$752/month for 350,000 API Requests

## Summary

Both the Listen Notes API and Taddy API are well documented and well built APIs. The three main drawbacks with the Listen Notes API are the control you get over the data you get back from the API, plus the lack of transcripts and webhook notifications. We believe this makes Taddy API a better value for what you get and a great alternative to the Listen Notes API. 

Make sure to check our list of the [ðŸ¥‡Top 5 Podcast APIs](https://taddy.org/blog/best-podcast-api-tools) if you would like a summary of all the different Podcast API options available.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

![Frame 1.png](The%205%20Top%20Podcast%20APIs%20dde76975ff654a98b59c8e6efe18d62a/Frame_1.png)

Article by: [Daniel Mathews](https://bsky.app/profile/dmathewwws.com)

Updated on September 12, 2025

More Links:  

[Sign Up For Taddy â†’](https://taddy.org/signup/developers)

[Podcast API](https://taddy.org/developers/podcast-api)

[The 5 Top Podcast APIs](https://taddy.org/blog/best-podcast-api-tools)

[iTunes API vs Taddy API - Which is better?](https://taddy.org/blog/itunes-vs-taddy-podcast-api)

[PodcastIndex.org API vs Taddy - Which is better?](https://taddy.org/blog/podcastindex-vs-taddy-podcast-api)

---

# Person

People listed on the podcast including their roles (Host, Guest, etc). This is taken from the [<podcast:person>](https://podcasting2.org/podcast-namespace/tags/person) tag in the podcastâ€™s RSS feed.

```jsx
type Person {
  " Unique identifier (an uuid) for a person "
  uuid: ID

  " The name of the person "
  name: String

  " The role of the person on the podcast "
  role: ContentRole

  " The url for the person "
  url: String

  " The url of the person's image "
  imageUrl: String
}
```

### ContentRole

Roles performed by the people listed on the podcast. Follows format: TYPE_ROLE_SUBROLE

```jsx
enum ContentRole {
  PODCASTSERIES_DIRECTOR
  PODCASTSERIES_ASSISTANT_DIRECTOR
  PODCASTSERIES_EXECUTIVE_PRODUCER
  PODCASTSERIES_SENIOR_PRODUCER
  PODCASTSERIES_PRODUCER
  PODCASTSERIES_ASSOCIATE_PRODUCER
  PODCASTSERIES_DEVELOPMENT_PRODUCER
  PODCASTSERIES_CREATIVE_DIRECTOR
  PODCASTSERIES_HOST
  PODCASTSERIES_CO_HOST
  PODCASTSERIES_GUEST_HOST
  PODCASTSERIES_GUEST
  PODCASTSERIES_VOICE_ACTOR
  PODCASTSERIES_NARRATOR
  PODCASTSERIES_ANNOUNCER
  PODCASTSERIES_REPORTER
  PODCASTSERIES_AUTHOR
  PODCASTSERIES_EDITORIAL_DIRECTOR
  PODCASTSERIES_CO_WRITER
  PODCASTSERIES_WRITER
  PODCASTSERIES_SONGWRITER
  PODCASTSERIES_GUEST_WRITER
  PODCASTSERIES_STORY_EDITOR
  PODCASTSERIES_MANAGING_EDITOR
  PODCASTSERIES_SCRIPT_EDITOR
  PODCASTSERIES_SCRIPT_COORDINATOR
  PODCASTSERIES_RESEARCHER
  PODCASTSERIES_EDITOR
  PODCASTSERIES_FACT_CHECKER
  PODCASTSERIES_TRANSLATOR
  PODCASTSERIES_TRANSCRIBER
  PODCASTSERIES_LOGGER
  PODCASTSERIES_STUDIO_COORDINATOR
  PODCASTSERIES_TECHNICAL_DIRECTOR
  PODCASTSERIES_TECHNICAL_MANAGER
  PODCASTSERIES_AUDIO_ENGINEER
  PODCASTSERIES_REMOTE_RECORDING_ENGINEER
  PODCASTSERIES_POST_PRODUCTION_ENGINEER
  PODCASTSERIES_AUDIO_EDITOR
  PODCASTSERIES_SOUND_DESIGNER
  PODCASTSERIES_FOLEY_ARTIST
  PODCASTSERIES_COMPOSER
  PODCASTSERIES_THEME_MUSIC
  PODCASTSERIES_MUSIC_PRODUCTION
  PODCASTSERIES_MUSIC_CONTRIBUTOR
  PODCASTSERIES_PRODUCTION_COORDINATOR
  PODCASTSERIES_BOOKING_COORDINATOR
  PODCASTSERIES_PRODUCTION_ASSISTANT
  PODCASTSERIES_CONTENT_MANAGER
  PODCASTSERIES_MARKETING_MANAGER
  PODCASTSERIES_SALES_REPRESENTATIVE
  PODCASTSERIES_SALES_MANAGER
  PODCASTSERIES_GRAPHIC_DESIGNER
  PODCASTSERIES_COVER_ART_DESIGNER
  PODCASTSERIES_SOCIAL_MEDIA_MANAGER
  PODCASTSERIES_MISC_CONSULTANT
  PODCASTSERIES_MISC_INTERN
  PODCASTSERIES_CAMERA_OPERATOR
  PODCASTSERIES_LIGHTING_DESIGNER
  PODCASTSERIES_CAMERA_GRIP
  PODCASTSERIES_ASSISTANT_CAMERA
  PODCASTSERIES_ASSISTANT_EDITOR
}
```

---

# PodcastContentType

Distinguishes between Audio and Video podcasts

```jsx
enum PodcastContentType {
  AUDIO
  VIDEO
}
```

---

# PodcastEpisode

GraphQL Type for a Podcast Episode.

```jsx
" Taddy's unique identifier (an uuid) "
uuid: ID

" Date when the episode was published (Epoch time in seconds) "
datePublished: Int

" The name of an episode "
name: String

" The description for a episode "
description(
  " (Optional) Option to remove the html tags from the description or leave the description as is (which might include html tags). Default is false (do not strip html tags from description)."
  shouldStripHtmlTags: Boolean
): String

" Extract all links from within the description. " 
descriptionLinks: [String]

" Cover Art for the episode (it may be different than podcast cover art) "
imageUrl: String

" A different hash means that details for this episode have updated since the last hash "
hash: String

" An episode's unique identifier from its RSS feed "
guid: String

" The subtitle of an episode (shorter version of episode description, limited to 255 characters long) "
subtitle: String

" Link to Audio Content "
audioUrl: String

" Link to Video Content "
videoUrl: String

" File Length of Content "
fileLength: Int

" Exact File Type of Content "
fileType: String

" Duration of Content (in seconds) "
duration: Int

" Episode Type ie) trailer, bonus or full "
episodeType: PodcastEpisodeType

" Season Number (if provided) "
seasonNumber: Int

" Episode Number (if provided) "
episodeNumber: Int

" Website Link for episode "
websiteUrl: String

" If the episode contain's explicit content "
isExplicitContent: Boolean

" If the episode has now been removed from the RSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the podcast for which this episode belongs to "
podcastSeries: PodcastSeries

" People listed on the episode including thier roles (Hosts, Guests, etc) "
persons: [Person]

" Status of transcript (complete, processing, not transcribed) "
taddyTranscribeStatus: PodcastEpisodeTranscriptionStatus

" Downloads the transcript, parses it and returns an array of text in paragraphs. "
transcript: [String]

" Download the transcript, parses it and return an array of transcript items (which includes text, speakers and timecodes) "
transcriptWithSpeakersAndTimecodes(
  " (Optional) Style option for transcript. Default is PARAGRAPH"
  style: TranscriptItemStyle
): [TranscriptItem]

" A list of urls where you can download the transcript for this episode "
transcriptUrls: [String]

" A list of urls where you can download the transcript for this episode, including more details "
transcriptUrlsWithDetails: [TranscriptLink]

" Download and parse the chapters "
chapters: [Chapter]

" A list of urls where you can download chapter details "
chaptersUrls: [String]

" A list of urls where you can download chapter details, including more details "
chaptersUrlsWithDetails: [ChapterLink]
```

### Other Referenced types in this document:

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisodeType](https://taddy.org/developers/podcast-api/podcast-episode-type)

[PodcastEpisodeTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastepisode-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[TranscriptItem](https://taddy.org/developers/podcast-api/episode-transcript-item)

[TranscriptLink](https://taddy.org/developers/podcast-api/episode-transcript-link)

[ChapterLink](https://taddy.org/developers/podcast-api/episode-chapter-link)

[Chapter](https://taddy.org/developers/podcast-api/episode-chapter-item)

---

# PodcastEpisodeTranscriptionStatus

Useful to check if Taddy is automatically generating transcripts and chapters for this podcast

```jsx
enum PodcastEpisodeTranscriptionStatus {
  NOT_TRANSCRIBING
  PROCESSING
  COMPLETED
  FAILED
}
```

`COMPLETED` - Transcript are now available. (This can be either be because the podcast has provided it or because we have automatically transcribed the episode)

`PROCESSING` - Currently in a queue, waiting to be transcribed. Please note that there can be 10k+ episodes that are queued to be transcribed (so it does not necessarily mean it is going to be transcribed in the next couple minutes).

`NOT_TRANSCRIBING` - We are not planning to transcribe this episode.

`FAILED` - There was an error generating a transcript for this episode.

For episodes in the `PROCESSING` or  `NOT_TRANSCRIBING` state, you still can get the transcript, however they are generated on-demand (takes ~10 seconds to transcribe every ~1hr of audio).

Please see [transcripts](https://taddy.org/developers/podcast-api/episode-transcripts) for more details.

---

# PodcastEpisodeType

Used to distinguish between different types of episodes.

```jsx
enum PodcastEpisodeType {
  FULL
  TRAILER
  BONUS
}
```

`FULL` (Default) - A regular episode

`TRAILER` - A promotional piece of content that represents a preview of the podcast. 

`BONUS` - Bonus or extra content (for example, behind the scenes information or interviews with the cast or cross-promotional content for another podcast).

---

# PodcastIndex.org API vs Taddy - Which is better?

## TLDR - Who is the winner?

Both PodcastIndexâ€™s Podcast API and Taddy Podcast API make it easy for you to integrate podcast data into your app. However, they come at it from very different perspectives.

PodcastIndexâ€™s is 100% free, co-founded by [Adam Curry](https://podcastindex.social/@adam) (one of the [inventors of podcasts](https://podyssey.fm/podcast/itunes430333725/episode243611023-Theyre-called-Podcasts-The-Vergecast)), and has the mission of contributing to an open and independent podcasting ecosystem. It provides an up-to-date directory of all podcasts and episodes and basic search but doesn't try to do more than that. 

Taddy Podcast API builds on top of that, along with having an up-to-date directory of all podcast and episodes it also provides episode transcripts, webhooks, advanced search, etc that make it easy to integrate podcast data into your app in minutes, instead of months. Taddy APIâ€™s mission is to provide a fair and affordable service so that app developers can focus on what makes their app unique without having to build and maintaining their own podcast infrastructure.

Below we go over a feature by feature comparison to help you get a summary of how to get the most out of each API:

## Feature Breakdown

| Feature | Taddy API | PodcastIndex API |
| --- | --- | --- |
| Get details on a podcast | âœ…Â - See [getPodcastSeries](https://taddy.org/developers/podcast-api/get-podcast-series) | âœ… |
| Get details on an episode | âœ… - See [getPodcastEpisode](https://taddy.org/developers/podcast-api/get-podcast-episode) | âœ… |
| Search for podcasts | âœ…Â - See [search](https://taddy.org/developers/podcast-api/search) | âœ… |
| Search for episodes | âœ…Â - See [search](https://taddy.org/developers/podcast-api/search) | âŒ |
| Episode Transcripts | âœ…Â - See [getEpisodeTranscript](https://taddy.org/developers/podcast-api/episode-transcripts) | âŒ Partial (Less than 1% of episodes in PodcastIndexâ€™s directory have a transcript available) |
| Webhooks | âœ…Â - Get a [webhook](https://taddy.org/developers/podcast-api/webhooks) notification whenever a new podcast or episode is released. | âŒ |
| Type of API | GraphQL | REST |
| Price | Free -  500 API Requests per month. 

[Paid - $75/month](https://taddy.org/developers/pricing) for 100,000 Requests per month. 

[Business - $150/month](https://taddy.org/developers/pricing) for 350,000 Requests per month.  | Free |

### Getting details on a podcast

Both APIs give you a way to get back details on a podcast.

Taddy API: See [**getPodcastSeries**](https://taddy.org/developers/podcast-api/get-podcast-series).

PodcastIndex API: See [/podcasts/byfeedurl](https://podcastindex-org.github.io/docs-api/#get-/podcasts/byfeedurl) endpoint.

### Getting details on an episode

Both APIs give you a way to get back details on an episode.

Taddy API: See [**getPodcastEpisode**](https://taddy.org/developers/podcast-api/get-podcast-episode).

PodcastIndex API: See [/episodes/byguid](https://podcastindex-org.github.io/docs-api/#get-/episodes/byguid) endpoint.

### Search for podcasts or episodes

Both APIs give you a way to perform a text search on millions of podcasts. However, only Taddy API allows searching on 180 million episodes.

Taddy API: See [**search](https://taddy.org/developers/podcast-api/search)** 

PodcastIndex API: See [**/search/byterm**](https://podcastindex-org.github.io/docs-api/#get-/search/byterm) endpoint

### Total Number of Podcasts & Episodes available

Taddy API: 4+ million podcasts, 95+ million episodes

PodcastIndex API: 4+ million podcasts, does not specify how many episodes

### Episode Transcripts

Paid users of **Taddy API**Â can access the transcript for any podcast episode. Business Users get 2000 episodes transcripts / month included in their Business Plan and can [purchase more episode transcripts](https://taddy.org/developers/pricing) if they need them.

PodcastIndex provides transcripts for leads than 1% of episodes that are in their directory (these are the transcripts that are provided by the podcast creators themselves). If you want to get the transcript for the other 99% of episodes, you will have to use an external transcription service.

For apps that need transcripts for accessibility, search, or AI features, Taddy's built-in transcript feature is a key differentiator.

### Webhooks

Taddy API gives you the ability to set up a [webhook](https://taddy.org/developers/podcast-api/webhooks) endpoint where you will receive notifications whenever there is a new or updated podcast or episode. 

This can be a critical feature depending on what app you are trying to build because it means you don't have to constantly check our API to see if a podcast has updated information, Taddy will push these updates immediately to you. A common example why this is useful is if you want to send a push notification to your users about a newly released episode. 

### **REST vs GraphQL**

Taddy API uses GraphQL, which lets you request exactly the data you need in a single query. For example, you can fetch a podcast's title, the latest 10 episodes, and only the duration and transcript for each episode, all in one request. 

PodcastIndex API uses REST, which will be more familiar to most developers. With REST, you typically work with predefined endpoints that return fixed data structures, and you might need multiple API calls to gather related information.

While REST's simplicity and widespread familiarity are advantages, GraphQL eliminates over-fetching and under-fetching of data, reduces the number of network requests, and gives you complete control over the data you get back.

### Price

PodcastIndex is 100% Free.

Taddy API: 

Free Plan - 500 API Requests / month

Pro Plan - $75/month for 100,000 API Requests / month

Business Plan - $150/month for 350,000 API Requests / month

## Summary

Both the PodcastIndex API and Taddy API are great APIs to use if you are building a podcast app but serve different needs. 

PodcastIndex provides essential, free infrastructure that ensures the podcast ecoysystem remain accessible and searchable. Taddy builds on this foundation to offer a more complete developer toolkit which can save you months of development time and thousands of dollars in your own infrastructure costs or third-party services.

Make sure to check our list of the [ðŸ¥‡Top 5 Podcast APIs](https://taddy.org/blog/best-podcast-api-tools) if you would like a summary of all the different Podcast API options available.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

![Frame 1.png](The%205%20Top%20Podcast%20APIs%20dde76975ff654a98b59c8e6efe18d62a/Frame_1.png)

Article by: [Daniel Mathews](https://bsky.app/profile/dmathewwws.com)

Updated on September 11, 2025

More Links:  

[Sign Up For Taddy â†’](https://taddy.org/signup/developers)

[Podcast API](https://taddy.org/developers/podcast-api)

[The 5 Top Podcast APIs](https://taddy.org/blog/best-podcast-api-tools)

[iTunes API vs Taddy API - Which is better?](https://taddy.org/blog/itunes-vs-taddy-podcast-api)

[Listen Notes API vs Taddy API - Which is better?](https://taddy.org/blog/listen-notes-vs-taddy-podcast-api)

---

# PodcastSeries

GraphQL Type for a Podcast. 

```jsx
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

### Referenced types in this document:

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[PodcastSeriesType](https://taddy.org/developers/podcast-api/podcast-series-type)

[PodcastContentType](https://taddy.org/developers/podcast-api/podcast-content-type)

[PodcastSeriesTranscriptionStatus](https://taddy.org/developers/podcast-api/podcastseries-transcription-status)

[Person](https://taddy.org/developers/podcast-api/person)

[SortOrder](https://taddy.org/developers/podcast-api/sort-order)

[iTunesInfo](https://taddy.org/developers/podcast-api/itunesinfo)

[FeedRefreshDetails](https://taddy.org/developers/podcast-api/feed-refresh-details)

---

# PodcastSeriesTranscriptionStatus

Useful to check if Taddy is automatically generating transcripts and chapters for this podcast

```jsx
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

---

# PodcastSeriesType

Used to distinguish between Episodic and Serial podcasts

```jsx
enum PodcastSeriesType {
	EPISODIC
	SERIAL
}
```

`EPISODIC` (Default) - When episodes are intended to be consumed without any specific order. This tells podcast players to show the latest episode first.

`SERIAL` - When episodes are intended to be consumed in sequential order ex) A True Crime Investigation. This tells podcast players to show the 1st episode first.

---

# PopularityRank

Returns how popular the podcast is. Returns null if in none of the below categories.

```jsx
enum PopularityRank {
  TOP_200 // is a top 200 podcast
  TOP_1000
  TOP_2000
  TOP_3000
  TOP_4000
  TOP_5000
  TOP_10000
  TOP_20000
  TOP_50000
  TOP_100000
  TOP_200000
}
```

---

# Search for podcasts or episodes

Use search to search through all 4 million podcasts and 180 million episodes in blazing fast speed.

## Examples:

### Searching

1. Searching for podcasts that match the term "Planet Money"

```jsx
{
  search(term:"Planet Money", filterForTypes:PODCASTSERIES){
    searchId
    podcastSeries{
      uuid
      name
      description
    }
  }
}
```

2. Searching for podcasts and episodes that match "Tim Ferriss"

```jsx
{
  search(term:"Tim Ferriss", filterForTypes:[PODCASTSERIES, PODCASTEPISODE]){
    searchId
    podcastSeries{
      uuid
      name
      description
    }
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
    }
  }
}
```

### Pagination

3. Searching for episodes from page 2 that match "Tim Ferriss"

```jsx
{
  search(term:"Tim Ferriss", filterForTypes:PODCASTEPISODE, page:2, limitPerPage: 25){
    searchId
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
    }
  }
}
```

### Sorting

By default, `search` will return the results that best match the exact term you pass it. However, you may want to prioritize results from popular podcasts over an exact match. 

4. When searching for podcasts with the term "Dentist" with sortBy: `EXACTNESS` (default) it will rank podcasts that have the exact title or description "Dentist" highest. 

However, if you use sortBy: `POPULARITY` it will give more weight to the podcastâ€™s popularity over an exact match. This is good if you care about more popular (and usually higher quality) results over an exact match. 

```jsx
{
  search(term:"Dentist", filterForTypes:PODCASTSERIES, sortBy:POPULARITY){
    searchId
    podcastSeries{
      uuid
      name
      description
    }
  }
}
```

**Note:** See [Sorting](https://taddy.org/developers/podcast-api/search-sort-by) documentation for more details.

### Matching

You have the ability to match the results being returned from `EXACT_PHRASE` (Strict), `ALL_TERMS`, or `MOST_TERMS`(Least strict & default). 

5. Searching "Jim Farley" episodes with matchBy: `EXACT_PHRASE` requires the exact phrase to be in the search results (which makes this option useful when searching for someoneâ€™s name)

```jsx
{
  search(term:"Jim Farley", filterForTypes:PODCASTEPISODE, matchBy:EXACT_PHRASE, sortBy:POPULARITY){
    searchId
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
      podcastSeries{
	      uuid
	      name
	    }
    }
  }
}
```

6. Searching "Music Theory Jazz" episodes with matchBy: `ALL_TERMS` requires all passed in words to be present.

```jsx
{
  search(term:"Music Theory Jazz", filterForTypes:PODCASTEPISODE, matchBy:ALL_TERMS, sortBy:POPULARITY){
    searchId
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
      podcastSeries{
	      uuid
	      name
	    }
    }
  }
}
```

**Note:** See [Matching](https://taddy.org/developers/podcast-api/search-match-by) documentation for more details.

### Filtering

You can filter your search for results from a specific country, genre, language, from a particular podcast, before or after a certain publish date, before or after the latest episode was published, or if there is a transcript available. You can see the full list of filtering options available in the [Query Input](https://taddy.org/developers/podcast-api/search) section below. 

7. Searching for Neil deGrasse Tyson episodes but only after a certain publish date

```jsx
{
  search(term:"Neil deGrasse Tyson", filterForTypes:PODCASTEPISODE, sortBy:POPULARITY, filterForPublishedAfter:1596649011){
    searchId
    podcastEpisodes{
      uuid
      name
      datePublished
      description
      audioUrl
      podcastSeries{
	      uuid
	      name
	    }
    }
  }
}
```

8. Searching for only Neil deGrasse Tyson episodes but only if they have a transcript available. 

```jsx
{
  search(term:"Neil deGrasse Tyson", filterForTypes:PODCASTEPISODE, sortBy:POPULARITY, filterForHasTranscript:true){
    searchId
    podcastEpisodes{
      uuid
      name
      datePublished
      description
      audioUrl
      transcript
      podcastSeries{
	      uuid
	      name
	    }
    }
  }
}
```

**Note:** All Taddy users can view transcripts that have been provided by the podcast, but you will need to be a Taddy Business user to view [transcripts that we automatically generate](https://taddy.org/developers/podcast-api/episode-transcripts).  

9. Searching for the term "James Webb Space Telescope" but filter only for episodes from the podcast StarTalk Radio.

```jsx
{
  search(term:"James Webb Space Telescope", filterForTypes:PODCASTEPISODE, filterForSeriesUuids:"e02ffac2-4a0e-4c6d-a42a-c59d02fe37bc"){
    searchId
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
      podcastSeries{
	      uuid
	      name
	    }
    }
  }
}
```

### Ranking Score

10. You can check the ranking score for each search result. The higher the ranking score, the more relevant the result.

```jsx
{
  search(term:"James Webb Space Telescope Black Hole", filterForTypes:PODCASTEPISODE, sortBy:POPULARITY){
    searchId
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
      podcastSeries{
        uuid
        name
      }
    }
    rankingDetails{
      id
      uuid
      rankingScore
    }
  }
}
```

### Exclude search terms

11. You can limit the results you get back by excluding words you donâ€™t want. You do this by adding a minus sign in front of any word you want to exclude.

```jsx
{
  search(term:"Tim Ferriss -crypto", filterForTypes:PODCASTEPISODE){
    searchId
    podcastEpisodes{
      uuid
      name
      description
      audioUrl
    }
  }
}
```

## Query Input:

For search, you can search for podcasts or episodes using these properties:

```jsx
" The term you are searching for "
term: String

" (Optional) Allows for pagination. Default is 1 (ie: page 1 of the results). Max value is 20. "
page: Int

" (Optional) The number of results per page. Default is 10. Max value is 25 (ie: that max results you can return in one query in 25) "
limitPerPage: Int

" (Optional) Filter for certain types of content. Default is PODCASTSERIES. Possible values are PODCASTSERIES, PODCASTEPISODE, COMICSERIES, CREATOR "
filterForTypes: [SearchContentType]

" (Optional) Filter for only content made in certain countries "
filterForCountries: [Country]

" (Optional) Filter for only content made in certain languages "
filterForLanguages: [Language]

" (Optional) Filter for only content from certain genres "
filterForGenres: [Genre]

" (Optional) Filter for results only from certain series "
filterForSeriesUuids: [ID]

" (Optional) Filter for results that are not from certain series "
filterForNotInSeriesUuids: [ID]

" (Optional) Filter to return only AUDIO or VIDEO podcasts. Default is null (include both AUDIO & VIDEO podcasts)."
filterForPodcastContentType: [PodcastContentType]

" (Optional) Filter for results that are published after a certain date (Epoch time in seconds)"
filterForPublishedAfter: Int

" (Optional) Filter for results that are published before a certain date (Epoch time in seconds) "
filterForPublishedBefore: Int

" (Optional - for PODCASTSERIES) Filter for content that have an episode published after a certain date (Epoch time in seconds). This filter is only for PODCASTSERIES and will return an empty array for PODCASTEPISODE "
filterForLastUpdatedAfter: Int

" (Optional - for PODCASTSERIES) Filter for results that have an episode published before a certain date (Epoch time in seconds). This filter is only for PODCASTSERIES and will return an empty array for PODCASTEPISODE "
filterForLastUpdatedBefore: Int

" (Optional - for PODCASTSERIES) Filter for only content that has a certain total number of episodes. This filter is only for PODCASTSERIES and will return an empty array for any other type "
filterForTotalEpisodesLessThan: Int

" (Optional - for PODCASTSERIES) Filter for only content that has a certain total number of episodes. This filter is only for PODCASTSERIES and will return an empty array for any other type "
filterForTotalEpisodesGreaterThan: Int

" (Optional - for PODCASTEPISODE) Filter for episodes that have a duration less than a certain number of seconds. This filter is only for PODCASTEPISODE and will return an empty array for any other type "
filterForDurationLessThan: Int

" (Optional - for PODCASTEPISODE) Filter for episodes that have a duration greater than a certain number of seconds. This filter is only for PODCASTEPISODE and will return an empty array for any other type "
filterForDurationGreaterThan: Int

" (Optional - for PODCASTEPISODE) Filter for episodes that have a transcript available. This filter is only for PODCASTEPISODE and will return an empty array for any other type "
filterForHasTranscript: Boolean

" (Optional - for PODCASTEPISODE) Filter for episodes that have chapter files available. This filter is only for PODCASTEPISODE and will return an empty array for any other type "
filterForHasChapters: Boolean

" (Optional) Choose how the results are sorted. Default is sort by EXACTNESS. Possible values are EXACTNESS and POPULARITY. "
sortBy: SearchSortOrder

" (Optional) Choose which results are matched as valid search results. Default is MOST_TERMS. Possible values are MOST_TERMS, ALL_TERMS, FREQUENCY. If you search has multiple terms, FREQUENCY gives more weight to the terms that appear less frequently in results "
matchBy: SearchMatchType

" (Optional) Choose to only return safe (not explicit) content or all content. Default is false (include everything, including explicit content) "
isSafeMode: Boolean
```

## Query Response:

The response you get back includes an array of [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries) and [PodcastEpisodes](https://taddy.org/developers/podcast-api/podcastepisode) that match your search term.

```jsx
" Identifier for the search query being sent (Used for caching)"
searchId: ID!

" A list of PodcastSeries items "
podcastSeries: [PodcastSeries]

" A list of PodcastEpisode items "
podcastEpisodes: [PodcastEpisode]

" Ranking information for each search result "
rankingDetails: [SearchRankingDetails]

" Additional information on the search results (Total # of results, pages, etc) "
responseDetails: [SearchResponseDetails]
```

### Referenced types in this document:

[Country](https://taddy.org/developers/podcast-api/country)

[Language](https://taddy.org/developers/podcast-api/language)

[Genre](https://taddy.org/developers/podcast-api/genre)

[TaddyType](https://taddy.org/developers/podcast-api/taddytype)

[PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries)

[PodcastEpisode](https://taddy.org/developers/podcast-api/podcastepisode)

[SearchContentType](https://taddy.org/developers/podcast-api/search-content-type)

[SearchMatchType](https://taddy.org/developers/podcast-api/search-match-by)

[SearchSortOrder](https://taddy.org/developers/podcast-api/search-sort-by)

[SearchRankingDetails](https://taddy.org/developers/podcast-api/search-query-ranking-details)

[SearchResponseDetails](https://taddy.org/developers/podcast-api/search-query-response-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# SearchContentType

The different types of content you can search for using the [`search`](https://taddy.org/developers/podcast-api/search) query on Taddy.

```jsx
enum SearchContentType {
  PODCASTSERIES
  PODCASTEPISODE
  COMICSERIES
  CREATOR
}
```

---

# SearchMatchType

Choose how your search matches contentâ€”by exact phrase, all terms, or most terms.

```jsx
enum SearchMatchType {
  EXACT_PHRASE
  MOST_TERMS
  ALL_TERMS
}
```

`EXACT_PHRASE`Â â€“ Returns results that contain the exact phrase only. This is ideal for searching names, e.g.,Â "Peter Smith", as results will only include instances where the full and exact phrase "Peter Smith" appears.

`ALL_TERMS`Â â€“ Returns results that contain all the provided search terms, regardless of the order of the terms. Exact phrase matches are ranked higher.

`MOST_TERMS`Â (Default) â€“ Returns results that contain any of the search terms provided. Exact phrase matches and multiple terms matched are ranked higher.

---

# SearchRankingDetails

Ranking details (including score) about each search result.

```jsx
type SearchRankingDetails {
  " Identifier for the search query being sent "
  id: ID!

  " The UUID of the item being returned in the search results "
  uuid: ID

  " The type of item being returned in the search results "
  type: SearchContentType

  " The ranking score for the search results from 100 to 0. The higher the score the more relevant the result. "
  rankingScore: Int
}
```

---

# SearchResponseDetails

Additional details about the search request.

```jsx
type SearchResponseDetails {
  " Identifier for the search query being sent "
  id: ID!

  " The type of item being returned in the search results "
  type: SearchContentType

  " Total number of search results returned for this type "
  totalCount: Int

  " Total number of pages of results returned for this type "
  pagesCount: Int
}
```

---

# SearchSortOrder

Choose if you want search results to be prioritized by exact term matching or popularity of the content.

```jsx
enum SearchSortOrder {
  EXACTNESS
  POPULARITY
}
```

`EXACTNESS`(Default) - Search results will prioritize exact matching based on title, description, publisher name and other relevant information. 

`POPULARITY` - Search will still match the terms you provide, but the result be filtered to include only the top 5% of the most popular podcasts. Popularity of the podcast is determined by the [iTunes Top Charts](https://taddy.org/developers/podcast-api/get-top-charts).  This is useful if you want popular (and usually higher quality) results over an exact match.

---

# Should I rebuild my app on Blueskyâ€™s AT Protocol?

Six years ago, I built a podcast app called [Podyssey](https://podyssey.fm). Itâ€™s like Goodreads but for podcasts ie) users recommend their favourite episodes on the app. However, I haven't updated the app in a couple of years, and Iâ€™ve been thinking about what a rebuild of Podyssey would look like.

Recently, IÂ joined Bluesky and had an idea: **What if I could see which podcasts the people I follow on Bluesky are recommending?** 

This thought sparked a larger idea, while Iâ€™ve always imagined a rebuild of Podyssey would fix some product frustrations Iâ€™ve had about the app, what if I took this opportunity to rebuild Podyssey to work on the AT Protocol (the protocol that Bluesky uses) and what would that look like? 

## What is AT Protocol?

First, I think it's important to talk about the big idea behind Bluesky. Bluesky is an alternative to X (Twitter). However, the real innovation behind Bluesky is that they have defined a protocol ([AT Protocol](https://atproto.com)) that anyone can use to store their own data (posts, likes etc). This means you donâ€™t have to store your data on Blueskyâ€™s servers to interact with other Bluesky users.

Bluesky's pitch is that users are better off in the long run if they aren't locked into centralized platforms. They achieve this by defining a protocol that anyone can build their apps on top of. As an indie developer, I've seen platforms like Meta, X, and Reddit start as great places to build audiences, only to later remove API access and kill third-party apps. The pitch with AT Protocol is that by building on a protocol that no single company controls, developers like me can get the benefits of working with large platform networks without the downsides of working without the fearing a company can change their mind in the future.

![From Blueskyâ€™s initial pitch. (Release the bird from its cage and into the blue sky)](https://ax0.taddy.org/blog/bluesky/twitter-to-bluesky.jpg)

From Blueskyâ€™s initial pitch. (Release the bird from its cage and into the blue sky)

That's exciting, but it's also a really ambitious idea because X is just one of many apps that could be rebuilt on AT Protocol. There are many apps that lock-in users to their platform that could be rebuilt on this protocol, for example, Instagram, TikTok, YouTube, or even my app, Podyssey.

## Bluesky as a growth channel

There are ideological reasons why I would want to build an app on an open protocol like AT Protocol, but there is one compelling business reason: It opens up Bluesky as a distribution channel for my app. Specifically, I get access to: 

1. Blueskyâ€™s open social graph (which Iâ€™ll dive into shortly) and
2. Blueskyâ€™s user base, which is growing really fast.

![Bluesky growth within the last 6 months](https://ax0.taddy.org/blog/bluesky/bluesky-active-users.jpg)

Bluesky growth within the last 6 months

## Is Bluesky decentralized?

Moreover, If I'm going to take the time to rebuild my app on a protocol that someone else created, I need to know that they canâ€™t:

- Change the protocol in a way that negatively affects my app
- Create barriers between me and my users
- Start charging me for what was previously free

These are all things that big centralized platforms have done in the past and have [burned](https://mashable.com/article/twitter-elon-musk-paid-enterprise-api-access-pricing) [developers](https://www.theverge.com/2023/5/31/23743993/reddit-apollo-client-api-cost). So, how much control does Bluesky have over AT Protocol - or said in a slightly different way - is AT Protocol decentralized? I think the best way to discuss this is to see how your data is stored:

![Screenshot 2024-11-23 at 5.14.59â€¯PM.png](https://ax0.taddy.org/blog/bluesky/bluesky-PDS-example.png)

This is my data, which was created using the Bluesky app and you can see a live version of it [here](https://atproto-browser.vercel.app/at/dmathewwws.com). You will notice that it has 4 collections:

- My Profile - which stores my name, profile pic etc
- My Posts - which stores every post (tweet) Iâ€™ve made on the app
- My Likes - which stores every like I've made on the app
- My Follows - which stores a link to every person I follow on the app

It's that simple. Every user has their own repository (aka: PDS) which is made up of collections and records (see below image). As long the data in your data repository is in this format, anyone can host their own PDS. Anyone can also build their own Bluesky-compatible app, they just have to support all the same collections as Bluesky. And lastly, anyone can define new collections (aka: a lexicon) for the different types of data they need for their app.

![This is what a PDS (Personal Data Server) looks like ](https://ax0.taddy.org/blog/bluesky/bluesky-PDS-architecture.webp)

This is what a PDS (Personal Data Server) looks like 

So, if AT Protocol just defines how collections of text documents should be saved, how much control could Bluesky have if I define my own collections and build my app on AT Protocol? 

Well, Iâ€™ve seen some valid [critiques](https://anderegg.ca/2024/11/15/maybe-bluesky-has-won) about Bluesky not being decentralized, yet. I believe their arguments can be summarized as Bluesky is theoretically decentralized (there is a protocol that no one company owns and anyone can build apps on that protocol) but practically Bluesky isnâ€™t decentralized. Bluesky, the company, has servers that host almost all user data, Bluesky provides services that make it easier to use AT Protocol (Relay, AppView, etc) and they build the Bluesky app, the app which most people use. So despite AT Protocol being open, Bluesky maintains significant control over the ecosystem, at least for now. 

To Blueskyâ€™s credit, I will mention 3 things: 

1. Their words and most importantly their actions have shown that they care about keeping AT Protocol as the central part of Bluesky.
2. If you want to build a protocol that people use, you could start with a technical document and hope people build the different things needed to build apps on the protocol, or you can build a product and the helpful tools yourself. Bluesky has taken the latter route and it's working for them, it's made AT Protocol a lot more practical than theoretical.
3. The only way that Bluesky has less control over AT Protocol is if more people build apps that work on the protocol. If we expect Bluesky to build and run everything that makes AT Protocol useful I think weâ€™ve missed the point of open protocols.

## Is one Social Graph enough?

The idea for building Podyssey on AT Protocol came from a very simple idea, I want to see the podcasts the people I follow on Bluesky would recommend to their followers. However, as I explored AT Protocol, it brought up some interesting product questions. 

The simplest way to show podcast recommendations from people I follow would be to let users sign in with their Bluesky account and use the Bluesky follow graph ([`app.bsky.graph.follow`](https://atproto-browser.vercel.app/at/did:plc:z7tuu4dmfvoqlm2wensjxons/app.bsky.graph.follow)). However, this creates a problem: if someone I follow on Bluesky recommends podcasts I'm not interested in, I'd have to unfollow them on Bluesky to stop seeing their podcast recommendations on Podyssey. That's not a great user experience.

Instead, it sounds like the user experience I want is to have my own lexicon for Podyssey. This is what I came up with:

![Screenshot 2024-11-25 at 5.18.02â€¯PM.png](https://ax0.taddy.org/blog/bluesky/bluesky-podyssey.png)

- My Recommendations  - which stores every recommendation Iâ€™ve made on the app
- My Podcasts - which stores every podcast I subscribe to (you can use Podyssey as your podcast player)
- My Follows - which stores a link to every person I follow on the app

To summarize, while it initially seemed that Bluesky's social graph could serve as the one social graph for all apps, I now think most apps should maintain their own social graphs to let users customize their experience. I can still use Bluesky's social graph to jumpstart my appâ€™s social graph, which would allow people with Bluesky to see which of the people they follow on Bluesky are also on Podyssey.

## Requirement to Have a Bluesky Account

Another question I had was: Should I require everyone who uses Podyssey to also have a Bluesky account? Well letâ€™s go through my options:

**1) Require everyone who uses Podyssey to sign in with Bluesky.** This would be the simpler option and require less technical work to implement. 

**2) Do not require sign-in with Bluesky.** The other option is to allow users without Bluesky accounts to either log in with their own data repository or have Podyssey create and host a data repository for them.

I think the second option offers a better user experience. However, as I was looking into it more, It did raise a couple of challenges: 

1. Bluesky has [open-source code](https://github.com/bluesky-social/pds) for hosting your own PDS, but it's unclear whether it supports hosting multiple users' PDSs (like in my case)
2. I don't want to build a hosting provider, I want to build a podcast app. From my understanding  PDS, a user has one PDS for all apps they use on AT Protocol. This raises concerns about hosting costs for me as users could use their Podyssey-hosted PDS to sign in to other AT Protocol apps and potentially upload images and videos, leaving me responsible for those hosting costs. That sounds like a different business with a different business model.

## Everything is public

You may have noticed something from being able to see all my Bluesky data. Everything is public on AT Protocol. All my posts, likes, every person I follow, and even every person I block. I think Bluesky can do this because it is a â€œpublic squareâ€ for discussion, but the question I have is, would this work for my app?

While many Podyssey users may not mind having public data, I think some will want the option to keep their data private. Moreover, even extroverted / outgoing users might feel uncomfortable with public data in certain situations. For example, someone listening to a health-related podcast may not want it appearing in their public podcast list, as this could reveal personal health information.

To Bluesky's credit, it seems like they realize this is an issue and are adding the ability to restrict certain appsâ€™ access to certain collections (via OAuth scopes) and are working on adding private data to your data repository. However, the implementation details and timeline for these are still unclear.

![Frame 10-3.png](https://ax0.taddy.org/blog/bluesky/bluesky-private-data.png)

## Novel product ideas

Working on an open protocol like AT Protocol opens up some novel product ideas:

### Multiple apps using the same collections

If there was a standardized lexicon for podcast apps, it could allow for better integrations between podcast apps. Here are 2 examples: 

1. You could use multiple podcast apps and know that whenever you added or deleted a new podcast in one app, it would be synchronized across any app you use for podcasts.
2. Any podcast app could leverage existing collections (like Podyssey's recommendations & podcasts list) to provide better discovery suggestions, even for their brand-new users.

![Screenshot 2024-11-27 at 2.27.18â€¯PM.png](https://ax0.taddy.org/blog/bluesky/podyssey-misc.png)

This idea could easily extend beyond podcasts to other media types, including synced reading lists between e-readers, shared bookmarks / collections, watchlists, and music playlists.

### Using Blueskyâ€™s Firehose

People are already recommending podcasts on Bluesky using Apple or Spotify links or certain podcast hashtags. One idea is to add a â€˜Trending on Blueskyâ€™ section which could be aggregated through data from the Bluesky Firehose. This is a great way to jumpstart content on a new app (Shoutout to [@ian](https://bsky.app/profile/iansmithbuilds.bsky.social) for the idea).

## Moderation

After hearing of some users being banned on Bluesky, it made me wonder if I am too tightly tied to Blueskyâ€™s moderation decisions. 

For example, letâ€™s consider a user who gets banned by Bluesky. While I understand that Bluesky won't host their PDS, what happens if I disagree with Bluesky's moderation decision and choose to host that user's PDS so they can use Podyssey? Since I still rely on helpful Bluesky's services like Relay and AppView (which crawl PDS and do things like aggregate the number of likes every person has made for my post), would these services allow the banned user's PDS to be included? Iâ€™m unsure about this policy at the moment, but it opens up questions about Bluesky's ability to control who can use my app. 

This problem goes away if there are alternative options for services like Relay + AppView that Bluesky does not run.

## User Experience

Between all the platforms X, Threads, Mastodon & Bluesky, I personally think Bluesky is the most likely to become the public square most people use in the future. 

There are several reasons for this, one of which is Bluesky is already emerging as a [leading source of traffic](https://www.youtube.com/watch?v=D4ghgVq9z4M) back to peopleâ€™s websites. However, Iâ€™d like to focus on a user-experience related reason based on my experience building a podcast app. Iâ€™ve spoken to many people, including developers, who are unaware that podcasts are powered by RSS, a decentralized technology, under the hood. To a regular person, it works just like every other content platform (YouTube, TikTok, Instagram) and most users neither know nor care that RSS is the underlying technology driving these apps.

I get this same experience when using Bluesky - I never think about AT Protocol. Bluesky works just like I expect it to, which is the gold standard that all decentralized apps should strive for. It's good to see an example of that, and it makes me think that if I rebuilt Podyssey on AT Protocol, I can also give my users a great user experience.

## Is building on top of AT Protocol economically viable?

It seems like the early adopters who are currently building on AT Protocol are doing so because they believe in the ideals of decentralization ie) not locking-in users into centralized platforms. However, it's still unclear to me whether the future of AT Protocol is a few public benefit companies / non-profit organizations that ensure the protocol continues to operate on behalf of these utopian ideals or AT Protocol transitions to become an economically viable platform for for-profit organizations to build their business on.

One significant advantage for Bluesky is that they have already built something that millions of people use every day. If economic viability depends on how widely the AT Protocol is adopted, this is a great start. The more apps and users that rely on the protocol, the more monetization opportunities arise both for Bluesky and 3rd party developers.

## Conclusion

After going through this exercise, I came up with a list of things that would make it a no-brainer for me to build Podyssey on AT Protocol:

- **Private Data in your data repository**. I want the ability to add data to userâ€™s data repositories that isnâ€™t publicly accessible to everyone.
- **Best Practices for building an app on AT Protocol.** After realizing I would have to host my own PDSs for users that donâ€™t already have Bluesky, I am curious what Bluesky would consider best practices for hosting PDSs, what information we should store on our own servers vs PDSs, and if we should use 3rd party Relays + AppViews or run our own.
- **Alternatives for Relay + AppView.** For Bluesky to have less control over AT Protocol there needs to be alternatives to using their Relay + AppView services or it needs to be cost effective for me to run these services for myself.
- **Examples of economically viable apps.** I would love to see examples of apps built on AT Protocol that are economically viable or at minimum have Bluesky articulate how they see apps built on AT Protocol can become economically viable.

Personally, I find what Bluesky has already achieved inspiring, they are taking on billion dollar companies with huge platform moats, and winning. 

While I am not ready to commit to building Podyssey on AT Protocol, one of my takeaways is that if you believe in the goals Bluesky is trying to achieve, and want to help them achieve it, there is a need for SaaS services that help app developers like me solve some of the pain points I mentioned above. It would help make AT Protocol fulfill its mission to be a truly decentralized protocol option and make it easier for indie developers like me take the plunge into the ATmosphere.

### If you want to take a deeper dive into AT Protocol:

AT Protocol Docs: [https://atproto.com](https://atproto.com/)

Bluesky Docs: [https://docs.bsky.app/docs/get-started](https://docs.bsky.app/docs/get-started)

AT Protocol Browser: [https://atproto-browser.vercel.app/at/dmathewwws.com](https://atproto-browser.vercel.app/at/dmathewwws.com)

Architecture of Bluesky: [https://newsletter.pragmaticengineer.com/p/Bluesky](https://newsletter.pragmaticengineer.com/p/bluesky)

<aside>
ðŸ‘‹

Here is my Bluesky skeet about this article if you would like to share / repost. Thanks!

[https://bsky.app/profile/dmathewwws.com/post/3lcied3jgfc2j](https://bsky.app/profile/dmathewwws.com/post/3lcied3jgfc2j)

</aside>

![Frame 1.png](The%205%20Top%20Podcast%20APIs%20dde76975ff654a98b59c8e6efe18d62a/Frame_1.png)

Article by: [Daniel Mathews](https://bsky.app/profile/dmathewwws.com)

Written on Dec 4, 2024

---

# SortOrder

SortOrder gives you the ability to return different items based on what option you pass in.

```jsx
enum SortOrder {
  LATEST
  OLDEST
  SEARCH
}
```

`LATEST` - Return latest (newest) first

`OLDEST` - Return oldest first

`SEARCH` - Return only episodes that match filtered `searchTerm` (another property that should be passed in whenever `SEARCH` is chosen as the SortOrder).

---

# TaddyType

The different types of media available on Taddy

```jsx
enum TaddyType {
  PODCASTSERIES
  PODCASTEPISODE
	COMICSERIES
  COMICISSUE
	CREATOR
}
```

`PODCASTSERIES` - A podcast

`PODCASTEPISODE` - An episode of a podcast

`COMICSERIES` - A comic

`COMICISSUE` - An issue (episode) of a comic

`CREATOR` - A creator (that made the podcast or comic).

---

# The 5 Top Podcast APIs

## 1. [Taddy Podcast API](https://taddy.org/developers/podcast-api)

**TLDR:** Taddy API is the best-in-class API for podcasts. It saves you from having to build the infrastructure needed to keep an up-to-date index of all podcasts and constantly check these podcast feeds for new or updated episodes. It has a free tier but if you want all the features (like transcripts and webhooks notifications) you will need the paid version.

![](https://ax0.taddy.org/blog/top-5-podcast-apis/taddy-screenshot.png)

### Features:

âœ…Â Ability to get details on a podcast  

âœ…Â Ability to get details on an episode

âœ…Â Search for a podcast

âœ…Â Search for an episode

âœ…Â Episode Transcripts (transcripts available for any episode)

âœ…Â [Webhook notifications](https://taddy.org/developers/podcast-api/webhooks) for newly released podcasts or episodes.

âœ…Â You can store or cache anything from the API on your own servers

### Pricing:

Free Plan - Up to 500 API Requests / month

Pro Plan - $75/month for 100,000 API Requests / month

Business Plan - $150/month for 350,000 API Requests / month

---

## 2. [PodcastIndex.org](https://podcastindex-org.github.io/docs-api/)

**TLDR:** Authentication is trickier with this API, but once you have it set up, it gives you information on all podcasts and episodes. It is also 100% free.

![](https://ax0.taddy.org/blog/top-5-podcast-apis/podcastindex-screenshot.png)

### Features:

âœ…Â Ability to get details on a podcast  

âœ…Â Ability to get details on an episode

âœ…Â Search for a podcast

âœ…Â You can store or cache anything from the API on your own servers

âŒÂ Search for an episode

âŒÂ Transcripts (Partial - less than 1% of all episodes in the PodcastIndex directory have transcripts)

âŒÂ Webhook notifications for newly released podcasts or episodes.

### Pricing:

Free

---

## 3. [Listen Notes Podcast API](https://www.listennotes.com/api/)

**TLDR:** Listen Notes has great documentation and is a well-built API. But, there are 3 main drawbacks with the Listen Notes API: 

1) It is quite expensive for what you get.

2) As per their [terms](https://www.listennotes.com/api/terms/), unless you are an Enterprise customer, you are not allowed to save any information you get from their API on your servers, which will lead to you making a lot more API requests (and therefore paying more). 

3) Listen Notes only provides transcripts for less than 1% of episodes in their directory. They do provide another paid service called Listen411, which you can use to get the transcript for any episode.

![](https://ax0.taddy.org/blog/top-5-podcast-apis/listennotes-screenshot.png)

### Features:

âœ…Â Ability to get details on a podcast  

âœ…Â Ability to get details on an episode

âœ…Â Search for a podcast

âœ…Â Search for an episode

âŒÂ Transcripts (Partial - less than 1% of all episodes in the Listen Notes directory have transcripts)

âŒÂ Webhook notifications for newly released podcasts or episodes.

âŒÂ Requires [â€œPowered by Listen Notesâ€](https://www.listennotes.com/api/terms/) Logo in your application. ([Enterprise customers](https://www.listennotes.com/api/terms/) customers do not have this restriction) 

âŒÂ You cannot store or cache anything from the API on your own servers (restricted to [Enterprise customers](https://www.listennotes.com/api/terms/) customers only) 

### Pricing:

Free Plan - Up to 300 API Requests / month

Pro Plan: 

$200/month for 5,000 API Requests / month

$352/month for 100,000 API Requests / month

$752/month for 350,000 API Requests / month

---

## 4. [Spotify Podcast API](https://developer.spotify.com/documentation/web-api/reference/#/?security=oauth_2_0)

**TLDR:** Spotify is one of the fastest-growing podcast players and a lot of podcast creators add their podcasts to Spotify and are therefore accessible through the Spotify API. However, the main drawback is that you need to be aware of the Spotify API Rate Limits or require every user that uses your app to Login with their Spotify account (so that every user has their own Rate Limit). 

![](https://ax0.taddy.org/blog/top-5-podcast-apis/spotify-screenshot.png)

### Features:

âœ…Â Ability to get details on a podcast  

âœ…Â Ability to get details on an episode

âœ…Â Search for a podcast

âœ…Â Search for an episode

âŒÂ Transcripts

âŒÂ Webhook notifications for newly released podcasts or episodes.

âŒ If you are running into Spotifyâ€™s API Rate Limits, you may be required to get your users to Login with their Spotify accounts.

### Pricing:

Free

---

## 5. [iTunes API](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/Searching.html#//apple_ref/doc/uid/TP40017632-CH5-SW1)

**TLDR:** Apple Podcasts is the largest podcast player and therefore most podcast creators want to be listed on Apple Podcasts. You can get details on all podcasts on Apple Podcasts via the iTunes API. 

![](https://ax0.taddy.org/blog/top-5-podcast-apis/itunes-screenshot.png)

### Features:

âœ…Â Podcast Directory of all the podcasts on Apple Podcasts

âŒÂ Ability to get details on a podcast  (You only get the podcasts RSS feed)

âŒÂ Ability to get details on an episode

âœ…Â Search for a podcast

âŒÂ Search for an episode

âŒÂ Transcripts

âŒÂ Webhook notifications for newly released podcasts or episodes.

âŒÂ Easy to hit API Limit of 20 API Requests / minute

### Pricing:

Free

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

![Frame 1.png](The%205%20Top%20Podcast%20APIs%20dde76975ff654a98b59c8e6efe18d62a/Frame_1.png)

Article by: [Daniel Mathews](https://bsky.app/profile/dmathewwws.com)

Updated on September 12, 2025

More Links:  

[Sign Up For Taddy â†’](https://taddy.org/signup/developers)

[Podcast API](https://taddy.org/developers/podcast-api)

[iTunes API vs Taddy API - Which is better?](https://taddy.org/blog/itunes-vs-taddy-podcast-api)

[Listen Notes API vs Taddy API - Which is better?](https://taddy.org/blog/listen-notes-vs-taddy-podcast-api)

[PodcastIndex.org API vs Taddy - Which is better?](https://taddy.org/blog/podcastindex-vs-taddy-podcast-api)

---

# Top Charts on Apple Podcasts

Use getTopChartsByCountry or getTopChartsByGenres to get the top podcasts or episodes from Apple Podcasts.

**Notes:**

- Top Charts are updated daily (~ 6 AM PST)
- For Podcasts, you can get Top Charts by country or genre
- For Episodes, you can only get Top Charts by country.

## Examples:

1. Get todayâ€™s Top Podcasts in the US

```jsx
{
  getTopChartsByCountry(taddyType:PODCASTSERIES, country:UNITED_STATES_OF_AMERICA){
    topChartsId
    podcastSeries{
      uuid
      name
    }
    podcastEpisodes{
      uuid
      name
      podcastSeries{
        uuid
        name
      }
    }
  }
}
```

2. Get todayâ€™s Top Episodes in the US

```jsx
{
  getTopChartsByCountry(taddyType:PODCASTEPISODE, country:UNITED_STATES_OF_AMERICA){
    topChartsId
    podcastSeries{
      uuid
      name
    }
    podcastEpisodes{
      uuid
      name
      podcastSeries{
        uuid
        name
      }
    }
  }
}
```

3. Get todayâ€™s Top Podcasts in the `PODCASTSERIES_TRUE_CRIME` Genre

```jsx
{
  getTopChartsByGenres(taddyType:PODCASTSERIES, genres:PODCASTSERIES_TRUE_CRIME){
    topChartsId
    podcastSeries{
      uuid
      name
    }
    podcastEpisodes{
      uuid
      name
      podcastSeries{
        uuid
        name
      }
    }
  }
}
```

4. (BONUS) You can get Top Episodes by Genre and Country. UseÂ `getTopChartsByGenres`Â and pass in theÂ `filterByCountry`. Behind the scenes, this is the same data from Appleâ€™s Top 200 Episodes chart for a country, we filter by genre to get a Top Episodes by Genre and Country chart. Since Apple provides only 200 top episodes per day per country, there is a chance that some genres will return no episodes.

```jsx
{
  getTopChartsByGenres(taddyType:PODCASTEPISODE, genres:PODCASTSERIES_TRUE_CRIME, filterByCountry:UNITED_STATES_OF_AMERICA){
    topChartsId
    podcastSeries{
      uuid
      name
    }
    podcastEpisodes{
      uuid
      name
      podcastSeries{
        uuid
        name
      }
    }
  }
}
```

## Query Input:

1. For getTopChartsByCountry, you can get the top charts for a specific platform (for podcasts or episodes):

```jsx
" The type of content you want to get top charts information on. "
taddyType: TaddyType!

" Which country do you want the Top Charts for."
country: Country!

" (Optional) The platform to check for top charts content. Default is APPLE_PODCASTS "
source: TopChartsSource

" (Optional) Allows for pagination. Default is 1 (ie: page 1 of the results). Max value is 20. "
page: Int

" (Optional) The number of results per page. Default is 10. Max value is 25 (ie: that max results you can return in one query in 25) "
limitPerPage: Int
```

2. For getTopChartsByGenres, you can get the top charts for a specific platform (for podcasts or episodes):

```jsx
" The type of content you want to get top charts information on. "
taddyType: TaddyType!

" Which genres do you want the Top Charts for."
genres: [Genre!]

" (Optional) The platform to check for top charts content. Default is APPLE_PODCASTS "
source: TopChartsSource

" (Optional) We filter the results by country (needed for PODCASTEPISODE taddyType)"
filterByCountry: Country

" (Optional) Allows for pagination. Default is 1 (ie: page 1 of the results). Max value is 20. "
page: Int

" (Optional) The number of results per page. Default is 10. Max value is 25 (ie: that max results you can return in one query in 25) "
limitPerPage: Int
```

## Query Response:

The response you get back includes an array of [PodcastSeries](https://taddy.org/developers/podcast-api/podcastseries) and [PodcastEpisodes](https://taddy.org/developers/podcast-api/podcastepisode) that match your top charts query.

```jsx
" Identifier for the top charts query being sent (Used for caching) "
topChartsId: ID!

" A list of PodcastSeries items "
podcastSeries: [PodcastSeries]

" A list of PodcastEpisode items "
podcastEpisodes: [PodcastEpisode]
```

### Referenced types in this document:

[TaddyType](https://taddy.org/developers/podcast-api/taddytype)

[Country](https://taddy.org/developers/podcast-api/country)

[Genre](https://taddy.org/developers/podcast-api/genre)

[TopChartsSource](https://taddy.org/developers/podcast-api/top-charts-source)

[TopChartsType](https://taddy.org/developers/podcast-api/top-charts-type)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# TopChartsSource

A list of platforms you can get Top Charts data from

```jsx
enum TopChartsSource {
  APPLE_PODCASTS // we only support Apple Podcasts at the moment
}
```

---

# TopChartsType

A list of possible categories for podcasts or episodes to be ranked in

```jsx
enum TopChartsType {
  GENRE
  COUNTRY
}
```

---

# TranscriptItem

Get the transcript for an episode. Includes text, timecodes and speaker info.

```jsx
type TranscriptItem {
  " The unique identifier for the transcript item "
  id: ID

  " The text of the transcript item "
  text: String

  " (Optional) The speaker of the transcript item "
  speaker: String

  " The start timecode of the transcript item in milliseconds "
  startTimecode: Int

  " The end timecode of the transcript item in milliseconds "
  endTimecode: Int
}
```

### TranscriptItemStyle

Depending on which style you pick, you will get back a different TranscriptItems.

```jsx
enum TranscriptItemStyle {
  UTTERANCE
  PARAGRAPH
}
```

`UTTERANCE` is a phrase, thought, or sentence spoken by a user and is the default returned via Open AIâ€™s Whisper model. 

`PARAGRAPH` (Default): Combines utterances into a complete sentence and then different paragraphs based on if there is greater than a 500 millisecond break in speech.

Please see [transcripts](https://taddy.org/developers/podcast-api/episode-transcripts) for additional context.

---

# TranscriptLink

Link details where you can download an episodeâ€™s transcript. Along with the url, it also contains additional information like file type and language, which may be useful to you.

```jsx
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

---

# Webhooks

**Webhooks are an incredibly useful feature of the Taddy API.** In the background, Taddy is constantly monitoring RSS feeds to check whenever they have been updated. Whenever there is new data, we send it to you via a webhook. This means you don't have to continuously check with us for new updates, we'll push those updates to you immediately.

## Example use-cases:

- Do you care about **SEO?** Get a webhook notification immediately whenever your website is mentioned on a new episode.
- Do you want **industry specific leads or insights**? Get a webhook notification immediately when a person or a brand has been mentioned on a new episode.
- Do you want to **push notify your users** whenever a new episode is released? Get a webhook notification immediately when a new episode is released. On your server, you match which users want a push notification and send one to them.

## Setting Up a Webhook

- Open up the [**Taddy Dashboard**](https://taddy.org/dashboard), you will see a â€œSetup a New Webhookâ€ button.
- You will have to enter in:
    1. The endpoint url where you want to receive the notifications (usually on your own server)
    2. Pick which webhook events you would like to receive.

**Note:** 

- You must be on a paid plan to add a webhook to your account.
- Webhook notifications do not count against your monthly API limits ðŸ¥³.
- The endpoint url you enter needs to be one publicly accessible over the internet. (For example, 127.0.0.1 and localhost URLs will not work, since Taddy servers will not be able to contact your local computer). If youâ€™d like to test receiving these notifications locally on your computer, [Ngrok](https://ngrok.com) provides a useful & free way to do so. It provides you a public url that is mapped to your localhost.
- Your endpoint url needs to be a  `POST` endpoint
- In the Webhooks section, one of the fields given to you is the Webhook Secret. This is an optional security feature. We add this secret as the header `X-TADDY-WEBHOOK-SECRET` to all webhook notifications sent to this webhook. Because your endpoint url is publicly accessible and if you dont share this secret with anyone else, you can be confident that any data you receive on your endpoint is from us and can be trusted.
- [Listen Up](https://heylistenup.app/) is a very simple app that receives and displays webhook events. It may be useful to you as a very easy way to see what kind of events are going to be received by your webhook endpoint.
- There is an [example project](https://github.com/taddyorg/taddy-api-example-project) which allows you to mock webhook events. It's a great way to test receiving webhook events before receiving the live events from Taddyâ€™s API.

## Webhook Events

A list of the possible events for:

### PodcastSeries

| **Event** | **Description** |
| --- | --- |
| `podcastseries.created` | Get a notification when a new podcast series has been added to Taddy |
| `podcastseries.updated` | Get a notification when any podcast series details have been updated (e.g. title, description, etc.) |
| `podcastseries.deleted` | Get a notification when a podcast series has been removed from Taddy, usually at the request of the podcast creator |
| `podcastseries.new_episodes_released` | Most users won't need to subscribe to this event. When new episodes have been released, you'll get this notification only once, no matter how many episodes have been added, updated, or removed. An example use case for this notification is if you're parsing the podcast RSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each episode that's been added or updated. |

### PodcastEpisode

| **Event** | **Description** |
| --- | --- |
| `podcastepisode.created` | Get a notification when a new podcast episode has been released |
| `podcastepisode.updated` | Get a notification when podcast episode details have been updated (e.g. title, description, audioUrl, etc.) |
| `podcastepisode.deleted` | Get a notification when a podcast episode has been removed. |

### iTunesInfo

| **Event** | **Description** |
| --- | --- |
| `itunesinfo.created` | Get a notification when itunes information for a podcast series has been added to Taddy (e.g. itunes artwork etc.) This is information from iTunes and not found on the podcast's RSS feed |
| `itunesinfo.updated` | Get a notification when itunes information for a podcast series has been updated (e.g. itunes artwork etc.) This is information from iTunes and not found on the podcast's RSS feed |
| `itunesinfo.deleted` | Get a notification when itunes information for a podcast series has been removed from Taddy, usually because it has been removed from iTunes |

## What does a webhook event look like?

A webhook event is made up of:  `uuid`, `taddyType`, `action`, `timestamp`, `data`.

### Examples:

```jsx
// EXAMPLE WEBHOOK EVENT for a PodcastSeries event
{
  uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
  taddyType: 'podcastseries',
  action: 'updated',
  timestamp: 1673984316,
  data: {
    uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
    hash: 'b8f97d07ce3a9916aeba1feda9db610b58aee8d62a2dfa0495a0b1893e857d91',
    name: 'The Daily',
    description: 'This is what the news should sound like. The biggest stories of our time, told by the best journalists in the world. Hosted by Michael Barbaro and Sabrina Tavernise. Twenty minutes a day, five days a week, ready by 6 a.m.',
    imageUrl: 'https://image.simplecastcdn.com/images/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/2cce5659-f647-4366-b318-46e4b67afcfa/3000x3000/c81936f538106550b804e7e4fe2c236319bab7fba37941a6e8f7e5c3d3048b88fc5b2182fb790f7d446bdc820406456c94287f245db89d8656c105d5511ec3de.jpeg?aid=rss_feed',
    datePublished: 1484687987,
    language: 'ENGLISH',
    seriesType: 'EPISODIC',
    contentType: 'AUDIO',
    isExplicitContent: false,
    copyright: 'Â© 2020-2021 THE NEW YORK TIMES COMPANY; The New York Times encourages the use of RSS feeds for personal use in a news reader or as part of a non-commercial blog, subject to your agreement to our Terms of Service.',
    websiteUrl: 'https://www.nytimes.com/the-daily',
    rssUrl: 'https://feeds.simplecast.com/54nAGcIl',
    rssOwnerName: 'The New York Times',
    rssOwnerPublicEmail: 'thedaily@nytimes.com',
    authorName: 'The New York Times',
    isCompleted: false,
    isBlocked: null,
    itunesId: 1200361736,
    genres: [ 'PODCASTSERIES_NEWS_DAILY_NEWS', 'PODCASTSERIES_NEWS' ],
    childrenHash: '95ef1fee188406c415e32915f85855eab3cf49e0f9052785e42ef368792333db',
    popularityRank: 'TOP_200',
    itunesInfo: {
      uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
      publisherId: 121664449,
      publisherName: 'The New York Times',
      baseArtworkUrl: 'https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/',
      baseArtworkUrlOf: 'https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/640x640bb.png'
    }
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for a PodcastEpisode event
{
  uuid: 'a2b41ecd-565c-4f29-8cf9-ac737bcc8d99',
  taddyType: 'podcastepisode',
  action: 'updated',
  timestamp: 1673984261,
  data: {
    uuid: 'a2b41ecd-565c-4f29-8cf9-ac737bcc8d99',
    hash: '8130da98420b6ea1bcafd9a7e411625bf0437e93cd2e44b0a2291430e7cd940c',
    name: 'Consider the Burying Beetle. (Or Else.)',
    description: '<p>The current level of biodiversity loss is extraordinary in human history: The global rate of species extinction is at least tens to hundreds of times higher than the average over the past 10 million years.Â </p><p>At the end of 2022, countries around the world came together in Montreal for an agreement akin to the Paris climate accord to tackle the biodiversity crisis. Hereâ€™s more on the effort and how it seeks to confront the problem.</p><p>Guest: <a href="https://www.nytimes.com/by/catrin-einhorn">Catrin Einhorn</a>, who reports on biodiversity and climate for The New York Times.</p><p>Background reading:Â </p><ul><li>Last year, roughly 190 nations, aiming to halt a dangerous decline in biodiversity,<a href="https://www.nytimes.com/2022/12/19/climate/biodiversity-cop15-montreal-30x30.html"> agreed to preserve 30 percent of the planetâ€™s land and seas</a>.Â </li></ul><p>For more information on todayâ€™s episode, visit <a href="http://nytimes.com/thedaily?smid=pc-thedaily">nytimes.com/thedaily</a>. Transcripts of each episode will be made available by the next workday.Â </p>\n',
    imageUrl: null,
    datePublished: 1673001900,
    guid: '848d3475-dc4d-4c53-b97c-e69f05bad846',
    subtitle: 'The current level of biodiversity loss is extraordinary in human history: The global rate of species extinction is at least tens to hundreds of times higher than the average over the past 10 million years. \n' +
      '\n' +
      'At the end of 2022, countries around the world came together in Montreal for an agreement akin to the Paris climate accord to tackle the biodiversity crisis. Hereâ€™s more on the effort and how it seeks to confront the problem.\n' +
      '\n' +
      'Guest: Catrin Einhorn, who reports on biodiversity and climate for The New York Times.',
    audioUrl: 'https://dts.podtrac.com/redirect.mp3/chrt.fm/track/8DB4DB/pdst.fm/e/nyt.simplecastaudio.com/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/episodes/b8f7e904-bf5d-4c60-b3d7-de67dfc271fc/audio/128/default.mp3?aid=rss_feed&awCollectionId=03d8b493-87fc-4bd1-931f-8a8e9b945d8a&awEpisodeId=b8f7e904-bf5d-4c60-b3d7-de67dfc271fc&feed=54nAGcIl',
    videoUrl: null,
    fileLength: 24695702,
    fileType: 'audio/mpeg',
    duration: 1543,
    episodeType: 'FULL',
    seasonNumber: null,
    episodeNumber: null,
    websiteUrl: 'https://www.nytimes.com/the-daily',
    isExplicitContent: false,
    isRemoved: null,
    podcastSeries: {
      uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
      name: 'The Daily',
      rssUrl: 'https://feeds.simplecast.com/54nAGcIl',
      itunesId: 1200361736
    }
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for a iTunesInfo event
{
  uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
  taddyType: 'itunesinfo',
  action: 'updated',
  timestamp: 1673984345,
  data: {
    uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
    hash: '2c022ad3c4482664cdecbcb05e87313e917aff2b697f1f674b69bb6514f55ed6',
    subtitle: null,
    summary: 'This is what the news should sound like. The biggest stories of our time, told by the best journalists in the world. Hosted by Michael Barbaro and Sabrina Tavernise. Twenty minutes a day, five days a week, ready by 6 a.m.',
    baseArtworkUrl: 'https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/',
    publisherId: 121664449,
    publisherName: 'The New York Times',
    country: 'UNITED_STATES_OF_AMERICA',
    podcastSeries: {
      uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
      name: 'The Daily',
      rssUrl: 'https://feeds.simplecast.com/54nAGcIl',
      itunesId: 1200361736
    }
  }
}
```

## Recommended Architecture for Webhooks

Taddy API adds ~1k new podcasts and ~50k new episodes daily. Whenever we add or update podcast data in our own database, we also send this data to you via a webhook event. To handle this volume of data being sent to your server, we recommend a message / queue based architecture:

![](https://ax0.taddy.org/docs/webhook-architecture-2.png)

**Key Points from image above:** 

- Taddy API is continuously checking 4 million podcast feeds for new and updated podcast data. Whenever we update our database with new data, we also send it to you via your Webhook endpoint (1)
- We recommend using a lambda function (or equivalent) for your Webhook endpoint. The function receives new podcast data from Taddy API and adds it to a queue to be processed at a later time. (2)
- We recommend having a worker (or workers) process messages in your queue and add this data into your database. Set up a Dead-letter Queue for failed message processing, which can be re-processed at a later time (3)

Sample Lambda code: [https://gist.github.com/dmathewwws/d3d56529313cb2959bc2a7ec62792a5a](https://gist.github.com/dmathewwws/d3d56529313cb2959bc2a7ec62792a5a)

**Note:** We have showcased AWS architecture in the above example, but you can accomplish this same workflow using Cloudflare Workers + Queues or by implementing this on your server - just make sure it can handle the volume of data being sent. If you have any questions, please contact **`danny@taddy.org`**

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# iTunes API vs Taddy API - Which is better?

## What Does Each API Do?

- **iTunes API**Â is a podcast directory service that returns RSS feed URLs and basic metadata for podcasts available on Apple Podcasts.
- **Taddy API** is a complete podcast data API that provides podcast and episode details, search, episode transcripts, and real-time updates through webhooks.

They both try to accomplish different goals, so understanding these difference will help you choose the right tool for your project.

## How to use iTunes API to get Podcast details

There are 2 main endpoints in the iTunes API:

- [Search](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/SearchExamples.html#//apple_ref/doc/uid/TP40017632-CH6-SW1) - You can search for a piece of content from the iTunes API by passing in the title or artist name.
- [Lookup](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/LookupExamples.html#//apple_ref/doc/uid/TP40017632-CH7-SW1) - You can lookup a piece of content by already knowing its ID.

Letâ€™s take an example of using the first endpoint (Search) and searching for a podcast called â€œThis American Lifeâ€:

```jsx
{
	"wrapperType":"track", 
	"kind":"podcast", 
	"artistId":364380278, 
	"trackId":201671138, 
	"artistName":"This American Life", 
	"feedUrl":"http://feed.thisamericanlife.org/talpodcast", 
	"artworkUrl600":"https://is1-ssl.mzstatic.com/image/thumb/Podcasts123/v4/4e/b9/bb/4eb9bb9b-ed19-f0b7-7739-1177f1b35207/mza_8452563123961176873.png/600x600bb.jpg", 
	"genres":["Personal Journals", "Podcasts", "Society & Culture", "Arts"]
},
```

<aside>
ðŸ’¡ Here are the useful properties you get back:

- `trackId` - a unique iTunes ID for this podcast
- `feedUrl` - a link to the RSS feed for this podcast
- `artwork` - iTunes artwork for this podcast
- `genres` - iTunes genre categories for this podcast
</aside>

The most useful property you get of all of these is the `feedUrl`. It is the link to the podcastâ€™s RSS feed where you can get more details on the podcast and all episodes. **But that is also the main difference between the iTunes API and Taddyâ€™s API**, you would then have to go to the url for each podcast to get the current details and constantly check it for new or updated episodes. 

That is why the iTunes API is more a directory of all the podcasts vs an API for you to build your next podcast app on. However, if all you need is a list of all RSS feeds, iTunes is exactly what you need.

## How to use Taddyâ€™s API to get Podcast details

The goal of Taddyâ€™s API is to make it easier for you build a great podcast app. The API lets you to do all the following:

- Be able to get [details on any podcast](https://taddy.org/developers/podcast-api/get-podcast-series).
- Be able to get [details on any episode](https://taddy.org/developers/podcast-api/get-podcast-episode).
- [Search](https://taddy.org/developers/podcast-api/search) for a particular podcast or episode.
- Episode [transcripts](https://taddy.org/developers/podcast-api/episode-transcripts).
- Return the latest episodes for the a list of podcasts
- Get the [most popular podcasts](https://taddy.org/developers/podcast-api/most-popular-podcasts)
- Get a webhook [notification](https://taddy.org/developers/podcast-api/webhooks) whenever there is new or updated podcast

Letâ€™s take an example of getting back details on the â€œThis American Lifeâ€ podcast:

```jsx
{
  "data": {
    "getPodcastSeries": {
      "uuid": "d682a935-ad2d-46ee-a0ac-139198b83bcc",
      "name": "This American Life",
      "itunesId": 201671138,
      "description": "This American Life is a weekly public radio show, heard by 2.2 million people on more than 500 stations. Another 2.5 million people download the weekly podcast. It is hosted by Ira Glass, produced in collaboration with Chicago Public Media, delivered to stations by PRX The Public Radio Exchange, and has won all of the major broadcasting awards.",
      "imageUrl": "https://files.thisamericanlife.org/sites/all/themes/thislife/img/tal-name-1400x1400.png",
      "itunesInfo": {
        "uuid": "d682a935-ad2d-46ee-a0ac-139198b83bcc",
        "publisherName": "This American Life",
        "baseArtworkUrlOf": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts123/v4/4e/b9/bb/4eb9bb9b-ed19-f0b7-7739-1177f1b35207/mza_8452563123961176873.png/640x640bb.png"
      },
      "episodes": [
        {
          "uuid": "7eb7759a-ece5-4ff3-ac8c-2d39a295f436",
          "name": "672: No Fair!",
          "description": "Stories of very small injustices and also one very big one.",
          "audioUrl": "https://dts.podtrac.com/redirect.mp3/chtbl.com/track/8DB4DB/pdst.fm/e/nyt.simplecastaudio.com/bbbcc290-ed3b-44a2-8e5d-5513e38cfe20/episodes/5a12fc83-9bf1-40df-8726-aef5f7610917/audio/128/default.mp3?awCollectionId=bbbcc290-ed3b-44a2-8e5d-5513e38cfe20&awEpisodeId=5a12fc83-9bf1-40df-8726-aef5f7610917"
        },
        //... Plus 9 more episodes
      ]
    }
  }
}
```

<aside>
ðŸ’¡ Here are some of the useful properties you get back:

- `name` - name / title of the podcast from the RSS feed
- `description` - description of the podcast from the RSS feed
- `imageUrl` - podcast artwork from the RSS feed
- `itunesInfo` - iTunes information for the podcast
- `episodes` - a list of the 10 latest episodes (you can pass in parameters to get different episodes back)
</aside>

Notice that this time you get back information about the podcast. Specifically you get information from the podcastâ€™s RSS feed, like `name`, `description`, `imageUrl` and `episodes`. 

Taddy API is constantly checking feeds for updates, so you also be sure that the information is up-to-date without you having to constantly do those checks yourself. If that is something you need, then using the Taddy API is a the right choice for you.

## Feature Breakdown

| Feature | Taddy API | iTunes API |
| --- | --- | --- |
| Podcast directory | âœ… | âœ… |
| Get details on a podcast | âœ…Â - See [getPodcastSeries](https://taddy.org/developers/podcast-api/get-podcast-series) | âŒ |
| Get details on an episode | âœ… - See [getPodcastEpisode](https://taddy.org/developers/podcast-api/get-podcast-episode) | âŒ |
| Search for podcasts | âœ…Â - See [search](https://taddy.org/developers/podcast-api/search) | âœ… |
| Search for episodes | âœ…Â - See [search](https://taddy.org/developers/podcast-api/search) | âŒ |
| Episode transcripts | âœ…Â - See [transcripts](https://taddy.org/developers/podcast-api/episode-transcripts) | âŒ |
| Webhooks | âœ…Â - Get a [webhook](https://taddy.org/developers/podcast-api/webhooks) notification whenever a new podcast or episode is released. | âŒ |
| API Requests Limit | Limit of 240 per minute | Limit of 20 per minute |
| Price | Free -  500 API Calls per month.

[Pro - $75/month](https://taddy.org/developers/pricing) for 100,000 API Calls per month. 

[Business - $150/month](https://taddy.org/developers/pricing) for 350,000 API Calls per month. 
 | 100% Free |

Keep in mind iTunes API just gives you the url of a RSS feed for the podcast, it doesn't give you any other podcast details, you have to go to the url to get those details yourself.

## Summary

In summary, which API you use will depend on your specific business use-case. If all you need is a directory of all the podcasts, iTunes API is 100% free and may be all you need, if you need some more features to help you build out your podcast app, you should check out Taddyâ€™s API. 

Make sure to check our list of the [ðŸ¥‡Top 5 Podcast APIs](https://taddy.org/blog/best-podcast-api-tools) if you would like a summary of all the different Podcast API options available.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

![Frame 1.png](The%205%20Top%20Podcast%20APIs%20dde76975ff654a98b59c8e6efe18d62a/Frame_1.png)

Article by: [Daniel Mathews](https://bsky.app/profile/dmathewwws.com)

Updated on September 12, 2025

More Links:  

[Sign Up For Taddy â†’](https://taddy.org/signup/developers)

[Podcast API](https://taddy.org/developers/podcast-api)

[The 5 Top Podcast APIs](https://taddy.org/blog/best-podcast-api-tools)

[Listen Notes API vs Taddy API - Which is better?](https://taddy.org/blog/listen-notes-vs-taddy-podcast-api)

[PodcastIndex.org API vs Taddy - Which is better?](https://taddy.org/blog/podcastindex-vs-taddy-podcast-api)

---

# iTunesInfo

GraphQL Type for additional information from iTunes on a particular podcast. (A small number of podcasts on Taddy API are not on Apple Podcasts and those will not return null for iTunesInfo details)

```jsx
" PodcastSeries unique identifier linked to this iTunesInfo "
uuid: ID

" A different hash signals that itunes information has changed since the last hash "
hash: String

" Subtitle given in Apple Podcasts "
subtitle: String

" Summary given in Apple Podcasts "
summary: String

" Base Url to the podcast's cover art from iTunes. NOTE: To get a working image, you need to pass in a size at the end of the url in the format {baseArtworkUrl}{size}x{size}bb.png ex {baseArtworkUrl}640x640bb.png "
baseArtworkUrl: String

" Helper Url to the podcast's cover art from iTunes. Pass in an interger for the size of the image you want "
baseArtworkUrlOf(
  size: Int
): String

" Publisher Id from iTunes "
publisherId: Int

" Publisher name from iTunes "
publisherName: String

" Country where the podcast is made "
country: Country

" PodcastSeries linked to this iTunesInfo "
podcastSeries: PodcastSeries
```

---

# Pricing

We have 3 tiers for our API: Free, Pro & Business. 

With the Free version of Taddy API, you get 500 API requests per month. 

With the Pro or Business versions of Taddy API, you get more API requests per month plus additional features.

## Pro ($75/month)

---

- [x]  Up to 100,000 API requests per month

- [x]  100 episode transcripts per month.

- [x]  Priority Support

## Business ($150/month)

---

- [x]  Up to 350,000 API requests per month

- [x]  2000 episode transcripts per month.

- [x]  [Webhook](https://taddy.org/developers/webhooks) notifications for new or updated content

- [x]  Priority Support

<aside>
ðŸŽ‰ Join Pro & Business users like Podyssey, Inkverse, Sond, Podmixt, and Podcast Kiosk by logging into your [Taddy Dashboard](https://taddy.org/dashboard) and clicking the â€œ**Upgrade To Proâ€** or â€œ**Upgrade To Businessâ€** buttons.

</aside>

### Pricing that scales with your application

$75/month (Pro) - 100k API requests per month. 100 Episode transcripts per month.

$150/month (Business) - 350k API requests per month. 2000 Episode transcripts per month. 

$250/month (Business) - 1 Million API requests per month. 2000 Episode transcripts per month.

Additional Episode Transcription packs - 2000 credits for $100/month (5c per transcript)

Contact `danny@taddy.org` if you need to make over 1 Million API requests per month or want to buy extra transcript packs. We have fair pricing that gets cheaper as you scale up.

More Links:  

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Webcomics API

An API for information on any comic that conforms to the [**SSS specification**](https://3s-docs.org). 

All comics uploaded using [Taddy Ink](https://taddy.org) are automatically added to this directory, plus any comic feed that conforms to the SSS standard can also be included in this API. If you have created your own comic feed, please [add it to our directory](https://taddy.org/developers/comics-api/add-a-comicseries-to-taddy). 

## What can you do with the API?

- Get details on any [comic series](https://taddy.org/developers/comics-api/get-comic-series).
- Get details on any [comic issue](https://taddy.org/developers/comics-api/get-comic-issue) (every issue in a comic series).
- Get details on any [comic story](https://taddy.org/developers/comics-api/get-comic-story) (every image in a comic issue).
- Blazing fast [full-text search](https://taddy.org/developers/comics-api/search) on all comics.
- Get notifications for newly released comics or issues through a [webhook](https://taddy.org/developers/comics-api/webhooks).
- Get details on [multiple comics](https://taddy.org/developers/comics-api/get-multiple-comic-series) or [multiple issues](https://taddy.org/developers/comics-api/get-multiple-comic-issues) (both return an array).
- [Add a comic](https://taddy.org/developers/comics-api/add-a-comicseries-to-taddy) to our directory.
- Bulk [download all comics](https://taddy.org/developers/comics-api/bulk-download-comicseries) from our directory.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

## [Sign Up for Free âž¤](https://taddy.org/signup/developers)

### API Reference:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries) - Details on a comic

[ComicIssue](https://taddy.org/developers/comics-api/comicissue) - Details on an issue (episode)

[ComicStory](https://taddy.org/developers/comics-api/comicstory) - Details on an story (artwork)

[Creator](https://taddy.org/developers/comics-api/creator) - Details on the creator of the comic 

[Language](https://taddy.org/developers/comics-api/language) - The language for the comic

[Genre](https://taddy.org/developers/comics-api/genre) - The genre of the comic

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant) - Options for image size / variants 

[ContentRating](https://taddy.org/developers/comics-api/content-rating) - The rating for the comic

[SeriesStatus](https://taddy.org/developers/comics-api/series-status) - Status of the comic

[ComicSeriesType](https://taddy.org/developers/comics-api/comic-series-type) - Type of comic

[ComicSeriesLayout](https://taddy.org/developers/comics-api/comic-series-layout) - Layout of the comic

[SortOrder](https://taddy.org/developers/comics-api/sort-order) - The option to have returned items sorted by newest or oldest.

[TaddyType](https://taddy.org/developers/comics-api/taddytype) - The types of media available on Taddy

[SearchContentType](https://taddy.org/developers/comics-api/search-content-type) - The types of media available to search for on Taddy

[SearchMatchType](https://taddy.org/developers/comics-api/search-match-by) - Choose between searching for all terms, some terms or frequency

[SearchSortOrder](https://taddy.org/developers/comics-api/search-sort-by) - The option to have search results be prioritized by exact term matching or popularity

[SearchRankingDetails](https://taddy.org/developers/comics-api/search-query-ranking-details) - Ranking details for each search result

[SearchResponseDetails](https://taddy.org/developers/comics-api/search-query-response-details) - Additional details for search request

[FeedRefreshDetails](https://taddy.org/developers/comics-api/feed-refresh-details) - Details on how often Taddy checks a comic feed for updates

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Podcast API](https://taddy.org/developers/podcast-api)

[Creator API](https://taddy.org/developers/creator-api)

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Changelog](https://taddy.org/developers/changelog)

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Add a Comic to Taddyâ€™s Directory

When you add your comic to Taddyâ€™s Directory, we notify all supported comic readers that a new comic has been released.

## How to add a comic to Taddy

1. Use the REST endpoint: **** **https://taddy.org/feeds/add/comicseries** (POST)
2. Add your `X-USER-ID` and `X-API-KEY` values to the headers (see [Intro Guide](https://taddy.org/developers/intro-to-taddy-graphql-api) for instructions on how to get your API Key and User ID)

```jsx
{
	"X-USER-ID": 7,
	"X-API-KEY": "96c5007c18858e86d..."
}
```

 3. Add `sssUrl` (required) to the data being passed with the POST request.

```jsx
{
	"sssUrl": "https://example.com/feeds/sss/comicseries/123",
}
```

You will get back one of the following responses:

**Success:** On a successful response you get back a `status` and the `uuid` for the added comic.

```jsx
{
	"status": "success",
	"uuid":  "d682a935-ad2d-46ee-a0ac-139198b83bcc"
}
```

**Error:** When we encounter an error adding a new comic to Taddy. We send back a string with the error message.

```jsx
`Could not add SSS Feed to Taddy`
```

### Example:

```jsx
curl -X POST \
https://taddy.org/feeds/add/comicseries \
-H "Content-Type: application/json" \
-H "X-USER-ID: 7" \
-H "X-API-KEY: 96c5007c18858e86d..." \
-d '{ "sssUrl": "https://example.com/feeds/sss/comicseries/123" }'
```

**Notes:**

- Once you send a request: 1) We immediately add the comic to Taddyâ€™s Directory and 2) Queue up a job to parse the comic feed, including all its issues. This second step may take up to 30 mins but is usually much sooner.
- Trying to re-add a comic that has already been added to our directory will return a successful response and return the uuid for the already added comic series.
- If you are run into any issues, please contact `danny@taddy.org`.

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Bulk download all comics

You can easily download basic information on all comic series from our directory. 

## Download Data

| **Date** | **URL** | **Size** |
| --- | --- | --- |
| Apr 08, 2024 | [https://archive.org/details/comicseries-2024-04-09T03-19-26.138Z.txt](https://archive.org/details/comicseries-2024-04-09T03-19-26.138Z.txt) | 1 MB |

## Format of File

A txt file with every new line of the file being information on a comic. Here is the information you get for every comic:

| **Property** | **Type** | **Description** |
| --- | --- | --- |
| `uuid` | Uuid | Unique id for content |
| `name` | String | Name of comic |
| `createdAt` | Date | Date when the comic was added to Taddy |
| `datePublished` | Date | Date when the comic was published |
| `sssUrl` | String | SSS feed for the comic |
| `hash` | String | A hash of all comic details. You know if you have a different hash, the comic details have updated since the last time you checked, use our API to get up-to-date details. |
| `issuesHash` | String | A hash of all issue details. You know if you have a different issuesHash, there are new or updated issue details for the comic since the last time you checked, use our API to get up-to-date details. |

---

# ComicIssue

GraphQL Type for a Comic Issue.

```jsx
" Unique identifier for a comic issue "
uuid: ID

" Unique identifier for a comic series this issue belongs to "
seriesUuid: ID

" Date when the issue was published (Epoch time in seconds) "
datePublished: Int

" The name (title) of the issue "
name: String

" Short note from the creator for the issue "
creatorNote: String

" Push notification message for the issue "
pushNotificationMessage: String

" A different hash means that details for this issue have updated since the last hash "
hash: String

" A different hash means that details for the stories that make up this issue have updated since the last hash "
storiesHash: String

" Stringified JSON details for the banner art. Convert to JSON to use."
bannerImageAsString: String

" Stringified JSON details for the thumbnail art. Convert to JSON to use."
thumbnailImageAsString: String

" The banner art for an issue "
bannerImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The thumbnail art for an issue "
thumbnailImageUrl: String

" All the images for this issue "
imageUrls: [String]

" Details on all the stories for this issue "
stories: [ComicStory]

" Position of this issue in relation to other issues. This is used to sort issues by oldest or latest in a series. "
position: Int

" If the issue has now been removed from the SSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the comic for which this issue belongs to "
comicSeries: ComicSeries
```

### Other Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicStory](https://taddy.org/developers/comics-api/comicstory)

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant)

---

# ComicSeries

GraphQL Type for a Comic. 

```jsx
" Unique identifier for this comic "
uuid: ID

" Date when the comic was published (Epoch time in seconds) "
datePublished: Int

" The name (title) for a comic "
name: String

" The description for a comic "
description: String

" Status of the comic "
status: SeriesStatus

" The cover art for a comic "
coverImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The banner art for a comic "
bannerImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The thumbnail art for a comic "
thumbnailImageUrl: String

" Stringified JSON details for the cover art. Convert to JSON to use."
coverImageAsString: String

" Stringified JSON details for the banner art. Convert to JSON to use."
bannerImageAsString: String

" Stringified JSON details for the thumbnail art. Convert to JSON to use."
thumbnailImageAsString: String

" A hash of all comic details. It may be useful for you to save this property in your database and compare it to know if any comic details have updated since the last time you checked "
hash: String

" A hash of the details for all issues for this comic. It may be useful for you to save this property in your database and compare it to know if there are any new or updated issues since the last time you checked "
issuesHash: String

" A list of issues for this comic "
issues(
  " (Optional) Returns issues based on SortOrder. Default is LATEST (newest issues first), another option is OLDEST (oldest issues first), and another option is SEARCH (pass in the property searchTerm) to filter for issues by title or description. "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000 "
  page: Int,

  " (Optional) Return up to this number of issues. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int,

  " (Optional) Only to be used when sortOrder is SEARCH. Filters through the title & description of issues for the searchTerm "
  searchTerm: String,

  " (Optional) The option to show issues that were once on the SSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedIssues: Boolean,
): [ComicIssue]

" The number of issues for this comic "
totalIssuesCount(
  " (Optional) Option to include issues that were once on the SSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedIssues: Boolean
): Int

" Tags for the comic "
tags: [String]

" A comic can belong to multiple genres but they are listed in order of importance. "
genres: [Genre]

" The language the comic is in "
language: Language

" Creators of the comic "
creators: [Creator]

" Type of the comic "
seriesType: ComicSeriesType

" Layout of the comic "
seriesLayout: ComicSeriesLayout

" Rating of the comic "
contentRating: ContentRating

" Url for the comic's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

### Referenced types in this document:

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

[Creator](https://taddy.org/developers/comics-api/creator)

[SeriesStatus](https://taddy.org/developers/comics-api/series-status)

[SortOrder](https://taddy.org/developers/comics-api/sort-order)

[ContentRating](https://taddy.org/developers/comics-api/content-rating)

[Language](https://taddy.org/developers/comics-api/language)

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant)

[ComicSeriesType](https://taddy.org/developers/comics-api/comic-series-type)

[ComicSeriesLayout](https://taddy.org/developers/comics-api/comic-series-layout)

[FeedRefreshDetails](https://taddy.org/developers/comics-api/feed-refresh-details)

---

# ComicSeriesLayout

Possible layout option of the comic.

```jsx
enum ComicSeriesType{
  WEBTOON
	MANGA
  MANHWA
  MANHUA
  AMERICAN_STYLE_COMIC
  ANTHOLOGY
  GRAPHIC_NOVEL
  ONE_SHOT	
}
```

---

# ComicSeriesType

Type of the comic.

```jsx
enum ComicSeriesType{
  WEBTOON
	MANGA
  MANHWA
  MANHUA
  AMERICAN_STYLE_COMIC
  ANTHOLOGY
  GRAPHIC_NOVEL
  ONE_SHOT	
}
```

---

# ComicStory

GraphQL Type for a ComicStory. 

```jsx
" Unique identifier for a comic story "
uuid: ID

" Unique identifier for a comic issue this story belongs to "
issueUuid: ID

" Unique identifier for a comic series this story belongs to "
seriesUuid: ID

" A different hash means that details for this story have updated since the last hash "
hash: String

" Stringified JSON details for the story art. Convert to JSON to use."
storyImageAsString: String

" The story art "
storyImageUrl:String

" If the story has now been removed from the SSS Feed "
isRemoved: Boolean

" Details on the comic issue that this story belongs to "
comicIssue: ComicIssue

" Details on the comic series that this story belongs to "
comicSeries: ComicSeries
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

---

# ContentRating

Possible Rating for comics.

```jsx
enum ContentRating {
  COMICSERIES_BABY
  COMICSERIES_KIDS
  COMICSERIES_TEENS
  COMICSERIES_MATURE_TEENS
  COMICSERIES_ADULTS
  COMICSERIES_EROTICA
}
```

`COMICSERIES_BABY` - Comic is suitable for all ages. The primary audience is children reading with the help of a parent.

`COMICSERIES_KIDS` - Comic is suitable for 6 year olds & above. Suitable rating for comics that include fantasy, mystery, mild scariness, magic & adventure.

`COMICSERIES_TEENS` -  Comic is suitable for 13 year olds & above. Suitable rating for comics that include romance, crime, scariness & mild horror.

`COMICSERIES_MATURE_TEENS` - Comic is suitable for 15 year olds & above. Suitable rating for comics that include stories around complex themes that include mental health, sexuality, drugs, addiction, bullying & other forms of harassment.

`COMICSERIES_ADULTS` - Comic is suitable for 18 year olds & above.  Suitable rating for comics that include gore, graphic violence, profanity, and mild nudity.

`COMICSERIES_EROTICA` - Comic is suitable for 18 year olds & above. Suitable rating for comics that include nudity or are meant to provide erotic satisfaction. **(Taddy does not support this Rating at this time, but we plan to in the future).**

---

# Creator

GraphQL Type for a Creator. 

```jsx
" Unique identifier for this creator "
uuid: ID

" Date when the creator feed was published (Epoch time in seconds) "
datePublished: Int

" The name of the creator "
name: String

" A short bio on the creator "
bio: String

" The avatar image for the creator"
avatarImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" Stringified JSON details for the avatar image. Convert to JSON to use."
avatarImageAsString: String

" A hash of all creator details. It may be useful for you to save this property in your database and compare it to know if any details have updated since the last time you checked "
hash: String

" A hash of the details for all different content a creator makes. It may be useful for you to save this property in your database and compare it to know if there are any new or updated content since the last time you checked "
contentHash: String

" A list of content for this creator "
content(
  " Sort order for the results. Default is LATEST (newest first), another option is OLDEST (oldest first) "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000. "
  page: Int,

  " (Optional) Return up to this number of results. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int
): [CreatorContent]

" The total number of content from this creator "
totalContentCount: Int

" Tags for the creator "
tags: [String]

" Stringified JSON details for the links to creator's website, email, and social media. Convert to JSON to use."
linksAsString: String

" Links to creator's website, email, and social media "
links: [LinkDetails]

" The country in which the creator is resides in or is from "
country: Country

" Url for the creator's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

---

# FeedRefreshDetails

Options for how often Taddy checks a comic feed for updates.

```jsx
enum FeedRefreshPriority {
  WEBSUB
  HIGH
  REGULAR
  LOW
  INACTIVE
  NEVER
}
```

`WEBSUB` - Feed is checked immediately for updates (within 30 mins, usually much sooner). Taddy gets notified of a change to the SSS feed via a [WebSub](https://en.wikipedia.org/wiki/WebSub) notification.

`HIGH` - Feed is checked 4 hours

`REGULAR` - Feed is checked every day

`LOW` - Feed is checked once a week

`INACTIVE` - Feed is checked once a week

`NEVER` - Feed is no longer checked for updates. (Only used in the rare circumstance a podcast violates our [content policy](https://taddy.org/terms-of-service/content-policy)).

If a feed has been set as LOW, INACTIVE, or NEVER priority, you can check the reason why: 

```jsx
enum FeedRefreshPriorityReason {
  INACTIVE_FOR_OVER_1_YEAR
  DUPLICATE_FEED
  ERROR_PARSING_FEED
  FEED_URL_NOT_WORKING
  VIOLATES_TADDY_DISTRIBUTION_POLICY
}
```

`INACTIVE_FOR_OVER_1_YEAR` - Feed has not had any updates in the last 12 months

`DUPLICATE_FEED` - There is another feed in our database that links to the same content

`ERROR_PARSING_FEED` - Error parsing document when trying to check the feed for new updates

`FEED_URL_NOT_WORKING` - Error when trying to load the feed url (404 error, etc)

`VIOLATES_TADDY_DISTRIBUTION_POLICY` - The feed has been reviewed by a Taddy staff member and is in violation of [our distribution policy](https://taddy.org/terms-of-service/distribution-policy).

## How to check the FeedRefreshPriority for a comic

If you would like to know how often a feed is being checked for updates, check its `feedRefreshDetails` property.

```jsx
type FeedRefreshDetails {
  " Taddy's unique identifier "
  uuid: ID

  " How often a feed is refreshed "
  priority: FeedRefreshPriority

  " The reason why feed has a LOW, INACTIVE, or NEVER priority "
  priorityReason: FeedRefreshPriorityReason

  " Date when the feed was refreshed last (Epoch time in seconds) "
  dateLastRefreshed: Int

  " Websub Details (if available)"
  websubDetails: WebsubDetails
}
```

Details for the WebSub hub that notifies Taddy of any changes on the feed. 

```jsx
" Websub Details "
type WebsubDetails {
  " Taddy's unique identifier "
  uuid: ID

  " The feed url for the websub "
  topicUrl: String

  " The url for the hub where you get the websub notification "
  websubHubUrl: String

  " If the websub notification is currently active "
  isVerified: Boolean
}
```

---

# Genre

Possible Genres for comics. Follows format: **TYPE_GENRE**.

```jsx
enum Genre {
  COMICSERIES_ACTION,
  COMICSERIES_COMEDY,
  COMICSERIES_CRIME,
  COMICSERIES_DRAMA,
  COMICSERIES_DYSTOPIA,
  COMICSERIES_EDUCATIONAL,
  COMICSERIES_FANTASY,
  COMICSERIES_HIGH_SCHOOL,
  COMICSERIES_HISTORICAL,
  COMICSERIES_HORROR,
  COMICSERIES_HAREM,
  COMICSERIES_ISEKAI,
  COMICSERIES_MYSTERY,
  COMICSERIES_ROMANCE,
  COMICSERIES_SCI_FI,
  COMICSERIES_SLICE_OF_LIFE,
  COMICSERIES_SUPERHERO,
  COMICSERIES_SUPERNATURAL,
  COMICSERIES_BL,
  COMICSERIES_GL,
  COMICSERIES_LGBTQ,
  COMICSERIES_THRILLER,
  COMICSERIES_ZOMBIES,
  COMICSERIES_POST_APOCALYPTIC,
  COMICSERIES_SPORTS,
  COMICSERIES_ANIMALS,
  COMICSERIES_GAMING
}
```

---

# Get comic details

Use getComicSeries to get details on a specific comic. 

## Example:

```jsx
{
  getComicSeries(uuid:"b00c3bc5-2758-483a-942c-4229eb742cb4"){
    uuid
    datePublished
    name
    description
    hash
    issuesHash
    tags
    genres
    language
    seriesType
    contentRating
    sssUrl
    cover_sm: coverImageUrl(variant:SMALL) # if you want to get multiple variant for the same image, you must use an alias
    cover_md: coverImageUrl(variant:MEDIUM)
    cover_lg: coverImageUrl(variant:LARGE)
    bannerImageUrl(variant:SMALL) # if you just need a specific varaint size, no need to use an alias
    thumbnailImageUrl
  	coverImageAsString
    bannerImageAsString
    thumbnailImageAsString
    creators{
      uuid
      name
      sssUrl
    }
    issues{
      uuid
      name
      bannerImageUrl
      thumbnailImageUrl
      storyImageUrls
    }
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getComicSeries": {
      "uuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
      "datePublished": 1683664725,
      "name": "Warrior's Strife",
      "description": "Frayja has trained all her life to become the greatest warrior in her clan. She has been taught to fear and hate elves and their magic. But a day on the battlefield dramatically alters her perspective on reality.",
      "hash": "397b0176c21bff026d7b58e7bf87147626d4b9c2f7c7a6ddb5de467aa8b1c751",
      "issuesHash": "c005d4e0aadf39c1a043ff3be40833480a2adab59cbffd3ea86620da0a21cc97",
      "tags": [
        "fantasy",
        "darkfantasy",
        "lgbtqia",
        "lgbt",
        "monsters",
        "viking",
        "elf",
        "magic",
        "action",
        "drama",
        "medieval"
      ],
      "genres": [
        "COMICSERIES_ROMANCE",
        "COMICSERIES_DRAMA"
      ],
      "language": "ENGLISH",
      "seriesType": "WEBTOON",
      "contentRating": "COMICSERIES_TEENS",
      "sssUrl": "https://taddy.org/feeds/sss/comicseries/b00c3bc5-2758-483a-942c-4229eb742cb4",
      "cover_sm": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/97699676-ece5-4ab5-b089-5288cfa7421c/cover-sm.webp",
      "cover_md": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/97699676-ece5-4ab5-b089-5288cfa7421c/cover-md.webp",
      "cover_lg": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/97699676-ece5-4ab5-b089-5288cfa7421c/cover-lg.webp",
      "bannerImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/banner-sm.webp",
      "thumbnailImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/a1718041-2b8c-42e9-94fe-099ade2b3417/thumbnail.webp",
      "coverImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/97699676-ece5-4ab5-b089-5288cfa7421c/\",\"cover_sm\":\"cover-sm.webp\",\"cover_md\":\"cover-md.webp\",\"cover_lg\":\"cover-lg.webp\"}",
      "bannerImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/\",\"banner_sm\":\"banner-sm.webp\",\"banner_md\":\"banner-md.webp\",\"banner_lg\":\"banner-lg.webp\"}",
      "thumbnailImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/a1718041-2b8c-42e9-94fe-099ade2b3417/\",\"thumbnail\":\"thumbnail.webp\"}",
      "creators": [
        {
          "uuid": "cbce3184-9975-4212-9c0f-3b51113b4ad6",
          "name": "Christopher Becker",
          "sssUrl": "https://taddy.org/feeds/sss/creator/cbce3184-9975-4212-9c0f-3b51113b4ad6"
        }
      ],
      "issues": [
        {
          "uuid": "d99eba13-b183-41af-a53d-9dcdda7928d0",
          "name": "Chapter 16",
          "bannerImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/banner-sm.webp",
          "thumbnailImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/7e64ff14-0f5a-4c18-b866-7a6683ca62aa/thumbnail.webp",
          "storyImageUrls": [
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/80b94c50-5ebd-44f0-ade4-d41f2fa7ae49/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/e6201d3b-23d7-4ac2-b3af-a88fc54e986f/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/9822851f-ab59-40d7-bf80-e447809acd60/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/d214683f-3b36-4e9a-a131-ac7d08cab378/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/5e29f90c-74e0-4093-ac49-42fc7fc722d3/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/d277ea8e-993e-45bd-a217-d17168f3ca6d/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/ee67dd53-3521-4d72-8b4f-4697f227ce58/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/1ec0d33a-291d-4ac3-a6c8-969a140a6980/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/d99eba13-b183-41af-a53d-9dcdda7928d0/7c091eb9-ec04-4b1c-ba77-24ffd760b841/story.webp"
          ]
        },
        {
          "uuid": "7996485c-6dc8-4275-90c9-3dcb5db6c961",
          "name": "Chapter 15 (Beginning of Book 3)",
          "bannerImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/banner-sm.webp",
          "thumbnailImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/da9926de-a9bf-4f95-aaea-3f48596190e7/thumbnail.webp",
          "storyImageUrls": [
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/7fd113f2-0ca6-4077-be7f-545a021e458b/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/e363b11e-6c17-4ea5-bff9-37e5490ad1dd/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/c56b90f6-3073-469f-a912-7d52734f9f2b/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/cc6255ec-e853-41d6-ae0a-e157283e53fa/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/e59545a4-db2d-4dd7-9c86-365721b0da7a/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/59ae8760-6894-4e5c-8666-daa310ef3693/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/a2b5917b-e3e1-4599-a681-3092d9f41116/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/2e22f812-f193-484a-868a-c4c30b384517/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/19f4abed-1135-4d80-9469-74a7e43d6b18/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/b922dfde-2fe7-4a30-9eed-c664acfbd35c/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/1aba40d3-f4b8-425a-a33b-4a10c6cc043c/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/b310b286-1526-4c60-b545-45c9f7d3d6d3/story.webp",
            "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/7996485c-6dc8-4275-90c9-3dcb5db6c961/0e827d2c-ff6d-4f1b-9b87-6405e38219fe/story.webp"
          ]
        },
        ...,
      ]
    }
  }
}
```

## Query Breakdown:

For getComicSeries, you can get details on any comic using one of the following properties:

```jsx
" Unique identifier for a comic "
uuid: ID

" A comic's SSS Feed "
sssUrl: String

" The name (title) of a comic. Note: Multiple comic can have the exact same name, in that case we always try to return the most popular comic (based on the infomation we have)"
name: String
```

The response you get back is a [ComicSeries](https://taddy.org/developers/comics-api/comicseries). That means you can return any of the following details:

```jsx
" Unique identifier for this comic "
uuid: ID

" Date when the comic was published (Epoch time in seconds) "
datePublished: Int

" The name (title) for a comic "
name: String

" The description for a comic "
description: String

" Status of the comic "
status: SeriesStatus

" The cover art for a comic "
coverImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The banner art for a comic "
bannerImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The thumbnail art for a comic "
thumbnailImageUrl: String

" Stringified JSON details for the cover art. Convert to JSON to use."
coverImageAsString: String

" Stringified JSON details for the banner art. Convert to JSON to use."
bannerImageAsString: String

" Stringified JSON details for the thumbnail art. Convert to JSON to use."
thumbnailImageAsString: String

" A hash of all comic details. It may be useful for you to save this property in your database and compare it to know if any comic details have updated since the last time you checked "
hash: String

" A hash of the details for all issues for this comic. It may be useful for you to save this property in your database and compare it to know if there are any new or updated issues since the last time you checked "
issuesHash: String

" A list of issues for this comic "
issues(
  " (Optional) Returns issues based on SortOrder. Default is LATEST (newest issues first), another option is OLDEST (oldest issues first), and another option is SEARCH (pass in the property searchTerm) to filter for issues by title or description. "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000 "
  page: Int,

  " (Optional) Return up to this number of issues. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int,

  " (Optional) Only to be used when sortOrder is SEARCH. Filters through the title & description of issues for the searchTerm "
  searchTerm: String,

  " (Optional) The option to show issues that were once on the SSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedIssues: Boolean,
): [ComicIssue]

" The number of issues for this comic "
totalIssuesCount(
  " (Optional) Option to include issues that were once on the SSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedIssues: Boolean
): Int

" Tags for the comic "
tags: [String]

" A comic can belong to multiple genres but they are listed in order of importance. "
genres: [Genre]

" The language the comic is in "
language: Language

" Creators of the comic "
creators: [Creator]

" Type of the comic "
seriesType: ComicSeriesType

" Layout of the comic "
seriesLayout: ComicSeriesLayout

" Rating of the comic "
contentRating: ContentRating

" Url for the comic's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

[Creator](https://taddy.org/developers/comics-api/creator)

[SeriesStatus](https://taddy.org/developers/comics-api/series-status)

[SortOrder](https://taddy.org/developers/comics-api/sort-order)

[ContentRating](https://taddy.org/developers/comics-api/content-rating)

[Language](https://taddy.org/developers/comics-api/language)

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant)

[ComicSeriesType](https://taddy.org/developers/comics-api/comic-series-type)

[ComicSeriesLayout](https://taddy.org/developers/comics-api/comic-series-layout)

[FeedRefreshDetails](https://taddy.org/developers/comics-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get comic issue details

Use getComicIssue to get details on a comic issue. A comic series is made up of multiple comic issues.

## Example:

```jsx
{
  getComicIssue(uuid:"826f3ba0-c231-4d93-95ad-d3e65f9d57b1"){
    uuid
    seriesUuid
    hash
    storiesHash
    bannerImageUrl(variant:SMALL)
    thumbnailImageUrl
    stories{
      uuid
      issueUuid
      seriesUuid
      storyImageAsString
    }
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getComicIssue": {
      "uuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
      "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
      "hash": "08b4ce69403beb038ce831ea7163d4c9ae81139c52b82c55e0eafe7160d18a99",
      "storiesHash": "94d497b23c9a506d1bec028c70e9901f83a0caafacc60445c77fc6a1f050a55f",
      "bannerImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/banner-sm.webp",
      "thumbnailImageUrl": "https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/43e484b0-6111-41f2-92e2-0b6515655969/thumbnail.webp",
      "stories": [
        {
          "uuid": "9f2120bb-5660-44a3-a3d5-9537bdd438e9",
          "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
          "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
          "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/9bb57b57-0c0c-4d1a-8eed-0d64daf347d9/\",\"story\":\"story.webp\"}"
        },
        {
          "uuid": "5a0bd406-d513-46cf-8fd1-b8474e4e1909",
          "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
          "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
          "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/a51fad16-89a9-4a0e-8d2c-88625f8bc957/\",\"story\":\"story.webp\"}"
        },
        ...,
      ]
    }
  }
}
```

## Query Breakdown:

For getComicIssue, you can get details on any issue using one of the following properties:

```jsx
" Unique identifier for a comic issue "
uuid: ID,

" The name (title) of an issue. Note: Multiple issues can have the exact same name, in that case we always try to return the most popular comic (based on infomation we have on the subscriber count)"
name: String
```

The response you get back is a [ComicIssue](https://taddy.org/developers/comics-api/comicissue). That means you can return any of the following details:

```jsx
" Unique identifier for a comic issue "
uuid: ID

" Unique identifier for a comic series this issue belongs to "
seriesUuid: ID

" Date when the issue was published (Epoch time in seconds) "
datePublished: Int

" The name (title) of the issue "
name: String

" Short note from the creator for the issue "
creatorNote: String

" Push notification message for the issue "
pushNotificationMessage: String

" A different hash means that details for this issue have updated since the last hash "
hash: String

" A different hash means that details for the stories that make up this issue have updated since the last hash "
storiesHash: String

" Stringified JSON details for the banner art. Convert to JSON to use."
bannerImageAsString: String

" Stringified JSON details for the thumbnail art. Convert to JSON to use."
thumbnailImageAsString: String

" The banner art for an issue "
bannerImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The thumbnail art for an issue "
thumbnailImageUrl: String

" All the images for this issue "
imageUrls: [String]

" Details on all the stories for this issue "
stories: [ComicStory]

" Position of this issue in relation to other issues. This is used to sort issues by oldest or latest in a series. "
position: Int

" If the issue has now been removed from the SSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the comic for which this issue belongs to "
comicSeries: ComicSeries
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

[ComicStory](https://taddy.org/developers/comics-api/comicstory)

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get comic story details

Use getComicStory to get details on a comic story ie) one of the images in an issue

## Example:

```jsx
{
  getComicStory(uuid:"9f2120bb-5660-44a3-a3d5-9537bdd438e9"){
    uuid
    issueUuid
    seriesUuid
    storyImageUrl
    storyImageAsString
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getComicStory": {
      "uuid": "9f2120bb-5660-44a3-a3d5-9537bdd438e9",
      "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
      "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
      "storyImageUrl": "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/9bb57b57-0c0c-4d1a-8eed-0d64daf347d9/story.webp",
      "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/9bb57b57-0c0c-4d1a-8eed-0d64daf347d9/\",\"story\":\"story.webp\"}"
    }
  }
}
```

## Query Breakdown:

For getComicStory, you can get details on a story by passing in an uuid. 

```jsx
" Unique identifier for a comic story"
uuid: ID
```

The response you get back is a [ComicStory](https://taddy.org/developers/comics-api/comicstory). That means you can return any of the following details:

```jsx
" Unique identifier for a comic story "
uuid: ID

" Unique identifier for a comic issue this story belongs to "
issueUuid: ID

" Unique identifier for a comic series this story belongs to "
seriesUuid: ID

" A different hash means that details for this story have updated since the last hash "
hash: String

" Stringified JSON details for the story art. Convert to JSON to use."
storyImageAsString: String

" The story art "
storyImageUrl:String

" If the story has now been removed from the SSS Feed "
isRemoved: Boolean

" Details on the comic issue that this story belongs to "
comicIssue: ComicIssue

" Details on the comic series that this story belongs to "
comicSeries: ComicSeries
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

[ComicStory](https://taddy.org/developers/comics-api/comicstory)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get details on multiple comics

Use getMultipleComicSeries to get details on multiple comics. There is a max limit of 25 comics per request.

## Example:

```jsx
{
  getMultipleComicSeries(uuids:["e418408c-2d72-4a5b-8765-46df6b724d53", 
"b00c3bc5-2758-483a-942c-4229eb742cb4"]){
    uuid
    name
    description
    sssUrl
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getMultipleComicSeries": [
      {
        "uuid": "e418408c-2d72-4a5b-8765-46df6b724d53",
        "name": "Dreams Broken : Purpose",
        "description": "Unfair from the day he was born, but born rich working as a janitor for his company. Losing family, love, and friends won't hurt Jin anymore. Jin who came to know about life at a younger age lost everything that mattered to him. Lost his family for saving his love and then lost the love of his life for saving the country.",
        "sssUrl": "https://taddy.org/feeds/sss/comicseries/e418408c-2d72-4a5b-8765-46df6b724d53"
      },
      {
        "uuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
        "name": "Warrior's Strife",
        "description": "Frayja has trained all her life to become the greatest warrior in her clan. She has been taught to fear and hate elves and their magic. But a day on the battlefield dramatically alters her perspective on reality.",
        "sssUrl": "https://taddy.org/feeds/sss/comicseries/b00c3bc5-2758-483a-942c-4229eb742cb4"
      }
    ]
  }
}
```

## Query Breakdown:

For getMultipleComicSeries, you can get details on comics by passing in an array of uuids. 

```jsx
" An Array of unique identifiers (uuid). Max 25 IDs allowed "
uuids: [ID]
```

The response you get back is an array of [ComicSeries](https://taddy.org/developers/comics-api/comicseries).

```jsx
" Unique identifier for this comic "
uuid: ID

" Date when the comic was published (Epoch time in seconds) "
datePublished: Int

" The name (title) for a comic "
name: String

" The description for a comic "
description: String

" Status of the comic "
status: SeriesStatus

" The cover art for a comic "
coverImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The banner art for a comic "
bannerImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The thumbnail art for a comic "
thumbnailImageUrl: String

" Stringified JSON details for the cover art. Convert to JSON to use."
coverImageAsString: String

" Stringified JSON details for the banner art. Convert to JSON to use."
bannerImageAsString: String

" Stringified JSON details for the thumbnail art. Convert to JSON to use."
thumbnailImageAsString: String

" A hash of all comic details. It may be useful for you to save this property in your database and compare it to know if any comic details have updated since the last time you checked "
hash: String

" A hash of the details for all issues for this comic. It may be useful for you to save this property in your database and compare it to know if there are any new or updated issues since the last time you checked "
issuesHash: String

" A list of issues for this comic "
issues(
  " (Optional) Returns issues based on SortOrder. Default is LATEST (newest issues first), another option is OLDEST (oldest issues first), and another option is SEARCH (pass in the property searchTerm) to filter for issues by title or description. "
  sortOrder: SortOrder,

  " (Optional) Taddy paginates the results returned. Default is 1, Max value allowed is 1000 "
  page: Int,

  " (Optional) Return up to this number of issues. Default is 10, Max value allowed is 25 results per page "
  limitPerPage: Int,

  " (Optional) Only to be used when sortOrder is SEARCH. Filters through the title & description of issues for the searchTerm "
  searchTerm: String,

  " (Optional) The option to show issues that were once on the SSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedIssues: Boolean,
): [ComicIssue]

" The number of issues for this comic "
totalIssuesCount(
  " (Optional) Option to include issues that were once on the SSS feed but have now been removed. Default is false (do not include removed episodes) "
  includeRemovedIssues: Boolean
): Int

" Tags for the comic "
tags: [String]

" A comic can belong to multiple genres but they are listed in order of importance. "
genres: [Genre]

" The language the comic is in "
language: Language

" Creators of the comic "
creators: [Creator]

" Type of the comic "
seriesType: ComicSeriesType

" Layout of the comic "
seriesLayout: ComicSeriesLayout

" Rating of the comic "
contentRating: ContentRating

" Url for the comic's SSS feed "
sssUrl: String

" Name to use for contacting the owner of this feed "
sssOwnerName: String

" Email to use for contacting the owner of this feed "
sssOwnerPublicEmail: String

" Copyright details for this feed "
copyright: String

" Details on how often the SSS feed is checked for new details "
feedRefreshDetails: FeedRefreshDetails

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

[Creator](https://taddy.org/developers/comics-api/creator)

[SortOrder](https://taddy.org/developers/comics-api/sort-order)

[ContentRating](https://taddy.org/developers/comics-api/content-rating)

[Language](https://taddy.org/developers/comics-api/language)

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant)

[ComicSeriesType](https://taddy.org/developers/comics-api/comic-series-type)

[ComicSeriesType](https://taddy.org/developers/comics-api/comic-series-type)

[ComicSeriesType](https://taddy.org/developers/comics-api/comic-series-type)

[FeedRefreshDetails](https://taddy.org/developers/comics-api/feed-refresh-details)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# Get details on multiple issues

Use getMultipleComicIssues to get details on multiple issues. There is a max limit of 25 issues per request.

## Example:

```jsx
{
  getMultipleComicIssues(uuids:["94a870ec-331d-4bd8-998d-033d8c274d9b", "ec683ff4-88a7-4867-8da3-6d09dc527a82"]){
    uuid
    hash
    name
    bannerImageAsString
    thumbnailImageAsString
    storiesHash
    storyImageUrls
  }
}
```

Response from Taddyâ€™s API:

```jsx
{
  "data": {
    "getMultipleComicIssues": [
      {
        "uuid": "94a870ec-331d-4bd8-998d-033d8c274d9b",
        "hash": "a81a70abb6b288b9beff54ffab7fba2aa6cc9e35011c38757c7f914acb73b178",
        "name": "Chapter 7 (End of Book 1)",
        "bannerImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/\",\"banner_sm\":\"banner-sm.webp\",\"banner_md\":\"banner-md.webp\",\"banner_lg\":\"banner-lg.webp\"}",
        "thumbnailImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/e3592703-7b84-4be8-81af-ee9becd6395f/\",\"thumbnail\":\"thumbnail.webp\"}",
        "storiesHash": "ccc2bf99382a01cd6ccd15b05f3c53bbd65ffca7972733f291edeaeedd0c49cc",
        "storyImageUrls": [
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/a9fa463c-7f61-4086-bd9c-c225ef7001c0/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/e99bf7a3-2758-414a-a8fe-04a79f7689fa/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/cc7f2c50-2f8b-4d9b-884e-7b13d2ca63b2/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/5358ac04-2e3b-4841-9fc0-3357ea8a54e3/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/9e4bb1e9-b1e4-4d9d-90a4-e36129469cd3/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/c94f4099-ee58-42f7-a4a7-dbdf8bc61b1b/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/af4f8335-025b-48f9-b7df-78a8c44ba114/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/7ac6b398-f84a-4ff5-8669-7cbc743171da/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/1e454f82-142a-4537-9de1-f6879269542f/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/0738a255-f1e0-4c39-92cf-50813a685b42/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/ed9ecfaf-b030-464a-bfd8-6c322357e047/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/69a6ad77-8a31-4db4-b5ea-600259b63862/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/416c50b6-7cdd-4281-bc6b-98aa18acffd0/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/32f1519e-d20f-4ff5-8aba-da9654419425/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/e1b15ae9-8ca1-40d4-9310-5a8014a7f9c3/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/40c1ab8f-09bd-4242-bf31-4943d6448a7d/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/ddd7a320-3465-4cdb-91ca-20318f96fd05/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/818cecbf-8316-4bf4-961f-bcb5d40e7ada/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/6aea7da4-6eeb-4614-b523-2e3294541dff/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/f2631b7c-977a-4722-b15d-ca703de0e227/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/478524a1-e2d6-4f90-8899-774aa8ad78fa/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/a3adfa8b-21f7-4e7a-94d7-beab0bf27866/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/27ae4f08-d180-45fa-b67d-100f201a9779/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/bcee7b91-2d23-423e-b197-f0c13ffdf942/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/94a870ec-331d-4bd8-998d-033d8c274d9b/762bbc61-da2f-4493-bb82-cce3e8f0a280/story.webp"
        ]
      },
      {
        "uuid": "ec683ff4-88a7-4867-8da3-6d09dc527a82",
        "hash": "79564fce286f4f8e5bd11e0da43d3e06478480b579115c16a3c22ed4074f21da",
        "name": "Chapter 8 (Beginning of Book 2)",
        "bannerImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/\",\"banner_sm\":\"banner-sm.webp\",\"banner_md\":\"banner-md.webp\",\"banner_lg\":\"banner-lg.webp\"}",
        "thumbnailImageAsString": "{\"base_url\":\"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/38e601d7-f20f-43f5-ab2f-70bd9f3c03a0/\",\"thumbnail\":\"thumbnail.webp\"}",
        "storiesHash": "b6617ec945f6ea3c00d0e055674f139a647c486f11818f6f59c2a540eaa66add",
        "storyImageUrls": [
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/f8274472-bfef-4d1b-a80d-2fb492be1b84/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/621d3a5a-0d71-4417-9b4d-a796a545fcb6/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/a485ff80-5974-4d0c-9455-29f4fc60efa3/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/b0ec77d3-5f4c-458d-9c8a-51dbed90763b/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/9a748190-8276-4bfa-bc9a-27f194be24c5/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/baaca97c-5508-4469-b425-26a538c47cc3/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/9665c24c-382e-4c7d-9a60-639e6913f0e6/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/fd5aecea-0bcc-4054-977c-d39b5d440c17/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/2b13fd21-5821-47be-bf4c-e8472d9a4c00/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/bbce0c2e-01fe-44c1-9dac-a3f3f25a841c/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/9893dac3-79d3-4576-9cb4-1b2082290db6/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/4eeb32cc-d122-4951-9dba-6c12109da0d8/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/4a1b4d4f-c1d9-46e4-a5ce-0f8ac8ef2305/story.webp",
          "https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/ec683ff4-88a7-4867-8da3-6d09dc527a82/ecbdda7f-9aad-4727-b54c-ede12342ad22/story.webp"
        ]
      }
    ]
  }
}
```

## Query Breakdown:

For getMultipleComicIssues, you can get details on issues by passing in an array of uuids. 

```jsx
" An Array of unique identifiers (uuid). Max 25 IDs allowed "
uuids: [ID]
```

The response you get back is an array of [ComicIssues](https://taddy.org/developers/comics-api/comicissue).

```jsx
" Unique identifier for a comic issue "
uuid: ID

" Unique identifier for a comic series this issue belongs to "
seriesUuid: ID

" Date when the issue was published (Epoch time in seconds) "
datePublished: Int

" The name (title) of the issue "
name: String

" Short note from the creator for the issue "
creatorNote: String

" Push notification message for the issue "
pushNotificationMessage: String

" A different hash means that details for this issue have updated since the last hash "
hash: String

" A different hash means that details for the stories that make up this issue have updated since the last hash "
storiesHash: String

" Stringified JSON details for the banner art. Convert to JSON to use."
bannerImageAsString: String

" Stringified JSON details for the thumbnail art. Convert to JSON to use."
thumbnailImageAsString: String

" The banner art for an issue "
bannerImageUrl(
  " (Optional) size / variant. Default is SMALL "
  variant: ImageVariant
):String

" The thumbnail art for an issue "
thumbnailImageUrl: String

" All the images for this issue "
imageUrls: [String]

" Details on all the stories for this issue "
stories: [ComicStory]

" Position of this issue in relation to other issues. This is used to sort issues by oldest or latest in a series. "
position: Int

" If the issue has now been removed from the SSS Feed "
isRemoved: Boolean

" If the content has violated Taddy's distribution policies for illegal or harmful content it will be blocked from getting any updates "
isBlocked: Boolean

" Details on the comic for which this issue belongs to "
comicSeries: ComicSeries
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[ComicIssue](https://taddy.org/developers/comics-api/comicissue)

[ComicStory](https://taddy.org/developers/comics-api/comicstory)

[ImageVariant](https://taddy.org/developers/comics-api/imagevariant)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# ImageVariant

Possible Image variants available:

```jsx
enum ImageVariant {
  SMALL
  MEDIUM
  LARGE
}
```

---

# Language

A list of possible Languages. Conforms to [ISO 639-2](https://www.loc.gov/standards/iso639-2/php/code_list.php)

```jsx
enum Language {
  ABKHAZIAN
  AFAR
  AFRIKAANS
  AKAN
  ALBANIAN
  AMHARIC
  ARABIC
  ARAGONESE
  ARMENIAN
  ASSAMESE
  AVARIC
  AVESTAN
  AYMARA
  AZERBAIJANI
  BAMBARA
  BASHKIR
  BASQUE
  BELARUSIAN
  BENGALI
  BIHARI_LANGUAGES
  BISLAMA
  BOSNIAN
  BRETON
  BULGARIAN
  BURMESE
  CENTRAL_KHMER
  CHAMORRO
  CHECHEN
  CHICHEWA_CHEWA_NYANJA
  CHINESE
  CHURCH_SLAVONIC
  CHUVASH
  CORNISH
  CORSICAN
  CREE
  CROATIAN
  CZECH
  DANISH
  DHIVEHI_MALDIVIAN
  DUTCH_FLEMISH
  DZONGKHA
  ENGLISH
  ESPERANTO
  ESTONIAN
  EWE
  FAROESE
  FARSI
  FIJIAN
  FINNISH
  FRENCH
  FULAH
  GAELIC
  GALICIAN
  GANDA
  GEORGIAN
  GERMAN
  GIKUYU
  GREEK
  GUARANI
  GUJARATI
  HAITIAN_CREOLE
  HAUSA
  HEBREW
  HERERO
  HINDI
  HIRI_MOTU
  HUNGARIAN
  ICELANDIC
  IDO
  IGBO
  INDONESIAN
  INTERLINGUA
  INTERLINGUE_OCCIDENTAL
  INUKTITUT
  INUPIAQ
  IRISH
  ITALIAN
  JAPANESE
  JAVANESE
  KALAALLISUT_GREENLANDIC
  KANNADA
  KANURI
  KASHMIRI
  KAZAKH
  KINYARWANDA
  KOMI
  KONGO
  KOREAN
  KURDISH
  KWANYAMA
  KYRGYZ
  LAO
  LATIN
  LATVIAN
  LETZEBURGESCH
  LIMBURGISH
  LINGALA
  LITHUANIAN
  LUBA_KATANGA
  MACEDONIAN
  MALAGASY
  MALAY
  MALAYALAM
  MALTESE
  MANX
  MAORI
  MARATHI
  MARSHALLESE
  MONGOLIAN
  NAURU
  NAVAJO
  NDONGA
  NEPALI
  NORTH_NDEBELE
  NORTHERN_SAMI
  NORWEGIAN
  NORWEGIAN_BOKMAL
  NORWEGIAN_NYNORSK
  NUOSU_SICHUAN_YI
  OCCITAN
  OJIBWA
  ORIYA
  OROMO
  OSSETIAN
  PALI
  PASHTO
  POLISH
  PORTUGUESE
  PUNJABI
  QUECHUA
  ROMANIAN_MOLDOVAN
  ROMANSH
  RUNDI
  RUSSIAN
  SAMOAN
  SANGO
  SANSKRIT
  SARDINIAN
  SERBIAN
  SHONA
  SINDHI
  SINHALA
  SLOVAK
  SLOVENIAN
  SOMALI
  SOTHO
  SOUTH_NDEBELE
  SPANISH
  SUNDANESE
  SWAHILI
  SWATI
  SWEDISH
  TAGALOG
  TAHITIAN
  TAJIK
  TAMIL
  TATAR
  TELUGU
  THAI
  TIBETAN
  TIGRINYA
  TONGA
  TSONGA
  TSWANA
  TURKISH
  TURKMEN
  TWI
  UKRAINIAN
  URDU
  UYGHUR
  UZBEK
  VALENCIAN_CATALAN
  VENDA
  VIETNAMESE
  VOLAPUK
  WALLOON
  WELSH
  WESTERN_FRISIAN
  WOLOF
  XHOSA
  YIDDISH
  YORUBA
  ZHUANG
  ZULU
}
```

---

# Search for comics

Use search to search through all comics. 

Be sure to include `filterForTypes: COMICSERIES` as an argument (By default search does not search for comics)

## Examples:

1. Searching for comics that match the term "Warrior's Strife"

```jsx
{
  search(term:"Warrior's Strife", filterForTypes:COMICSERIES){
    searchId
    comicSeries{
      uuid
      name
      sssUrl
    }
  }
}
```

### Filtering

You have the ability to filter for results from a specific country, genre, language, before or after a certain publish date, before or after the latest episode was published, You can see the full list of filtering options available in the [Query Breakdown](https://taddy.org/developers/podcast-api/search) section below. 

2. Searching for comics in the Romance Genre

```jsx
{
  search(filterForTypes:COMICSERIES, filterForGenres:COMICSERIES_ROMANCE){
    searchId
    comicSeries{
      uuid
      name
      sssUrl
    }
  }
}
```

3. Searching for comics with tags â€œMonsterloversâ€ or â€œMonster loversâ€

```jsx
{
  search(filterForTypes:COMICSERIES, filterForTags:["Monsterlovers", "Monster lovers"]){
    searchId
    comicSeries{
      uuid
      name
      sssUrl
    }
  }
}
```

### Pagination

4. Searching for page 2 of comics with tags â€œMonsterloversâ€ or â€œMonster loversâ€

```jsx

{
  search(filterForTypes:COMICSERIES, filterForGenres:COMICSERIES_ROMANCE, page:2, limitPerPage:25){
    searchId
    comicSeries{
      uuid
      name
      sssUrl
    }
  }
}
```

## Query Breakdown:

For search, you can search for podcasts or episodes using theses properties:

```jsx
" The term you are searching for "
term: String

" (Optional) Allows for pagination. Default is 1 (ie: page 1 of the results). Max value is 20. "
page: Int

" (Optional) The number of results per page. Default is 10. Max value is 25 (ie: that max results you can return in one query in 25) "
limitPerPage: Int

" (Optional) Filter for certain types of content. Default is PODCASTSERIES. Possible values are PODCASTSERIES, PODCASTEPISODE, COMICSERIES, CREATOR "
filterForTypes: [SearchContentType]

" (Optional) Filter for only content made in certain countries "
filterForCountries: [Country]

" (Optional) Filter for only content made in certain languages "
filterForLanguages: [Language]

" (Optional) Filter for only content from certain genres "
filterForGenres: [Genre]

" (Optional) Filter for results only from certain series "
filterForSeriesUuids: [ID]

" (Optional) Filter for results that are not from certain series "
filterForNotInSeriesUuids: [ID]

" (Optional) Filter for content that have an episode published after a certain date (Epoch time in seconds). This filter is only for PODCASTSERIES & COMICSERIES and will return an empty array for any other type "
filterForLastUpdatedAfter: Int

" (Optional) Filter for results that have an episode published before a certain date (Epoch time in seconds). This filter is only for PODCASTSERIES & COMICSERIES and will return an empty array for any other type "
filterForLastUpdatedBefore: Int

" (Optional) Filter for only content that includes a certain tag. Tags are case sensitive and must be exact matches. This filter is only for COMICSERIES & CREATOR and will return an empty array for any other type "
filterForTags: [String]

" (Optional) Filter for only content that has a certain content rating. This filter is only for COMICSERIES and will return an empty array for any other types. For PODCASTSERIES & PODCASTEPISODE use isSafeMode "
filterForContentRatings: [ContentRating]

" (Optional) Filter for if a comic's series type is WEBTOON, MANGA, etc. Default is null (include all comic types). This filter is only for COMICSERIES and will return an empty array for any other type "
filterForComicSeriesType: [ComicSeriesType]

" (Optional) Filter for if a comic's series layout is VERTICAL_SCROLL_TOP_TO_BOTTOM or PAGE etc. Default is null (include all comic layouts). This filter is only for COMICSERIES and will return an empty array for any other type "
filterForComicSeriesLayout: [ComicSeriesLayout]

" (Optional) Filter for only content that has a certain total number of issues. This filter is only for COMICSERIES and will return an empty array for any other type "
filterForTotalIssuesLessThan: Int

" (Optional) Filter for only content that has a certain total number of issues. This filter is only for COMICSERIES and will return an empty array for any other type "
filterForTotalIssuesGreaterThan: Int

" (Optional) Filter for only content that has a certain scopes for exclusive content. This filter is only for COMICSERIES and will return an empty array for any other type "
filterForExclusiveContent: [String]

" (Optional) Choose how the results are sorted. Default is sort by EXACTNESS. Possible values are EXACTNESS and POPULARITY. "
sortBy: SearchSortOrder

" (Optional) Choose which results are matched as valid search results. Default is MOST_TERMS. Possible values are MOST_TERMS, ALL_TERMS, FREQUENCY. If you search has multiple terms, FREQUENCY gives more weight to the terms that appear less frequently in results "
matchBy: SearchMatchType

" (Optional) Choose to only return safe (not explicit) content or all content. Default is false (include everything, including explicit content) "
isSafeMode: Boolean
```

The response you get back includes an array of [ComicSeries](https://taddy.org/developers/comics-api/comicseries) that match your search term.

```jsx

" Identifier for the search query being sent "
searchId: ID!

" A list of ComicSeries items "
comicSeries: [ComicSeries]

" Ranking information for each search result "
rankingDetails: [SearchRankingDetails]

" Additional information on the search results (Total # of results, pages, etc) "
responseDetails: [SearchResponseDetails]
```

### Referenced types in this document:

[ComicSeries](https://taddy.org/developers/comics-api/comicseries)

[Country](https://taddy.org/developers/creator-api/country)

[Language](https://taddy.org/developers/comics-api/language)

[Genre](https://taddy.org/developers/comics-api/genre)

[TaddyType](https://taddy.org/developers/comics-api/taddytype)

<aside>
ðŸ‘‹ If you are new to using Taddyâ€™s API, **Get Started** with our [**ðŸ¤–Â Intro to Taddyâ€™s API](https://taddy.org/developers/intro-to-taddy-graphql-api).**

</aside>

More Links:  

[Sign Up â†’](https://taddy.org/signup/developers)

[Taddy Homepage â†’](https://taddy.org/) 

[Taddy Dashboard â†’](https://taddy.org/dashboard) 

[Pricing](https://taddy.org/developers/pricing)

[Terms of Service](https://taddy.org/terms-of-service)

[Developer Policy](https://taddy.org/terms-of-service/developer-policy)

---

# SearchContentType

The different types of content you can search for using the [`search`](https://taddy.org/developers/podcast-api/search) query on Taddy.

```jsx
enum SearchContentType {
  PODCASTSERIES
  PODCASTEPISODE
  COMICSERIES
  CREATOR
}
```

---

# SearchMatchType

Choose how your search matches contentâ€”by exact phrase, all terms, or most terms.

```jsx
enum SearchMatchType {
  EXACT_PHRASE
  MOST_TERMS
  ALL_TERMS
}
```

`EXACT_PHRASE`Â â€“ Returns results that contain the exact phrase only. This is ideal for searching names, e.g.,Â "Peter Smith", as results will only include instances where the full and exact phrase "Peter Smith" appears.

`ALL_TERMS`Â â€“ Returns results that contain all the provided search terms, regardless of the order of the terms. Exact phrase matches are ranked higher.

`MOST_TERMS`Â (Default) â€“ Returns results that contain any of the search terms provided. Exact phrase matches and multiple terms matched are ranked higher.

---

# SearchRankingDetails

Ranking details (including score) about each search result.

```jsx
type SearchRankingDetails {
  " Identifier for the search query being sent "
  id: ID!

  " The UUID of the item being returned in the search results "
  uuid: ID

  " The type of item being returned in the search results "
  type: SearchContentType

  " The ranking score for the search results from 100 (exact match) to 0 (no match) "
  rankingScore: Int
}
```

---

# SearchResponseDetails

Additional details about the search request.

```jsx
type SearchResponseDetails {
  " Identifier for the search query being sent "
  id: ID!

  " The type of item being returned in the search results "
  type: SearchContentType

  " Total number of search results returned for this type "
  totalCount: Int

  " Total number of pages of results returned for this type "
  pagesCount: Int
}
```

---

# SearchSortOrder

Choose if you want search results to be prioritized by exact term matching or popularity of the content.

```jsx
enum SearchSortOrder {
  EXACTNESS
  POPULARITY
}
```

`EXACTNESS`(Default) - Search results will prioritize exact matching based on title, description, publisher name and other relevant information. 

`POPULARITY` - Search will still match the terms you provide, but the result be filtered to include only the top 5%  by most popular comics.

---

# SeriesStatus

Status of the Comic

```jsx
enum SeriesStatus {
  ONGOING
  HIATUS
  COMPLETED
  CANCELLED
  ANNOUNCED
  UNDER_REVISION
}
```

`ONGOING` - The series is actively being published.

`HIATUS` - The series is on a temporary break.

`COMPLETED` - The series has finished publication.

`CANCELLED` - The series has been stopped before its planned conclusion.

`ANNOUNCED` - The series has been announced but not yet started.

`UNDER_REVISION` - The series is being reworked or overhauled.

---

# SortOrder

SortOrder gives you the ability to return different items based on what option you pass in.

```jsx
enum SortOrder {
  LATEST
  OLDEST
  SEARCH
}
```

`LATEST` - Return latest (newest) first

`OLDEST` - Return oldest first

`SEARCH` - Return only issues that match the filtered `searchTerm` property (which should be passed in whenever `SEARCH` is chosen as the SortOrder).

---

# TaddyType

The different types of media available on Taddy

```jsx
enum TaddyType {
  PODCASTSERIES
  PODCASTEPISODE
	COMICSERIES
  COMICISSUE
	CREATOR
}
```

`PODCASTSERIES` - A podcast

`PODCASTEPISODE` - An episode of a podcast

`COMICSERIES` - A comic

`COMICISSUE` - An issue (episode) of a comic

`CREATOR` - A creator (that made the comic).

---

# Webhooks

**Webhooks are an incredibly useful feature of the Taddy API.** In the background, Taddy is constantly monitoring SSS feeds to know whenever they have been updated. Whenever there is new data, we send it to you via a webhook. This means you don't have to continuously check with us for new updates, we'll push those updates to you immediately.

## Example use-cases:

- Are you **building a comic app?** Get a webhook notification immediately after new issues release (so you can display the new issue and send your users a push notification about it)
- Do you care about **SEO?** Get a webhook notification immediately your website gets linked to from any new comic.
- Do you provide **translation** creation services? Get a webhook notification immediately after your customer releases a new issue. Remind them to use your product on the newly released issue (or proactively reach out to them if haven't been using your service)

## Setting Up a Webhook

- Open up the [**Taddy Dashboard**](https://taddy.org/dashboard), you will see a â€œSetup a New Webhookâ€ button.
- You will have to enter in:
    1. The endpoint url where you want to receive the notifications (usually on your own server)
    2. Pick which webhook events you would like to receive.

**Note:** 

- You must be on a paid plan to add a webhook to your account.
- Webhook notifications do not count against your monthly API limits ðŸ¥³.
- The endpoint url you enter needs to be one publicly accessible over the internet. (For example, 127.0.0.1 and localhost URLs will not work, since Taddy servers will not be able to contact your local computer). If youâ€™d like to test receiving these notifications locally on your computer, [Ngrok](https://ngrok.com) provides a useful & free way to do so. It provides you a public url that is mapped to your localhost.
- Your endpoint url needs to be a  `POST` endpoint
- In the Webhooks section, one of the fields given to you is the Webhook Secret. This is an optional security feature. We add this secret as the header `X-TADDY-WEBHOOK-SECRET` to all webhook notifications sent to this webhook. Because your endpoint url is publicly accessible and if you dont share this secret with anyone else, you can be confident that any data you receive on your endpoint is from us and can be trusted.
- [Listen Up](https://heylistenup.app/) is a very simple app that receives and displays webhook events. It may be useful to you as a very easy way to see what kind of events are going to be received by your webhook endpoint.
- There is an [example project](https://github.com/taddyorg/taddy-api-example-project) which allows you to mock webhook events. It's a great way to test receiving webhook events before receiving the live events from Taddyâ€™s API.

## Webhook Events

A list of possible events for:

### ComicSeries

| **Event** | **Description** |
| --- | --- |
| `comicseries.created` | Get a notification when a new comic series has been added to Taddy |
| `comicseries.updated` | Get a notification when any comic series details have been updated (e.g. name, description, etc.) |
| `comicseries.deleted` | Get a notification when a comic series has been removed from Taddy, usually at the request of the comic creator. |
| `comicseries.new_issues_released` | Most users won't need to subscribe to this event. When new issues have been released, you'll get this notification only once, no matter how many issues have been added, updated, or removed. An example use case for this notification is if you're parsing the comic SSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each issue that's been added or updated. |

### ComicIssue

| **Event** | **Description** |
| --- | --- |
| `comicissue.created` | Get a notification when a new comic issue has been released |
| `comicissue.updated` | Get a notification when comic issue details have been updated (e.g. name, description, images, etc.) |
| `comicissue.deleted` | Get a notification when a comic issue has been removed. |

### Creator

| **Event** | **Description** |
| --- | --- |
| `creator.created` | Get a notification when a new creator feed has been released |
| `creator.updated` | Get a notification when creator details have been updated (e.g. name, description, avatar image, etc.) |
| `creator.deleted` | Get a notification when a comic feed has been removed from Taddy, usually at the request of the creator |
| `creator.new_content_released` | Most users won't need to subscribe to this event. When new creatorcontent have been released, you'll get this notification only once, no matter how many creatorcontent items have been added, updated, or removed. An example use case for this notification is if you're parsing the creator SSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each piece of creatorcontent that's been added or updated. |

### CreatorContent

| **Event** | **Description** |
| --- | --- |
| `creatorcontent.created` | Get a notification when the creator has released a new type of content (along with their role in making the content). ex) Creator had made a ComicSeries and had the roles COMICSERIES_ARTIST & COMICSERIES_WRITER on it. |
| `creatorcontent.updated` | Get a notification when a details around the the role they performed in creating the content are updated. |
| `creatorcontent.deleted` | Get a notification when a creator has removed a type of content from their content feed. |

## What does a webhook event look like?

A webhook event is made up of:  `uuid`, `taddyType`, `action`, `timestamp`, `data`.

### Example:

```jsx
// EXAMPLE WEBHOOK EVENT for ComicSeries event
{
  uuid: 'b00c3bc5-2758-483a-942c-4229eb742cb4',
  taddyType: 'comicseries',
  action: 'created',
  timestamp: 1684445348,
  data: {
    uuid: 'b00c3bc5-2758-483a-942c-4229eb742cb4',
    name: "Warrior's Strife",
    description: 'Frayja has trained all her life to become the greatest warrior in her clan. She has been taught to fear and hate elves and their magic. But a day on the battlefield dramatically alters her perspective on reality.',
    hash: '397b0176c21bff026d7b58e7bf87147626d4b9c2f7c7a6ddb5de467aa8b1c751',
    issuesHash: 'c005d4e0aadf39c1a043ff3be40833480a2adab59cbffd3ea86620da0a21cc97',
    datePublished: 1683664725,
    coverImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/97699676-ece5-4ab5-b089-5288cfa7421c/","cover_sm":"cover-sm.webp","cover_md":"cover-md.webp","cover_lg":"cover-lg.webp"}',
    bannerImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/","banner_sm":"banner-sm.webp","banner_md":"banner-md.webp","banner_lg":"banner-lg.webp"}',
    thumbnailImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/a1718041-2b8c-42e9-94fe-099ade2b3417/","thumbnail":"thumbnail.webp"}',
    tags: [
      'fantasy',  'darkfantasy',
      'lgbtqia',  'lgbt',
      'monsters', 'viking',
      'elf',      'magic',
      'action',   'drama',
      'medieval'
    ],
    genres: [ 'COMICSERIES_ROMANCE', 'COMICSERIES_DRAMA' ],
    language: 'ENGLISH',
    contentRating: 'COMICSERIES_TEENS',
    seriesType: 'WEBTOON',
    sssUrl: 'https://taddy.org/feeds/sss/comicseries/b00c3bc5-2758-483a-942c-4229eb742cb4',
    sssOwnerName: null,
    sssOwnerPublicEmail: null,
    copyright: 'Copyright notice available at http://3s-docs.org/creator-friendly-copyright-notice',
    isCompleted: false,
    isBlocked: null,
    totalIssuesCount: 16
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for ComicIssue event
{
  uuid: '826f3ba0-c231-4d93-95ad-d3e65f9d57b1',
  taddyType: 'comicissue',
  action: 'created',
  timestamp: 1684445690,
  data: {
    uuid: '826f3ba0-c231-4d93-95ad-d3e65f9d57b1',
    seriesUuid: 'b00c3bc5-2758-483a-942c-4229eb742cb4',
    name: 'Chapter 1 Book 1',
    creatorNote: null,
    hash: '08b4ce69403beb038ce831ea7163d4c9ae81139c52b82c55e0eafe7160d18a99',
    storiesHash: '94d497b23c9a506d1bec028c70e9901f83a0caafacc60445c77fc6a1f050a55f',
    datePublished: 1683664717,
    bannerImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/","banner_sm":"banner-sm.webp","banner_md":"banner-md.webp","banner_lg":"banner-lg.webp"}',
    thumbnailImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/43e484b0-6111-41f2-92e2-0b6515655969/","thumbnail":"thumbnail.webp"}',
    stories: [
      {
        "uuid": "9f2120bb-5660-44a3-a3d5-9537bdd438e9",
        "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
        "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
        "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/9bb57b57-0c0c-4d1a-8eed-0d64daf347d9/\",\"story\":\"story.webp\"}"
      },
      {
        "uuid": "5a0bd406-d513-46cf-8fd1-b8474e4e1909",
        "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
        "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
        "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/a51fad16-89a9-4a0e-8d2c-88625f8bc957/\",\"story\":\"story.webp\"}"
      },
      ...,
    ],
    position: 0,
    isRemoved: null,
    isBlocked: null
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for Creator event
{
  uuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
  taddyType: 'creator',
  action: 'created',
  timestamp: 1684448992,
  data: {
    uuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    name: 'Zachary Morris',
    bio: null,
    hash: '2b80ecea81312717bcbe1872aa80c52ede308993819fdbafa2027ee123ed565c',
    contentHash: '9d982608495697f438e0e31d768fdb297d1e6984f013842b2ccc982562c343a6',
    avatarImageAsString: '{"base_url":"https://ax1.taddy.org/5a4977e8-b0da-4cc7-a516-16a0fb6973d8/c414e9a8-8d27-4280-9e43-a2e5445a9626/","avatar_sm":"avatar-sm.webp","avatar_md":"avatar-md.webp","avatar_lg":"avatar-lg.webp"}',
    tags: [ 'zachmorris', 'willdrawforfood1', 'cartoonist' ],
    country: 'UNITED_STATES_OF_AMERICA',
    linksAsString: '[{"type":"PATREON","base_url":"https://patreon.com/","value":"zachmorris"},{"type":"TWITTER","base_url":"https://twitter.com/","value":"toonzach"}]',
    sssUrl: 'https://taddy.org/feeds/sss/creator/5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    sssOwnerName: null,
    sssOwnerPublicEmail: null,
    copyright: 'Copyright notice available at http://3s-docs.org/creator-friendly-copyright-notice',
    isBlocked: null,
    totalContentCount: 1
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for CreatorContent event
{
  uuid: '3c90e534-0aba-4536-8bab-43e9dd0c4ac9',
  taddyType: 'creatorcontent',
  action: 'created',
  timestamp: 1684449090,
  data: {
    hash: 'f2c93ef053a62b05da1092cb39e41726da9fbbe436888b4d53dd6c9067839013',
    creatorUuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    contentUuid: '71113968-45a2-4c30-b770-655b57ae0de6',
    contentType: 'COMICSERIES',
    roles: [ 'COMICSERIES_ARTIST', 'COMICSERIES_WRITER' ],
    position: 0,
    contentPosition: 0
  }
}
```

---

# Webhooks

**Webhooks are an incredibly useful feature of the Taddy API.** In the background, Taddy is constantly monitoring RSS feeds to know whenever they have been updated. Whenever there is new data, we send it to you via a webhook. This means you don't have to continuously check with us for new updates, we'll push those updates to you immediately.

## Example use-cases:

- Do you care about **SEO?** Get a webhook notification immediately whenever your website is mentioned on a new episode.
- Do you want **industry specific leads**? Get a webhook notification immediately when a person or a brand has been mentioned on a new episode.
- Do you want to **push notify your users** whenever a new episode is released? Get a webhook notification immediately when a new episode is released. On your server, you match which users want a push notification and send one to them.

## Setting Up a Webhook

- Open up the [**Taddy Dashboard**](https://taddy.org/dashboard), you will see a â€œSetup a New Webhookâ€ button.
- You will have to enter in:
    1. The endpoint url where you want to receive the notifications (usually on your own server)
    2. Pick which webhook events you would like to receive.

**Note:** 

- You must be on a paid plan to add a webhook to your account.
- Webhook notifications do not count against your monthly API limits ðŸ¥³.
- The endpoint url you enter needs to be one publicly accessible over the internet. (For example, 127.0.0.1 and localhost URLs will not work, since Taddy servers will not be able to contact your local computer). If youâ€™d like to test receiving these notifications locally on your computer, [Ngrok](https://ngrok.com) provides a useful & free way to do so (It provides you a public url that is mapped to your localhost).
- Your endpoint url needs to be a  `POST` endpoint
- In the Webhooks section, one of the fields given to you is the Webhook Secret. This is an optional security feature. We add this secret as the header `X-TADDY-WEBHOOK-SECRET` to all webhook notifications sent to this webhook. Because your endpoint url is publicly accessible and if you dont share this secret with anyone else, you can be confident that any data you receive on your endpoint is from us and can be trusted.
- [Listen Up](https://heylistenup.app/) is a very simple app that receives and displays webhook events. Itâ€™s a great way to test and see what kind of events your webhook endpoint is going to receive.
- There is an [example project](https://github.com/taddyorg/taddy-api-example-project) which allows you to mock webhook events. It's a great way to test receiving webhook events before receiving the live events from Taddyâ€™s API.

## Webhook Events

<aside>
ðŸ’¡ If you are interested in receiving webhooks for **Podcasts**, the following 3 webhook types may be useful to you:

- PodcastSeries
- PodcastEpisode
- iTunesInfo
</aside>

### PodcastSeries

| **Event** | **Description** |
| --- | --- |
| `podcastseries.created` | Get a notification when a new podcast series has been added to Taddy |
| `podcastseries.updated` | Get a notification when any podcast series details have been updated (e.g. title, description, etc.) |
| `podcastseries.deleted` | Get a notification when a podcast series has been removed from Taddy, usually at the request of the podcast creator |
| `podcastseries.new_episodes_released` | Most users won't need to subscribe to this event. When new episodes have been released, you'll get this notification only once, no matter how many episodes have been added, updated, or removed. An example use case for this notification is if you're parsing the podcast RSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each episode that's been added or updated. |

### PodcastEpisode

| **Event** | **Description** |
| --- | --- |
| `podcastepisode.created` | Get a notification when a new podcast episode has been released |
| `podcastepisode.updated` | Get a notification when podcast episode details have been updated (e.g. title, description, audioUrl, etc.) |
| `podcastepisode.deleted` | Get a notification when a podcast episode has been removed. |

### iTunesInfo

| **Event** | **Description** |
| --- | --- |
| `itunesinfo.created` | Get a notification when itunes information for a podcast series has been added to Taddy (e.g. itunes artwork etc.) This is information from iTunes and not found on the podcast's RSS feed |
| `itunesinfo.updated` | Get a notification when itunes information for a podcast series has been updated (e.g. itunes artwork etc.) This is information from iTunes and not found on the podcast's RSS feed |
| `itunesinfo.deleted` | Get a notification when itunes information for a podcast series has been removed from Taddy, usually because it has been removed from iTunes |

<aside>
ðŸ’¡ If you are interested in receiving webhooks for **Comics**, the following 4 webhook types may be useful to you:

- ComicSeries
- ComicIssue
- Creator
- CreatorContent
</aside>

### ComicSeries

| **Event** | **Description** |
| --- | --- |
| `comicseries.created` | Get a notification when a new comic series has been added to Taddy |
| `comicseries.updated` | Get a notification when any comic series details have been updated (e.g. name, description, etc.) |
| `comicseries.deleted` | Get a notification when a comic series has been removed from Taddy, usually at the request of the comic creator. |
| `comicseries.new_issues_released` | Most users won't need to subscribe to this event. When new issues have been released, you'll get this notification only once, no matter how many issues have been added, updated, or removed. An example use case for this notification is if you're parsing the comic SSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each issue that's been added or updated. |

### ComicIssue

| **Event** | **Description** |
| --- | --- |
| `comicissue.created` | Get a notification when a new comic issue has been released |
| `comicissue.updated` | Get a notification when comic issue details have been updated (e.g. name, description, images, etc.) |
| `comicissue.deleted` | Get a notification when a comic issue has been removed. |

### Creator

| **Event** | **Description** |
| --- | --- |
| `creator.created` | Get a notification when a new creator feed has been released |
| `creator.updated` | Get a notification when creator details have been updated (e.g. name, description, avatar image, etc.) |
| `creator.deleted` | Get a notification when a comic feed has been removed from Taddy, usually at the request of the creator |
| `creator.new_content_released` | Most users won't need to subscribe to this event. When new creatorcontent have been released, you'll get this notification only once, no matter how many creatorcontent items have been added, updated, or removed. An example use case for this notification is if you're parsing the creator SSS feed yourself and want to be notified once when the feed has changed, instead of getting multiple notifications for each piece of creatorcontent that's been added or updated. |

### CreatorContent

| **Event** | **Description** |
| --- | --- |
| `creatorcontent.created` | Get a notification when the creator has released a new type of content (along with their role in making the content). ex) Creator had made a ComicSeries and had the roles COMICSERIES_ARTIST & COMICSERIES_WRITER on it. |
| `creatorcontent.updated` | Get a notification when a details around the the role they performed in creating the content are updated. |
| `creatorcontent.deleted` | Get a notification when a creator has removed a type of content from their content feed. |

## What does a webhook event look like?

A webhook event is made up of:  `uuid`, `taddyType`, `action`, `timestamp`, `data`.

### Examples:

```jsx
// EXAMPLE WEBHOOK EVENT for a PodcastSeries event
{
  uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
  taddyType: 'podcastseries',
  action: 'updated',
  timestamp: 1673984316,
  data: {
    uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
    hash: 'b8f97d07ce3a9916aeba1feda9db610b58aee8d62a2dfa0495a0b1893e857d91',
    name: 'The Daily',
    description: 'This is what the news should sound like. The biggest stories of our time, told by the best journalists in the world. Hosted by Michael Barbaro and Sabrina Tavernise. Twenty minutes a day, five days a week, ready by 6 a.m.',
    imageUrl: 'https://image.simplecastcdn.com/images/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/2cce5659-f647-4366-b318-46e4b67afcfa/3000x3000/c81936f538106550b804e7e4fe2c236319bab7fba37941a6e8f7e5c3d3048b88fc5b2182fb790f7d446bdc820406456c94287f245db89d8656c105d5511ec3de.jpeg?aid=rss_feed',
    datePublished: 1484687987,
    language: 'ENGLISH',
    seriesType: 'EPISODIC',
    contentType: 'AUDIO',
    isExplicitContent: false,
    copyright: 'Â© 2020-2021 THE NEW YORK TIMES COMPANY; The New York Times encourages the use of RSS feeds for personal use in a news reader or as part of a non-commercial blog, subject to your agreement to our Terms of Service.',
    websiteUrl: 'https://www.nytimes.com/the-daily',
    rssUrl: 'https://feeds.simplecast.com/54nAGcIl',
    rssOwnerName: 'The New York Times',
    rssOwnerPublicEmail: 'thedaily@nytimes.com',
    authorName: 'The New York Times',
    isCompleted: false,
    isBlocked: null,
    itunesId: 1200361736,
    genres: [ 'PODCASTSERIES_NEWS_DAILY_NEWS', 'PODCASTSERIES_NEWS' ],
    childrenHash: '95ef1fee188406c415e32915f85855eab3cf49e0f9052785e42ef368792333db',
    popularityRank: 'TOP_200',
    itunesInfo: {
      uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
      publisherId: 121664449,
      publisherName: 'The New York Times',
      baseArtworkUrl: 'https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/',
      baseArtworkUrlOf: 'https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/640x640bb.png'
    }
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for a PodcastEpisode event
{
  uuid: 'a2b41ecd-565c-4f29-8cf9-ac737bcc8d99',
  taddyType: 'podcastepisode',
  action: 'updated',
  timestamp: 1673984261,
  data: {
    uuid: 'a2b41ecd-565c-4f29-8cf9-ac737bcc8d99',
    hash: '8130da98420b6ea1bcafd9a7e411625bf0437e93cd2e44b0a2291430e7cd940c',
    name: 'Consider the Burying Beetle. (Or Else.)',
    description: '<p>The current level of biodiversity loss is extraordinary in human history: The global rate of species extinction is at least tens to hundreds of times higher than the average over the past 10 million years.Â </p><p>At the end of 2022, countries around the world came together in Montreal for an agreement akin to the Paris climate accord to tackle the biodiversity crisis. Hereâ€™s more on the effort and how it seeks to confront the problem.</p><p>Guest: <a href="https://www.nytimes.com/by/catrin-einhorn">Catrin Einhorn</a>, who reports on biodiversity and climate for The New York Times.</p><p>Background reading:Â </p><ul><li>Last year, roughly 190 nations, aiming to halt a dangerous decline in biodiversity,<a href="https://www.nytimes.com/2022/12/19/climate/biodiversity-cop15-montreal-30x30.html"> agreed to preserve 30 percent of the planetâ€™s land and seas</a>.Â </li></ul><p>For more information on todayâ€™s episode, visit <a href="http://nytimes.com/thedaily?smid=pc-thedaily">nytimes.com/thedaily</a>. Transcripts of each episode will be made available by the next workday.Â </p>\n',
    imageUrl: null,
    datePublished: 1673001900,
    guid: '848d3475-dc4d-4c53-b97c-e69f05bad846',
    subtitle: 'The current level of biodiversity loss is extraordinary in human history: The global rate of species extinction is at least tens to hundreds of times higher than the average over the past 10 million years. \n' +
      '\n' +
      'At the end of 2022, countries around the world came together in Montreal for an agreement akin to the Paris climate accord to tackle the biodiversity crisis. Hereâ€™s more on the effort and how it seeks to confront the problem.\n' +
      '\n' +
      'Guest: Catrin Einhorn, who reports on biodiversity and climate for The New York Times.',
    audioUrl: 'https://dts.podtrac.com/redirect.mp3/chrt.fm/track/8DB4DB/pdst.fm/e/nyt.simplecastaudio.com/03d8b493-87fc-4bd1-931f-8a8e9b945d8a/episodes/b8f7e904-bf5d-4c60-b3d7-de67dfc271fc/audio/128/default.mp3?aid=rss_feed&awCollectionId=03d8b493-87fc-4bd1-931f-8a8e9b945d8a&awEpisodeId=b8f7e904-bf5d-4c60-b3d7-de67dfc271fc&feed=54nAGcIl',
    videoUrl: null,
    fileLength: 24695702,
    fileType: 'audio/mpeg',
    duration: 1543,
    episodeType: 'FULL',
    seasonNumber: null,
    episodeNumber: null,
    websiteUrl: 'https://www.nytimes.com/the-daily',
    isExplicitContent: false,
    isRemoved: null,
    podcastSeries: {
      uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
      name: 'The Daily',
      rssUrl: 'https://feeds.simplecast.com/54nAGcIl',
      itunesId: 1200361736
    }
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for a iTunesInfo event
{
  uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
  taddyType: 'itunesinfo',
  action: 'updated',
  timestamp: 1673984345,
  data: {
    uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
    hash: '2c022ad3c4482664cdecbcb05e87313e917aff2b697f1f674b69bb6514f55ed6',
    subtitle: null,
    summary: 'This is what the news should sound like. The biggest stories of our time, told by the best journalists in the world. Hosted by Michael Barbaro and Sabrina Tavernise. Twenty minutes a day, five days a week, ready by 6 a.m.',
    baseArtworkUrl: 'https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/1c/ac/04/1cac0421-4483-ff09-4f80-19710d9feda4/mza_12421371692158516891.jpeg/',
    publisherId: 121664449,
    publisherName: 'The New York Times',
    country: 'UNITED_STATES_OF_AMERICA',
    podcastSeries: {
      uuid: 'cb8d858a-3ef4-4645-8942-67e55c0927f2',
      name: 'The Daily',
      rssUrl: 'https://feeds.simplecast.com/54nAGcIl',
      itunesId: 1200361736
    }
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for ComicSeries event
{
  uuid: 'b00c3bc5-2758-483a-942c-4229eb742cb4',
  taddyType: 'comicseries',
  action: 'created',
  timestamp: 1684445348,
  data: {
    uuid: 'b00c3bc5-2758-483a-942c-4229eb742cb4',
    name: "Warrior's Strife",
    description: 'Frayja has trained all her life to become the greatest warrior in her clan. She has been taught to fear and hate elves and their magic. But a day on the battlefield dramatically alters her perspective on reality.',
    hash: '397b0176c21bff026d7b58e7bf87147626d4b9c2f7c7a6ddb5de467aa8b1c751',
    issuesHash: 'c005d4e0aadf39c1a043ff3be40833480a2adab59cbffd3ea86620da0a21cc97',
    datePublished: 1683664725,
    coverImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/97699676-ece5-4ab5-b089-5288cfa7421c/","cover_sm":"cover-sm.webp","cover_md":"cover-md.webp","cover_lg":"cover-lg.webp"}',
    bannerImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/","banner_sm":"banner-sm.webp","banner_md":"banner-md.webp","banner_lg":"banner-lg.webp"}',
    thumbnailImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/a1718041-2b8c-42e9-94fe-099ade2b3417/","thumbnail":"thumbnail.webp"}',
    tags: [
      'fantasy',  'darkfantasy',
      'lgbtqia',  'lgbt',
      'monsters', 'viking',
      'elf',      'magic',
      'action',   'drama',
      'medieval'
    ],
    genres: [ 'COMICSERIES_ROMANCE', 'COMICSERIES_DRAMA' ],
    language: 'ENGLISH',
    contentRating: 'COMICSERIES_TEENS',
    seriesType: 'WEBTOON',
    sssUrl: 'https://taddy.org/feeds/sss/comicseries/b00c3bc5-2758-483a-942c-4229eb742cb4',
    sssOwnerName: null,
    sssOwnerPublicEmail: null,
    copyright: 'Copyright notice available at http://3s-docs.org/creator-friendly-copyright-notice',
    isCompleted: false,
    isBlocked: null,
    totalIssuesCount: 16
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for ComicIssue event
{
  uuid: '826f3ba0-c231-4d93-95ad-d3e65f9d57b1',
  taddyType: 'comicissue',
  action: 'created',
  timestamp: 1684445690,
  data: {
    uuid: '826f3ba0-c231-4d93-95ad-d3e65f9d57b1',
    seriesUuid: 'b00c3bc5-2758-483a-942c-4229eb742cb4',
    name: 'Chapter 1 Book 1',
    creatorNote: null,
    hash: '08b4ce69403beb038ce831ea7163d4c9ae81139c52b82c55e0eafe7160d18a99',
    storiesHash: '94d497b23c9a506d1bec028c70e9901f83a0caafacc60445c77fc6a1f050a55f',
    datePublished: 1683664717,
    bannerImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/f8f972c4-6d77-404e-b5c3-5cf37c3172f3/","banner_sm":"banner-sm.webp","banner_md":"banner-md.webp","banner_lg":"banner-lg.webp"}',
    thumbnailImageAsString: '{"base_url":"https://ax1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/43e484b0-6111-41f2-92e2-0b6515655969/","thumbnail":"thumbnail.webp"}',
    stories: [
      {
        "uuid": "9f2120bb-5660-44a3-a3d5-9537bdd438e9",
        "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
        "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
        "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/9bb57b57-0c0c-4d1a-8eed-0d64daf347d9/\",\"story\":\"story.webp\"}"
      },
      {
        "uuid": "5a0bd406-d513-46cf-8fd1-b8474e4e1909",
        "issueUuid": "826f3ba0-c231-4d93-95ad-d3e65f9d57b1",
        "seriesUuid": "b00c3bc5-2758-483a-942c-4229eb742cb4",
        "storyImageAsString": "{\"base_url\":\"https://ay1.taddy.org/b00c3bc5-2758-483a-942c-4229eb742cb4/826f3ba0-c231-4d93-95ad-d3e65f9d57b1/a51fad16-89a9-4a0e-8d2c-88625f8bc957/\",\"story\":\"story.webp\"}"
      },
      ...,
    ],
    position: 0,
    isRemoved: null,
    isBlocked: null
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for Creator event
{
  uuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
  taddyType: 'creator',
  action: 'created',
  timestamp: 1684448992,
  data: {
    uuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    name: 'Zachary Morris',
    bio: null,
    hash: '2b80ecea81312717bcbe1872aa80c52ede308993819fdbafa2027ee123ed565c',
    contentHash: '9d982608495697f438e0e31d768fdb297d1e6984f013842b2ccc982562c343a6',
    avatarImageAsString: '{"base_url":"https://ax1.taddy.org/5a4977e8-b0da-4cc7-a516-16a0fb6973d8/c414e9a8-8d27-4280-9e43-a2e5445a9626/","avatar_sm":"avatar-sm.webp","avatar_md":"avatar-md.webp","avatar_lg":"avatar-lg.webp"}',
    tags: [ 'zachmorris', 'willdrawforfood1', 'cartoonist' ],
    country: 'UNITED_STATES_OF_AMERICA',
    linksAsString: '[{"type":"PATREON","base_url":"https://patreon.com/","value":"zachmorris"},{"type":"TWITTER","base_url":"https://twitter.com/","value":"toonzach"}]',
    sssUrl: 'https://taddy.org/feeds/sss/creator/5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    sssOwnerName: null,
    sssOwnerPublicEmail: null,
    copyright: 'Copyright notice available at http://3s-docs.org/creator-friendly-copyright-notice',
    isBlocked: null,
    totalContentCount: 1
  }
}
```

```jsx
// EXAMPLE WEBHOOK EVENT for CreatorContent event
{
  uuid: '3c90e534-0aba-4536-8bab-43e9dd0c4ac9',
  taddyType: 'creatorcontent',
  action: 'created',
  timestamp: 1684449090,
  data: {
    hash: 'f2c93ef053a62b05da1092cb39e41726da9fbbe436888b4d53dd6c9067839013',
    creatorUuid: '5a4977e8-b0da-4cc7-a516-16a0fb6973d8',
    contentUuid: '71113968-45a2-4c30-b770-655b57ae0de6',
    contentType: 'COMICSERIES',
    roles: [ 'COMICSERIES_ARTIST', 'COMICSERIES_WRITER' ],
    position: 0,
    contentPosition: 0
  }
}
```

---

# Why we Built Taddy

---
