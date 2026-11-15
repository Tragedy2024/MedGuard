import { apiGet } from './client'
import type { HealthInfo } from './models'

export const fetchHealth = () => apiGet<HealthInfo>('/api/health')
