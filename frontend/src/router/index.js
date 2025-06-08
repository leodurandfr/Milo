// frontend/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import HomeView from '@/views/HomeView.vue';
import MultiroomView from '@/views/MultiroomView.vue';

const routes = [
  {
    path: '/',
    name: 'home',
    component: HomeView,
    meta: {
      title: 'oakOS - Audio System'
    }
  },
  {
    path: '/multiroom',
    name: 'multiroom',
    component: MultiroomView,
    meta: {
      title: 'oakOS - Multiroom Control'
    }
  }
  // D'autres routes seront ajoutées ultérieurement
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