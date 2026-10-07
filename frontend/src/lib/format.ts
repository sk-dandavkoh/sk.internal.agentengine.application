const dateTime = new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
})

export function formatDateTime(iso: string): string {
    return dateTime.format(new Date(iso))
}

/** 3600 → "1 h", 900 → "15 min", 90 → "1 min 30 s", 45 → "45 s". */
export function formatSeconds(seconds: number): string {
    const s = Math.round(seconds)
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const rest = s % 60
    const parts = [
        h > 0 && `${String(h)} h`,
        m > 0 && `${String(m)} min`,
        (rest > 0 || s === 0) && `${String(rest)} s`,
    ]
    return parts.filter(Boolean).join(' ')
}

/** 1048576 → "1 MB", 1536 → "1.5 KB": binary units, as limits are set. */
export function formatBytes(bytes: number): string {
    const units = ['bytes', 'KB', 'MB', 'GB']
    let value = bytes
    let unit = 0
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024
        unit += 1
    }
    const shown = Number.isInteger(value) ? String(value) : value.toFixed(1)
    return `${shown} ${units[unit] ?? 'bytes'}`
}
