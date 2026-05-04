// Stripe helpers — Checkout sessions + webhook signature verification.

// import Stripe from 'https://esm.sh/stripe@14?target=deno'
// const stripe = new Stripe(Deno.env.get('STRIPE_SECRET_KEY')!, {
//     apiVersion: '2024-04-10',
// })

export interface CheckoutSessionArgs {
    user_id: string
    user_email: string
    plan: "monthly" | "yearly"
    trial: boolean
}

export async function createCheckoutSession(args: CheckoutSessionArgs): Promise<{ url: string }> {
    // TODO: stripe.checkout.sessions.create with:
    //   - mode: 'subscription'
    //   - customer_email: user.email
    //   - line_items: [{ price: STRIPE_PRICE_MONTHLY|YEARLY, quantity: 1 }]
    //   - subscription_data: { trial_period_days: args.trial ? 7 : undefined }
    //   - success_url: 'https://bullseye.app/upgrade-success?session_id={CHECKOUT_SESSION_ID}'
    //   - cancel_url: 'https://bullseye.app/pricing'
    //   - client_reference_id: args.user_id  (so webhook can link)
    throw new Error("not implemented")
}

export async function verifyWebhookSignature(
    body: string,
    signature: string
): Promise<{ type: string; data: any }> {
    // TODO: stripe.webhooks.constructEvent(body, signature, STRIPE_WEBHOOK_SECRET)
    // CRITICAL: never skip this. Without it, anyone could POST a fake
    // "subscription created" event and become a paid user.
    throw new Error("not implemented")
}
