import { execFileSync } from 'node:child_process'

// Removes the containers (PostgreSQL, Redis) of tests/fullstack/backend.sh. Done
// here, not by a signal trap in the script: Playwright's server shutdown
// does not reliably give a shell the chance to clean up.
export default function teardown() {
    const ids = execFileSync(
        'docker',
        ['ps', '-aq', '--filter', 'label=akasha-e2e=fullstack'],
        { encoding: 'utf8' },
    )
        .split('\n')
        .filter(Boolean)
    if (ids.length > 0) execFileSync('docker', ['rm', '-f', ...ids])
}
