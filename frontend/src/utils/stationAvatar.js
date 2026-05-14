// Generates a deterministic SVG avatar for radio stations without a favicon.
// Uses Space Mono Bold with a pastel background and vivid text color derived from the station name.
//
// Only the inline form is exported: SVGs loaded via <img src=data:...> render in
// an isolated context and do NOT inherit document @font-face, so they would fall
// back to the system monospace and break visual parity with the rest of the app.
// Consumers must render the returned markup via v-html (e.g. LazyImage,
// AudioPlayer, AudioScreensaver).

const VIEW = 1024;
const FONT_FAMILY = "'Space Mono Bold', 'Space Mono Regular', monospace";

// 24 evenly-spaced HSL hues — pastel background, vivid text (~7:1 contrast).
const PALETTE = Array.from({ length: 24 }, (_, i) => {
  const hue = i * 15;
  return {
    bg: `hsl(${hue} 60% 88%)`,
    text: `hsl(${hue} 55% 32%)`,
  };
});

const svgCache = new Map();

// Trigger font load early so measureText below uses real Space Mono metrics,
// not a fallback. On resolution we clear the cache so entries built before the
// font was ready get rebuilt with correct measurements on the next render.
let fontReady = false;
if (typeof document !== 'undefined' && document.fonts) {
  document.fonts.load(`700 100px ${FONT_FAMILY}`)
    .then(() => { fontReady = true; svgCache.clear(); })
    .catch(() => { fontReady = true; });
}

// Single reusable canvas context for text measurement (no rasterization).
let measureCtx = null;
function getMeasureCtx() {
  if (!measureCtx) {
    measureCtx = document.createElement('canvas').getContext('2d');
  }
  return measureCtx;
}

function splitLines(name) {
  const words = name.trim().split(/\s+/);
  return words.length <= 3 ? words : words.slice(0, 3);
}

// FNV-1a — good distribution for short strings.
function hashName(name) {
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function computeFontSize(ctx, lines) {
  const maxWidth = VIEW * 0.82;
  const startSize = lines.length === 1 ? 310 : lines.length === 2 ? 245 : 195;
  const minFontSize = 80;
  let fontSize = startSize;

  while (fontSize > minFontSize) {
    ctx.font = `700 ${fontSize}px ${FONT_FAMILY}`;
    const maxLineWidth = Math.max(...lines.map(l => ctx.measureText(l).width));
    if (maxLineWidth <= maxWidth) break;
    fontSize -= 4;
  }

  return fontSize;
}

const XML_ESCAPES = { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' };
function escapeXml(s) {
  return s.replace(/[<>&"']/g, c => XML_ESCAPES[c]);
}

function buildSvg(name) {
  const color = PALETTE[hashName(name) % PALETTE.length];
  const lines = splitLines(name);
  const ctx = getMeasureCtx();

  const fontSize = computeFontSize(ctx, lines);
  ctx.font = `700 ${fontSize}px ${FONT_FAMILY}`;
  const metrics = ctx.measureText(lines[0]);
  const ascent = metrics.actualBoundingBoxAscent;
  const descent = metrics.actualBoundingBoxDescent;
  const glyphHeight = ascent + descent;

  const lineGap = fontSize * 0.22;
  const totalHeight = lines.length * glyphHeight + (lines.length - 1) * lineGap;
  const startY = (VIEW - totalHeight) / 2 + ascent;

  const tspans = lines.map((line, i) => {
    const y = (startY + i * (glyphHeight + lineGap)).toFixed(2);
    return `<tspan x="${VIEW / 2}" y="${y}">${escapeXml(line)}</tspan>`;
  }).join('');

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${VIEW} ${VIEW}" width="100%" height="100%" preserveAspectRatio="xMidYMid slice">`
    + `<rect width="${VIEW}" height="${VIEW}" fill="${color.bg}"/>`
    + `<text text-anchor="middle" font-family="${FONT_FAMILY}" font-weight="700" font-size="${fontSize}" fill="${color.text}">`
    + tspans
    + `</text></svg>`;
}

function getSvg(name) {
  if (svgCache.has(name)) return svgCache.get(name);
  const svg = buildSvg(name);
  if (fontReady) svgCache.set(name, svg);
  return svg;
}

/**
 * Returns the raw SVG markup for inline rendering (v-html).
 * Renders in the same frame as the host DOM, with no image-decode delay,
 * and inherits document @font-face for accurate typography.
 */
export function generateStationAvatarSvg(name) {
  if (!name) return '';
  return getSvg(name);
}
