# Connecting ChatGPT

## 1. Publish the hub (one click)

Dashboard → **Connect ChatGPT** → **Publish**. The hub asks Tailscale Funnel (must be installed and signed in) to map

```
https://<pc>.<tailnet>.ts.net/hub-<secret>/...   ->   http://127.0.0.1:8795/c/<secret>/...
```

Only that secret path is public. The dashboard and `/api/v1` are not published, and the plain `/master/mcp` paths answer only
requests from this PC. Anyone with the full URL can use the tools, so treat it like a password ("Copy URL" on the Connect page).
**Unpublish** removes the mapping. Using another tunnel instead: point it at `http://127.0.0.1:8795/c/<secret>` and type its
public address into Settings → server → public url. If your tunnel forwards the whole port, also set `server.allowed_hosts`
and `api.api_key` (REST then requires the key).

## 2. Register the connectors in ChatGPT

**Injector (fast):** Connect page → tick the connectors → **Copy injector script** → open chatgpt.com (signed in) → F12 → Console →
paste → Enter. The script uses your own ChatGPT session inside that tab to create each connector and prints a result table.
Then install each one: ChatGPT → **Plugins → Personal → +** and accept ChatGPT's consent dialog. In a chat, type `@Master` /
`@Agent` to select the plugin (typing `@EmaraAI` picks an older plugin with that name if you have one).
With `driver.kind: playwright` the button **Inject into ChatGPT** does the same without the console.
"Always allow these tools" removes ChatGPT's confirmation prompt for these connectors — leave it off unless you want that.

**By hand:** ChatGPT → Settings → Apps & Connectors → Developer mode → Create, one connector per row of the Connect page
(name + "Copy URL", authentication: none).

Master chat: enable **Master** (+ PC tools if the master also works). Agent chats: **Agent** + the PC groups that agent needs.
Never enable Master and Agent in the same chat. Each connector has its own batch tool (`hub_batch`, `pc_batch`, `browser_batch`,
`ui_batch`) — the names differ on purpose so a chat with several connectors cannot mix them up.
Tip: create a ChatGPT **Project** with those connectors and put its URL in Settings → driver → chatgpt new chat url so new chats open inside it.

## 3. Start a project

In a new chat with EmaraAI Master enabled:
> Create a project called `shop` whose goal is …, then start as master.

The model calls `project_create` → `session_start` → creates agents → assigns tasks. From there the supervisor opens agent chats.

## Drivers (how the hub types into chats)

| `driver.kind` | Opens chats | Observes state | Needs |
|---|---|---|---|
| `manual` (default) | no — shows boot prompts on the dashboard | no (uses tool-activity timing) | nothing |
| `legacy_bridge` | yes | yes | `legacy.enabled: true`, EmaraAI running, Chrome bridge |
| `playwright` | yes | yes | `pip install -e ".[playwright]"`, Chrome started with `--remote-debugging-port=9222 --user-data-dir=C:\chatgpt-profile` and logged in |

**Chats you open yourself** (normally the master chat) are found automatically by `legacy_bridge` / `playwright`: the hub looks for the
single ChatGPT tab that shows the chat's session id and binds to it. If two tabs match, it does not guess — prompts for that chat stay
on the dashboard until you close the extra tab or bind it with `POST /api/v1/sessions/<id>/bind`.

**Manual mode:** when a prompt must be sent (new agent chat, wake, continue), it appears under *Manual prompts* on the dashboard → Copy → paste in the chat → *Mark done*. For a new chat also call `POST /api/v1/sessions/<id>/bind {"url": "<chat url>"}` if you later switch to an automatic driver.

**Selectors:** ChatGPT changes its HTML. If chats show `chat_state: unknown` or sending fails, update `config/chatgpt_selectors.yaml` (composer, send_button, stop_button, turn selectors, limit/error phrases). No code change needed.

**Known limit:** whether connectors are auto-enabled in a brand-new chat depends on ChatGPT (Project settings / developer mode). If a new agent chat says it has no `session_start` tool, enable the connector in that chat once, or use a ChatGPT Project that has them on.
