// frontend/tests/composables/useScreensaver.test.js
/**
 * The screensaver is the physical display's idle state, and two things end it:
 * a finger on that display, and playback stopping. This file covers the
 * visibility half of useScreensaver, where those two live alongside the case
 * that must NOT end it: the sliver between two tracks, and a sender that has
 * no play state at all. A screensaver keyed on "not playing" closed itself in
 * the gap — no touch, no user — and took a connected sender's away for good.
 *
 * The three questions are asked separately and asserted separately: playback
 * gates *arming* the countdown; the session ending dismisses an overlay at
 * once; and a pause dismisses it only once it has outlasted any handover —
 * which is what the timings below are about.
 *
 * The stores are the real ones, driven through the handler the WebSocket calls.
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
// The screensaver belongs to the Pi's own screen; off it the composable never
// arms at all, which would make every case below vacuously green.
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => true }));

import { useScreensaver } from '@/composables/useScreensaver';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { makeSession, publishState } from '../helpers/audioState';

const DELAY_SECONDS = 15;
/** PAUSE_DISMISS_MS in the composable — restated so a bump shows up here. */
const PAUSE_DISMISS_MS = 3000;

const TRACK = { title: 'Future Green', artist: 'Masahiro Sugaya' };

/** A Spotify session in `phase`, as the backend publishes it. */
function spotify(phase, track = TRACK) {
  return {
    source: 'spotify',
    service: 'running',
    session: makeSession({ phase, ...track }),
    controls: phase === 'paused' ? ['resume', 'seek', 'next', 'prev'] : ['pause', 'next', 'prev'],
  };
}

/** A Bluetooth sender: a phone with an AVRCP player, or one Milō cannot read. */
function bluetooth(phase, track = TRACK) {
  const transport = phase === 'paused' ? ['resume', 'next', 'prev'] : ['pause', 'next', 'prev'];
  return {
    source: 'bluetooth',
    service: 'running',
    session: makeSession({ phase, senders: ['iPhone'], ...track }),
    controls: phase === 'connected' ? ['disconnect'] : [...transport, 'disconnect'],
  };
}

/** Two Macs streaming: always `connected`, never a play state. */
const MAC = {
  source: 'mac',
  service: 'running',
  session: makeSession({ phase: 'connected', senders: ['Studio'] }),
};

function publish(store, overrides = spotify('playing')) {
  publishState(store, overrides);
}

function mountScreensaver() {
  let api;
  const Host = defineComponent({
    setup() {
      api = useScreensaver();
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { api, wrapper };
}

/** Let the countdown expire, then settle the watchers it woke. */
async function idle() {
  vi.advanceTimersByTime(DELAY_SECONDS * 1000 + 100);
  await nextTick();
}

/**
 * Advance by a measured stretch rather than a whole idle period.
 *
 * The leading tick is load-bearing: a watcher arms its timer on the flush that
 * follows the store write, so advancing first would start the clock before the
 * timer exists and every window below would read as longer than it is.
 */
async function advance(ms) {
  await nextTick();
  vi.advanceTimersByTime(ms);
  await nextTick();
}

describe('useScreensaver visibility', () => {
  let store;
  let wrapper;

  beforeEach(() => {
    vi.useFakeTimers();
    store = useUnifiedAudioStore();
    useSettingsStore().screenScreensaver = {
      screensaver_enabled: true,
      screensaver_delay_seconds: DELAY_SECONDS,
    };
    publish(store);
  });

  afterEach(() => {
    wrapper?.unmount();
    vi.useRealTimers();
  });

  it('appears once the unit has been left alone while playing', async () => {
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('survives the gap between two tracks', async () => {
    // The regression this file was written for: a track ending is not a user.
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, spotify('paused'));
    await advance(PAUSE_DISMISS_MS - 500);
    expect(mounted.api.isScreensaverVisible.value).toBe(true);

    publish(store, spotify('playing', { title: 'Sonic the Hedgehog', artist: 'Masahiro Sugaya' }));
    // Well past the window the handover just spent: resuming must have taken
    // the pending dismissal down with it, not merely postponed it.
    await advance(PAUSE_DISMISS_MS * 2);

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('holds while the next track is still loading, however long that takes', async () => {
    // Same stop, with the source saying what it is doing: a handover slow
    // enough to outlast the window is still a handover, not a pause.
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, spotify('loading'));
    await advance(PAUSE_DISMISS_MS * 3);

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('steps aside once the stop has outlasted any handover', async () => {
    // What the owner asked for: pausing from a remote or a phone is the one way
    // to reach this screen without touching it, and it must reveal the UI.
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, spotify('paused'));
    await advance(PAUSE_DISMISS_MS - 500);
    expect(mounted.api.isScreensaverVisible.value).toBe(true);

    await advance(600);

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('leaves on a touch, and comes back after another idle stretch', async () => {
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    // What AudioScreensaver's own pointerdown handler emits.
    mounted.api.closeScreensaver();
    expect(mounted.api.isScreensaverVisible.value).toBe(false);

    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('goes away when the source it was drawing leaves the air', async () => {
    // The dismissal that needs no waiting: the overlay would otherwise show a
    // track nothing is playing at all.
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, { source: 'spotify', service: 'running' });
    await nextTick();

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('never arms while the track is only paused', async () => {
    // The arming half of the split: a paused unit has nothing to fade into, so
    // the countdown never starts and no overlay is owed in the first place.
    publish(store, spotify('paused'));
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;

    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('arms for a receiver that has no play state of its own', async () => {
    // A `connected` session has no play state; the link is the whole
    // condition, and gating it on playback would leave it screensaverless.
    publish(store, MAC);
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;

    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('never dismisses that receiver for want of a play state', async () => {
    // The other end of the same gate: no play state is not a stop, so a
    // connected sender keeps its screensaver for as long as it is connected.
    publish(store, MAC);
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    await advance(PAUSE_DISMISS_MS * 3);

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('dismisses a Bluetooth sender that publishes an AVRCP transport', async () => {
    // A phone: BlueALSA connected it, AVRCP says what is playing, and the view
    // behind the overlay is the player carrying the pause button just pressed.
    publish(store, bluetooth('playing'));
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, bluetooth('paused'));
    await advance(PAUSE_DISMISS_MS + 100);

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('never arms for an AVRCP sender that is sitting paused', async () => {
    // The symmetry the two halves owe each other: a sender whose pause takes the
    // overlay away must not be handed one in the first place, or a paused phone
    // would draw a screensaver every idle stretch just to lose it 3 s later.
    publish(store, bluetooth('paused'));
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;

    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('dismisses an AVRCP sender that names no track either', async () => {
    // The transport is what matters, not what is drawable. Measured on the unit
    // 2026-09-18: a Mac mini publishes a working player and no track text at all,
    // so the overlay is the status card — and pausing it still gives the screen
    // back. A video or a browser tab lands here the same way.
    const NO_TRACK = { title: null, artist: null };
    publish(store, bluetooth('playing', NO_TRACK));
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, bluetooth('paused', NO_TRACK));
    await advance(PAUSE_DISMISS_MS + 100);

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('keeps one Milō cannot read the play state of', async () => {
    // No AVRCP player — a sender that registers none, or the window before one
    // appears: the session is `connected` throughout. Reading that as a pause
    // would take this screensaver away three seconds in and never give it back.
    publish(store, bluetooth('connected', { title: null, artist: null }));
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    await advance(PAUSE_DISMISS_MS * 3);

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });
});
