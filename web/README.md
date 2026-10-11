# Watch Tower desk

The browser half of `wt desk`. From the repository root:

```bash
wt desk
```

The UI listens on http://127.0.0.1:47221 and proxies `/api` to the FastAPI process on port 8641. To run the two processes yourself:

```bash
python3 -m uvicorn wt.deskapp:app --host 127.0.0.1 --port 8641
cd web && npm run dev -- --port 47221 --hostname 0.0.0.0
```
