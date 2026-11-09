/**
 * The live queue as the queue view's two tabs. Every row carries its index in
 * the queue, the one index `play_index`, `remove_from_queue` and
 * `move_in_queue` take, whatever its position on screen.
 *
 * A row is keyed by its track and that track's occurrence in the queue (a
 * track can be queued twice), never by position: one track on, every
 * position moves, and the rows would all be built again.
 *
 * @param {Array<{id: string}>} queue - The queue, in play order
 * @param {number} queueIndex - The current track's index, -1 with none
 * @returns {{upcoming: Array, played: Array}} The current track then what
 *   follows, in play order; and what played before it, most recent first
 */
export function splitQueue(queue, queueIndex) {
  const seen = new Map();
  const rows = queue.map((song, index) => {
    const nth = (seen.get(song.id) ?? 0) + 1;
    seen.set(song.id, nth);
    return { key: `${song.id}#${nth}`, song, queueIndex: index, current: index === queueIndex };
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
