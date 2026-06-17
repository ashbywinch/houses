import { createRouter, createWebHashHistory } from 'vue-router'
import PropertyList from '../views/PropertyList.vue'
import PropertyDetail from '../views/PropertyDetail.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: PropertyList },
    { path: '/property/:rid', component: PropertyDetail },
  ],
})
