<!-- ArtistNames.vue - An artist line drawn name by name: a name with a page to
     open is a link of its own, the separators and a name with none are text.
     Inline, so the line it sits in keeps its one-line ellipsis — which is also
     why a link dims when pressed rather than shrinking (an inline box takes no
     transform). Drawn by PlayerBody and DetailHeader. -->
<template>
  <span class="artist-names"><template v-for="(artist, index) in artists" :key="index"><template
    v-if="index">, </template><span v-if="artist.link" v-press role="button" tabindex="0" class="artist-name-link"
    @pointerdown.stop @click.stop="emit('open', index)" @keydown.enter.space.prevent.stop="emit('open', index)">{{
      artist.name }}</span><template v-else>{{ artist.name }}</template></template></span>
</template>

<script setup>
defineProps({
  // The line's names in its order: `{ name, link }`, `link` when it opens a page.
  artists: {
    type: Array,
    required: true
  }
});

// The index of the name pressed.
const emit = defineEmits(['open']);
</script>

<style scoped>
.artist-name-link {
  cursor: pointer;
}
</style>
