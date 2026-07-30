<!-- frontend/src/components/gallery/demos/StructureDemo.vue -->
<template>
  <GalleryItem id="Modal">
    <GalleryVariant label="teleported to body, so it escapes this card by design">
      <Button variant="brand" @click="modalOpen = true">Open</Button>
      <Button @click="modalTallOpen = true">Open, then grow</Button>
    </GalleryVariant>
    <Modal :is-open="modalOpen" @close="modalOpen = false">
      <NavigationHeader title="A modal" subtitle="Tap the scrim or the header to close" show-back
        @back="modalOpen = false" />
      <p class="modal-copy text-body">
        The container springs to the height of its content. Descendants that change
        size ask for a new height through the provided `modalRequestHeightDelta`
        rather than measuring the modal themselves.
      </p>
    </Modal>
    <Modal :is-open="modalTallOpen" @close="modalTallOpen = false">
      <NavigationHeader title="Growing content" @back="modalTallOpen = false" />
      <ToggleSection title="Expand me" :enabled="innerOpen" @change="innerOpen = $event">
        <p class="modal-copy text-body">
          This is the path ToggleSection's inject exists for: the section animates its
          own 0fr to 1fr grid while the modal springs its container to the measured
          delta, so the two never fight.
        </p>
      </ToggleSection>
    </Modal>
  </GalleryItem>

  <GalleryItem id="NavigationHeader">
    <GalleryVariant label='variant="contrast" (default)' stacked>
      <NavigationHeader title="Radio" @back="backs++" />
      <NavigationHeader title="Radio Nova" subtitle="Paris, France" show-back icon="radio" @back="backs++" />
    </GalleryVariant>
    <GalleryVariant label='variant="background-neutral"' stacked>
      <NavigationHeader title="Settings" variant="background-neutral" @back="backs++" />
      <NavigationHeader title="Music Library" subtitle="1 284 albums" variant="background-neutral" show-back
        @back="backs++" />
    </GalleryVariant>
    <GalleryVariant label="actions slot — the slot prop carries the matching icon variant" stacked>
      <NavigationHeader title="Queue" show-back @back="backs++">
        <template #actions="{ iconVariant }">
          <IconButton icon="shuffle" :variant="iconVariant" />
          <IconButton icon="trash" :variant="iconVariant" />
        </template>
      </NavigationHeader>
      <NavigationHeader title="Queue" variant="background-neutral" show-back @back="backs++">
        <template #actions="{ iconVariant }">
          <IconButton icon="shuffle" :variant="iconVariant" />
          <IconButton icon="trash" :variant="iconVariant" />
        </template>
      </NavigationHeader>
    </GalleryVariant>
    <GalleryVariant :label="`titleMuted — back presses: ${backs}`" stacked>
      <NavigationHeader title="Nothing playing" title-muted @back="backs++" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="Dock" />

  <GalleryItem id="VolumeBar" />
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const modalOpen = ref(false);
const modalTallOpen = ref(false);
const innerOpen = ref(false);
const backs = ref(0);
</script>

<style scoped>
.modal-copy {
  margin: 0;
  padding: var(--space-04);
  color: var(--color-text-secondary);
}
</style>
