// A throwaway backend for the full-stack suite: the backend from this
// checkout (uv) on a fresh PostgreSQL container whose data lives in tmpfs,
// so every run starts empty, and a Redis queue for the in-process worker,
// so executions really run. The containers carry the akasha-e2e label;
// Playwright's globalTeardown (teardown.ts) removes them, and leftovers of a
// crashed run are removed here before starting.
//
// Node rather than a shell script: Playwright starts web servers through
// cmd.exe on Windows, which cannot run one. Run as `node backend.ts`
// (type stripping, Node >= 22.18).
import { execFileSync, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { resolve } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

const port = process.env.AKASHA_E2E_API_PORT
if (!port) throw new Error('AKASHA_E2E_API_PORT is not set')
const label = 'akasha-e2e=fullstack'
const backend = resolve(import.meta.dirname, '../../../backend')

const docker = (...args: string[]) =>
    execFileSync('docker', args, { encoding: 'utf8' }).trim()

async function until(check: () => void) {
    for (;;) {
        try {
            check()
            return
        } catch {
            // Not ready yet: the container is still starting.
            await sleep(300)
        }
    }
}

const stale = docker('ps', '-aq', '--filter', `label=${label}`)
if (stale) docker('rm', '-f', ...stale.split(/\s+/))

const pg = docker(
    'run',
    '-d',
    '--rm',
    '--label',
    label,
    '--tmpfs',
    '/var/lib/postgresql/data',
    '-e',
    'POSTGRES_USER=akasha',
    '-e',
    'POSTGRES_PASSWORD=akasha',
    '-e',
    'POSTGRES_DB=akasha',
    '-p',
    '127.0.0.1::5432',
    'postgres:17.2',
)
const redis = docker(
    'run',
    '-d',
    '--rm',
    '--label',
    label,
    '-p',
    '127.0.0.1::6379',
    'redis:8.2.1-alpine',
)
// -h: the image's init phase serves only the unix socket; TCP means ready.
await until(() =>
    docker(
        'exec',
        pg,
        'pg_isready',
        '-q',
        '-h',
        '127.0.0.1',
        '-U',
        'akasha',
        '-d',
        'akasha',
    ),
)
await until(() => docker('exec', redis, 'redis-cli', 'ping'))
const hostPort = (id: string, containerPort: string) =>
    docker('port', id, containerPort).split('\n')[0]?.split(':').at(-1) ?? ''

// The developer's AKASHA_* variables must not leak in: only these settings.
const env: NodeJS.ProcessEnv = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith('AKASHA_')),
)
Object.assign(env, {
    AKASHA_ENVIRONMENT: 'test',
    AKASHA_DATABASE_URL: `postgresql://akasha:akasha@127.0.0.1:${hostPort(pg, '5432/tcp')}/akasha`,
    AKASHA_REDIS_URL: `redis://127.0.0.1:${hostPort(redis, '6379/tcp')}/0`,
    AKASHA_HOST: '127.0.0.1',
    AKASHA_PORT: port,
    // Node logs are stored down to the process log level (architecture §52).
    AKASHA_LOG_LEVEL: 'INFO',
    // Throwaway, like the database: credentials die with the run. URL-safe
    // base64 with padding, as the backend expects.
    AKASHA_CREDENTIAL_ENCRYPTION_KEY: randomBytes(32)
        .toString('base64')
        .replaceAll('+', '-')
        .replaceAll('/', '_'),
})
const akasha = ['run', '--directory', backend, '--frozen', 'akasha']

execFileSync('uv', [...akasha, 'migrate'], { env, stdio: 'inherit' })
// Once teardown has removed the containers, the in-process worker and
// trigger scheduler log failed polls until the server stops: expected.
// Playwright stops the server by signalling this process (or, on Windows,
// by killing its process tree): pass the signal on, exit with the server.
const server = spawn('uv', [...akasha, 'serve'], { env, stdio: 'inherit' })
for (const signal of ['SIGTERM', 'SIGINT'] as const)
    process.on(signal, () => server.kill(signal))
server.on('exit', (code) => process.exit(code ?? 1))
