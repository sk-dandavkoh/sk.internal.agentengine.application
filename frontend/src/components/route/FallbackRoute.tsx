import appConfig from '@/configs/app.config'
import { useAuth } from '@/auth'
import { Navigate } from 'react-router'

const { authenticatedEntryPath, unAuthenticatedEntryPath } = appConfig

const FallbackRoute = () => {

    const { authenticated } = useAuth()

    return (
        <Navigate replace to={ authenticated ? authenticatedEntryPath : unAuthenticatedEntryPath } />
    )
}

export default FallbackRoute