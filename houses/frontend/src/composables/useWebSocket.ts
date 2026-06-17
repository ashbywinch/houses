import { ref, onUnmounted } from 'vue'
import { usePropertiesStore } from '../stores/properties'

export function useWebSocket() {
  const connected = ref(false)
  const store = usePropertiesStore()
  let ws: WebSocket | null = null

  function connect(url: string) {
    ws = new WebSocket(url)
    ws.onopen = () => { connected.value = true }
    ws.onclose = () => { connected.value = false }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'property_updated' && msg.rid) {
          store.updateProperty(msg.rid, msg.data)
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
  }

  onUnmounted(disconnect)

  return { connected, connect, disconnect }
}
