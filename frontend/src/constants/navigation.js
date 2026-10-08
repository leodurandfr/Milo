/**
 * How many pages of a source's navigation stay mounted once left, so going
 * back to one shows it again instead of building it anew. Four covers the
 * deepest common path (home, a list, an artist, an album); past it the page
 * least recently shown is dropped, and builds again if it is gone back to.
 * Each kept page holds its whole DOM, the Spotify home ~2800 elements.
 */
export const KEPT_PAGES = 4;
