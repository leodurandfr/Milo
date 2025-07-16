// router/index.js - Phase 3 : Route unique simplifiée
import { createRouter, createWebHistory } from 'vue-router';
import MainView from '@/views/MainView.vue';

const routes = [
  {
    path: '/',
    name: 'main',
    component: MainView,
    meta: {
      title: 'oakOS'
    }
  },
  // Redirections pour compatibilité (optionnel, à supprimer plus tard)
  {
    path: '/bluetooth',
    redirect: '/'
  },
  {
    path: '/roc',
    redirect: '/'
  },
  {
    path: '/librespot',
    redirect: '/'
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