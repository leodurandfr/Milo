<!-- frontend/src/components/dsp/FrequencyResponseGraph.vue -->
<!-- SVG frequency response visualization for parametric EQ -->
<template>
  <div class="frequency-response-graph" :class="{ mobile: isMobile }">
    <svg :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="xMidYMid meet">
      <!-- Grid lines -->
      <g class="grid">
        <!-- Horizontal grid (dB) -->
        <line
          v-for="db in dbGridLines"
          :key="'h' + db"
          :x1="padding.left"
          :x2="width - padding.right"
          :y1="dbToY(db)"
          :y2="dbToY(db)"
          class="grid-line"
          :class="{ 'zero-line': db === 0 }"
        />

        <!-- Vertical grid (frequency) -->
        <line
          v-for="freq in freqGridLines"
          :key="'v' + freq"
          :x1="freqToX(freq)"
          :x2="freqToX(freq)"
          :y1="padding.top"
          :y2="height - padding.bottom"
          class="grid-line"
        />
      </g>

      <!-- Frequency labels -->
      <g class="labels freq-labels">
        <text
          v-for="freq in freqLabels"
          :key="'fl' + freq"
          :x="freqToX(freq)"
          :y="height - padding.bottom + 14"
          text-anchor="middle"
          class="label-text"
        >
          {{ formatFreq(freq) }}
        </text>
      </g>

      <!-- dB labels -->
      <g class="labels db-labels">
        <text
          v-for="db in dbLabels"
          :key="'dl' + db"
          :x="padding.left - 6"
          :y="dbToY(db) + 4"
          text-anchor="end"
          class="label-text"
        >
          {{ db > 0 ? '+' : '' }}{{ db }}
        </text>
      </g>

      <!-- Combined response curve -->
      <path :d="responsePath" class="response-curve" />

      <!-- Filter point indicators -->
      <circle
        v-for="filter in activeFilters"
        :key="filter.id"
        :cx="freqToX(filter.freq)"
        :cy="dbToY(filter.gain)"
        r="5"
        class="filter-point"
      />
    </svg>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  filters: {
    type: Array,
    required: true
  },
  sampleRate: {
    type: Number,
    default: 48000
  },
  isMobile: {
    type: Boolean,
    default: false
  }
});

// SVG dimensions
const width = 600;
const height = 180;
const padding = { top: 15, right: 15, bottom: 25, left: 35 };

// Grid configuration
const freqGridLines = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
const freqLabels = [20, 100, 1000, 10000];
const dbGridLines = [-15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15];
const dbLabels = [-15, 0, 15];

// Active filters (non-zero gain)
const activeFilters = computed(() => {
  return props.filters.filter(f => f.gain !== 0 && f.enabled !== false);
});

// Logarithmic frequency to X position
function freqToX(freq) {
  const minLog = Math.log10(20);
  const maxLog = Math.log10(20000);
  const logFreq = Math.log10(Math.max(20, Math.min(20000, freq)));
  const normalized = (logFreq - minLog) / (maxLog - minLog);
  return padding.left + normalized * (width - padding.left - padding.right);
}

// Linear dB to Y position
function dbToY(db) {
  const clampedDb = Math.max(-15, Math.min(15, db));
  const normalized = (clampedDb + 15) / 30;
  return height - padding.bottom - normalized * (height - padding.top - padding.bottom);
}

// Format frequency for display
function formatFreq(freq) {
  if (freq >= 1000) {
    return `${freq / 1000}k`;
  }
  return freq.toString();
}

// Calculate Biquad filter magnitude response at a given frequency
function calculateBiquadResponse(freq, filterFreq, gain, q, type) {
  if (gain === 0) return 0;

  const sampleRate = props.sampleRate;
  const w0 = 2 * Math.PI * filterFreq / sampleRate;
  const A = Math.pow(10, gain / 40);
  const alpha = Math.sin(w0) / (2 * q);

  let b0, b1, b2, a0, a1, a2;

  // Normalize type string
  const filterType = (type || 'Peaking').toLowerCase();

  switch (filterType) {
    case 'peaking':
      b0 = 1 + alpha * A;
      b1 = -2 * Math.cos(w0);
      b2 = 1 - alpha * A;
      a0 = 1 + alpha / A;
      a1 = -2 * Math.cos(w0);
      a2 = 1 - alpha / A;
      break;

    case 'lowshelf': {
      const sqrtA = Math.sqrt(A);
      const sqrtA2alpha = 2 * sqrtA * alpha;
      b0 = A * ((A + 1) - (A - 1) * Math.cos(w0) + sqrtA2alpha);
      b1 = 2 * A * ((A - 1) - (A + 1) * Math.cos(w0));
      b2 = A * ((A + 1) - (A - 1) * Math.cos(w0) - sqrtA2alpha);
      a0 = (A + 1) + (A - 1) * Math.cos(w0) + sqrtA2alpha;
      a1 = -2 * ((A - 1) + (A + 1) * Math.cos(w0));
      a2 = (A + 1) + (A - 1) * Math.cos(w0) - sqrtA2alpha;
      break;
    }

    case 'highshelf': {
      const sqrtA = Math.sqrt(A);
      const sqrtA2alpha = 2 * sqrtA * alpha;
      b0 = A * ((A + 1) + (A - 1) * Math.cos(w0) + sqrtA2alpha);
      b1 = -2 * A * ((A - 1) + (A + 1) * Math.cos(w0));
      b2 = A * ((A + 1) + (A - 1) * Math.cos(w0) - sqrtA2alpha);
      a0 = (A + 1) - (A - 1) * Math.cos(w0) + sqrtA2alpha;
      a1 = 2 * ((A - 1) - (A + 1) * Math.cos(w0));
      a2 = (A + 1) - (A - 1) * Math.cos(w0) - sqrtA2alpha;
      break;
    }

    default:
      return 0;
  }

  // Calculate magnitude at frequency
  const w = 2 * Math.PI * freq / sampleRate;
  const phi = Math.pow(Math.sin(w / 2), 2);

  const num = Math.pow(b0 + b1 + b2, 2) - 4 * (b0 * b1 + 4 * b0 * b2 + b1 * b2) * phi + 16 * b0 * b2 * phi * phi;
  const den = Math.pow(a0 + a1 + a2, 2) - 4 * (a0 * a1 + 4 * a0 * a2 + a1 * a2) * phi + 16 * a0 * a2 * phi * phi;

  if (den <= 0) return 0;

  return 10 * Math.log10(num / den);
}

// Generate combined response curve path
const responsePath = computed(() => {
  const points = [];
  const numPoints = 200;

  for (let i = 0; i <= numPoints; i++) {
    const t = i / numPoints;
    // Logarithmic distribution from 20Hz to 20kHz
    const freq = 20 * Math.pow(1000, t);

    let totalDb = 0;
    for (const filter of props.filters) {
      if (filter.enabled !== false && filter.gain !== 0) {
        const response = calculateBiquadResponse(
          freq,
          filter.freq,
          filter.gain,
          filter.q || 1.41,
          filter.type || 'Peaking'
        );
        totalDb += response;
      }
    }

    // Clamp to visible range
    totalDb = Math.max(-15, Math.min(15, totalDb));

    points.push({ x: freqToX(freq), y: dbToY(totalDb) });
  }

  // Generate SVG path
  if (points.length === 0) return '';

  return points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
});
</script>

<style scoped>
.frequency-response-graph {
  width: 100%;
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  padding: var(--space-02);
}

.frequency-response-graph svg {
  width: 100%;
  height: auto;
  display: block;
}

.grid-line {
  stroke: var(--color-border);
  stroke-width: 0.5;
  opacity: 0.5;
}

.zero-line {
  stroke: var(--color-text-light);
  stroke-width: 1;
  opacity: 0.8;
}

.label-text {
  fill: var(--color-text-light);
  font-family: 'Space Mono Regular', monospace;
  font-size: 9px;
}

.response-curve {
  fill: none;
  stroke: var(--color-brand);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.filter-point {
  fill: var(--color-brand);
  stroke: var(--color-background-neutral);
  stroke-width: 2;
}

/* Mobile adjustments */
.frequency-response-graph.mobile {
  padding: var(--space-01);
}

.frequency-response-graph.mobile .label-text {
  font-size: 8px;
}

@media (max-aspect-ratio: 4/3) {
  .frequency-response-graph {
    padding: var(--space-01);
  }

  .label-text {
    font-size: 8px;
  }
}
</style>
