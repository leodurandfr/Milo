// frontend/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import MainView from '@/views/MainView.vue';
import StyleGuide from '@/views/StyleGuide.vue';
import CardsStyleGuide from '@/views/CardsStyleGuide.vue';

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
    path: '/style-guide',
    name: 'StyleGuide',
    component: StyleGuide,
    meta: {
      title: 'Style Guide - Milō'
    }
  },
  {
    path: '/cards-style-guide',
    name: 'CardsStyleGuide',
    component: CardsStyleGuide,
    meta: {
      title: 'Cards Style Guide - Milō'
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