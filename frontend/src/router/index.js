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