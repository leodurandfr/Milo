# SearchSortOrder
SearchSortOrder               

[🧑‍💻Taddy API](https://taddy.org/developers)/[📻Podcast API](https://taddy.org/developers/podcast-api)/

SearchSortOrder

[PRICING](https://taddy.org/developers/pricing)
[SIGN UP](https://taddy.org/signup/developers)

SearchSortOrder
===============

Choose if you want search results to be prioritized by exact term matching or popularity of the content.

```
enum SearchSortOrder {
  EXACTNESS
  POPULARITY
}
```


`EXACTNESS`(Default) - Search results will prioritize exact matching based on title, description, publisher name and other relevant information.

`POPULARITY` - Search will still match the terms you provide, but the result be filtered to include only the top 5% of the most popular podcasts. Popularity of the podcast is determined by the [iTunes Top Charts](https://taddy.org/developers/podcast-api/get-top-charts). This is useful if you want popular (and usually higher quality) results over an exact match.