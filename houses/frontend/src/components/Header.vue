<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <header class="header">
    <div class="header__inner">
      <h1 class="header__title">{{ title }}</h1>
      <div class="header__actions">
        <slot name="actions" />
      </div>
    </div>
  </header>
</template>

<style scoped>
.header {
  background: var(--header-bg);
  color: #fff;
  position: sticky;
  top: 0;
  z-index: 10;
}
.header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  max-width: 1200px;
  margin: 0 auto;
}
.header__title {
  font-size: 20px;
  font-weight: 700;
}
.header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
