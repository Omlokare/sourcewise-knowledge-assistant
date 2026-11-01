# Sourcewise — RAG Knowledge Assistant

A full-stack document Q&A starter inspired by the supplied project brief. Upload PDF, DOCX, and TXT files, ask questions across a searchable knowledge base, and inspect page/source citations. Includes accounts, chat history, an evaluation page, Docker deployment, and a local extractive mode.

## About the project

Sourcewise helps people find answers in their own documents instead of searching files one by one. Upload PDFs, Word documents, or text files, then ask questions in plain language. The app extracts and chunks the content, ranks relevant passages, and displays source excerpts with page citations so answers can be checked against the originals.

The project uses a React interface and a FastAPI backend. It includes account sign-in, private document libraries, saved conversations, and an evaluation dashboard. Add an OpenAI API key to generate concise answers from retrieved passages; without a key, Sourcewise runs in local extractive mode. Docker and Render configuration are included for local setup and deployment.

## Quick start (Docker)

1. Copy `.env.example` to `.env` and set `SECRET_KEY` to a long random value.
2. For generated answers, set `OPENAI_API_KEY` (optional). Without it, Sourcewise uses local extractive answers and does not send document text to an external service.
3. Run `docker compose up --build`.
4. Open http://localhost:8000. Register a user and upload a document.

## Local development

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend (Node 20+), in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL=http://localhost:8000` in `frontend/.env.local` if the API is not on the default same origin.

## Features

- PDF, DOCX, TXT upload with extraction, chunking, page metadata, and ownership isolation.
- Hybrid-ready retrieval foundation: normalized lexical relevance scoring with citations and page labels; OpenAI Responses API generation can be enabled via environment variables.
- Conversations and messages persisted in SQLite; bearer-token login with hashed passwords.
- Evaluation area with an editable question set, answer coverage score, and citation presence indicator.
- Responsive UI, health endpoint, Dockerfile, compose file, and environment template.

## Deploy

Deploy the Docker image to Render, Railway, Fly.io, or any container host. The included Render Blueprint uses the free web service plan for a demo. Set `SECRET_KEY` and optionally `OPENAI_API_KEY`. The free plan has an ephemeral filesystem, so uploaded documents and chat history can disappear when the service restarts or redeploys. For persistent production use, switch to a paid Render web service with a disk mounted at `/data` (currently about $7/month for the service plus $0.25/GB/month for disk storage), or migrate the repository from SQLite to managed Postgres and use object storage for original uploads. The application limits uploads to 20 MB.

## GitHub

Create a repository, then from this directory run:

```bash
git init
git add .
git commit -m "Build Sourcewise knowledge assistant"
git branch -M main
git remote add origin https://github.com/YOUR-USER/YOUR-REPO.git
git push -u origin main
```

Never commit `.env` or real API keys. `.gitignore` excludes them.

## API

FastAPI docs are available at `/docs`. Core routes: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/documents`, `POST /api/documents`, `POST /api/chats`, `POST /api/chats/{id}/messages`, and `GET /api/evaluation`.

## Limitations and next steps

Retrieval currently uses transparent lexical ranking rather than a vector database, keeping local setup simple. Swap in embeddings plus Qdrant/pgvector for larger corpora. Add malware scanning, object storage, rate limits, account recovery, and a managed database before exposing a production instance to untrusted users.
