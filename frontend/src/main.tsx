import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './fonts.css'
import './index.css'
import './design.css'
import './styles/base.css'
import './styles/console.css'
import './styles/home.css'
import './styles/login.css'
import './styles/overrides.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
