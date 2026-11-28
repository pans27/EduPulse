# Email Function (DigitalOcean Functions + SendGrid)

The dispatcher delivers each alert in `notification_outbox` by POSTing it to this Function, which
sends it through SendGrid. The SendGrid key is stored only in the Function's environment, never
in the Swarm cluster.

```
dispatcher ──HTTPS, Bearer mail_token──▶ Function edupulse/send ──API key──▶ SendGrid ──▶ inbox
```

## Contract with the dispatcher

Request: `POST`, JSON `{message_id, to, subject, text}`, header `Authorization: Bearer <MAIL_TOKEN>`.
`message_id` is `<outbox-id@edupulse>` and becomes the email's `Message-ID` header.

| Function replies | When | Dispatcher (`worker/message.py` `is_permanent`) |
|---|---|---|
| 202 | SendGrid accepted the email | marks the row sent |
| 401 | missing or wrong bearer token | permanent: marks the row failed |
| 422 | a field missing, a bad `message_id` or subject, or SendGrid answered 400/413 | permanent |
| 429 | SendGrid rate limit | retries with backoff |
| 502 | SendGrid unreachable, wrong key (401), unverified sender (403), 5xx | retries with backoff |

Only a problem with the message itself is permanent. A configuration or provider problem is
retried (10 s doubling to 15 min, 8 attempts), so fixing the key in time loses no alert.

Delivery is at least once: if the dispatcher dies after a 202 but before it marks the row sent, the
alert is sent again with the same `Message-ID`. The Function keeps no state, so it does not drop
that resend, and neither does Gmail: two sends with one `Message-ID` on Nov 27 showed as two
emails. Exactly-once would need the Function to remember sent ids (e.g. in object storage).

## One-time setup

1. SendGrid: verify a sender address (Settings → Sender Authentication → Single Sender
   Verification) and create an API key with only the **Mail Send** permission.
2. `cp functions/.env.example functions/.env` and fill in `SENDGRID_API_KEY`, `MAIL_FROM` (the
   verified address) and `MAIL_TOKEN` (`openssl rand -hex 32`). `.env` is ignored by git.
3. Install the Functions plugin and create a namespace in Toronto:

   ```bash
   doctl serverless install
   doctl serverless namespaces create --label edupulse --region tor1
   doctl serverless connect edupulse
   ```

4. Give the cluster the same token, as a Docker secret (it never passes through the shell history):

   ```bash
   grep '^MAIL_TOKEN=' functions/.env | cut -d= -f2- | tr -d '\n' | ssh root@138.197.173.188 'docker secret create mail_token -'
   ```

## Deploy

```bash
doctl serverless deploy functions
doctl sls fn get edupulse/send --url
```

If the URL changed (a new namespace), put it in `MAIL_ENDPOINT` of the dispatcher in [`infra/swarm/stack.yml`](../infra/swarm/stack.yml),
commit, and run `scripts/deploy.sh`.

Redeploy the Function after changing `.env` or the code. To rotate `MAIL_TOKEN`, change `.env`,
redeploy the Function, then replace the secret: `docker secret` values are immutable, so create
`mail_token_v2`, point the stack at it, and deploy.

## Test

Unit tests (SendGrid is replaced; nothing is sent):

```bash
uv run --with pytest pytest functions/tests
```

Against the deployed Function (sends one real email to `$TO`):

```bash
URL=$(doctl sls fn get edupulse/send --url)
TOKEN=$(grep '^MAIL_TOKEN=' functions/.env | cut -d= -f2-)
curl -sS -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"message_id\":\"<test-$(date +%s)@edupulse>\",\"to\":\"$TO\",\"subject\":\"[EduPulse] test\",\"text\":\"Function test\"}"
```

Logs: `doctl serverless activations logs --function edupulse/send --limit 5`. They contain the
`message_id` and status codes only, never the address or the key.

Local Compose, the tests in `services/worker` and the evaluation scripts keep using the mock
`mail-sink`, which records messages so they can be counted.
