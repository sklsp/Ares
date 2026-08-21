# AI E-commerce Operations Agent

An operations console for a product catalog. The agent can inspect products, inventory, sales, and content quality, then prepare catalog changes for explicit human approval. The local store is persisted in SQLite and the agent uses real tool calls through the configured LLM provider.

## Run locally

Use two PowerShell terminals from the repository root.

### Backend

```powershell
cd W:\AI-E-Commerce-Agent\backend
py -m venv .venv # only on a clean clone
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m app.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

The seed command creates the schema when needed and loads the demo catalog. It resets demo data by default; use `python -m app.seed --keep` to preserve an existing catalog. API docs are at http://localhost:8000/docs.

### Frontend

```powershell
cd W:\AI-E-Commerce-Agent
Copy-Item .env.example .env.local # optional; defaults already target port 8000
npm install
npm run dev
```

Open http://localhost:3000. The console loads analytics and catalog data, streams agent activity over SSE, and exposes approve/reject controls for write tools.

## LLM configuration

The default provider is Ollama:

```powershell
ollama pull llama3.2
ollama serve
```

For an OpenAI-compatible provider, create `backend/.env` with:

```dotenv
LLM_PROVIDER=openai_compatible
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4o-mini
```

Never put provider keys in the frontend or commit `.env` files. The app reports a degraded health status when the configured provider is unavailable; catalog and analytics reads still work.

## Tests

```powershell
Push-Location backend; .\.venv\Scripts\python.exe -m pytest; Pop-Location
npm run lint
npm run build
npm test
```

The backend suite uses a scripted test provider and does not require Ollama. The browser smoke test expects the backend to be running and seeded.This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
