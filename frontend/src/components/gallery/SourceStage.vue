<!-- frontend/src/components/gallery/SourceStage.vue -->
<!--
  One audio source, in one of its states — the component behind every page in
  the gallery's Sources section.

  It does two things and delegates everything else. It writes the scenario's
  snapshot into the canvas's own `unifiedAudioStore`, and then it gets out of
  the way: for 8 of the 11 sources it mounts the real `AudioSourceView`,
  the app's own dispatcher, and whatever appears is whatever `useRichDisplay()`,
  `useSourceStatusDisplay()` and `currentDeviceName` decide from that record.
  Nothing here chooses a player or draws a card, which is why a scenario cannot
  disagree with the app — see sources.js.

  The exception is `via: 'browser'`. Radio, Podcasts and Music Library dispatch
  to `*Source.vue` files that own feature stores and fetch on mount, so mounting
  *those* would read the real catalogue. What is reassembled here is only that
  wrapper — the header props it forwards, and the pane it puts `AudioPlayer` in.
  Everything below is the app's: a real `AudioSourceLayout`, the source's real
  browsing view, its real store, its real cards. The scenario supplies what the
  backend would have (see canvasHttp.js), and the store parses it by its own
  code path — so "radio with no favourites" is the empty state the app draws,
  not a picture of one.

  Two of their scenarios still go through the dispatcher: `starting` and
  `error`, both of which `useRichDisplay()` short-circuits before it can reach a
  `*Source.vue`, so the status card is reached honestly there too.
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
    <Transition name="audio-content" appear>
      <div :key="slotKey" class="source-stage__slot">
        <!-- Store-driven: the app's dispatcher decides what this is. -->
        <AudioSourceView v-if="!browser" />

        <AudioSourceLayout
          v-else
          :gradient="page.source"
          :header-icon="page.source"
          :header-title="t(browser.layout.titleKey)"
          :header-show-back="!!browser.layout.showBack"
          :header-title-muted="!!browser.layout.titleMuted"
          header-variant="background-neutral"
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

          <template v-if="browser.layout.actions?.length" #header-actions="{ iconVariant }">
            <IconButton
              v-for="icon in browser.layout.actions"
              :key="icon"
              :icon="icon"
              :variant="iconVariant"
            />
          </template>

          <template v-if="browser.player" #player>
            <!-- Props transcribed per source too: radio falls back from the
                 track's cover to the station's and passes the station name as
                 the avatar seed, podcasts pass only the episode, music library
                 is the one that hands over a queue. -->
            <AudioPlayer
              :source="page.source"
              visible
              :artwork="playerArtwork"
              :fallback-name="playerFallbackName"
              :title="playerTitle"
              :is-playing="!!player.isPlaying"
              :is-loading="!!player.isLoading"
              :swipe-enabled="page.source !== 'radio'"
              :tracks="player.tracks || []"
              :current-index="player.currentIndex ?? -1"
            >
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

              <!--
                Transcribed from each source's own #info, and they are three
                different shapes — a generic one would be inventing a screen.
                Radio drops the station to a kicker only once a track is
                recognised AND that track has artwork; Podcasts pass a kicker and
                a title but never a secondary; Music Library passes title +
                secondary and no flat lines at all, because on mobile its
                mini-bar is the swipe carousel rather than this slot.
              -->
              <template #info="{ expanded }">
                <template v-if="page.source === 'radio'">
                  <template v-if="player.track">
                    <PlayerInfoText
                      class="vertical-layout"
                      :kicker="player.track.artwork ? player.station.name : null"
                      :kicker-icon="player.track.artwork ? player.station.artwork : null"
                      :kicker-fallback-name="player.track.artwork ? player.station.name : null"
                      :title="player.track.title"
                      :secondary="player.track.artist"
                    />
                    <template v-if="!expanded">
                      <p class="player-title text-body horizontal-layout">{{ player.track.title }}</p>
                      <p class="player-subtitle text-body horizontal-layout">{{ player.track.artist }}</p>
                    </template>
                  </template>
                  <template v-else>
                    <PlayerInfoText class="vertical-layout" :title="player.station.name" />
                    <p v-if="!expanded" class="player-title text-body horizontal-layout">
                      {{ player.station.name }}
                    </p>
                  </template>
                </template>

                <template v-else-if="page.source === 'podcast'">
                  <PlayerInfoText
                    class="vertical-layout"
                    :kicker="player.podcastName"
                    :title="player.episodeName"
                  />
                  <template v-if="!expanded">
                    <p class="player-title text-body horizontal-layout">{{ player.episodeName }}</p>
                    <p v-if="player.podcastName" class="player-subtitle text-body horizontal-layout">
                      {{ player.podcastName }}
                    </p>
                  </template>
                </template>

                <PlayerInfoText
                  v-else
                  class="vertical-layout"
                  :title="player.title"
                  :secondary="player.artist"
                />
              </template>

              <template v-if="browser.player.progress" #progress>
                <ProgressBar
                  :current-position="browser.player.progress.currentPosition"
                  :duration="browser.player.progress.duration"
                  :progress-percentage="browser.player.progress.progressPercentage"
                  variant="dark"
                  :interactive="false"
                />
              </template>

              <!--
                Each source's own transport, because there is no shared one: the
                #controls slot has a default (a lone play/pause) and all three
                sources replace it. Radio uses a text Button plus a favourite,
                Podcasts a seek pair around play with a speed Dropdown, Music
                Library a five-button row — and half of AudioPlayer's CSS keys
                off those exact class names (.ml-transport-main, .speed-selector,
                .desktop-only), so the classes are the contract, not decoration.
                Handlers are left off: the state is the scenario's to describe.
              -->
              <template #controls="{ expanded }">
                <div v-if="page.source === 'radio'" class="radio-controls">
                  <div class="radio-controls-main vertical-layout">
                    <Button
                      variant="on-dark"
                      :left-icon="browser.player.isPlaying ? 'stop' : 'play'"
                      :loading="!!browser.player.isLoading"
                    >
                      {{ browser.player.isPlaying
                        ? t('audioSources.radioSource.stopRadio')
                        : t('audioSources.radioSource.playRadio') }}
                    </Button>
                    <IconButton
                      :icon="controls.favorite ? 'heart' : 'heartOff'"
                      variant="on-dark"
                      size="medium"
                    />
                  </div>
                  <!-- Mobile mini-bar only: no room for a text button + heart. -->
                  <div v-if="!expanded" class="playback-controls horizontal-layout">
                    <IconButton
                      :icon="browser.player.isPlaying ? 'stop' : 'play'"
                      variant="ghost"
                      size="medium"
                      :loading="!!browser.player.isLoading"
                    />
                  </div>
                </div>

                <template v-else-if="page.source === 'podcast'">
                  <div class="playback-controls">
                    <IconButton icon="rewind15" variant="ghost" size="small" class="desktop-only" />
                    <IconButton
                      :icon="browser.player.isPlaying ? 'pause' : 'play'"
                      variant="ghost"
                      size="medium"
                      :loading="!!browser.player.isLoading"
                    />
                    <IconButton icon="forward30" variant="ghost" size="small" class="desktop-only" />
                  </div>
                  <div class="speed-selector desktop-only">
                    <Dropdown :model-value="speedValue" :options="speedOptions" variant="minimal" />
                  </div>
                </template>

                <div v-else class="ml-controls">
                  <div class="playback-controls">
                    <IconButton
                      icon="shuffle" variant="ghost" size="small" class="ml-transport-extra"
                      :color="controls.shuffle ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                    />
                    <div class="ml-transport-main">
                      <IconButton icon="previous" variant="ghost" size="small" class="ml-transport-extra" />
                      <IconButton
                        :icon="browser.player.isPlaying ? 'pause' : 'play'"
                        variant="ghost"
                        size="medium"
                        :loading="!!browser.player.isLoading"
                      />
                      <IconButton
                        icon="next" variant="ghost" size="small" class="ml-transport-extra"
                        :disabled="controls.hasNext === false"
                      />
                    </div>
                    <IconButton
                      :icon="controls.starred ? 'heart' : 'heartOff'"
                      variant="ghost" size="small" class="ml-transport-extra"
                      :color="controls.starred ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                    />
                  </div>
                </div>
              </template>
            </AudioPlayer>
          </template>
        </AudioSourceLayout>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, watchEffect } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { sourcePageById } from './sources';
import { setApiFixtures } from './canvasHttp';
import FavoritesView from '@/components/radio/FavoritesView.vue';
import LibraryHome from '@/components/music-library/views/LibraryHome.vue';
import PodcastHome from '@/components/podcasts/HomeView.vue';
import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import { useIsMobile } from '@/composables/useIsMobile';

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
  'podcast-home': PodcastHome
};

const { t } = useI18n();
const { isMobile } = useIsMobile();
const unifiedStore = useUnifiedAudioStore();
const stores = {
  radio: useRadioStore(),
  musicLibrary: useMusicLibraryStore(),
  podcast: usePodcastStore()
};

const page = computed(() => sourcePageById(props.page));

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
 * The transport's own state — the favourite, the shuffle, whether there is a
 * next track. Separate from the player's playback state because these are what
 * a reader comes here to flip: a heart that is only ever hollow documents half
 * the button.
 */
const controls = computed(() => browser.value?.player?.controls ?? {});
const player = computed(() => browser.value?.player ?? {});

/**
 * The four artwork/title props, resolved the way each source resolves them —
 * radio's two-level fallback (track cover, else the station's) is a rule, not a
 * value, so the fixture carries the station and the track and this derives what
 * the app would.
 */
const playerArtwork = computed(() => {
  if (page.value?.source === 'radio') return player.value.track?.artwork || player.value.station?.artwork || null;
  if (page.value?.source === 'podcast') return player.value.episodeImage || null;
  return player.value.artwork || null;
});

const playerTitle = computed(() => {
  if (page.value?.source === 'radio') return player.value.track?.title || player.value.station?.name || '';
  if (page.value?.source === 'podcast') return player.value.episodeName || '';
  return player.value.title || '';
});

/** Only radio seeds the generated avatar, and only from the station's name. */
const playerFallbackName = computed(() =>
  page.value?.source === 'radio' ? player.value.station?.name || null : null
);

/**
 * The speed list is the backend's, not ours: the store fetches it and the
 * scenario serves that fetch (see the podcast fixtures), so the dropdown here
 * shows whatever the appliance would offer rather than a second hardcoded list.
 */
const speedOptions = computed(() =>
  stores.podcast.playbackSpeeds.map(speed => ({ label: `${speed}x`, value: String(speed) }))
);
const speedValue = computed(() => String(stores.podcast.playbackSpeed || 1));

/**
 * Where a scenario's events go — the same rows App.vue's RAW_EVENTS declares
 * for these pairs, and the reason the page can claim the app decided what it
 * shows: the payload is validated and applied by the app's own handler, not
 * written into the store from the side.
 *
 * A map rather than one blind call, so a scenario that grows a pair nothing
 * routes fails loudly here (and in the guardrail, which checks the two lists
 * against each other) instead of being swallowed.
 */
const DISPATCH = {
  'system.transition_start': unifiedStore.updateState,
  'system.transition_complete': unifiedStore.updateState,
  'system.state_changed': unifiedStore.updateState,
  'source.state_changed': unifiedStore.updateState
};

/**
 * The whole write, and the only one. Runs during setup — before
 * AudioSourceView mounts — so the dispatcher never sees a stale record and
 * animates a transition nobody asked for.
 *
 * No socket is involved: the envelopes are built in sources.js and handed
 * straight to the handler, so this replays a broadcast without there being one
 * to listen to. `updateState` replaces the record wholesale, which is what
 * keeps the previous scenario's `client_name` or `disc_id` from surviving into
 * the next — the drift this page exists to make visible.
 */
watchEffect(() => {
  const scenario = current.value;
  if (!scenario) return;

  for (const event of scenario.events) {
    DISPATCH[`${event.category}.${event.type}`]?.(event);
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
});
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
