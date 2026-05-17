/**
 * Podcast playback speeds.
 * The canonical list is owned by the backend
 * (backend/sources/podcast/source.py) and fetched at runtime via
 * GET /api/podcast/playback-speeds. The store caches the result.
 *
 * This file intentionally exports no constant list — kept as a documentation
 * anchor and to satisfy the convention "shared constants live here".
 */
export const PODCAST_SPEEDS_ENDPOINT = '/api/podcast/playback-speeds';
