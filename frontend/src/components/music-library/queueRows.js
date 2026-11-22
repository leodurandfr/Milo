/**
 * The queue the queue view lists. The state carries a window of the queue
 * around the current track; the whole of it is read from
 * `GET /api/music-library/queue` and listed only while it agrees with the
 * state — as long, and holding the window's tracks at the window's place — so
 * no row is played, removed or moved by an index the queue has moved past.
 * The current index is the state's: a track change moves it alone. The edits
 * this screen sent since that read are applied first, so the list does not
 * fall back to the window for the round trip that follows one.
 *
 * @param {Array<{id: string}>|null} whole - The last whole queue read, null before any
 * @param {Array<{index: number}|{from: number, to: number}>} edits - The removes
 *   and moves sent since that read, oldest first, by whole-queue index
 * @param {{queue: Array, queueIndex: number, offset: number, length: number}} window - The state's
 * @returns {{queue: Array, queueIndex: number, offset: number, whole: boolean}} What
 *   splitQueue takes, and whether it is the whole queue (false: the window)
 */
export function listedQueue(whole, edits, window) {
  if (whole) {
    for (const candidate of [withEdits(whole, edits), whole]) {
      if (agrees(candidate, window)) {
        const queueIndex = window.queueIndex < 0 ? -1 : window.offset + window.queueIndex;
        return { queue: candidate, queueIndex, offset: 0, whole: true };
      }
    }
  }
  return { queue: window.queue, queueIndex: window.queueIndex, offset: window.offset, whole: false };
}

function agrees(whole, { queue, offset, length }) {
  return whole.length === length && queue.every((song, i) => whole[offset + i]?.id === song.id);
}

/** The queue once the backend has applied `edits`, as remove_from_queue and move_in_queue do. */
function withEdits(whole, edits) {
  if (!edits.length) return whole;
  const out = [...whole];
  for (const edit of edits) {
    if ('index' in edit) out.splice(edit.index, 1);
    else out.splice(edit.to, 0, ...out.splice(edit.from, 1));
  }
  return out;
}

/**
 * The live queue as the queue view's two tabs, from the queue `listedQueue`
 * picks — the whole queue, or the state's window of it; every row carries
 * its index in the WHOLE queue (the window's offset plus its place in it), the
 * one index `play_index`, `remove_from_queue` and `move_in_queue` take,
 * whatever its position on screen or in the window.
 *
 * A row is keyed by its track and that track's occurrence in the list (a
 * track can be queued twice), never by position: one track on, every
 * position moves, and the rows would all be built again. An occurrence is
 * counted in the list, so a duplicate's key moves when its earlier copy
 * slides out of a window — rare, and only that row is built again.
 *
 * @param {Array<{id: string}>} queue - The queue listed, in play order
 * @param {number} queueIndex - The current track's index in it, -1 with none
 * @param {number} [offset] - Where it starts in the whole queue (0 for the whole)
 * @returns {{upcoming: Array, played: Array}} The current track then what
 *   follows, in play order; and what played before it, most recent first
 */
export function splitQueue(queue, queueIndex, offset = 0) {
  const keys = occurrenceKeys(queue);
  const rows = queue.map((song, index) => (
    { key: keys[index], song, queueIndex: offset + index, current: index === queueIndex }
  ));
  const start = Math.max(queueIndex, 0);
  return { upcoming: rows.slice(start), played: rows.slice(0, start).reverse() };
}

/**
 * The upcoming tab with one row moved from position `from` to `to`, each row
 * renumbered for the place it now holds in the queue.
 */
export function moveRow(rows, from, to) {
  const order = [...rows];
  const [moved] = order.splice(from, 1);
  order.splice(to, 0, moved);
  const base = rows[0].queueIndex;
  return order.map((row, i) => ({ ...row, queueIndex: base + i }));
}

/**
 * The `move_in_queue` indices for a row dragged from position `from` to `to`
 * of the upcoming tab, the row as it was at the press.
 */
export function queueMove(row, from, to) {
  return { fromIndex: row.queueIndex, toIndex: row.queueIndex - from + to };
}

/**
 * One key per row of a list that may hold a track more than once (a queue, a
 * playlist): the track and its occurrence in the list, `id#n`. Unique where
 * the track id alone is not — two rows sharing a key are diffed as one, and a
 * reorder of the list left a row too many on screen — and kept by a row when
 * the rows around it move, which a key by position is not.
 *
 * @param {Array<{id: string}>} songs - The rows, in order
 * @returns {string[]} Their keys, in the same order
 */
export function occurrenceKeys(songs) {
  const seen = new Map();
  return songs.map((song) => {
    const nth = (seen.get(song.id) ?? 0) + 1;
    seen.set(song.id, nth);
    return `${song.id}#${nth}`;
  });
}
