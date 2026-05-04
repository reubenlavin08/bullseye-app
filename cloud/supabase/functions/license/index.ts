// /license — return user's tier + limits + kill switch.
//
// Flow:
//   1. requireUser
//   2. SELECT * FROM licenses WHERE user_id = auth.uid()
//   3. If tier='paid' AND current_period_end < NOW(): downgrade -> free
//      (UPDATE in this same call, then return the new tier)
//   4. If tier='trial' AND trial_ends_at < NOW(): downgrade -> free
//      (UPDATE in this same call, then return the new tier)
//   5. Return:
//       {
//         tier, watches_limit, poll_interval_min,
//         expires_at, trial_ends_at,
//         min_supported_version    <-- KILL SWITCH
//       }
//
// `watches_limit`: 3 for free, null (unlimited) for paid/trial.
// `poll_interval_min`: 30 for free, 5 for paid/trial.
//
// Note on trial expiry: we do the downgrade synchronously inside this
// endpoint instead of via a pg_cron job. pg_cron isn't enabled by
// default on Supabase Free projects, and there's no real benefit to
// sweeping in the background — the desktop app calls /license every
// hour, so any user whose trial expired will get downgraded the next
// time they're online (which is when it actually matters anyway,
// since an offline user can't use Pro features).

// import { requireUser, jsonResponse } from '../_shared/auth.ts'
// import { currentMinSupportedVersion } from '../_shared/normalize.ts'

Deno.serve(async (req: Request) => {
    // const user = await requireUser(req)
    // const license = await fetchLicense(user.id)
    // const limits = license.tier === 'free'
    //     ? { watches_limit: 3, poll_interval_min: 30 }
    //     : { watches_limit: null, poll_interval_min: 5 }
    // return jsonResponse({
    //     ...license,
    //     ...limits,
    //     min_supported_version: currentMinSupportedVersion(),
    // })
    return new Response("not implemented", { status: 501 })
})
