import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '../services/api'

export interface AuthUser {
  email: string
  name: string
  picture: string
  person: string | null
  person_id: string | null
  is_superuser: boolean
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const loading = ref(true)

  // Superuser mode — when active, a header bar shows with an impersonation dropdown
  const superuserMode = ref(false)
  const impersonating = ref<string | null>(null) // Person name being impersonated

  /** The person the session is ACTING AS — the single resolution point.
   *  Every view reads this one getter; no view re-derives the
   *  precedence (impersonated person while a superuser impersonates,
   *  else the session's linked person). */
  const actingAs = computed<string | null>(() => {
    if (superuserMode.value && impersonating.value) return impersonating.value
    return user.value?.person_id ?? null
  })

  /** The one place that decides "the wire must impersonate": the mode
   *  AND an impersonated person. authHeaders() reads only this — a
   *  cookie-borne impersonating claim with the mode off must never
   *  reach the server (silent impersonation after reload). */
  const isImpersonating = computed(() => superuserMode.value && impersonating.value !== null)

  let _pendingCheck: Promise<void> | null = null

  async function checkAuth() {
    if (_pendingCheck) return _pendingCheck
    _pendingCheck = _doCheck()
    try {
      return await _pendingCheck
    } finally {
      _pendingCheck = null
    }
  }

  async function _doCheck() {
    try {
      const r = await fetch('/api/auth/me')
      if (!r.ok) {
        console.error('Auth check failed:', r.status)
        loading.value = false
        return  // keep current user state on transient errors
      }
      const data = await r.json()
      if (data.authenticated) {
        user.value = data
        if (!data.is_superuser) {
          // Exit superuser mode if user is no longer a superuser
          superuserMode.value = false
          impersonating.value = null
        } else {
          // The session claim survives restarts — adopt it. The gate
          // (isImpersonating) keeps it inert until the mode is on.
          impersonating.value = data.impersonating ?? null
        }
      } else {
        user.value = null
      }
    } catch (e) {
      console.error('Auth check exception:', e)
      // keep current user state on transient errors
    } finally {
      loading.value = false
    }
  }

  function toggleSuperuser() {
    superuserMode.value = !superuserMode.value
    if (!superuserMode.value) {
      impersonating.value = null
    }
  }

  async function setImpersonating(person: string | null) {
    const resp = await api.impersonate(person)
    if (!resp.ok) {
      console.error('Failed to update impersonation on server')
      return
    }
    impersonating.value = person
  }

  async function login(): Promise<{ ok: boolean; error?: string }> {
    try {
      const r = await fetch('/api/auth/login')
      if (!r.ok) {
        console.error('Auth login failed:', r.status)
        return { ok: false, error: 'Sign in is unavailable. Please try again later.' }
      }
      const data = await r.json()
      if (data.status === 'error') {
        console.error('Auth login error:', data.detail)
        return { ok: false, error: 'Sign in is unavailable. Please try again later.' }
      }
      if (data.auth_url) {
        window.location.href = data.auth_url
        return { ok: true }
      }
      return { ok: true }
    } catch (e) {
      console.error('Auth login exception:', e)
      return { ok: false, error: 'Could not connect to the server.' }
    }
  }

  async function logout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
    } catch {
      console.error('Logout request failed — clearing local state anyway')
    } finally {
      user.value = null
      superuserMode.value = false
      impersonating.value = null
    }
  }

  return {
    user,
    loading,
    superuserMode,
    impersonating,
    actingAs,
    isImpersonating,
    toggleSuperuser,
    setImpersonating,
    checkAuth,
    login,
    logout,
  }
})
