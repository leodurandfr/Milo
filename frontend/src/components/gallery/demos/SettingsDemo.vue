<!-- frontend/src/components/gallery/demos/SettingsDemo.vue -->
<template>
  <GalleryItem id="SectionStack">
    <GalleryVariant label="its whole contribution: one gap between sections" stacked>
      <SectionStack>
        <SectionCard title="Volume">
          <SettingItem label="Startup volume">
            <ControlSample />
          </SettingItem>
        </SectionCard>
        <SectionCard title="Screen">
          <SettingItem label="Brightness">
            <ControlSample />
          </SettingItem>
        </SectionCard>
      </SectionStack>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SectionCard">
    <GalleryVariant label=":title — the built-in heading" stacked>
      <SectionCard title="Volume">
        <SettingItem label="Startup volume">
          <ControlSample />
        </SettingItem>
      </SectionCard>
    </GalleryVariant>
    <GalleryVariant label="header slot — replaces the title, which is why both are never passed" stacked>
      <SectionCard title="Ignored while the slot is filled">
        <template #header>
          <SectionHeader title="Volume" subtitle="Startup level and limits" />
        </template>
        <SettingItem label="Startup volume">
          <ControlSample />
        </SettingItem>
      </SectionCard>
    </GalleryVariant>
    <GalleryVariant label="no title at all — a card with only its content" stacked>
      <SectionCard>
        <ControlSample />
      </SectionCard>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="ToggleSection">
    <GalleryVariant :label="`enabled — ${sectionOn}`" stacked>
      <ToggleSection title="Auto-stop" :enabled="sectionOn" @change="sectionOn = $event">
        <SettingItem label="Stop after a pause of">
          <ControlSample />
        </SettingItem>
      </ToggleSection>
    </GalleryVariant>
    <GalleryVariant label=":description + :disabled — what Hardware shows while it reboots" stacked>
      <ToggleSection title="Power button" description="Wakes and shuts down the unit." :enabled="true" disabled>
        <SettingItem label="LED −">
          <ControlSample />
        </SettingItem>
      </ToggleSection>
    </GalleryVariant>
    <GalleryVariant label='heading="3" + actions slot, no content — the toggle alone' stacked>
      <ToggleSection title="Loudness" heading="3" :enabled="sectionAltOn" @change="sectionAltOn = $event">
        <template #actions>
          <span class="text-mono-small">40 dB</span>
        </template>
      </ToggleSection>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SettingItem">
    <GalleryVariant label=":label — mono, secondary, above the control" stacked>
      <SettingItem label="Startup volume">
        <ControlSample />
      </SettingItem>
    </GalleryVariant>
    <GalleryVariant label="no label — the wrapper alone, for a full-width control" stacked>
      <SettingItem>
        <ControlSample />
      </SettingItem>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SectionHeader">
    <GalleryVariant label=":title" stacked>
      <SectionHeader title="Stations" />
    </GalleryVariant>
    <GalleryVariant label=":title + :subtitle" stacked>
      <SectionHeader title="Stations" subtitle="24 saved" />
    </GalleryVariant>
    <GalleryVariant label="actions slot — right-aligned, and it stacks below 4:3" stacked>
      <SectionHeader title="Stations" subtitle="24 saved">
        <template #actions>
          <Button size="small" variant="tinted">Add</Button>
        </template>
      </SectionHeader>
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import ControlSample from '../samples/ControlSample.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import SectionHeader from '@/components/ui/SectionHeader.vue';
import Button from '@/components/ui/Button.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const sectionOn = ref(true);
const sectionAltOn = ref(false);
</script>
