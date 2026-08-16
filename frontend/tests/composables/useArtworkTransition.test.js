// frontend/tests/composables/useArtworkTransition.test.js
/**
 * useArtworkTransition holds the cover currently on screen until the next one
 * has decoded, and decides what counts as a cover at all.
 *
 * Four rules carry the weight:
 *
 *  - an image that decodes at a real size is promoted,
 *  - one that decodes at a pixel or two is NOT a cover but a tracking pixel or
 *    a broken favicon, and is rejected so the caller's fallback shows,
 *  - a track change whose cover is not known yet (Bluetooth resolves its own,
 *    about a second late) holds the outgoing cover rather than flashing the
 *    fallback between two covers,
 *  - and the wait is bounded, because nothing ever reports "there will be no
 *    cover for this track" — an image that will not decode fires no event.
 *
 * The size rule sits here rather than in the two views because they are
 * superimposed during the screensaver's leave crossfade: when it lived in each
 * consumer, the player promoted a 1×1 image the screensaver rejected, and the
 * same track showed a cover in one view and a generated avatar in the other.
 * artworkParity.test.js pins the wiring; this pins the behaviour.
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useArtworkTransition, ARTWORK_WAIT_MS } from '@/composables/useArtworkTransition';

/** Mount a host exposing the composable for `target`/`trackKey`. */
function mountTransition(target, trackKey) {
  let api;
  const Host = defineComponent({
    setup() {
      api = useArtworkTransition(target, trackKey);
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { ...api, wrapper };
}

/** What the off-screen preloader hands its @load handler. */
function decodedAt(width, height) {
  return { target: { naturalWidth: width, naturalHeight: height } };
}

describe('useArtworkTransition', () => {
  // Faked throughout: every path arms the bounded wait, and a real 4 s timer
  // would outlive the test that created it.
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('promotes a cover that decodes at a real size', () => {
    const target = ref('/api/music-library/cover/abc');
    const { shownArtwork, preloadArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, ref('track|artist'));

    // The immediate watch sends it to the preloader, veil up, nothing shown yet.
    expect(preloadArtwork.value).toBe('/api/music-library/cover/abc');
    expect(artworkPending.value).toBe(true);
    expect(shownArtwork.value).toBe('');

    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/music-library/cover/abc');
    expect(artworkPending.value).toBe(false);
    expect(preloadArtwork.value).toBe('');
  });

  it('rejects an image too small to be a cover', () => {
    // The bug this replaces: the player accepted this, the screensaver did not.
    const target = ref('http://sender.local/art.png');
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, ref('track|artist'));

    settleFromLoad(decodedAt(1, 1));

    expect(shownArtwork.value).toBe('');
    expect(artworkPending.value).toBe(false);
  });

  it('rejects a cover that fails to load', () => {
    const target = ref('/api/airplay/artwork?v=1');
    const { shownArtwork, artworkPending, settleFromError } =
      mountTransition(target, ref('track|artist'));

    settleFromError();

    expect(shownArtwork.value).toBe('');
    expect(artworkPending.value).toBe(false);
  });

  it('holds the outgoing cover while the next one decodes', async () => {
    const target = ref('/api/cover/one');
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, ref('first|artist'));
    settleFromLoad(decodedAt(600, 600));

    target.value = '/api/cover/two';
    await nextTick();

    // Still the old cover on screen, under the veil — not a blank square.
    expect(shownArtwork.value).toBe('/api/cover/one');
    expect(artworkPending.value).toBe(true);

    settleFromLoad(decodedAt(600, 600));
    expect(shownArtwork.value).toBe('/api/cover/two');
  });

  it('holds it across a track change whose cover is not known yet', async () => {
    // Bluetooth: the title arrives over AVRCP, the cover is looked up from the
    // track text afterwards and lands about a second later.
    const target = ref('/api/cover/one');
    const trackKey = ref('first|artist');
    const { shownArtwork, artworkPending, settleFromLoad } = mountTransition(target, trackKey);
    settleFromLoad(decodedAt(600, 600));

    target.value = '';
    trackKey.value = 'second|artist';
    await nextTick();

    expect(shownArtwork.value).toBe('/api/cover/one');
    expect(artworkPending.value).toBe(true);

    target.value = '/api/cover/two';
    await nextTick();
    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/cover/two');
  });

  it('disarms the wait when the cover resolved is the one already shown', async () => {
    // Bluetooth again, but track 2 of the SAME album: the lookup answers with
    // the cover on screen. Nothing is going to load, so if the wait armed by the
    // track change is not disarmed here it fires at T+4 s and replaces a correct
    // cover with the fallback, mid-track.
    const target = ref('/api/cover/album');
    const trackKey = ref('first|artist');
    const { shownArtwork, artworkPending, settleFromLoad } = mountTransition(target, trackKey);
    settleFromLoad(decodedAt(600, 600));

    target.value = '';
    trackKey.value = 'second|artist';
    await nextTick();
    expect(artworkPending.value).toBe(true);

    target.value = '/api/cover/album';
    await nextTick();
    vi.advanceTimersByTime(ARTWORK_WAIT_MS);

    expect(shownArtwork.value).toBe('/api/cover/album');
    expect(artworkPending.value).toBe(false);
  });

  it('lifts the veil when no cover ever arrives', async () => {
    const target = ref('/api/cover/one');
    const trackKey = ref('first|artist');
    const { shownArtwork, artworkPending } = mountTransition(target, trackKey);

    target.value = '';
    trackKey.value = 'second|artist';
    await nextTick();
    expect(artworkPending.value).toBe(true);

    // No load event is coming — the timeout is the only thing left.
    vi.advanceTimersByTime(ARTWORK_WAIT_MS);

    expect(artworkPending.value).toBe(false);
    expect(shownArtwork.value).toBe('');
  });

  it('paints a cover the browser never reports on, rather than waiting forever', async () => {
    const target = ref('');
    const { shownArtwork, artworkPending } = mountTransition(target, ref('track|artist'));

    target.value = '/api/cover/slow';
    await nextTick();
    expect(artworkPending.value).toBe(true);

    vi.advanceTimersByTime(ARTWORK_WAIT_MS);

    // A URL we have but no `load` for: show it anyway — the opposite fallback
    // from the no-URL case above, which has nothing to show.
    expect(shownArtwork.value).toBe('/api/cover/slow');
    expect(artworkPending.value).toBe(false);
  });
});
