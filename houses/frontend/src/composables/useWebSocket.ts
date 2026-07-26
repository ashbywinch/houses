import { ref, onUnmounted, getCurrentInstance } from 'vue'
import { usePropertiesStore } from '../stores/properties'

const MAX_RETRIES = 10
const BASE_DELAY = 1000
let retries = 0
let timer: ReturnType<typeof setTimeout> | null = null
let wsFactory = (url: string) => new WebSocket(url)

export function useWebSocket(factory?: (url: string) => WebSocket) {
  if (factory) wsFactory = factory

  const connected = ref(false)
  const store = usePropertiesStore()
  let ws: WebSocket | null = null

  function connect(url: string) {
    ws = wsFactory(url)
    ws.onopen = () => { connected.value = true; retries = 0 }
    ws.onclose = () => {
      connected.value = false
      if (retries < MAX_RETRIES) {
        const delay = BASE_DELAY * Math.pow(2, retries)
        timer = setTimeout(() => connect(url), delay + Math.random() * 1000)
        retries++
      }
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'property_updated' && msg.rid) {
          store.updateSummary(msg.rid, msg.data)
          // Extract triage from response using same pattern as loadAll()
          const t = msg.data?.triage
          if (t) {
            store.triage[msg.rid] = {
              favourite: t.favourite?.value ?? false,
              dismissed: t.dismissed?.value ?? false,
              is_viewed: t.is_viewed?.value ?? false,
              user_notes: t.user_notes?.value ?? '',
              triage_status: t.triage_status?.value ?? '',
            }
          }
          // Ensure rid is in the list
          if (!store.rids.includes(msg.rid)) {
            store.rids.push(msg.rid)
          }
        }
      } catch {
        // ignore parse errors
      }
    }
  }

  function disconnect() {
    ws?.close()
    ws = null
    connected.value = false
    if (timer) clearTimeout(timer)
  }

  if (getCurrentInstance()) onUnmounted(disconnect)

  return { connected, connect, disconnect }
}
