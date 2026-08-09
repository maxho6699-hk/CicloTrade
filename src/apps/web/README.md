# CicloTrade Web

Responsive React decision terminal for US equities and A-shares.

```powershell
npm install
npm run dev -- --host 127.0.0.1
```

The Vite development server proxies `/api/rewrite` to `http://127.0.0.1:8001`.

Available routes:

- `/today`
- `/markets`
- `/portfolio`
- `/trade`
- `/reports`
- `/notifications`
- `/account`
- `/help`
- `/membership`
- `/mystic`

The interface uses demo data while logged out or offline. After authentication it binds canonical recommendation records, historical snapshots, paper positions, membership, risk, alerts, and Telegram state. Historical marks and disabled external sources remain visibly labeled.

Verification:

```powershell
npm run build
npm run lint
```
