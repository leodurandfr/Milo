# SearchMatchType
SearchMatchType               

[🧑‍💻Taddy API](https://taddy.org/developers)/[📻Podcast API](https://taddy.org/developers/podcast-api)/


SearchMatchType
===============

Choose how your search matches content—by exact phrase, all terms, or most terms.

```
enum SearchMatchType {
  EXACT_PHRASE
  MOST_TERMS
  ALL_TERMS
}
```


`EXACT_PHRASE` – Returns results that contain the exact phrase only. This is ideal for searching names, e.g., "Peter Smith", as results will only include instances where the full and exact phrase "Peter Smith" appears.

`ALL_TERMS` – Returns results that contain all the provided search terms, regardless of the order of the terms. Exact phrase matches are ranked higher.

`MOST_TERMS` (Default) – Returns results that contain any of the search terms provided. Exact phrase matches and multiple terms matched are ranked higher.