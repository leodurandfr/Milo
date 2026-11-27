// frontend/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import MainView from '@/views/MainView.vue';

const routes = [
  {
    path: '/',
    name: 'main',
    component: MainView,
    meta: {
      title: 'Milō'
    }
  },
  {
    // The primitive gallery. Lazily imported so it lands in its own chunk and
    // an end user who never opens the URL pays nothing for it. `chrome: false`
    // drops the Dock and the warm colour filter: this is a reference page read
    // from a computer, and both would sit over the component being judged.
    path: '/components',
    name: 'components',
    component: () => import('@/views/ComponentsView.vue'),
    meta: {
      title: 'Milō — Components',
      chrome: false
    }
  },
  {
    // The logs page (Settings › System). Lazily imported for the same reason
    // as the gallery: whoever never opens it pays nothing. A full page with no
    // chrome — no Dock over its last rows, no modal over it (App.vue renders
    // none on a chrome-less route) — read as much from a phone as from the
    // panel; it carries its own way back.
    path: '/logs',
    name: 'logs',
    component: () => import('@/views/LogsView.vue'),
    meta: {
      title: 'Milō — Logs',
      chrome: false
    }
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

router.beforeEach((to, from, next) => {
  if (to.meta.title) {
    document.title = to.meta.title;
  }
  next();
});

export default router;
