<!-- frontend/src/components/gallery/SourceStage.vue -->
<!--
  One audio source, in one of its states — the component behind every page in
  the gallery's Sources section.

  It does two things and delegates everything else. It publishes the
  scenario's state into the canvas's own `unifiedAudioStore`, and then it gets
  out of the way: for 6 of the 10 sources it mounts the real `AudioSourceView`,
  the app's own dispatcher, and whatever appears is whatever `richSourceFor()`,
  `displayStateFor()` and the session's `senders` decide from that state.
  Nothing here chooses a player or draws a card, which is why a scenario cannot
  disagree with the app — see sources.js.

  The exception is `via: 'browser'`. Radio, Podcasts, Music Library and Spotify dispatch
  to `*Source.vue` files that own feature stores and fetch on mount, so mounting
  *those* would read the real catalogue. What is reassembled here is only that
  wrapper — the header props it forwards, the pane it puts `AudioPlayer` in, and
  the `BrowserSourceViews` shell that swaps the navigation for the full player
  that bar's expand button opens (its back button returns, as in the app).
  Everything below is the app's: a real `AudioSourceLayout`, the source's real
  browsing view, its real store, its real cards. The scenario supplies what the
  backend would have (see canvasHttp.js), and the store parses it by its own
  code path — so "radio with no favourites" is the empty state the app draws,
  not a picture of one.

  Some of their scenarios still go through the dispatcher: switching, a failed
  service and a missing link, all of which `richSourceFor()` answers with the
  card before it can reach a `*Source.vue`, so the status card is reached
  honestly there too.
-->
<template>
  <div class="source-stage">
    <!--
      The same `audio-content` swap AudioSourceView uses between its own slots
      (design-system.css), for the same reason: going from the status card to a
      browser is a source view giving way to another, and in the app it
      cross-fades rather than cutting. Keyed on the *kind* of slot, so the card →
      browsing animates while one browsing view → the next does not — that one
      is AudioSourceLayout's own contentKey cross-fade, exactly as in prod.
    -->
    <Transition name="audio-content" appear @enter="swapIn" @leave="swapOut">
      <div :key="slotKey" class="source-stage__slot">
        <!-- Store-driven: the app's dispatcher decides what this is. -->
        <AudioSourceView v-if="!browser" />

        <!-- The navigation and the full player its bar expands into, swapped
             by the same shell the four sources use. What is not a command —
             radio's favorite — is the source's, read from the scenario. -->
        <BrowserSourceViews v-else :source="page.source">
          <template #navigation="{ bar }">
            <AudioSourceLayout
              :gradient="page.source"
              :header-icon="page.source"
              :header-title="t(browser.layout.titleKey)"
              :header-show-back="!!browser.layout.showBack"
              :header-title-muted="!!browser.layout.titleMuted"
              :show-player="!!browser.player"
              :player-mobile-height="144"
              :header-actions-key="scenario"
              :content-key="scenario"
            >
              <!-- The source's own browsing view, mounted for real: its store is
                   seeded and its fetches are served, so what renders is the app's
                   screen rather than a drawing of it. -->
              <template #content>
                <component :is="VIEWS[browser.view]" v-bind="browser.props || {}" :key="scenario" />
              </template>

              <template v-if="browser.layout.actions?.length" #header-actions>
                <IconButton
                  v-for="icon in browser.layout.actions"
                  :key="icon"
                  :icon="icon"
                />
              </template>

              <template v-if="browser.player" #player>
                <!-- The app's own bar, which reads what it draws — cover, lines,
                     bar, transport — from the state this page publishes, through
                     the body it shares with the full player. What the sources
                     still add is transcribed: radio's badge and favorite. -->
                <AudioPlayer :source="page.source" v-bind="bar">
                  <!-- Radio, mobile, track recognised: the station icon rides behind
                       the track cover. Only ever rendered in the docked mini-bar. -->
                  <template v-if="page.source === 'radio' && isMobile && player.track?.artwork" #artwork-badge>
                    <LazyImage
                      class="player-artwork-badge"
                      :src="player.station.artwork"
                      :fallback-name="player.station.name"
                      alt=""
                    />
                  </template>

                  <template v-if="page.source === 'radio'" #transport-end>
                    <IconButton :icon="controls.favorite ? 'heart' : 'heartOff'" variant="ghost" size="medium" />
                  </template>

                </AudioPlayer>
              </template>
            </AudioSourceLayout>
          </template>

          <template v-if="page.source === 'radio'" #transport-end>
            <IconButton :icon="controls.favorite ? 'heart' : 'heartOff'" variant="ghost" size="medium" />
          </template>
        </BrowserSourceViews>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { sourcePageById, replayedState } from './sources';
import { setApiFixtures } from './canvasHttp';
import FavoritesView from '@/components/radio/FavoritesView.vue';
import LibraryHome from '@/components/music-library/views/LibraryHome.vue';
import PodcastHome from '@/components/podcasts/HomeView.vue';
import SpotifyHome from '@/components/spotify/views/SpotifyHome.vue';
import SpotifyProfilesView from '@/components/spotify/views/SpotifyProfilesView.vue';
import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import BrowserSourceViews from '@/components/audio/BrowserSourceViews.vue';
import IconButton from '@/components/ui/IconButton.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import { useIsMobile } from '@/composables/useIsMobile';
import { usePlayerExpansion } from '@/composables/usePlayerExpansion';
import { swapIn, swapOut } from '@/utils/sourceMotion';

const props = defineProps({
  /**
   * Source page id (`source:spotify`). Locked to one option per page in the
   * descriptor — the sidebar is what switches source, not the props panel.
   */
  page: {
    type: String,
    required: true
  },
  /** Scenario id within that page. This is the control the reader drives. */
  scenario: {
    type: String,
    required: true
  }
});

/**
 * The browsing views a scenario can name, and the stores a scenario can seed.
 *
 * Both maps are closed on purpose. A view reached by string would otherwise be
 * whatever the fixture happens to spell, and a store written by string would be
 * whichever one a typo names — the guardrail checks a seed against the keys the
 * store actually exports, and it can only do that against a list.
 */
const VIEWS = {
  'radio-favourites': FavoritesView,
  'ml-home': LibraryHome,
  'podcast-home': PodcastHome,
  'spotify-home': SpotifyHome,
  'spotify-profiles': SpotifyProfilesView
};

const { t } = useI18n();
const { isMobile } = useIsMobile();
const unifiedStore = useUnifiedAudioStore();
const stores = {
  radio: useRadioStore(),
  musicLibrary: useMusicLibraryStore(),
  spotify: useSpotifyStore()
};

const page = computed(() => sourcePageById(props.page));

// A scenario is a fresh screen: it opens on the navigation, whatever the last
// one was left on.
const { collapse } = usePlayerExpansion();
watch(() => props.scenario, collapse);

const current = computed(() => {
  const scenarios = page.value?.scenarios ?? [];
  return scenarios.find(entry => entry.id === props.scenario) ?? scenarios[0];
});

const browser = computed(() => current.value?.browser ?? null);

/**
 * What the cross-fade keys on: the *kind* of surface, not the scenario. Moving
 * between two browsing scenarios of one source keeps the layout mounted so its
 * own contentKey cross-fade runs instead — which is what the app does when you
 * navigate inside a browser rather than switch source.
 */
const slotKey = computed(() => (browser.value ? `browser-${page.value?.source}` : 'dispatcher'));

/**
 * What the source adds that the state does not carry — radio's favourite: a
 * heart that is only ever hollow documents half the button — and its station
 * for the phone's badge.
 */
const controls = computed(() => browser.value?.player?.controls ?? {});
const player = computed(() => browser.value?.player ?? {});

/**
 * Where a scenario's events go — the same row App.vue declares for the pair,
 * and the reason the page can claim the app decided what it shows: the state is
 * validated by the strict schema and applied by the app's own handler, not
 * written into the store from the side.
 *
 * A map rather than one blind call, so a scenario that grows a pair nothing
 * routes fails loudly here (and in the guardrail, which checks the two lists
 * against each other) instead of being swallowed.
 */
const DISPATCH = {
  'source.state': unifiedStore.updateState
};

/**
 * The whole write, and the only one. Runs during setup — before
 * AudioSourceView mounts — so the dispatcher never sees a stale state and
 * animates a transition nobody asked for.
 *
 * A watch on the scenario alone, not an effect: the seeding and the loaders
 * read the stores they write, and the stores react to the state just
 * published, so an effect tracking those reads re-ran itself without end
 * (Spotify's scenarios froze the page).
 *
 * No socket is involved: the envelopes are built in sources.js and handed
 * straight to the handler, so this replays a broadcast without there being one
 * to listen to — published now, which is the one thing a replay changes (the
 * anchor's instant, see replayedState). `updateState` replaces the state
 * wholesale, which is what keeps the previous scenario's sender or disc from
 * surviving into the next — the drift this page exists to make visible.
 */
watch(current, (scenario) => {
  if (!scenario) return;

  const now = Date.now() / 1000;
  for (const event of scenario.events) {
    DISPATCH[`${event.category}.${event.type}`]?.({ ...event, data: replayedState(event.data, now) });
  }

  // Ordered: the fixtures have to be in place before the view mounts and
  // fetches, and the seed before it reads. Both are set even when the scenario
  // declares neither, so nothing carries over from the one before it.
  setApiFixtures(scenario.browser?.api);

  const seed = scenario.browser?.seed ?? {};
  for (const [name, fields] of Object.entries(seed)) {
    Object.assign(stores[name], fields);
  }

  // Some state has no seedable field behind it: radio exposes its favourites as
  // a sorted computed, so the only way in is the loader that fills the ref
  // underneath. Calling it against the fixture above is the better half of the
  // bargain anyway — the store parses the response by its own code path, so a
  // change to the shape it expects shows up here rather than staying hidden
  // behind a value the gallery wrote by hand.
  for (const [name, action, ...args] of scenario.browser?.prime ?? []) {
    stores[name][action](...args);
  }
}, { immediate: true });
</script>

<style scoped>
.source-stage {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

/* Both slots share one absolutely-positioned cell so the leaving surface and
   the entering one overlap instead of stacking — the same shape
   AudioSourceView gives its own slots, and what makes the cross-fade read as
   one view replacing another. The grid row is clamped (minmax min: 0) so a
   browser taller than the stage scrolls inside AudioSourceLayout rather than
   growing the row. */
.source-stage__slot {
  position: absolute;
  inset: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
}

/* The canvas sizes a lone AudioPlayer to the 340px pane it does not have there
   (see CanvasApp.vue). Here it *does* have one — AudioSourceLayout's sticky
   pane — so that rule has to give way, or the player overflows the pane by the
   stage's full height. The `.player-wrapper` step is what carries the win: the
   canvas rule is class + scope-attribute + class, so matching its specificity
   would leave the outcome to stylesheet order. */
.source-stage :deep(.player-wrapper .audio-player) {
  width: 100%;
  height: 100%;
}
</style>
