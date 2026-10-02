const dateTime = new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
})

export function formatDateTime(iso: string): string {
    return dateTime.format(new Date(iso))
}
