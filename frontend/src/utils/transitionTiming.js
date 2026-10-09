import { logger } from '@/services/logger';

// A transition token (`--transition-*`, a duration then an easing) as the timing
// of a Web Animation. Read once per token, a failure included: the tokens do not
// move at runtime (the stylesheet is applied before the app mounts), and every
// read of a computed style can lay the page out again.
const cache = new Map();

function read(name) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const parsed = value.match(/^([\d.]+)(m?s)\s+([\s\S]+)$/);
  if (parsed) return { duration: parseFloat(parsed[1]) * (parsed[2] === 's' ? 1000 : 1), easing: parsed[3] };
  logger.warn('ui', `Transition token unreadable: ${name}`, { value });
  return null;
}

/** `{ duration, easing }` of the token `name`, or null when it cannot be read. */
export function transitionTiming(name) {
  if (!cache.has(name)) cache.set(name, read(name));
  return cache.get(name);
}
