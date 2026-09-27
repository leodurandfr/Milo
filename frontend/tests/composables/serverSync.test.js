// frontend/tests/composables/serverSync.test.js
/**
 * serverSync — what App.vue's resyncStores() rests on.
 *
 * What breaks if these stop holding: a page loaded before the backend listens
 * (the kiosk after a reboot) keeps whatever its boot fetch got — English, no
 * radio favorites, no settings — until someone reloads it; or overlapping
 * resyncs (boot, socket, tab return) hit the backend twice as often as needed,
 * or a wedged request freezes every later resync behind it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createServerSync } from '@/services/serverSync';

/** A domain whose answers are scripted: one entry per call, the last repeating. */
function domain(name, ...answers) {
  const calls = [];
  return {
    name,
    calls,
    resync: vi.fn(() => {
      const answer = answers[Math.min(calls.length, answers.length - 1)];
      calls.push(Date.now());
      return typeof answer === 'function' ? answer() : Promise.resolve(answer);
    }),
  };
}

/** A resync that stays pending until the test settles it. */
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

let hidden;
let sync;

function make(stages) {
  // random() = 0.5 puts every jittered delay exactly on its nominal value
  sync = createServerSync(stages, { isHidden: () => hidden, random: () => 0.5 });
  return sync;
}

beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
});

afterEach(() => {
  sync?.dispose();
  vi.useRealTimers();
});

describe('retry', () => {
  it('retries a failed domain alone until it succeeds', async () => {
    const settings = domain('settings', false, false, true);
    const radio = domain('radio', true);
    make([[settings, radio]]);

    expect(await sync.request('boot')).toBe(false);
    expect(settings.resync).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(settings.resync).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(settings.resync).toHaveBeenCalledTimes(3);

    // It succeeded: nothing is scheduled any more, and the domain that had
    // succeeded from the start was never asked again.
    await vi.advanceTimersByTimeAsync(60000);
    expect(settings.resync).toHaveBeenCalledTimes(3);
    expect(radio.resync).toHaveBeenCalledTimes(1);
  });

  it('counts a thrown resync as failed and keeps going', async () => {
    const broken = domain('broken', () => Promise.reject(new Error('boom')), true);
    make([[broken]]);

    expect(await sync.request('boot')).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(broken.resync).toHaveBeenCalledTimes(2);
  });

  it('caps the delay between retries', async () => {
    const down = domain('down', false);
    make([[down]]);
    await sync.request('boot');

    // 1 + 2 + 4 + 8 = 15 s for the first four, then one every 15 s
    await vi.advanceTimersByTimeAsync(15000);
    expect(down.resync).toHaveBeenCalledTimes(5);
    await vi.advanceTimersByTimeAsync(15000);
    expect(down.resync).toHaveBeenCalledTimes(6);
    await vi.advanceTimersByTimeAsync(15000);
    expect(down.resync).toHaveBeenCalledTimes(7);
  });

  it('holds retries while the page is hidden', async () => {
    const down = domain('down', false, true);
    make([[down]]);
    await sync.request('boot');

    hidden = true;
    await vi.advanceTimersByTimeAsync(60000);
    expect(down.resync).toHaveBeenCalledTimes(1);

    hidden = false;
    await vi.advanceTimersByTimeAsync(15000);
    expect(down.resync).toHaveBeenCalledTimes(2);
  });

  it('a new request supersedes the pending retry and runs everything at once', async () => {
    const down = domain('down', false, true);
    const up = domain('up', true);
    make([[down, up]]);
    await sync.request('boot');

    await vi.advanceTimersByTimeAsync(500);
    expect(await sync.request('reconnect')).toBe(true);
    expect(down.resync).toHaveBeenCalledTimes(2);
    expect(up.resync).toHaveBeenCalledTimes(2);

    // ...and the retry it replaced does not fire on top of it
    await vi.advanceTimersByTimeAsync(60000);
    expect(down.resync).toHaveBeenCalledTimes(2);
  });

  it('gives up after a bounded number of retries, until the next request', async () => {
    // A backend that is up but keeps refusing one route must not be polled for
    // the life of the page — some of these resyncs probe the whole fleet.
    const refused = domain('refused', false);
    make([[refused]]);
    await sync.request('boot');

    await vi.advanceTimersByTimeAsync(10 * 60000);
    expect(refused.resync).toHaveBeenCalledTimes(1 + 8);

    await sync.request('visible');
    await vi.advanceTimersByTimeAsync(1000);
    expect(refused.resync).toHaveBeenCalledTimes(1 + 8 + 2);
  });

  it('stops retrying once disposed', async () => {
    const down = domain('down', false);
    make([[down]]);
    await sync.request('boot');

    sync.dispose();
    await vi.advanceTimersByTimeAsync(60000);
    expect(down.resync).toHaveBeenCalledTimes(1);
  });
});

describe('overlapping requests', () => {
  it('folds requests made during a pass into exactly one more pass', async () => {
    const gate = deferred();
    const slow = domain('slow', () => gate.promise, true);
    make([[slow]]);

    const boot = sync.request('boot');
    const reconnect = sync.request('reconnect');
    sync.request('visible');
    sync.request('online');
    expect(slow.resync).toHaveBeenCalledTimes(1);

    gate.resolve(true);
    await boot;
    await reconnect;
    // The first pass may have read the server before what prompted the three
    // requests; one more pass covers all of them, and no more than one.
    expect(slow.resync).toHaveBeenCalledTimes(2);
  });

  it('runs stages in order, the next only once the previous settled', async () => {
    const gate = deferred();
    const audio = domain('audio', () => gate.promise);
    const radio = domain('radio', true);
    make([[audio], [radio]]);

    const pass = sync.request('boot');
    await vi.advanceTimersByTimeAsync(0);
    expect(radio.resync).not.toHaveBeenCalled();

    gate.resolve(true);
    await pass;
    expect(radio.resync).toHaveBeenCalledTimes(1);
  });

  it('a failed stage skips the stages after it, and the retry runs them once it heals', async () => {
    // A store resynced against a stale audio mirror would report success (the
    // music library reads its availability there) and never be run again.
    const audio = domain('audio', false, true);
    const library = domain('library', true);
    make([[audio], [library]]);

    expect(await sync.request('boot')).toBe(false);
    expect(library.resync).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1000);
    expect(audio.resync).toHaveBeenCalledTimes(2);
    expect(library.resync).toHaveBeenCalledTimes(1);
  });

  it('a domain that never answers fails its pass instead of freezing every later one', async () => {
    const wedged = domain('wedged', () => new Promise(() => {}), true);
    const radio = domain('radio', true);
    make([[wedged, radio]]);

    const boot = sync.request('boot');
    await vi.advanceTimersByTimeAsync(65000);
    expect(await boot).toBe(false);

    // The retry reaches it again, and this time it answers
    await vi.advanceTimersByTimeAsync(1000);
    expect(wedged.resync).toHaveBeenCalledTimes(2);
    expect(await sync.request('visible')).toBe(true);
  });
});
