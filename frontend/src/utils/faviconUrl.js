/**
 * Resolves a station favicon URL for display.
 *
 * - Empty input → empty string (caller is expected to render a fallback).
 * - Anything served by this origin (a root-relative path) → returned as-is.
 * - External URLs → routed through `/api/radio/favicon?url=...` so the backend
 *   proxy can spoof browser-like request headers and bypass WAF rules that
 *   reject bare User-Agent fetches.
 *
 * The passthrough is any leading `/`, not the `/api/radio/images/` prefix alone:
 * the proxy takes a URL to *fetch*, and a root-relative path is already served
 * by this unit — handing it over produces a request the backend cannot resolve,
 * whatever the path. Nothing changes for the app (the only same-origin favicon
 * the backend writes is a custom station's `/api/radio/images/…` upload); what
 * it buys is that any locally served image resolves through the same path a
 * custom upload does, which is how the gallery shows that branch.
 */
export function getFaviconUrl(faviconUrl) {
  if (!faviconUrl) return '';
  if (faviconUrl.startsWith('/')) return faviconUrl;
  return `/api/radio/favicon?url=${encodeURIComponent(faviconUrl)}`;
}
