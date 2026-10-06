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
  // the route through now — views that need the answer await the same
  // check before fetching (SettingsView does) — and send an
  // unauthenticated visitor to /login the moment it lands. (App.vue
  // starts the same check on mount; the store settles it once per app
  // load and shares the answer, so neither caller re-requests it.)
  void auth.checkAuth().then(() => {
    if (!auth.user) return router.replace('/login')
  })
  return true
})

export default router
