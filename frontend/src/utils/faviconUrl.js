/**
 * Resolves a station favicon URL for display.
 *
 * - Empty input → empty string (caller is expected to render a fallback).
 * - Local backend-hosted images (`/api/radio/images/...`) → returned as-is.
 * - External URLs → routed through `/api/radio/favicon?url=...` so the backend
 *   proxy can spoof browser-like request headers and bypass WAF rules that
 *   reject bare User-Agent fetches.
 */
export function getFaviconUrl(faviconUrl) {
  if (!faviconUrl) return '';
  if (faviconUrl.startsWith('/api/radio/images/')) return faviconUrl;
  return `/api/radio/favicon?url=${encodeURIComponent(faviconUrl)}`;
}
