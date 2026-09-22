// frontend/tests/composables/useSourcePlaybackVisibility.test.js
/**
 * `useSourcePlaybackVisibility` decides whether a browser source's player pane
 * is on screen, and what it draws while it leaves.
 *
 * Both halves have already failed once. The pane used to hide on the READY
 * transition, which forced radio, podcast and the music library to each keep a
 * sticky copy of what they had just lost — three copies of one fact on three
 * lifetimes, none of them the published state. The backend publishes what a
 * stop would resume now, so the pane follows content rather than state.
 *
 * Removing those copies then re-introduced what they had legitimately bought:
 * on an ending, the backend publishes nothing, so binding the pane straight to
 * the live value blanked its artwork and title MID-FADE — the podcast fell to
 * "No episode" over the mic placeholder while the leave animation played. The
 * latch below is what stops that, and nothing in this suite could see it.
 *
 * The store is the real one, driven through the handler the WebSocket calls.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { ref, nextTick } from 'vue';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility';

function publish(store, fullState) {
  store.updateState({
    data: {
      full_state: {
        active_source: 'podcast',
        source_state: 'active',
        transitioning: false,
        multiroom_enabled: false,
        equalizer_effects_enabled: true,
        network_unavailable: null,
        metadata: {},
        ...fullState,
      },
    },
  });
}

const EPISODE = { uuid: 'ep1', name: 'Episode 12' };

/** Let the composable's two requestAnimationFrame hops land. */
async function settle() {
  await nextTick();
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  await nextTick();
}

describe('useSourcePlaybackVisibility', () => {
  let store;
  let content;
  let api;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useUnifiedAudioStore();
    content = ref(null);
    api = useSourcePlaybackVisibility('podcast', { content: () => content.value });
  });

  it('shows the pane once the source has something to draw', async () => {
    publish(store, { metadata: { is_playing: true } });
    content.value = EPISODE;
    await settle();

    expect(api.shouldShowPlayer.value).toBe(true);
    expect(api.displayed.value).toStrictEqual(EPISODE);
  });

  it('keeps the pane through a stop that leaves something to resume', async () => {
    publish(store, { metadata: { is_playing: true } });
    content.value = EPISODE;
    await settle();

    // An auto-stop: no session left, but the episode a play press reopens is
    // still published, so the store still names it.
    publish(store, { source_state: 'ready', metadata: { is_playing: false } });
    await settle();

    expect(api.shouldShowPlayer.value).toBe(true);
    expect(api.isPlaying.value).toBe(false);
  });

  it('holds what it was drawing until the pane has finished leaving', async () => {
    publish(store, { metadata: { is_playing: true } });
    content.value = EPISODE;
    await settle();

    // An ending publishes no resume identity at all.
    content.value = null;
    await settle();

    expect(api.shouldShowPlayer.value).toBe(false);
    // The leave is still running: blanking here is the bug this exists for.
    expect(api.displayed.value).toStrictEqual(EPISODE);

    api.onAfterHide();
    expect(api.displayed.value).toBeNull();
  });

  it('does not blank a pane that came back inside its own leave', async () => {
    publish(store, { metadata: { is_playing: true } });
    content.value = EPISODE;
    await settle();
    content.value = null;
    await settle();

    // Play pressed again before the leave finished — the pane is live again,
    // and the late `after-hide` from the cancelled leave must not empty it.
    const next = { uuid: 'ep2', name: 'Episode 13' };
    content.value = next;
    await settle();
    api.onAfterHide();

    expect(api.displayed.value).toStrictEqual(next);
  });

  it('hides the pane when another source takes the air', async () => {
    publish(store, { metadata: { is_playing: true } });
    content.value = EPISODE;
    await settle();

    publish(store, { active_source: 'spotify' });
    await settle();

    expect(api.shouldShowPlayer.value).toBe(false);
  });
});
