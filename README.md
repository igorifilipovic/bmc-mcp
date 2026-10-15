# bmc-mcp

Read-only MCP server za BMC kontrolere (**iDRAC, iRMC, iLO, iLOM**) preko Redfish API-ja.
HTTPS na portu **8001**, autentikacija **Bearer tokenom**, endpoint: `https://<host>:8001/mcp`.

## Alati

| Alat | Šta radi |
|---|---|
| `list_servers` | Spisak konfigurisanih BMC-ova (bez kredencijala) |
| `get_power_state` | Power state (On/Off) + health jednog servera |
| `get_health_summary` | Model, BIOS, power state, Health/HealthRollup |
| `get_fleet_status` | Power + health svih servera odjednom |
| `list_log_services` | Log servisi na BMC-u (SEL, Lclog, IML, IEL...) |
| `get_event_logs` | Zapisi iz logova (filter: log, min. severity, poslednjih N sati) |
| `get_critical_events` | Samo `Critical` zapisi, default poslednja 24h |

Read-only je obezbeđeno na tri nivoa: Redfish klijent ima samo `GET`, svi alati su označeni `readOnlyHint`,
a BMC nalog koji koristiš treba da ima read-only rolu (vidi dole).

## Pokretanje

```bash
cp .env.example .env                      # popuni MCP_AUTH_TOKENS i BMC lozinke
cp config/hosts.example.yaml config/hosts.yaml   # upiši svoje servere
mkdir -p certs && cp /putanja/do/tvoj.crt certs/server.crt && cp /putanja/do/tvoj.key certs/server.key

openssl rand -hex 32                      # generiši token za MCP_AUTH_TOKENS
docker compose up -d --build
```

Provera:

```bash
curl -k https://localhost:8001/healthz                          # {"status":"ok"}  (bez tokena)
curl -k -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -X POST https://localhost:8001/mcp \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Povezivanje klijenta

Claude Code:

```bash
claude mcp add --transport http bmc https://<host>:8001/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Pošto je sertifikat self-signed, klijent mora da mu veruje, npr. `NODE_EXTRA_CA_CERTS=/putanja/server.crt`
(Node klijenti) ili da se sertifikat doda u sistemski trust store. Ako cert nema SAN koji odgovara
hostname-u kojim se pristupa, validacija će pasti.

## Tokeni

- `MCP_AUTH_TOKENS` – jedan ili više tokena, odvojeni zarezom (min. 24 karaktera).
- `MCP_AUTH_TOKENS_FILE` – fajl sa po jednim tokenom po redu.
- Umesto tokena može `sha256:<hex>` (da u `.env` ne stoji čist token).
- Više tokena = po jedan po klijentu, pa se rotacija/opoziv radi bez prekida drugih.

## BMC nalozi (preporuka)

Napravi poseban nalog sa minimalnim pravima na svakom BMC-u:

- **iDRAC**: rola *Read Only*
- **iLO**: samo *Login* privilegija (bez Remote Console, Virtual Power/Reset, Config...)
- **iRMC**: rola *Monitor* / read-only
- **iLOM**: rola *Read Only* (`o`)

Lozinke idu kroz env promenljive (`password_env` u `hosts.yaml`), ne u fajl.

## Konfiguracija

`config/hosts.yaml`: `name`, `vendor` (informativna oznaka), `address`, `username`/`username_env`,
`password`/`password_env`, opciono `port` (443), `verify_tls` (false), `timeout` (20 s), `max_concurrency` (2).

Env: `MCP_PORT` (8001), `MCP_HOSTS_FILE`, `MCP_TLS_CERT`, `MCP_TLS_KEY`, `BMC_MAX_LOG_PAGES` (20), `MCP_LOG_LEVEL`.

## Napomene i ograničenja

- Otkrivanje logova je generičko (`/redfish/v1/{Systems,Managers,Chassis}/*/LogServices`), bez
  vendor-specifičnih putanja. Testirano samo protiv mock Redfish servera (`tests/mock_bmc.py`),
  **ne** protiv stvarnog iDRAC/iRMC/iLO/iLOM hardvera. Pre produkcije probaj na po jednom serveru od svakog
  proizvođača; razlike u poljima (npr. `Severity`, paginacija) su najverovatniji izvor problema.
- Veliki logovi (npr. Dell Lclog) se čitaju stranicu po stranicu do `BMC_MAX_LOG_PAGES`. Ako je
  `fetch_truncated: true`, rezultat može da propusti noviji deo loga kod BMC-ova koji vraćaju zapise
  rastućim redosledom; povećaj limit ili suzi upit na jedan log servis.
- Svaki poziv otvara novu vezu ka BMC-u (basic auth, bez sesija) i ograničen je na `max_concurrency` paralelnih
  zahteva po serveru, jer BMC web serveri su spori.
- `/healthz` je jedina putanja bez tokena i ne vraća nikakve podatke.
- Kontejner radi kao non-root, sa `read_only` fajl sistemom i `cap_drop: ALL`.
