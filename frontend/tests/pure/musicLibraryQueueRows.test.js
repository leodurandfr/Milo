// frontend/tests/pure/musicLibraryQueueRows.test.js
/**
 * The queue view draws the live queue as two tabs — the current track then
 * what follows, and what played before it, most recent first — so a row's
 * position on screen is no longer its index in the queue. Every command the
 * view sends (`play_index`, `remove_from_queue`, `move_in_queue`) takes the
 * queue index; a row carrying its tab position instead plays, removes or
 * moves another track than the one tapped, which the backend accepts
 * whenever that other track's id is the one sent.
 */
import { describe, it, expect } from 'vitest';
import { listedQueue, moveRow, occurrenceKeys, queueMove, splitQueue } from '@/components/music-library/queueRows';

const queue = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => ({ id }));

describe('splitQueue', () => {
  it('gives each row of both tabs the index of its own track in the queue', () => {
    const { upcoming, played } = splitQueue(queue, 2);

    expect(upcoming.map((row) => row.song.id)).toEqual(['c', 'd', 'e', 'f']);
    expect(played.map((row) => row.song.id)).toEqual(['b', 'a']);
    for (const row of [...upcoming, ...played]) {
      expect(queue[row.queueIndex]).toBe(row.song);
    }
    expect(upcoming.filter((row) => row.current).map((row) => row.queueIndex)).toEqual([2]);
  });

  it('lists the whole queue as upcoming, nothing current, with no current track', () => {
    const { upcoming, played } = splitQueue(queue, -1);

    expect(upcoming.map((row) => row.queueIndex)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(upcoming.some((row) => row.current)).toBe(false);
    expect(played).toEqual([]);
  });

  it('numbers the rows of a window by the whole queue, not by the window', () => {
    // The state carries the queue around the current track only: a window
    // starting 40 entries in. A row numbered by its place in the window plays,
    // removes or moves the track 40 entries earlier.
    const whole = Array.from({ length: 50 }, (_, n) => ({ id: `t${n}` }));
    const offset = 40;
    const { upcoming, played } = splitQueue(whole.slice(offset), 2, offset);

    for (const row of [...upcoming, ...played]) {
      expect(whole[row.queueIndex]).toBe(row.song);
    }
    expect(upcoming.find((row) => row.current).queueIndex).toBe(42);
  });

  it('keys a track queued twice apart, and the same across a track change', () => {
    const twice = [{ id: 'a' }, { id: 'b' }, { id: 'a' }];
    const before = splitQueue(twice, 0).upcoming.map((row) => row.key);
    const after = splitQueue(twice, 1);

    expect(new Set(before).size).toBe(3);
    expect(after.upcoming.map((row) => row.key)).toEqual(before.slice(1));
    expect(after.played.map((row) => row.key)).toEqual(before.slice(0, 1));
  });
});

describe('moving an upcoming row', () => {
  // The backend's move: entry `fromIndex` taken out, put back so that it
  // ends at `toIndex`.
  function applyMove(list, { fromIndex, toIndex }) {
    const out = [...list];
    const [moved] = out.splice(fromIndex, 1);
    out.splice(toIndex, 0, moved);
    return out;
  }

  it.each([
    [1, 3],
    [3, 1],
    [1, 2],
  ])('sends the queue indices of a drag from %i to %i, and shows the order the backend makes', (from, to) => {
    const { upcoming } = splitQueue(queue, 2);

    const move = queueMove(upcoming[from], from, to);
    const moved = moveRow(upcoming, from, to);

    expect(moved.map((row) => row.song)).toEqual(applyMove(queue, move).slice(2));
    expect(moved.map((row) => row.queueIndex)).toEqual([2, 3, 4, 5]);
    expect(moved[0].current).toBe(true);
  });
});

/**
 * A playlist (or a queue) may hold a track twice — an .m3u, an album added
 * over one of its tracks. Rows keyed by the track id alone share a key, and
 * Vue's keyed diff of [c, a, b, a] → [a, b, a, c], which is the server's order
 * coming back after a refused reorder, leaves five rows on screen for four
 * entries.
 */
describe('occurrenceKeys', () => {
  const list = (...ids) => ids.map((id) => ({ id }));

  it('gives every row its own key, duplicates included', () => {
    const keys = occurrenceKeys(list('c', 'a', 'b', 'a'));

    expect(new Set(keys).size).toBe(4);
  });

  it('keeps a row\'s key when another track moves past it', () => {
    const before = occurrenceKeys(list('c', 'a', 'b', 'a'));
    const after = occurrenceKeys(list('a', 'b', 'a', 'c'));

    expect(after[3]).toBe(before[0]);
    expect(after[1]).toBe(before[2]);
  });
});

/**
 * The state carries 32 tracks on either side of the one playing; the queue
 * view lists the whole queue, read from GET /api/music-library/queue. Listed
 * past the moment the queue moved, a row past the window plays another track
 * than the one tapped (play_index carries no track id to refuse it by); never
 * listed, a track past the 33rd can be neither seen nor played, removed or
 * moved.
 */
describe('listedQueue', () => {
  const WINDOW = 32;
  const whole = Array.from({ length: 100 }, (_, n) => ({ id: `h${n}` }));
  /** The window the backend publishes around entry `index` of `tracks`. */
  const windowOf = (tracks, index) => {
    const offset = Math.max(0, index - WINDOW);
    return {
      queue: tracks.slice(offset, index + WINDOW + 1), queueIndex: index - offset,
      offset, length: tracks.length,
    };
  };
  const ids = (list) => list.map((song) => song.id);

  it('lists every track of a whole queue that agrees with the state, by its whole index', () => {
    const state = windowOf(whole, 0);
    const listed = listedQueue(whole, [], state);
    const { upcoming } = splitQueue(listed.queue, listed.queueIndex, listed.offset);

    expect(state.queue).toHaveLength(WINDOW + 1);
    expect(upcoming).toHaveLength(100);
    for (const row of upcoming) expect(whole[row.queueIndex]).toBe(row.song);
  });

  it('takes the current track from the state, which a track change moves alone', () => {
    const listed = listedQueue(whole, [], windowOf(whole, 70));

    expect(listed).toMatchObject({ queue: whole, queueIndex: 70, whole: true });
  });

  it.each([
    ['a track was added', [...whole, { id: 'new' }]],
    ['the queue was shuffled', [whole[0], ...whole.slice(1).reverse()]],
  ])('falls back to the window when %s since the read', (_, now) => {
    const state = windowOf(now, 0);

    expect(listedQueue(whole, [], state)).toEqual(
      { queue: state.queue, queueIndex: state.queueIndex, offset: state.offset, whole: false });
  });

  it('lists the window before the first read', () => {
    const state = windowOf(whole, 50);

    expect(listedQueue(null, [], state).queue).toBe(state.queue);
  });

  it('keeps a remove sent past the window listed before and after the state answers it', () => {
    const edits = [{ index: 80 }];
    const after = whole.filter((_, n) => n !== 80);

    expect(listedQueue(whole, edits, windowOf(whole, 0)).queue).toBe(whole);
    expect(ids(listedQueue(whole, edits, windowOf(after, 0)).queue)).toEqual(ids(after));
  });

  it('lists a move sent into the window as the backend makes it', () => {
    const after = [...whole];
    after.splice(20, 0, ...after.splice(90, 1));

    const listed = listedQueue(whole, [{ from: 90, to: 20 }], windowOf(after, 0));

    expect(ids(listed.queue)).toEqual(ids(after));
    expect(listed.queue[20].id).toBe('h90');
  });
});

/**
 * The queue view memoizes each row on its row object (`v-memo`), and every
 * state the backend publishes hands over a queue parsed anew. Rows built
 * afresh each time defeated the memo on every mounted row: with the whole
 * queue listed, a pause re-rendered up to every queued track for one icon.
 * A row nothing moved on must be the previous split's own object.
 */
describe('splitQueue across published states', () => {
  const tracks = Array.from({ length: 80 }, (_, n) => ({
    id: `t${n}`, title: `Track ${n}`, coverArt: `al-${n}`, artists: [{ id: `ar-${n}`, name: 'Someone' }],
  }));
  /** A state's window over `list` around entry `index`, parsed anew as every state is. */
  const stateOf = (list, index) => {
    const offset = Math.max(0, index - 32);
    return {
      queue: structuredClone(list.slice(offset, index + 33)), queueIndex: index - offset,
      offset, length: list.length,
    };
  };
  const split = (whole, state, previous) => {
    const listed = listedQueue(whole, [], state);
    return splitQueue(listed.queue, listed.queueIndex, listed.offset, previous);
  };
  const all = ({ upcoming, played }) => [...upcoming, ...played];
  /** The keys of the rows of `after` that are not `before`'s own objects. */
  const rebuilt = (before, after) => {
    const kept = new Set(all(before));
    return all(after).filter((row) => !kept.has(row)).map((row) => row.key);
  };

  it('keeps every row across a state that moved only the phase', () => {
    const first = split(tracks, stateOf(tracks, 10));
    const paused = split(tracks, stateOf(tracks, 10), first);

    expect(all(first)).toHaveLength(80);
    expect(rebuilt(first, paused)).toEqual([]);
  });

  it('rebuilds only the rows that stopped and started being current on a track change', () => {
    const first = split(tracks, stateOf(tracks, 10));
    const next = split(tracks, stateOf(tracks, 11), first);

    expect(rebuilt(first, next)).toEqual(['t11#1', 't10#1']);
    expect(all(next).find((row) => row.current).song.id).toBe('t11');
  });

  it('keeps every row when the whole queue read again holds the same tracks', () => {
    const first = split(tracks, stateOf(tracks, 10));
    const reread = split(structuredClone(tracks), stateOf(tracks, 10), first);

    expect(rebuilt(first, reread)).toEqual([]);
  });

  it('keeps the rows of the window, the list before the first read', () => {
    const first = split(null, stateOf(tracks, 40));
    const paused = split(null, stateOf(tracks, 40), first);

    expect(all(first)).toHaveLength(65);
    expect(rebuilt(first, paused)).toEqual([]);
  });

  it('rebuilds a row whose track or queue index moved', () => {
    // A kept row carrying its old index would play or remove its neighbor.
    const first = split(tracks, stateOf(tracks, 10));
    const renamed = tracks.map((song) => (song.id === 't3' ? { ...song, title: 'Renamed' } : song));
    const removed = renamed.filter((song) => song.id !== 't70');
    const after = split(removed, stateOf(removed, 10), first);

    expect(rebuilt(first, after)).toEqual(['t71#1', 't72#1', 't73#1', 't74#1', 't75#1', 't76#1',
      't77#1', 't78#1', 't79#1', 't3#1']);
    for (const row of all(after)) expect(removed[row.queueIndex].id).toBe(row.song.id);
    expect(all(after).find((row) => row.key === 't3#1').song.title).toBe('Renamed');
  });
});
