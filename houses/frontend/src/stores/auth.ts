import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface AuthUser {
  email: string
  name: string
  picture: string
  person: string | null
  is_superuser: boolean
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const authAvailable = ref(false)
  const loading = ref(true)

  // Superuser mode — when active, a header bar shows with an impersonation dropdown
  const superuserMode = ref(false)
  const impersonating = ref<string | null>(null) // Person name being impersonated

  async function checkAuth() {
    try {
      const r = await fetch('/api/auth/me')
      if (!r.ok) {
        user.value = null
        authAvailable.value = false
        loading.value = false
        return
      }
      const data = await r.json()
      authAvailable.value = data.auth_available ?? false
      if (data.authenticated) {
        user.value = data
        // Exit superuser mode if user is no longer a superuser
        if (!data.is_superuser) {
          superuserMode.value = false
          impersonating.value = null
        }
      } else {
        user.value = null
      }
    } catch {
      user.value = null
      authAvailable.value = false
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

  function setImpersonating(person: string) {
    impersonating.value = person
  }

  async function login() {
    const r = await fetch('/api/auth/login')
    if (!r.ok) throw new Error('Auth unavailable')
    const data = await r.json()
    if (data.status === 'unconfigured') return false
    if (data.auth_url) {
      window.location.href = data.auth_url
    }
    return true
  }

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' })
    user.value = null
    superuserMode.value = false
    impersonating.value = null
  }

  return {
    user,
    authAvailable,
    loading,
    superuserMode,
    impersonating,
    toggleSuperuser,
    setImpersonating,
    checkAuth,
    login,
    logout,
  }
})
