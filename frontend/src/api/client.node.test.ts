// @vitest-environment node
// A real HTTP server on 127.0.0.1 stands in for the backend, so the client's
// actual fetch, headers, status handling and aborts are exercised.
import {
    createServer,
    type IncomingMessage,
    type ServerResponse,
} from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { z } from 'zod'
import { fetchSession, login, logout } from './auth'
import { ApiClient } from './client'
import { ApiError } from './errors'
import { getProject, publishProject } from './projects'

type Handler = (req: IncomingMessage, body: string, res: ServerResponse) => void

let handler: Handler
const pending: ServerResponse[] = []
const server = createServer((req, res) => {
    let body = ''
    req.on('data', (chunk: Buffer) => (body += chunk.toString()))
    req.on('end', () => {
        handler(req, body, res)
    })
})
let baseUrl: string

beforeAll(async () => {
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const { port } = server.address() as AddressInfo
    baseUrl = `http://127.0.0.1:${String(port)}/api/v1`
})
afterAll(async () => {
    for (const res of pending) res.destroy()
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
})
beforeEach(() => {
    handler = (_req, _body, res) => {
        json(res, 500, {})
    }
})

function json(
    res: ServerResponse,
    status: number,
    data: unknown,
    headers = {},
) {
    res.writeHead(status, { 'Content-Type': 'application/json', ...headers })
    res.end(JSON.stringify(data))
}

function client(options: { timeoutMs?: number } = {}) {
    return new ApiClient({
        baseUrl,
        ...(options.timeoutMs !== undefined && {
            timeoutMs: options.timeoutMs,
        }),
    })
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
    const error = await promise.then(
        () => undefined,
        (e: unknown) => e,
    )
    if (!(error instanceof ApiError))
        throw new Error(`expected ApiError, got ${String(error)}`)
    return error
}

const user = {
    id: 'user_1',
    username: 'ada',
    role: 'admin',
    disabled: false,
    created_at: '2026-10-01T12:00:00Z',
    updated_at: '2026-10-01T12:00:00+00:00',
}
const session = { expires_at: '2026-10-02T00:00:00Z', user }

describe('successful requests', () => {
    it('sends method, path, query, JSON body and headers; returns parsed data', async () => {
        let seen: { req: IncomingMessage; body: string } | undefined
        handler = (req, body, res) => {
            seen = { req, body }
            json(res, 200, { id: 'p1', extra: 'ignored' })
        }

        const result = await client().request(
            'POST',
            '/projects',
            z.object({ id: z.string() }),
            {
                body: { name: 'x' },
                query: {
                    limit: 10,
                    after: undefined,
                    archived: false,
                    status: ['failed', 'timeout'],
                },
            },
        )

        expect(result).toEqual({ id: 'p1' })
        expect(seen?.req.method).toBe('POST')
        expect(seen?.req.url).toBe(
            '/api/v1/projects?limit=10&archived=false&status=failed&status=timeout',
        )
        expect(seen?.body).toBe('{"name":"x"}')
        expect(seen?.req.headers['content-type']).toBe('application/json')
        expect(seen?.req.headers.accept).toBe('application/json')
        // The session is the HttpOnly cookie; scripts never set a token.
        expect(seen?.req.headers.authorization).toBeUndefined()
        expect(seen?.req.headers['x-akasha-csrf']).toBe('1')
        expect(seen?.req.headers['x-request-id']).toMatch(/^[0-9a-f-]{36}$/)
    })

    it('sends a raw body as-is with its content type', async () => {
        let seen: { req: IncomingMessage; body: string } | undefined
        handler = (req, body, res) => {
            seen = { req, body }
            json(res, 200, { id: 'p1' })
        }

        await client().request(
            'POST',
            '/plugins',
            z.object({ id: z.string() }),
            {
                rawBody: {
                    data: new Blob(['PK-bytes']),
                    contentType: 'application/zip',
                },
            },
        )

        expect(seen?.body).toBe('PK-bytes')
        expect(seen?.req.headers['content-type']).toBe('application/zip')
    })

    it('omits Content-Type when there is no body', async () => {
        let headers: IncomingMessage['headers'] = {}
        handler = (req, _body, res) => {
            headers = req.headers
            json(res, 200, session)
        }

        await expect(fetchSession(client())).resolves.toEqual(session)
        expect(headers['content-type']).toBeUndefined()
    })

    it('parses an empty 204 body as undefined', async () => {
        handler = (_req, _body, res) => {
            res.writeHead(204).end()
        }
        await expect(logout(client())).resolves.toBeUndefined()
    })

    it('posts login credentials and validates the session response', async () => {
        let body = ''
        handler = (_req, b, res) => {
            body = b
            json(res, 200, { token: null, ...session })
        }
        const result = await login(client(), {
            username: 'ada',
            password: 'pw',
        })
        // The session goes into the HttpOnly cookie, never into the app.
        expect(JSON.parse(body)).toEqual({
            username: 'ada',
            password: 'pw',
            cookie: true,
        })
        expect(result).toEqual(session)
    })
})

describe('API errors', () => {
    const envelope = (code: string, message: string, details = {}) => ({
        code,
        message,
        details,
        request_id: 'req-from-server',
    })

    it.each([
        [401, 'AUTHENTICATION_ERROR', 'authentication'],
        [403, 'AUTHORIZATION_ERROR', 'authorization'],
        [404, 'NOT_FOUND', 'not_found'],
        [409, 'INVALID_STATE_TRANSITION', 'conflict'],
        [422, 'VALIDATION_ERROR', 'validation'],
        [429, 'RATE_LIMITED', 'rate_limited'],
        [413, 'PAYLOAD_TOO_LARGE', 'http'],
        [500, 'INTERNAL_ERROR', 'server'],
        [503, 'CONFIGURATION_ERROR', 'server'],
    ] as const)(
        'maps %i %s to kind %s with the envelope',
        async (status, code, kind) => {
            handler = (_req, _body, res) => {
                json(
                    res,
                    status,
                    envelope(code, 'Readable message.', { field: 'bad' }),
                )
            }

            const error = await failure(
                client().request('GET', '/x', z.unknown()),
            )

            expect(error.kind).toBe(kind)
            expect(error.status).toBe(status)
            expect(error.code).toBe(code)
            expect(error.message).toBe('Readable message.')
            expect(error.details).toEqual({ field: 'bad' })
            expect(error.requestId).toBe('req-from-server')
        },
    )

    it('rejects an expired session as an authentication error', async () => {
        handler = (_req, _body, res) => {
            json(
                res,
                401,
                envelope('AUTHENTICATION_ERROR', 'Session expired.'),
                {
                    'WWW-Authenticate': 'Bearer',
                },
            )
        }
        const error = await failure(fetchSession(client()))
        expect(error.kind).toBe('authentication')
        expect(error.message).toBe('Session expired.')
    })

    it('falls back to a user-facing message when the error body is not an envelope', async () => {
        // e.g. the dev proxy or a reverse proxy answering for a dead backend
        handler = (_req, _body, res) => {
            res.writeHead(502, {
                'Content-Type': 'text/html',
                'X-Request-ID': 'rid-1',
            })
            res.end('<html>Bad Gateway</html>')
        }
        const error = await failure(client().request('GET', '/x', z.unknown()))
        expect(error.kind).toBe('server')
        expect(error.code).toBe('SERVER')
        expect(error.message).toBe(
            'The server is unavailable. Try again later.',
        )
        expect(error.requestId).toBe('rid-1')
    })
})

describe('malformed responses', () => {
    it('rejects a body that violates the schema', async () => {
        handler = (_req, _body, res) => {
            json(res, 200, { ...user, role: 'root' })
        }
        const error = await failure(fetchSession(client()))
        expect(error.kind).toBe('malformed_response')
        expect(error.status).toBe(200)
        expect(error.cause).toBeInstanceOf(z.ZodError)
    })

    it('rejects a non-JSON success body (e.g. an SPA fallback page)', async () => {
        handler = (_req, _body, res) => {
            res.writeHead(200, { 'Content-Type': 'text/html' }).end(
                '<!doctype html>',
            )
        }
        const error = await failure(client().request('GET', '/x', z.unknown()))
        expect(error.kind).toBe('malformed_response')
    })

    it('rejects an empty body where content is expected', async () => {
        handler = (_req, _body, res) => {
            res.writeHead(200).end()
        }
        const error = await failure(fetchSession(client()))
        expect(error.kind).toBe('malformed_response')
    })
})

describe('transport failures', () => {
    it('reports an unreachable backend as a network error', async () => {
        const dead = new ApiClient({ baseUrl: 'http://127.0.0.1:1/api/v1' })
        const error = await failure(dead.request('GET', '/x', z.unknown()))
        expect(error.kind).toBe('network')
        expect(error.status).toBeUndefined()
        expect(error.message).toMatch(/could not be reached/)
    })

    it('reports a hanging backend as a timeout', async () => {
        handler = (_req, _body, res) => pending.push(res)
        const error = await failure(
            client({ timeoutMs: 50 }).request('GET', '/x', z.unknown()),
        )
        expect(error.kind).toBe('timeout')
    })

    it('reports caller cancellation as cancelled, not as a failure of the backend', async () => {
        let arrived: () => void = () => undefined
        const requestArrived = new Promise<void>(
            (resolve) => (arrived = resolve),
        )
        handler = (_req, _body, res) => {
            pending.push(res)
            arrived()
        }
        const controller = new AbortController()

        const promise = client().request('GET', '/x', z.unknown(), {
            signal: controller.signal,
        })
        await requestArrived
        controller.abort()

        expect((await failure(promise)).kind).toBe('cancelled')
    })

    it('does not send a request whose signal is already aborted', async () => {
        let hits = 0
        handler = (_req, _body, res) => {
            hits++
            json(res, 200, {})
        }
        const error = await failure(
            client().request('GET', '/x', z.unknown(), {
                signal: AbortSignal.abort(),
            }),
        )
        expect(error.kind).toBe('cancelled')
        expect(hits).toBe(0)
    })
})

describe('path segments from untrusted input', () => {
    // Ids come from route params a crafted link controls. encodeURIComponent
    // leaves '.' alone, and URL parsing collapses dot segments, so "..",
    // unchecked, would turn POST /projects/../publish into POST /publish.
    it.each(['.', '..'])('never sends a request whose id is %j', async (id) => {
        let hits = 0
        handler = (_req, _body, res) => {
            hits++
            json(res, 200, {})
        }

        const error = await failure(publishProject(client(), id))

        expect(error.kind).toBe('not_found')
        expect(hits).toBe(0)
    })

    it('still encodes ids that merely contain dots or slashes', async () => {
        let url = ''
        handler = (req, _body, res) => {
            url = req.url ?? ''
            json(res, 404, {})
        }
        await failure(getProject(client(), '../a.b'))
        expect(url).toBe('/api/v1/projects/..%2Fa.b')
    })
})
