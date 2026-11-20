/**
 * The live queue as the queue view's two tabs. The state carries a window of
 * the queue around the current track, never the whole of it; every row carries
 * its index in the WHOLE queue (the window's offset plus its place in it), the
 * one index `play_index`, `remove_from_queue` and `move_in_queue` take,
 * whatever its position on screen or in the window.
 *
 * A row is keyed by its track and that track's occurrence in the window (a
 * track can be queued twice), never by position: one track on, every
 * position moves, and the rows would all be built again. An occurrence is
 * counted in the window, so a duplicate's key moves when its earlier copy
 * slides out of it — rare, and only that row is built again.
 *
 * @param {Array<{id: string}>} queue - The window of the queue, in play order
 * @param {number} queueIndex - The current track's index in it, -1 with none
 * @param {number} [offset] - Where the window starts in the whole queue
 * @returns {{upcoming: Array, played: Array}} The current track then what
 *   follows, in play order; and what played before it, most recent first
 */
export function splitQueue(queue, queueIndex, offset = 0) {
  const seen = new Map();
  const rows = queue.map((song, index) => {
    const nth = (seen.get(song.id) ?? 0) + 1;
    seen.set(song.id, nth);
    return { key: `${song.id}#${nth}`, song, queueIndex: offset + index, current: index === queueIndex };
  });
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
