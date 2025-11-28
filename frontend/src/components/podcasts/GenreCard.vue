<template>
  <div class="genre-card" @click="$emit('click')">
    <div class="genre-image">
      <img :src="genreImage" :alt="label" class="genre-img" />
    </div>
    <p class="genre-label heading-4">{{ label }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

// Genre images
import comedyImg from '@/assets/podcasts/genres/comedy.jpg'
import societyCultureImg from '@/assets/podcasts/genres/society_and_culture.jpg'
import newsImg from '@/assets/podcasts/genres/news.jpg'
import trueCrimeImg from '@/assets/podcasts/genres/true_crime.jpg'
import businessImg from '@/assets/podcasts/genres/business.jpg'
import educationImg from '@/assets/podcasts/genres/education.jpg'
import healthImg from '@/assets/podcasts/genres/health_and_fitness.jpg'
import sportsImg from '@/assets/podcasts/genres/sports.jpg'
import artsImg from '@/assets/podcasts/genres/arts.jpg'
import scienceImg from '@/assets/podcasts/genres/science.jpg'
import tvFilmImg from '@/assets/podcasts/genres/tv_and_film.jpg'
import musicImg from '@/assets/podcasts/genres/music.jpg'

const GENRE_IMAGES = {
  'PODCASTSERIES_COMEDY': comedyImg,
  'PODCASTSERIES_SOCIETY_AND_CULTURE': societyCultureImg,
  'PODCASTSERIES_NEWS': newsImg,
  'PODCASTSERIES_TRUE_CRIME': trueCrimeImg,
  'PODCASTSERIES_BUSINESS': businessImg,
  'PODCASTSERIES_EDUCATION': educationImg,
  'PODCASTSERIES_HEALTH_AND_FITNESS': healthImg,
  'PODCASTSERIES_SPORTS': sportsImg,
  'PODCASTSERIES_ARTS': artsImg,
  'PODCASTSERIES_SCIENCE': scienceImg,
  'PODCASTSERIES_TV_AND_FILM': tvFilmImg,
  'PODCASTSERIES_MUSIC': musicImg
}

const props = defineProps({
  label: {
    type: String,
    required: true
  },
  value: {
    type: String,
    required: true
  }
})

defineEmits(['click'])

const genreImage = computed(() => GENRE_IMAGES[props.value])
</script>

<style scoped>
.genre-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-03) var(--space-04) var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.genre-image {
  aspect-ratio: 8 / 3;
  width: 100%;
  border-radius: var(--radius-03);
  overflow: hidden;
}

.genre-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.genre-label {
  margin: 0;
  color: var(--color-text);
}
</style>
