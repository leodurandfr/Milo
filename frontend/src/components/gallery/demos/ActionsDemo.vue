<!-- frontend/src/components/gallery/demos/ActionsDemo.vue -->
<template>
  <GalleryItem id="Button">
    <GalleryVariant label="variant — on a light surface">
      <Button v-for="v in LIGHT_BUTTON_VARIANTS" :key="v" :variant="v" @click="clicks++">{{ v }}</Button>
    </GalleryVariant>
    <GalleryVariant label="variant — on a dark surface">
      <div class="dark-strip">
        <Button variant="on-dark" @click="clicks++">on-dark</Button>
      </div>
    </GalleryVariant>
    <GalleryVariant label='size="small"'>
      <Button variant="brand" size="small" @click="clicks++">brand</Button>
      <Button size="small" @click="clicks++">background-strong</Button>
      <Button variant="outline" size="small" @click="clicks++">outline</Button>
    </GalleryVariant>
    <GalleryVariant label="leftIcon">
      <Button left-icon="heart" variant="brand" @click="clicks++">Favourite</Button>
      <Button left-icon="trash" variant="important" @click="clicks++">Delete</Button>
      <Button left-icon="arrowClockwise" size="small" @click="clicks++">Rescan</Button>
    </GalleryVariant>
    <GalleryVariant label="states">
      <Button disabled>disabled</Button>
      <Button variant="brand" loading>loading</Button>
      <Button variant="brand" loading :loading-label="false">hidden label</Button>
      <Button variant="brand" loading disabled>loading + disabled</Button>
    </GalleryVariant>
    <p class="counter text-mono">click count: {{ clicks }}</p>
  </GalleryItem>

  <GalleryItem id="IconButton">
    <GalleryVariant label="variant — on a light surface">
      <IconButton icon="play" variant="background-strong" @click="clicks++" />
      <IconButton icon="play" variant="rounded" @click="clicks++" />
      <IconButton icon="play" variant="brand" @click="clicks++" />
    </GalleryVariant>
    <GalleryVariant label="variant — on a dark surface">
      <div class="dark-strip">
        <IconButton icon="play" variant="on-dark" @click="clicks++" />
        <IconButton icon="play" variant="ghost" @click="clicks++" />
      </div>
    </GalleryVariant>
    <GalleryVariant label="variant — on a mid-tone surface, where on-grey is used (CD artwork, mobile)">
      <div class="mid-strip">
        <IconButton icon="play" variant="on-grey" @click="clicks++" />
        <IconButton icon="play" variant="on-dark" @click="clicks++" />
      </div>
    </GalleryVariant>
    <GalleryVariant label="size">
      <IconButton icon="next" size="small" @click="clicks++" />
      <IconButton icon="next" size="medium" @click="clicks++" />
      <IconButton icon="next" size="large" @click="clicks++" />
    </GalleryVariant>
    <GalleryVariant label="states + color override">
      <IconButton icon="pause" disabled />
      <IconButton icon="pause" loading />
      <IconButton icon="heart" color="var(--color-brand)" @click="clicks++" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="ButtonGroup">
    <GalleryVariant :label="`v-model — ${quality}`" stacked>
      <ButtonGroup v-model="quality" :options="QUALITY_OPTIONS" />
    </GalleryVariant>
    <GalleryVariant label='size="small" + inactiveVariant="background-neutral"' stacked>
      <ButtonGroup v-model="quality" :options="QUALITY_OPTIONS" size="small" inactive-variant="background-neutral" />
    </GalleryVariant>
    <GalleryVariant label="a disabled option, then the whole group disabled" stacked>
      <ButtonGroup v-model="preset" :options="PRESET_OPTIONS" />
      <ButtonGroup v-model="preset" :options="PRESET_OPTIONS" disabled />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="ListItemButton">
    <GalleryVariant label='action="none" / "caret"' stacked>
      <ListItemButton title="Plain row" @click="clicks++" />
      <ListItemButton title="Drill down" subtitle="A subtitle stacks under the title" action="caret" @click="clicks++" />
    </GalleryVariant>
    <GalleryVariant label='action="toggle" / "radio" — both driven by modelValue' stacked>
      <ListItemButton title="Loudness" action="toggle" :model-value="loudness" @click="loudness = !loudness" />
      <ListItemButton title="Balanced" action="radio" :model-value="picked === 'balanced'"
        @click="picked = 'balanced'" />
      <ListItemButton title="Responsive" action="radio" :model-value="picked === 'responsive'"
        @click="picked = 'responsive'" />
    </GalleryVariant>
    <GalleryVariant label="icon slot" stacked>
      <ListItemButton title="Radio" subtitle="With an icon in the leading slot" action="caret" @click="clicks++">
        <template #icon>
          <AppIcon name="radio" :size="32" />
        </template>
      </ListItemButton>
    </GalleryVariant>
    <GalleryVariant label='variant="background" / interactive="false" / disabled' stacked>
      <ListItemButton title="On the app background" variant="background" action="caret" @click="clicks++" />
      <ListItemButton title="Read-only row" subtitle="No button semantics, no press" :interactive="false" />
      <ListItemButton title="Disabled" action="toggle" :model-value="false" disabled />
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import AppIcon from '@/components/ui/AppIcon.vue';

// Split by backdrop, not alphabetically. Six of Button's seven variants are
// self-coloured; `on-dark` is translucent and only legible over the tone it was
// drawn for, so judging it on the light stage is how a variant gets called broken.
const LIGHT_BUTTON_VARIANTS = ['background-strong', 'background-neutral', 'brand', 'outline', 'outline-neutral', 'important'];

const QUALITY_OPTIONS = [
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' }
];

const PRESET_OPTIONS = [
  { label: 'Balanced', value: 'balanced' },
  { label: 'Responsive', value: 'responsive' },
  { label: 'Unavailable', value: 'off', disabled: true }
];

const clicks = ref(0);
const quality = ref('medium');
const preset = ref('balanced');
const loudness = ref(true);
const picked = ref('balanced');
</script>

<style scoped>
.dark-strip,
.mid-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-02);
  width: 100%;
  padding: var(--space-03);
  border-radius: var(--radius-03);
}

.dark-strip {
  background: var(--color-background-contrast);
}

/* The app's own scrim tone — what a translucent variant sits on over artwork. */
.mid-strip {
  background: var(--color-background-medium-32);
}

.counter {
  margin: 0;
  color: var(--color-text-light);
}
</style>
