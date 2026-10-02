import { createContext, useContext } from 'react'
import type { ApiClient } from './client'

// Injected rather than imported as a module singleton, so tests and the auth
// feature can supply their own client.
export const ApiClientContext = createContext<ApiClient | null>(null)

export function useApiClient(): ApiClient {
    const client = useContext(ApiClientContext)
    if (!client)
        throw new Error('useApiClient must be used inside ApiClientContext')
    return client
}
