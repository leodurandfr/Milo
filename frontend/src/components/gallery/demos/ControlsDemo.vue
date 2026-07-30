<!-- frontend/src/components/gallery/demos/ControlsDemo.vue -->
<template>
  <GalleryItem id="Toggle">
    <GalleryVariant label="variant — primary / secondary">
      <Toggle v-model="primaryOn" />
      <Toggle v-model="secondaryOn" variant="secondary" />
    </GalleryVariant>
    <GalleryVariant label='size="compact" — what ListItemButton embeds'>
      <Toggle v-model="primaryOn" size="compact" />
      <Toggle v-model="secondaryOn" size="compact" variant="secondary" />
    </GalleryVariant>
    <GalleryVariant label="title + disabled" stacked>
      <Toggle v-model="primaryOn" title="Labelled toggle" />
      <Toggle :model-value="true" title="Disabled, on" disabled />
      <Toggle :model-value="false" title="Disabled, off" disabled />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="ToggleSection">
    <GalleryVariant :label="`enabled — ${sectionOn}`" stacked>
      <ToggleSection title="Compressor" :enabled="sectionOn" @change="sectionOn = $event">
        <p class="text-body">
          Content revealed by the header toggle. The 0fr to 1fr grid transition is the
          components own; a host Modal springs its container height in parallel.
        </p>
      </ToggleSection>
    </GalleryVariant>
    <GalleryVariant label='heading="3" + actions slot, no content' stacked>
      <ToggleSection title="Loudness" heading="3" :enabled="sectionAltOn" @change="sectionAltOn = $event">
        <template #actions>
          <span class="text-mono-small">40 dB</span>
        </template>
      </ToggleSection>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="Radio">
    <GalleryVariant label="a caller owns exclusivity — these three share one ref">
      <Radio :model-value="pick === 'a'" @update:model-value="pick = 'a'" />
      <Radio :model-value="pick === 'b'" @update:model-value="pick = 'b'" />
      <Radio :model-value="pick === 'c'" @update:model-value="pick = 'c'" />
      <span class="text-mono-small">{{ pick }}</span>
    </GalleryVariant>
    <GalleryVariant label="disabled">
      <Radio :model-value="true" disabled />
      <Radio :model-value="false" disabled />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="InputText">
    <GalleryVariant :label="`v-model — ${text || '(empty)'}`" stacked>
      <InputText v-model="text" placeholder="Type here" />
    </GalleryVariant>
    <GalleryVariant label='variant="background-neutral" + icon' stacked>
      <InputText v-model="search" variant="background-neutral" icon="search" placeholder="Search a station" />
    </GalleryVariant>
    <GalleryVariant label='type="password" + maxlength, then disabled' stacked>
      <InputText v-model="secret" type="password" :maxlength="16" placeholder="Wi-Fi password" />
      <InputText model-value="Read only" disabled />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="Dropdown">
    <GalleryVariant :label="`variant — outline / minimal / background-neutral (${country})`" stacked>
      <Dropdown v-model="country" :options="COUNTRY_OPTIONS" />
      <Dropdown v-model="country" :options="COUNTRY_OPTIONS" variant="minimal" />
      <Dropdown v-model="country" :options="COUNTRY_OPTIONS" variant="background-neutral" />
    </GalleryVariant>
    <GalleryVariant label='size="small" / placeholder on an empty value / disabled' stacked>
      <Dropdown v-model="country" :options="COUNTRY_OPTIONS" size="small" />
      <Dropdown v-model="unset" :options="COUNTRY_OPTIONS" placeholder="Pick a country" />
      <Dropdown v-model="country" :options="COUNTRY_OPTIONS" disabled />
    </GalleryVariant>
    <GalleryVariant label="displayOverride — a computed label over the raw value" stacked>
      <Dropdown v-model="country" :options="COUNTRY_OPTIONS" :display-override="`${country.toUpperCase()} selected`" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="RangeSlider">
    <GalleryVariant :label="`horizontal — ${level}`" stacked>
      <RangeSlider v-model="level" @drag-start="dragging = true" @drag-end="dragging = false" />
    </GalleryVariant>
    <GalleryVariant label="valueUnit / hideInlineValue / muted / disabled" stacked>
      <RangeSlider v-model="gain" :min="-40" :max="6" :step="0.5" value-unit="dB" />
      <RangeSlider v-model="level" hide-inline-value />
      <RangeSlider v-model="level" muted />
      <RangeSlider v-model="level" disabled />
    </GalleryVariant>
    <GalleryVariant :label="`orientation=&quot;vertical&quot; — dragging: ${dragging}`">
      <div class="vertical-slot">
        <RangeSlider v-model="level" orientation="vertical" @drag-start="dragging = true"
          @drag-end="dragging = false" />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="DoubleRangeSlider">
    <GalleryVariant :label="`min ${band.min} / max ${band.max}, gap floor 10`" stacked>
      <DoubleRangeSlider v-model="band" />
    </GalleryVariant>
    <GalleryVariant label="a crossover range in Hz, gap 200" stacked>
      <DoubleRangeSlider v-model="crossover" :min="20" :max="20000" :step="10" :gap="200" value-unit="Hz" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="VirtualKeyboard" />
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import Toggle from '@/components/ui/Toggle.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import Radio from '@/components/ui/Radio.vue';
import InputText from '@/components/ui/InputText.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';

const COUNTRY_OPTIONS = [
  { label: 'France', value: 'fr' },
  { label: 'Germany', value: 'de' },
  { label: 'United Kingdom', value: 'gb' }
];

const primaryOn = ref(true);
const secondaryOn = ref(false);
const sectionOn = ref(true);
const sectionAltOn = ref(false);
const pick = ref('a');
const text = ref('');
const search = ref('');
const secret = ref('');
const country = ref('fr');
const unset = ref('');
const level = ref(60);
const gain = ref(-6);
const dragging = ref(false);
const band = ref({ min: 20, max: 80 });
const crossover = ref({ min: 80, max: 2000 });
</script>

<style scoped>
/* The vertical orientation stretches to its container, so it needs a flex one —
   and one at least as tall as the track's own 260px floor, or it overflows. */
.vertical-slot {
  display: flex;
  flex-direction: column;
  height: 280px;
}
</style>
