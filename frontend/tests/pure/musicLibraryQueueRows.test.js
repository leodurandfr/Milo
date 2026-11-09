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
import { moveRow, queueMove, splitQueue } from '@/components/music-library/queueRows';

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
