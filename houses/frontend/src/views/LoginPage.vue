<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const loginError = ref<string | null>(null)

onMounted(() => {
  // If already authenticated, go straight to properties
  if (auth.user) {
    router.push('/')
  }
})

// Auth state may resolve asynchronously after mount — redirect once it does
watch(() => auth.user, (user) => {
  if (user) {
    router.push('/')
  }
})

async function signIn() {
  loginError.value = null
  const result = await auth.login()
  if (!result.ok && result.error) {
    loginError.value = result.error
  }
}
</script>

<template>
  <main class="login-page">
    <div class="login-card">
      <h1 class="login-card__title">Houses</h1>
      <p class="login-card__desc">Sign in to continue</p>
      <button
        class="login-card__btn"
        :disabled="auth.loading"
        @click="signIn"
      >
        {{ auth.loading ? 'Loading…' : 'Sign in with Google' }}
      </button>
      <p v-if="loginError" class="login-card__error">{{ loginError }}</p>
    </div>
  </main>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--bg, #f5f5f5);
}

.login-card {
  background: #fff;
  border-radius: 12px;
  padding: 40px;
  text-align: center;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  max-width: 360px;
  width: 100%;
}

.login-card__title {
  font-size: 28px;
  font-weight: 700;
  margin: 0 0 8px;
  color: #1a1a1a;
}

.login-card__desc {
  font-size: 14px;
  color: #666;
  margin: 0 0 24px;
}

.login-card__btn {
  padding: 12px 24px;
  border-radius: 8px;
  border: none;
  background: #1a73e8;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
}

.login-card__btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-card__error {
  font-size: 13px;
  color: #999;
}
</style>
