// /account-export — GDPR/PIPEDA data export.
//
// Returns a JSON blob containing every row this user owns:
//   {
//     account: { email, created_at, tier },
//     watches: [...],
//     email_history: [...],
//     telemetry: [...]   // last 90 days
//   }
//
// Future: gzip + email a download link if the export is large.

Deno.serve(async (req: Request) => {
    return new Response("not implemented", { status: 501 })
})
