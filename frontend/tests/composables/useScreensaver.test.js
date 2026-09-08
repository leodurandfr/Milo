// frontend/tests/composables/useScreensaver.test.js
/**
 * The screensaver is the physical display's idle state, so what dismisses it is
 * a finger on that display — nothing else. This file covers the visibility half
 * of useScreensaver, and the case it exists for is the one that was broken:
 * `is_playing` dips to false when a track ends on its own (Spotify's
 * `not_playing`, Tidal's BUFFERING/IDLE, DLNA's STOPPED, AirPlay's `pfls`), and
 * the screensaver closed itself there — no touch, no user, just the gap. A skip
 * commanded from the sender never produced the dip, so the bug read as random.
 *
 * The two questions are therefore separate and asserted separately: playback
 * gates *arming* the countdown, and only the source going off the air dismisses
 * an overlay already up.
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

const DELAY_SECONDS = 15;

const PLAYING = { title: 'Future Green', artist: 'Masahiro Sugaya', is_playing: true };
/** What the wire carries between two tracks, and on a real pause. */
const NOT_PLAYING = { ...PLAYING, is_playing: false };

function publish(store, fullState) {
  store.updateState({
    data: {
      full_state: {
        active_source: 'spotify',
        source_state: 'active',
        transitioning: false,
        multiroom_enabled: false,
        equalizer_effects_enabled: false,
        metadata: PLAYING,
        ...fullState,
      },
    },
  });
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

    publish(store, { metadata: NOT_PLAYING });
    await nextTick();
    expect(mounted.api.isScreensaverVisible.value).toBe(true);

    publish(store, { metadata: { ...PLAYING, title: 'Sonic the Hedgehog' } });
    await nextTick();

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });

  it('stays up on a real pause too, since nobody touched the screen', async () => {
    // Same wire state as the gap above — the frontend cannot tell them apart,
    // which is precisely why neither may dismiss anything.
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, { metadata: NOT_PLAYING });
    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
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
    // The one automatic dismissal left: the overlay would otherwise show a
    // track nothing is playing.
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;
    await idle();

    publish(store, { source_state: 'ready', metadata: NOT_PLAYING });
    await nextTick();

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('never arms while the track is only paused', async () => {
    // The other half of the split: a paused unit has nothing to fade into, so
    // the countdown does not start — it just cannot close what is already up.
    publish(store, { metadata: NOT_PLAYING });
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;

    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(false);
  });

  it('arms for a receiver that has no play state of its own', async () => {
    // Bluetooth and Mac publish no is_playing; a connected sender is the whole
    // condition, and gating them on playback would leave them screensaverless.
    publish(store, { active_source: 'mac', metadata: { client_names: ['Studio'] } });
    const mounted = mountScreensaver();
    wrapper = mounted.wrapper;

    await idle();

    expect(mounted.api.isScreensaverVisible.value).toBe(true);
  });
});
