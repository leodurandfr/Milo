// Generates a deterministic colored avatar (canvas data URL) for radio stations without a favicon.
// Uses Space Mono Bold with a pastel background and vivid text color derived from the station name.

const PALETTE = [
  { bg: '#E8D5F5', text: '#6B3FA0' },  // violet
  { bg: '#D5EAF5', text: '#2E6B9E' },  // blue
  { bg: '#F5E0D5', text: '#B85C3A' },  // terracotta
  { bg: '#D5F5E0', text: '#2E8B57' },  // green
  { bg: '#F5F0D5', text: '#8B7D2E' },  // golden
  { bg: '#F5D5E8', text: '#9E2E6B' },  // pink
  { bg: '#D5F5F0', text: '#2E8B80' },  // teal
  { bg: '#F5D5D5', text: '#9E3A3A' },  // red
  { bg: '#E0D5F5', text: '#5A3FA0' },  // indigo
  { bg: '#F5ECD5', text: '#9E7B2E' },  // amber
  { bg: '#D5E0F5', text: '#3A5A9E' },  // steel blue
  { bg: '#E8F5D5', text: '#5A8B2E' },  // lime
  { bg: '#F0D5F5', text: '#8B2E8B' },  // magenta
  { bg: '#D5F0F5', text: '#2E7B8B' },  // cyan
  { bg: '#F5DDD5', text: '#A0522D' },  // sienna
  { bg: '#DDF5D5', text: '#3D8B2E' },  // forest
];

const SIZE = 1024;
const FONT_FAMILY = "'Space Mono Bold', 'Space Mono Regular', monospace";
const cache = new Map();

/**
 * Split station name into 1–3 lines (1 word per line, max 3 words).
 */
function splitLines(name) {
  const words = name.trim().split(/\s+/);
  if (words.length <= 3) return words;
  return words.slice(0, 3);
}

/**
 * Compute font size that fits within the canvas width with padding.
 */
function computeFontSize(ctx, lines) {
  const maxWidth = SIZE * 0.82;
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

/**
 * FNV-1a hash — much better distribution than djb2 for short strings.
 */
function hashName(name) {
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0);
}

/**
 * Generate a station avatar as a data URL.
 * Returns a cached result if already generated for this name.
 */
export function generateStationAvatar(name) {
  if (!name) return '';
  if (cache.has(name)) return cache.get(name);

  const color = PALETTE[hashName(name) % PALETTE.length];
  const lines = splitLines(name);
  const canvas = document.createElement('canvas');
  canvas.width = SIZE;
  canvas.height = SIZE;
  const ctx = canvas.getContext('2d');

  // Background
  ctx.fillStyle = color.bg;
  ctx.fillRect(0, 0, SIZE, SIZE);

  // Text setup
  const fontSize = computeFontSize(ctx, lines);
  ctx.fillStyle = color.text;
  ctx.font = `700 ${fontSize}px ${FONT_FAMILY}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';

  // Measure actual glyph height for precise optical centering
  const metrics = ctx.measureText(lines[0]);
  const ascent = metrics.actualBoundingBoxAscent;
  const descent = metrics.actualBoundingBoxDescent;
  const glyphHeight = ascent + descent;

  // Compute total block height and center it
  const lineGap = fontSize * 0.22;
  const totalHeight = lines.length * glyphHeight + (lines.length - 1) * lineGap;
  const startY = (SIZE - totalHeight) / 2 + ascent;

  for (let i = 0; i < lines.length; i++) {
    ctx.fillText(lines[i], SIZE / 2, startY + i * (glyphHeight + lineGap));
  }

  const url = canvas.toDataURL('image/png');
  cache.set(name, url);
  return url;
}
