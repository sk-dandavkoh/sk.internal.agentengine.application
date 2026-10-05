/** 32 random hex digits: the id part of backend `new_id`. Not
 * `crypto.randomUUID`, which browsers offer only in secure contexts (HTTPS
 * or localhost) and a plain-HTTP deployment would lack (docs/deployment.md). */
export const randomHex = () =>
    Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
        b.toString(16).padStart(2, '0'),
    ).join('')
