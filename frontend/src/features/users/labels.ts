import type { User, UserRole } from '../../api/auth'

export const roleLabels: Record<UserRole, string> = {
    admin: 'Administrator',
    member: 'Member',
}

export const displayName = (user: User) => user.display_name ?? user.username
