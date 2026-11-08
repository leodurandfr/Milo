// frontend/tests/composables/useArtworkTransition.test.js
/**
 * useArtworkTransition keeps the cover currently on screen until the next one
 * has decoded, and decides what counts as a cover at all. Nothing is laid over
 * a cover while it waits — no veil, no spinner: the outgoing one stays as it
 * is, and the incoming one replaces it the moment it has decoded.
 *
 * Six rules carry the weight:
 *
 *  - an image that decodes at a real size is promoted,
 *  - one that decodes at a pixel or two is NOT a cover but a tracking pixel or
 *    a broken favicon, and is rejected so the caller's fallback shows,
 *  - a track change whose cover the backend announced (`artwork_pending`: the
 *    CD's jacket, Bluetooth's and radio's iTunes lookups) holds the outgoing
 *    cover until the backend answers — but for ARTWORK_WAIT_MS at most: past
 *    it the record's stand-in (or the fallback) is shown, since a lookup can
 *    last over a minute and the old cover belongs to another title,
 *  - a track change with no cover and no announcement has none: the fallback
 *    is drawn at once, as the answer,
 *  - the decode wait is bounded, because an image that will not decode fires
 *    no event,
 *  - and a cover already on screen is never waited for again.
 *
 * The announced wait used to be unbounded and veiled with a spinner; the bound
 * is what lets the veil go without leaving a stale cover beside a new title.
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
import { computed, defineComponent, h, nextTick, ref } from 'vue';
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

  /** A player showing `url`, decoded, on track `key`. */
  function showing(url, key = 'first|artist') {
    const target = ref(url);
    const trackKey = ref(key);
    const announced = ref(false);
    const api = mountTransition(target, trackKey, announced);
    api.settleFromLoad(decodedAt(600, 600));
    return { ...api, target, trackKey, announced };
  }

  /**
   * A player showing `artwork`, decoded, wired the way both shells wire it:
   * three computeds over one record, as usePlayerMetadata derives them. One
   * publish then moves all three at once, and the watchers run in the order
   * production triggers them — the cover's last. Setting three refs by hand
   * picks that order instead, and can hide a guard behind another watcher.
   */
  function playingRecord(artwork, title = 'first') {
    const record = ref({ title, artist: 'artist', artwork, pending: false });
    const api = mountTransition(
      computed(() => record.value.artwork),
      computed(() => `${record.value.title}|${record.value.artist}`),
      computed(() => record.value.pending),
    );
    api.settleFromLoad(decodedAt(600, 600));
    return { ...api, record };
  }

  it('promotes a cover that decodes at a real size', () => {
    const target = ref('/api/music-library/cover/abc');
    const { shownArtwork, preloadArtwork, settleFromLoad } =
      mountTransition(target, ref('track|artist'));

    // Sent to the preloader on mount, nothing shown until it has decoded.
    expect(preloadArtwork.value).toBe('/api/music-library/cover/abc');
    expect(shownArtwork.value).toBe('');

    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/music-library/cover/abc');
    expect(preloadArtwork.value).toBe('');
  });

  it('rejects an image too small to be a cover', () => {
    // The bug this replaces: one view accepted this, another did not.
    const target = ref('http://sender.local/art.png');
    const { shownArtwork, preloadArtwork, settleFromLoad } =
      mountTransition(target, ref('track|artist'));

    settleFromLoad(decodedAt(1, 1));

    expect(shownArtwork.value).toBe('');
    expect(preloadArtwork.value).toBe('');
  });

  it('rejects a cover that fails to load', () => {
    const target = ref('/api/airplay/artwork?v=1');
    const { shownArtwork, preloadArtwork, settleFromError } =
      mountTransition(target, ref('track|artist'));

    settleFromError();

    expect(shownArtwork.value).toBe('');
    expect(preloadArtwork.value).toBe('');
  });

  it('keeps the outgoing cover until the next one has decoded, then swaps at once', async () => {
    // A station or track change: the old cover stays as it is — not a blank
    // square, not the fallback — and the new one replaces it on its load, not
    // after a fixed wait.
    const { shownArtwork, preloadArtwork, target, trackKey, settleFromLoad } =
      showing('/api/radio/favicon?url=a', 'Station A|');

    target.value = '/api/radio/favicon?url=b';
    trackKey.value = 'Station B|';
    await nextTick();

    expect(shownArtwork.value).toBe('/api/radio/favicon?url=a');
    expect(preloadArtwork.value).toBe('/api/radio/favicon?url=b');

    vi.advanceTimersByTime(200);
    settleFromLoad(decodedAt(300, 300));
    expect(shownArtwork.value).toBe('/api/radio/favicon?url=b');

    // Nothing left armed to replace it again later.
    vi.advanceTimersByTime(ARTWORK_WAIT_MS * 2);
    expect(shownArtwork.value).toBe('/api/radio/favicon?url=b');
  });

  it('paints a cover the browser never reports on, rather than waiting forever', async () => {
    const target = ref('');
    const { shownArtwork } = mountTransition(target, ref('track|artist'));

    target.value = '/api/cover/slow';
    await nextTick();
    vi.advanceTimersByTime(ARTWORK_WAIT_MS - 1);
    expect(shownArtwork.value).toBe('');

    vi.advanceTimersByTime(1);

    // A URL we have but no `load` for: show it anyway — the opposite fallback
    // from the no-URL case below, which has nothing to show.
    expect(shownArtwork.value).toBe('/api/cover/slow');
  });

  it('holds it across a track change whose cover is announced', async () => {
    // Bluetooth: the title arrives over AVRCP with `artwork_pending`, the cover
    // is looked up from the track text afterwards and lands about a second later.
    const { shownArtwork, target, trackKey, announced, settleFromLoad } =
      showing('/api/cover/one');

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();
    vi.advanceTimersByTime(1000);

    expect(shownArtwork.value).toBe('/api/cover/one');

    target.value = '/api/cover/two';
    announced.value = false;
    await nextTick();
    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/cover/two');
  });

  it('holds the cover on screen while one is announced, not the stand-in', async () => {
    // Radio: a new song from the stream publishes the station logo as its
    // cover while the song's own is looked up. Heading for the logo would
    // flash it between two covers.
    const { shownArtwork, preloadArtwork, record, settleFromLoad } =
      playingRecord('/api/cover/one');

    record.value = { title: 'second', artist: 'artist', artwork: '/api/radio/favicon?url=logo', pending: true };
    await nextTick();

    expect(shownArtwork.value).toBe('/api/cover/one');
    expect(preloadArtwork.value).toBe('');

    record.value = { title: 'second', artist: 'artist', artwork: '/api/cover/two', pending: false };
    await nextTick();
    settleFromLoad(decodedAt(600, 600));

    expect(shownArtwork.value).toBe('/api/cover/two');
  });

  it('shows the stand-in once an announced cover has kept it waiting too long', async () => {
    // A lookup can outlast the bound by far (iTunes queued, the archive
    // retrying). The previous song's cover must not sit beside the new title
    // for all that time: past the bound the record's stand-in is the slot's.
    const { shownArtwork, preloadArtwork, record, settleFromLoad } =
      playingRecord('/api/cover/one');

    record.value = { title: 'second', artist: 'artist', artwork: '/api/radio/favicon?url=logo', pending: true };
    await nextTick();
    vi.advanceTimersByTime(ARTWORK_WAIT_MS - 1);
    expect(shownArtwork.value).toBe('/api/cover/one');
    expect(preloadArtwork.value).toBe('');

    vi.advanceTimersByTime(1);
    // Decoded like any cover, so the old one stays until the logo is ready.
    expect(preloadArtwork.value).toBe('/api/radio/favicon?url=logo');
    expect(shownArtwork.value).toBe('/api/cover/one');
    settleFromLoad(decodedAt(300, 300));
    expect(shownArtwork.value).toBe('/api/radio/favicon?url=logo');

    // And the cover the backend answers later still replaces it.
    record.value = { title: 'second', artist: 'artist', artwork: '/api/cover/two', pending: false };
    await nextTick();
    expect(preloadArtwork.value).toBe('/api/cover/two');
    settleFromLoad(decodedAt(600, 600));
    expect(shownArtwork.value).toBe('/api/cover/two');
  });

  it('shows the fallback once an announced cover with no stand-in has kept it waiting too long', async () => {
    // Bluetooth or CD: no stand-in, so past the bound the fallback is the slot's
    // until the backend answers.
    const { shownArtwork, target, trackKey, announced } = showing('/api/cover/a');

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();
    vi.advanceTimersByTime(ARTWORK_WAIT_MS);

    expect(shownArtwork.value).toBe('');
  });

  it('does not extend the hold on a second track change while still announced', async () => {
    // Two quick skips on Bluetooth: the first track's cover is held for the
    // bound from the first change, not re-armed by each one.
    const { shownArtwork, target, trackKey, announced } = showing('/api/cover/a');

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();
    vi.advanceTimersByTime(ARTWORK_WAIT_MS - 1000);
    trackKey.value = 'third|artist';
    await nextTick();
    vi.advanceTimersByTime(1000);

    expect(shownArtwork.value).toBe('');
  });

  it('drops a cover still decoding for the previous track when the next is announced', async () => {
    // Track A's cover was on its way when track B came in announced: painting
    // A's cover on its load would put it beside B's title.
    const { shownArtwork, preloadArtwork, target, trackKey, announced } =
      showing('/api/cover/zero');

    target.value = '/api/cover/a';
    trackKey.value = 'a|artist';
    await nextTick();
    expect(preloadArtwork.value).toBe('/api/cover/a');

    target.value = '';
    trackKey.value = 'b|artist';
    announced.value = true;
    await nextTick();

    expect(preloadArtwork.value).toBe('');
    expect(shownArtwork.value).toBe('/api/cover/zero');
  });

  it('shows the placeholder the moment the backend gives up', async () => {
    // The archive has no jacket, or could not be reached: the flag drops with no
    // URL, and the placeholder is the answer — right away, not when a wait
    // armed by the track change runs out.
    const { shownArtwork, target, trackKey, announced } = showing('/api/cover/old');

    target.value = '';
    trackKey.value = 'new|';
    announced.value = true;
    await nextTick();
    vi.advanceTimersByTime(500);
    announced.value = false;
    await nextTick();

    expect(shownArtwork.value).toBe('');
  });

  it('heads for the stand-in once the backend found no cover', async () => {
    // The lookup missed: the station logo is now the answer, and it loads.
    const { shownArtwork, preloadArtwork, target, trackKey, announced, settleFromLoad } =
      showing('/api/cover/one');

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

  it('shows the fallback at once on a track change nothing announced a cover for', async () => {
    // Radio, stepping from a station with a logo to one without: nothing is
    // being looked up, so the generated avatar is the answer now — not the
    // previous station's logo held for the bounded wait.
    const { shownArtwork, target, trackKey } = showing('/api/radio/favicon?url=a', 'Station A|');

    target.value = '';
    trackKey.value = 'Station B|';
    await nextTick();

    expect(shownArtwork.value).toBe('');
  });

  it('shows what the record carries when mounted while a cover is announced', async () => {
    // Bluetooth, an iPhone taken back from a Mac; radio expanded mid-lookup:
    // the player mounts with nothing on screen to hold, so the stand-in (or the
    // fallback) is shown at once rather than a blank slot under a spinner.
    const withStandIn = mountTransition(ref('/api/radio/favicon?url=logo'), ref('song|artist'), ref(true));
    expect(withStandIn.preloadArtwork.value).toBe('/api/radio/favicon?url=logo');
    withStandIn.settleFromLoad(decodedAt(300, 300));
    expect(withStandIn.shownArtwork.value).toBe('/api/radio/favicon?url=logo');

    const target = ref('');
    const announced = ref(true);
    const bare = mountTransition(target, ref('track|'), announced);
    expect(bare.shownArtwork.value).toBe('');
    expect(bare.preloadArtwork.value).toBe('');

    // And the answer, when it comes, is headed for as usual.
    target.value = '/api/cd/cover/disc';
    announced.value = false;
    await nextTick();
    bare.settleFromLoad(decodedAt(500, 500));
    expect(bare.shownArtwork.value).toBe('/api/cd/cover/disc');
  });

  it('disarms the wait when the cover resolved is the one already shown', async () => {
    // Bluetooth again, but track 2 of the SAME album: the lookup answers with
    // the cover on screen. Nothing is going to load, so if the hold is not
    // settled here its bound fires at T+4 s and replaces a correct cover.
    const { shownArtwork, preloadArtwork, target, trackKey, announced } =
      showing('/api/cover/album');

    target.value = '';
    trackKey.value = 'second|artist';
    announced.value = true;
    await nextTick();

    target.value = '/api/cover/album';
    announced.value = false;
    await nextTick();
    expect(preloadArtwork.value).toBe('');
    vi.advanceTimersByTime(ARTWORK_WAIT_MS * 2);

    expect(shownArtwork.value).toBe('/api/cover/album');
  });
});
