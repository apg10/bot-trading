import './styles/layout.css'
import App from './App'

const rootEl = document.getElementById('root')
if (rootEl) {
  rootEl.innerHTML = ''
  // Simple mount — React will hydrate in next step
  const placeholder = document.createElement('div')
  placeholder.textContent = 'Cargando terminal...'
  rootEl.appendChild(placeholder)
}
