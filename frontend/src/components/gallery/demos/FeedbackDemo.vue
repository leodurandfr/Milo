<!-- frontend/src/components/gallery/demos/FeedbackDemo.vue -->
<template>
  <GalleryItem id="LoadingSpinner">
    <GalleryVariant label="size — 24 / 32 (default) / 48">
      <LoadingSpinner :size="24" />
      <LoadingSpinner :size="32" />
      <LoadingSpinner :size="48" />
    </GalleryVariant>
    <GalleryVariant label="currentColor — it carries no colour of its own, the host names one">
      <div class="dark-strip spinner-on-dark">
        <LoadingSpinner :size="32" />
        <LoadingSpinner :size="48" />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="Badge">
    <GalleryVariant label="tone">
      <Badge tone="success">Connected</Badge>
      <Badge tone="warning" pulse>Connecting…</Badge>
      <Badge tone="error">Not connected</Badge>
      <Badge tone="brand">Ready</Badge>
      <Badge>80 Hz</Badge>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="NoticeBox">
    <GalleryVariant label='kind="error" — the default' stacked>
      <NoticeBox>Could not reach this server. Check that it is on and on the same network.</NoticeBox>
    </GalleryVariant>
    <GalleryVariant label='kind="warning"' stacked>
      <NoticeBox kind="warning">Saved, but the server did not connect. You can retry from the list.</NoticeBox>
    </GalleryVariant>
    <GalleryVariant label='kind="empty" — a list with nothing in it' stacked>
      <NoticeBox kind="empty">No server found on your network.</NoticeBox>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="NotificationBanner">
    <GalleryVariant label="title only" contain :contain-height="90">
      <NotificationBanner title="Snapcast client reconnected" />
    </GalleryVariant>
    <GalleryVariant label="title + detail + dismissable" contain :contain-height="130">
      <NotificationBanner
        v-if="bannerShown"
        title="CamillaDSP restart failed"
        detail="hw:Loopback,0,0 is held by snapclient — check the routing mode."
        dismissable
        @dismiss="bannerShown = false"
      />
    </GalleryVariant>
    <GalleryVariant :label="`dismissed: ${!bannerShown}`">
      <Button size="small" :disabled="bannerShown" @click="bannerShown = true">Bring it back</Button>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="MessageContent">
    <GalleryVariant label="icon + title + subtitle + details" stacked>
      <MessageContent
        icon="radio"
        title="No favourites yet"
        subtitle="Stations you like end up here"
        details="Search for a station, then tap the heart to keep it."
      />
    </GalleryVariant>
    <GalleryVariant label="two CTAs — ctaClick is a Function prop, not an event" stacked>
      <MessageContent
        icon="network"
        title="Not connected"
        details="Milo needs a network to reach the catalogue."
        cta-label="Open Wi-Fi settings"
        :cta-click="() => ctaHits++"
        cta-secondary-label="Retry"
        :cta-secondary-click="() => ctaHits++"
      />
      <span class="text-mono-small">CTA presses: {{ ctaHits }}</span>
    </GalleryVariant>
    <GalleryVariant label="loading — the spinner is held back by loadingDelay (200 ms)" stacked>
      <MessageContent loading title="Scanning the library" />
    </GalleryVariant>
    <GalleryVariant label='variant="on-contrast" — card-less, for use over artwork' stacked>
      <div class="dark-strip">
        <MessageContent
          variant="on-contrast"
          icon="lyrics"
          title="No lyrics for this track"
          details="LRCLIB has no match for this artist and title."
        />
      </div>
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import Badge from '@/components/ui/Badge.vue';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import NotificationBanner from '@/components/ui/NotificationBanner.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const bannerShown = ref(true);
const ctaHits = ref(0);
</script>

<style scoped>
.dark-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: var(--space-04);
  width: 100%;
  padding: var(--space-04);
  background: var(--color-contrast);
  border-radius: var(--radius-03);
}

/* The spinner draws in currentColor and nothing else, so a dark host has to name
   a light one — which is the whole of what the variant above shows. */
.spinner-on-dark {
  color: var(--color-text-on-contrast);
}
</style>
