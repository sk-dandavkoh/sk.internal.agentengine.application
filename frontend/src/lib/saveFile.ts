// Long enough for the browser to start reading the blob; a revoke right
// after click() can cancel the download in some browsers.
const REVOKE_AFTER_MS = 40_000

/** Hands `data` to the browser as a download named `name`. */
export function saveFile(name: string, data: Blob) {
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = name
    link.click()
    setTimeout(() => {
        URL.revokeObjectURL(url)
    }, REVOKE_AFTER_MS)
}
