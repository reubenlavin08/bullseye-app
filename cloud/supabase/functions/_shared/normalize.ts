// Generic helpers shared across functions.

export function getEnv(key: string): string {
    const v = Deno.env.get(key)
    if (!v) throw new Error(`Missing required env: ${key}`)
    return v
}

export function appVersionGate(clientVersion: string, minVersion: string): boolean {
    // Returns true if client is OLDER than min — i.e. should be killed.
    const parse = (v: string) => v.split(".").map(Number)
    const [a1, a2, a3] = parse(clientVersion)
    const [b1, b2, b3] = parse(minVersion)
    if (a1 !== b1) return a1 < b1
    if (a2 !== b2) return a2 < b2
    return a3 < b3
}

// Read this from the `min_supported_version` env var on each /license
// call. Bump when shipping a breaking change to force-update old clients.
export function currentMinSupportedVersion(): string {
    return Deno.env.get("MIN_SUPPORTED_VERSION") ?? "0.1.0"
}
