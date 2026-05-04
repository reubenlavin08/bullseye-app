// /checkout-create — create a Stripe Checkout session for upgrade.
//
// Receives { plan: 'monthly'|'yearly', trial: bool }.
// Returns { url: 'https://checkout.stripe.com/...' } for client to redirect to.
//
// trial=true sets subscription_data.trial_period_days=7 in the
// Checkout config. No card required during trial; Stripe collects
// card on day 7 if not cancelled.

Deno.serve(async (req: Request) => {
    // const user = await requireUser(req)
    // const { plan, trial } = await req.json()
    // const session = await createCheckoutSession({
    //     user_id: user.id, user_email: user.email, plan, trial,
    // })
    // return jsonResponse({ url: session.url })
    return new Response("not implemented", { status: 501 })
})
