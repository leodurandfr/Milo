// frontend/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import LibrespotView from '@/views/LibrespotView.vue';
import BluetoothView from '@/views/BluetoothView.vue';
import RocView from '@/views/RocView.vue';
import MultiroomView from '@/views/MultiroomView.vue';
import EqualizerView from '@/views/EqualizerView.vue';

const routes = [
  {
    path: '/',
    redirect: '/spotify' // Redirection par défaut vers Spotify
  },
  {
    path: '/spotify',
    name: 'spotify',
    component: LibrespotView,
    meta: {
      title: 'oakOS - Spotify'
    }
  },
  {
    path: '/bluetooth',
    name: 'bluetooth',
    component: BluetoothView,
    meta: {
      title: 'oakOS - Bluetooth'
    }
  },
  {
    path: '/roc',
    name: 'roc',
    component: RocView,
    meta: {
      title: 'oakOS - ROC for Mac'
    }
  },
  {
    path: '/multiroom',
    name: 'multiroom',
    component: MultiroomView,
    meta: {
      title: 'oakOS - Multiroom Control'
    }
  },
  {
    path: '/equalizer',
    name: 'equalizer',
    component: EqualizerView,
    meta: {
      title: 'oakOS - Equalizer'
    }
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

// Mise à jour du titre de la page
router.beforeEach((to, from, next) => {
  if (to.meta.title) {
    document.title = to.meta.title;
  }
  next();
});

export default router;