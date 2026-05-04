// /telemetry — bulk-insert client telemetry events.
//
// Auth: JWT preferred (links events to user_id), but anonymous
// install_id-only events are also accepted (for pre-login app-open
// events).
//
// Body: { events: [{ event_name, properties, app_version, install_id }] }

Deno.serve(async (req: Request) => {
    return new Response("not implemented", { status: 501 })
})
