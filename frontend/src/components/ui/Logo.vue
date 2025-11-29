<!-- Logo.vue - Composant simplifié avec 3 états -->
<template>
  <div class="logo-container" :class="`logo-${state}`">
    <svg
      xmlns="http://www.w3.org/2000/svg"
      class="logo-svg"
      viewBox="0 0 57 32"
      preserveAspectRatio="xMidYMid meet"
    >
      <g fill="#A6ACA6">
        <path d="M48.833 12.1421C51.043 12.1421 52.863 12.8181 54.293 14.1441C55.723 15.4701 56.451 17.1601 56.451 19.2141C56.451 21.2681 55.723 22.9581 54.267 24.2841C52.837 25.6101 51.017 26.2861 48.833 26.2861C46.623 26.2861 44.777 25.6361 43.347 24.3101C41.917 22.9841 41.189 21.2681 41.189 19.2141C41.189 17.1601 41.917 15.4701 43.347 14.1441C44.777 12.8181 46.623 12.1421 48.833 12.1421ZM48.833 14.1701C47.247 14.1701 45.947 14.6381 44.933 15.5741C43.919 16.5101 43.425 17.7321 43.425 19.2141C43.425 20.6961 43.919 21.9181 44.933 22.8541C45.947 23.7901 47.247 24.2581 48.833 24.2581C50.393 24.2581 51.667 23.7901 52.681 22.8541C53.695 21.9181 54.189 20.6961 54.189 19.2141C54.189 17.7321 53.695 16.5101 52.681 15.5741C51.667 14.6381 50.393 14.1701 48.833 14.1701ZM53.123 8.37207V10.2441H44.491V8.37207H53.123Z"/>
        <path d="M35.3462 7.33203H37.6862V26H35.3462V7.33203Z"/>
        <path d="M31.0373 10.036H28.4893V7.33203H31.0373V10.036ZM28.5933 12.402H30.9333V26H28.5933V12.402Z"/>
        <path d="M21.542 9.59403L13.482 26H11.4019L3.31595 9.59403V26H0.897949V7.33203H4.77195L12.4679 23.036L20.19 7.33203H23.9599V26H21.542V9.59403Z"/>
      </g>
    </svg>
  </div>
</template>

<script setup>
defineProps({
  state: {
    type: String,
    default: 'center',
    validator: (value) => ['center', 'top', 'hidden'].includes(value)
  }
});
</script>

<style scoped>
.logo-container {
  position: fixed;
  left: 50%;
  z-index: 100;
  pointer-events: none;
  transition: all var(--transition-spring);
}

.logo-svg {
  display: block;
  height: 32px;
}

/* === État CENTER - Logo grand, centré === */
.logo-center {
  top: 50%;
  transform: translate(-50%, -50%);
  opacity: 1;
}

.logo-center .logo-svg {
  height: 48px;
  width: auto;
}

/* === État TOP - Logo petit, en haut === */
.logo-top {
  top: var(--space-06);
  transform: translateX(-50%);
  opacity: 1;
}

.logo-top .logo-svg {
  width: auto;
}

/* === État HIDDEN - Disparition vers le haut === */
.logo-hidden {
  top: var(--space-06);
  opacity: 0;
  transform: translateX(-50%) translateY(calc( -1 * var(--space-05)));
}

.logo-hidden .logo-svg {
  width: auto;
}

/* === RESPONSIVE - MOBILE === */
@media (max-aspect-ratio: 4/3) {

  /* .logo-hidden {
    transform: translateX(-50%) translateY(calc( -1 * var(--space-04)));
  } */
}

/* === iOS === */
.ios-app .logo-top,
.ios-app .logo-hidden {
  top: var(--space-09);
}
</style>
