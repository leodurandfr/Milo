// frontend/tests/composables/useArtworkTransition.test.js
/**
 * useArtworkTransition holds the cover currently on screen until the next one
 * has decoded, and decides what counts as a cover at all.
 *
 * Five rules carry the weight:
 *
 *  - an image that decodes at a real size is promoted,
 *  - one that decodes at a pixel or two is NOT a cover but a tracking pixel or
 *    a broken favicon, and is rejected so the caller's fallback shows,
 *  - a track change whose cover the backend announced (`artwork_pending`: the
 *    CD's jacket, Bluetooth's and radio's iTunes lookups) holds the outgoing
 *    cover, veiled, until the backend answers — with no bound of its own,
 *  - a track change with no cover and no announcement has none: the fallback
 *    is drawn at once, as the answer,
 *  - and the decode wait is bounded, because an image that will not decode
 *    fires no event.
 *
 * The size rule sits here rather than in a view: when it lived in each
 * consumer, one view promoted a 1×1 image another rejected, and the same track
 * showed a cover in one view and a generated avatar in the other.
 * artworkParity.test.js pins the wiring; this pins the behaviour.
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useArtworkTransition, ARTWORK_WAIT_MS } from '@/composables/useArtworkTransition';

/** Mount a host exposing the composable for `target`/`trackKey`/`announced`. */
function mountTransition(target, trackKey, announced) {
  let api;
  const Host = defineComponent({
    setup() {
      api = useArtworkTransition(target, trackKey, announced);
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
    // The bug this replaces: one view accepted this, another did not.
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

  it('holds it across a track change whose cover is announced', async () => {
    // Bluetooth: the title arrives over AVRCP with `artwork_pending`, the cover
    // is looked up from the track text afterwards and lands about a second later.
    const target = ref('/api/cover/one');
    const trackKey = ref('first|artist');
    const announced = ref(false);
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, trackKey, announced);
    settleFromLoad(decodedAt(600, 600));

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();

    expect(shownArtwork.value).toBe('/api/cover/one');
    expect(artworkPending.value).toBe(true);

    target.value = '/api/cover/two';
    announced.value = false;
    await nextTick();
    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/cover/two');
  });

  it('veils the placeholder for as long as the backend announces a cover', async () => {
    // CD: the disc is published first and its jacket fetched after, `artwork_pending`
    // meanwhile. Without the veil the placeholder is swapped for the cover a
    // second later, with nothing saying one was coming — and the fetch can last
    // over a minute when the archive retries, so the 4 s bound must not apply.
    const target = ref('');
    const announced = ref(true);
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, ref('track|'), announced);

    expect(artworkPending.value).toBe(true);
    vi.advanceTimersByTime(ARTWORK_WAIT_MS * 10);
    expect(artworkPending.value).toBe(true);

    target.value = '/api/cd/cover/disc';
    announced.value = false;
    await nextTick();
    expect(artworkPending.value).toBe(true); // now waiting on the decode

    settleFromLoad(decodedAt(500, 500));
    expect(shownArtwork.value).toBe('/api/cd/cover/disc');
    expect(artworkPending.value).toBe(false);
  });

  it('lifts the announced veil the moment the backend gives up', async () => {
    // The archive has no jacket, or could not be reached: the flag drops with no
    // URL, and the placeholder is the answer — right away, not 4 s later.
    const announced = ref(true);
    const { shownArtwork, artworkPending } =
      mountTransition(ref(''), ref('track|'), announced);

    announced.value = false;
    await nextTick();

    expect(artworkPending.value).toBe(false);
    expect(shownArtwork.value).toBe('');
  });

  it('lifts it at once even when a track change had armed the bounded wait', async () => {
    // A new disc changes the title, which arms the 4 s wait; the archive then
    // answers "no jacket" half a second later. The backend's answer is final —
    // the placeholder must not sit veiled for the rest of the 4 s.
    const target = ref('');
    const trackKey = ref('old|');
    const announced = ref(true);
    const { artworkPending } = mountTransition(target, trackKey, announced);

    trackKey.value = 'new|';
    await nextTick();
    vi.advanceTimersByTime(500);
    announced.value = false;
    await nextTick();

    expect(artworkPending.value).toBe(false);
  });

  it('shows the fallback at once on a track change nothing announced a cover for', async () => {
    // Radio, stepping from a station with a logo to one without: nothing is
    // being looked up, so the generated avatar is the answer now — not the
    // previous station's logo held under a spinner for the bounded wait.
    const target = ref('/api/radio/favicon?url=a');
    const trackKey = ref('Station A|');
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, trackKey, ref(false));
    settleFromLoad(decodedAt(300, 300));

    target.value = '';
    trackKey.value = 'Station B|';
    await nextTick();

    expect(shownArtwork.value).toBe('');
    expect(artworkPending.value).toBe(false);
  });

  it('holds the cover past the bounded wait while the next one is announced', async () => {
    // Bluetooth: the track lands with no cover and `artwork_pending`, and the
    // lookup can take longer than the decode bound (the resolver queues its
    // lookups, 8 s each at worst). The glyph must not replace the held cover
    // before the backend has answered.
    const target = ref('/api/cover/a');
    const trackKey = ref('first|artist');
    const announced = ref(false);
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, trackKey, announced);
    settleFromLoad(decodedAt(600, 600));

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();
    vi.advanceTimersByTime(ARTWORK_WAIT_MS + 1);

    expect(shownArtwork.value).toBe('/api/cover/a');
    expect(artworkPending.value).toBe(true);
  });

  it('veils a player mounted while the backend announces a cover', () => {
    // Bluetooth, an iPhone taken back from a Mac: the full player mounts on a
    // track whose cover is still being looked up. No track change is seen, so
    // only the announcement keeps the glyph from showing before the cover.
    const { shownArtwork, artworkPending } =
      mountTransition(ref(''), ref('track|artist'), ref(true));

    expect(artworkPending.value).toBe(true);
    expect(shownArtwork.value).toBe('');
  });

  it('holds the cover on screen while one is announced, not the stand-in', async () => {
    // Radio: a new song from the stream publishes the station logo as its
    // cover while the song's own is looked up. Heading for the logo would
    // flash it between two covers.
    const target = ref('/api/cover/one');
    const trackKey = ref('first|artist');
    const announced = ref(false);
    const { shownArtwork, preloadArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, trackKey, announced);
    settleFromLoad(decodedAt(600, 600));

    target.value = '/api/radio/favicon?url=logo';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();

    expect(shownArtwork.value).toBe('/api/cover/one');
    expect(preloadArtwork.value).toBe('');
    expect(artworkPending.value).toBe(true);
    vi.advanceTimersByTime(ARTWORK_WAIT_MS * 10);
    expect(shownArtwork.value).toBe('/api/cover/one');

    target.value = '/api/cover/two';
    announced.value = false;
    await nextTick();
    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/cover/two');
    expect(artworkPending.value).toBe(false);
  });

  it('heads for the stand-in once the backend found no cover', async () => {
    // The lookup missed: the station logo is now the answer, and it loads.
    const target = ref('/api/cover/one');
    const trackKey = ref('first|artist');
    const announced = ref(false);
    const { shownArtwork, preloadArtwork, settleFromLoad } =
      mountTransition(target, trackKey, announced);
    settleFromLoad(decodedAt(600, 600));

    target.value = '/api/radio/favicon?url=logo';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();
    announced.value = false;
    await nextTick();

    expect(preloadArtwork.value).toBe('/api/radio/favicon?url=logo');
    settleFromLoad(decodedAt(300, 300));
    expect(shownArtwork.value).toBe('/api/radio/favicon?url=logo');
  });

  it('disarms the wait when the cover resolved is the one already shown', async () => {
    // Bluetooth again, but track 2 of the SAME album: the lookup answers with
    // the cover on screen. Nothing is going to load, so if the veil is not
    // settled here it waits for a load that never comes, and the wait fires at
    // T+4 s over a correct cover.
    const target = ref('/api/cover/album');
    const trackKey = ref('first|artist');
    const announced = ref(false);
    const { shownArtwork, artworkPending, settleFromLoad } =
      mountTransition(target, trackKey, announced);
    settleFromLoad(decodedAt(600, 600));

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();
    expect(artworkPending.value).toBe(true);

    target.value = '/api/cover/album';
    announced.value = false;
    await nextTick();
    // Settled now: no load is coming to lift a veil.
    expect(artworkPending.value).toBe(false);
    vi.advanceTimersByTime(ARTWORK_WAIT_MS);

    expect(shownArtwork.value).toBe('/api/cover/album');
    expect(artworkPending.value).toBe(false);
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
