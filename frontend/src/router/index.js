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
