// frontend/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import LibrespotView from '@/views/LibrespotView.vue';
import BluetoothView from '@/views/BluetoothView.vue';
import RocView from '@/views/RocView.vue';

const routes = [
  {
    path: '/',
    redirect: '/librespot' // Redirection par défaut vers Librespot
  },
  {
    path: '/librespot',
    name: 'librespot',
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
  }
  // Plus de routes multiroom/equalizer - maintenant des modales
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