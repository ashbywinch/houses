import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import PropertyList from '../views/PropertyList.vue'
import PropertyDetail from '../views/PropertyDetail.vue'
import LoginPage from '../views/LoginPage.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginPage },
    { path: '/', component: PropertyList, meta: { requiresAuth: true } },
    { path: '/property/:rid', component: PropertyDetail, meta: { requiresAuth: true } },
  ],
})

router.beforeEach(async (to) => {
  if (!to.meta.requiresAuth) return true

  const auth = useAuthStore()
  // Wait for auth check to complete if it hasn't yet
  if (auth.loading) {
    await auth.checkAuth()
  }
  if (!auth.user) {
    return '/login'
  }
  return true
})

export default router
