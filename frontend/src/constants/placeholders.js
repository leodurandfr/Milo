/**
 * The images Milō draws in place of a cover it does not have: a disc for
 * anything musical (album, track, playlist, artist, CD), a microphone for a
 * podcast episode, and the Liked Songs cover (LikedCover.vue).
 *
 * Imported from here and nowhere else. Reaching for the asset directly is a
 * second chance to pick a different drawing for the same silence, which is how
 * one CD ended up drawn twice — as a 20 KB JPEG with a baked white background
 * for the player, and as a transparent SVG for the cards.
 *
 * The format follows the drawing, not the source. The microphone is three
 * strokes, so it stays a vector. The disc is a bitmap: it is an angular sheen
 * — measured from the pressed-CD artwork it replaces, an annulus of r 198.9
 * around a hole of r 71.2 on a 512 grid, flat along every radius, with a
 * 180-degree period — which SVG could only approximate as 240 translucent
 * antialiased sectors, and the GPU retraced all of them on every raster of the
 * tile. Opening the full player on a track with no cover froze the frames
 * twice, ~100 ms then ~80 ms, on the Pi's GPU; a bitmap is one textured draw
 * however often it is rastered.
 *
 * Both are one neutral gray (#8a9099) carried by opacity only, so they sit on a
 * card of any color and the disc's hole shows that color. To change the disc,
 * redraw its SVG and render it again, never edit the bitmap: the SVG is
 * `git show d610d1c67b5f0c23d48f944e990f6d13811efbf5` — the blob, which
 * outlives a rewrite of the commits; music-placeholder.svg at 253837b6 —
 * opened alone in Chromium at a 1024×1024 viewport, DPR 1, under a
 * transparent default background (`Emulation.setDefaultBackgroundColorOverride`),
 * captured as PNG and saved as lossless WebP. 1024 covers the kiosk's
 * 728-device-px cover; a dense phone's full player (~1200) stretches it
 * slightly, which a gradient this soft does not show.
 *
 * The Liked Songs cover is the brand orange under a grain in soft-light, baked
 * into one bitmap rather than blended in CSS: --color-brand is the same in both
 * themes, so nothing is left to recolor, and a blend mode is a compositing
 * group the Pi's GPU re-rasters where a bitmap is one draw. Its heart is not in
 * the bitmap — each consumer sizes it to its own tile. To change it, change the
 * Figma component `asset/placeholder/liked` (Milo OS v3, page Images), never
 * the bitmap: exported as PNG at scale 1.2325 — 493 px, the grain's native
 * width — and saved as lossy WebP at quality 90.
 */
import likedCover from '@/assets/images/liked-cover.webp';
import musicPlaceholder from '@/assets/images/music-placeholder.webp';
import podcastPlaceholder from '@/assets/images/podcast-placeholder.svg';

export { likedCover, musicPlaceholder, podcastPlaceholder };
