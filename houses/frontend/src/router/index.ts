import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import PropertyList from '../views/PropertyList.vue'

// The index is eager — it IS the entry screen. Every other view is a
// dynamic import so Vite emits it as its own chunk instead of inflating
// the entry bundle.
const PropertyDetail = () => import('../views/PropertyDetail.vue')
const LoginPage = () => import('../views/LoginPage.vue')
const SettingsView = () => import('../views/SettingsView.vue')

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginPage },
    { path: '/', component: PropertyList, meta: { requiresAuth: true } },
    { path: '/property/:rid', component: PropertyDetail, meta: { requiresAuth: true } },
    { path: '/settings', component: SettingsView, meta: { requiresAuth: true } },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.requiresAuth) return true

  const auth = useAuthStore()
  if (auth.user) return true
  if (!auth.loading) return '/login'

  // /api/auth/me must not gate the first paint: start the check, let
  // the route through now — the views hold their property data back
  // until it resolves — and send an unauthenticated visitor to /login
  // the moment the answer lands. (App.vue starts the same check on
  // mount; the store de-dupes it into one request.)
  void auth.checkAuth().then(() => {
    if (!auth.user) return router.replace('/login')
  })
  return true
})

export default router
