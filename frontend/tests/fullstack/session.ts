import { inflateRawSync } from 'node:zlib'
import {
    expect,
    request as http,
    type APIRequestContext,
    type Download,
    type Page,
} from '@playwright/test'

// Shared by the journey specs: the run's admin (created by whichever spec
// gets there first) and a bearer-token context for setup and assertions.

export const UI_HEADERS = { 'X-Akasha-CSRF': '1' }

export const admin = () => ({
    username: 'e2e-admin',
    password: process.env.AKASHA_E2E_ADMIN_PASSWORD ?? '',
})

export async function adminApi(baseURL: string): Promise<APIRequestContext> {
    const anonymous = await http.newContext({ baseURL })
    // 409: another spec of this run already created the admin.
    expect([201, 409]).toContain(
        (
            await anonymous.post('/api/v1/auth/setup', { data: admin() })
        ).status(),
    )
    const login = await anonymous.post('/api/v1/auth/login', { data: admin() })
    expect(login.status()).toBe(200)
    const token = String(((await login.json()) as { token: unknown }).token)
    await anonymous.dispose()
    return http.newContext({
        baseURL,
        extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    })
}

/** Signs the page's browser context in: the session cookie is set. */
export async function signIn(page: Page) {
    const response = await page.request.post('/api/v1/auth/login', {
        data: { ...admin(), cookie: true },
        headers: UI_HEADERS,
    })
    expect(response.status()).toBe(200)
}

/** Waits for the download `click` starts and returns its bytes. */
export async function downloaded(
    page: Page,
    click: () => Promise<void>,
): Promise<{ download: Download; path: string }> {
    const [download] = await Promise.all([
        page.waitForEvent('download'),
        click(),
    ])
    const path = await download.path()
    return { download, path }
}

/** The entries of a zip archive as the backend writes them (stored or
 * deflated, sizes in the local headers): enough to inspect a package. */
export function unzip(data: Buffer): Map<string, string> {
    const entries = new Map<string, string>()
    let at = 0
    while (data.readUInt32LE(at) === 0x04034b50) {
        if (data.readUInt16LE(at + 6) & 0x08)
            throw new Error('zip entry sizes in a data descriptor')
        const method = data.readUInt16LE(at + 8)
        const size = data.readUInt32LE(at + 18)
        const nameLength = data.readUInt16LE(at + 26)
        const extraLength = data.readUInt16LE(at + 28)
        const name = data.toString('utf8', at + 30, at + 30 + nameLength)
        const start = at + 30 + nameLength + extraLength
        const raw = data.subarray(start, start + size)
        entries.set(
            name,
            (method === 8 ? inflateRawSync(raw) : raw).toString('utf8'),
        )
        at = start + size
    }
    return entries
}
