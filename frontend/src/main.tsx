import './styles/layout.css'
import App from './App'
import { createRoot } from 'react-dom/client'

const rootEl = document.getElementById('root')
if (rootEl) {
  const root = createRoot(rootEl)
  root.render(<App />)
}
