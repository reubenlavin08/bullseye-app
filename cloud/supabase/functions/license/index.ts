// /license — return user's tier + limits + kill switch.
//
// Flow:
//   1. requireUser
//   2. SELECT * FROM licenses WHERE user_id = auth.uid()
//   3. If tier='paid' AND current_period_end < NOW(): downgrade -> free
//   4. If tier='trial' AND trial_ends_at < NOW(): downgrade -> free
//   5. Return:
//       {
//         tier, watches_limit, poll_interval_min,
//         expires_at, trial_ends_at,
//         min_supported_version    <-- KILL SWITCH
//       }
//
// `watches_limit`: 3 for free, null (unlimited) for paid/trial.
// `poll_interval_min`: 30 for free, 5 for paid/trial.

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
