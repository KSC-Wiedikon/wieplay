import './lib/legacy' // first: moves settings saved under the old name (Nomal)
import '@fontsource/bricolage-grotesque/latin-800.css'
import './index.css'
import './i18n'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { ToastProvider } from './components/ToastProvider'
import { createQueryClient } from './lib/queryClient'
import { initTheme } from './lib/theme'
import { createRouter } from './router'

initTheme()

const container = document.getElementById('root')
if (!container) throw new Error('#root is missing from index.html')

const queryClient = createQueryClient()
const router = createRouter()

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
