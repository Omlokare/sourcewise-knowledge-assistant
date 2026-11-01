import os, re, sqlite3, secrets, math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from pypdf import PdfReader
from docx import Document as DocxDocument

DB = os.getenv("DATABASE_PATH", "./data/sourcewise.db")
Path(DB).parent.mkdir(parents=True, exist_ok=True)
SECRET = os.getenv("SECRET_KEY", "local-development-secret-change-before-deploy")
ALGORITHM = "HS256"
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Sourcewise API", version="1.0.0")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, kind TEXT NOT NULL, pages INTEGER NOT NULL, chunks INTEGER NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE, page INTEGER NOT NULL, content TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE, role TEXT NOT NULL, content TEXT NOT NULL, citations TEXT NOT NULL DEFAULT '[]', score REAL NOT NULL DEFAULT 0, created TEXT NOT NULL);
        """)
init()

def now(): return datetime.now(timezone.utc).isoformat()
def token_for(user): return jwt.encode({"sub": str(user["id"]), "exp": datetime.now(timezone.utc)+timedelta(days=7)}, SECRET, algorithm=ALGORITHM)
def current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Please sign in")
    try: uid = int(jwt.decode(authorization[7:], SECRET, algorithms=[ALGORITHM])["sub"])
    except (JWTError, ValueError): raise HTTPException(401,"Session expired; please sign in again")
    with db() as c: user=c.execute("SELECT id,email,name FROM users WHERE id=?",(uid,)).fetchone()
    if not user: raise HTTPException(401,"Account not found")
    return dict(user)

class AuthIn(BaseModel):
    email: EmailStr
    password: str
    name: str = ""

@app.get("/api/health")
def health(): return {"status":"ok","service":"Sourcewise"}

@app.post("/api/auth/register")
def register(body: AuthIn):
    if len(body.password)<8: raise HTTPException(400,"Use a password with at least 8 characters")
    try:
        with db() as c: c.execute("INSERT INTO users(email,name,password,created) VALUES(?,?,?,?)",(body.email.lower(),body.name.strip() or body.email.split("@")[0],pwd.hash(body.password),now()))
    except sqlite3.IntegrityError: raise HTTPException(409,"An account with this email already exists")
    with db() as c: user=c.execute("SELECT id,email,name FROM users WHERE email=?",(body.email.lower(),)).fetchone()
    return {"token":token_for(user),"user":dict(user)}

@app.post("/api/auth/login")
def login(body: AuthIn):
    with db() as c: user=c.execute("SELECT * FROM users WHERE email=?",(body.email.lower(),)).fetchone()
    if not user or not pwd.verify(body.password,user["password"]): raise HTTPException(401,"Email or password is incorrect")
    return {"token":token_for(user),"user":{"id":user["id"],"email":user["email"],"name":user["name"]}}

@app.get("/api/auth/me")
def me(user=Depends(current_user)): return user

def extract(file: UploadFile):
    name=file.filename or "document"
    data=file.file.read()
    if len(data)>int(os.getenv("MAX_UPLOAD_MB","20"))*1024*1024: raise HTTPException(413,"File exceeds the upload limit")
    ext=Path(name).suffix.lower()
    if ext==".pdf":
        try:
            pages=[(i+1,p.extract_text() or "") for i,p in enumerate(PdfReader(__import__('io').BytesIO(data)).pages)]
        except Exception: raise HTTPException(400,"Could not read this PDF")
    elif ext==".docx":
        try:
            doc=DocxDocument(__import__('io').BytesIO(data)); pages=[(1,"\n".join(p.text for p in doc.paragraphs))]
        except Exception: raise HTTPException(400,"Could not read this DOCX")
    elif ext==".txt": pages=[(1,data.decode("utf-8",errors="replace"))]
    else: raise HTTPException(400,"Supported formats: PDF, DOCX, TXT")
    chunks=[]
    for page,text in pages:
        words=text.split()
        for start in range(0,len(words),180):
            part=" ".join(words[start:start+220]).strip()
            if part: chunks.append((page,part))
    if not chunks: raise HTTPException(400,"No readable text found in this document")
    return name,ext[1:].upper(),len(pages),chunks

@app.post("/api/documents")
def upload(file: UploadFile=File(...), user=Depends(current_user)):
    name,kind,pages,chunks=extract(file)
    with db() as c:
        cur=c.execute("INSERT INTO documents(user_id,name,kind,pages,chunks,created) VALUES(?,?,?,?,?,?)",(user["id"],name,kind,pages,len(chunks),now())); did=cur.lastrowid
        c.executemany("INSERT INTO chunks(document_id,page,content) VALUES(?,?,?)",[(did,p,t) for p,t in chunks])
    return {"id":did,"name":name,"kind":kind,"pages":pages,"chunks":len(chunks)}

@app.get("/api/documents")
def documents(user=Depends(current_user)):
    with db() as c: rows=c.execute("SELECT id,name,kind,pages,chunks,created FROM documents WHERE user_id=? ORDER BY created DESC",(user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.delete("/api/documents/{did}")
def delete_document(did:int,user=Depends(current_user)):
    with db() as c:
        r=c.execute("DELETE FROM documents WHERE id=? AND user_id=?",(did,user["id"]))
        if not r.rowcount: raise HTTPException(404,"Document not found")
    return {"ok":True}

@app.get("/api/chats")
def chats(user=Depends(current_user)):
    with db() as c: rows=c.execute("SELECT id,title,created,updated FROM chats WHERE user_id=? ORDER BY updated DESC",(user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/chats")
def create_chat(user=Depends(current_user)):
    stamp=now()
    with db() as c: cur=c.execute("INSERT INTO chats(user_id,title,created,updated) VALUES(?,?,?,?)",(user["id"],"New conversation",stamp,stamp)); cid=cur.lastrowid
    return {"id":cid,"title":"New conversation","messages":[]}

@app.get("/api/chats/{cid}")
def get_chat(cid:int,user=Depends(current_user)):
    with db() as c:
        chat=c.execute("SELECT id,title,created,updated FROM chats WHERE id=? AND user_id=?",(cid,user["id"])).fetchone()
        if not chat: raise HTTPException(404,"Conversation not found")
        msgs=c.execute("SELECT id,role,content,citations,score,created FROM messages WHERE chat_id=? ORDER BY id",(cid,)).fetchall()
    return {**dict(chat),"messages":[{**dict(m),"citations":__import__('json').loads(m["citations"])} for m in msgs]}

def rank(query, user_id):
    terms=set(re.findall(r"[a-zA-Z0-9]{2,}",query.lower()))
    with db() as c: rows=c.execute("SELECT chunks.id,documents.name,documents.id document_id,chunks.page,chunks.content FROM chunks JOIN documents ON documents.id=chunks.document_id WHERE documents.user_id=?",(user_id,)).fetchall()
    scored=[]
    for row in rows:
        words=re.findall(r"[a-zA-Z0-9]{2,}",row["content"].lower()); counts=Counter(words)
        overlap=sum(min(counts[t],2) for t in terms)
        coverage=len(terms & counts.keys())/max(1,len(terms))
        score=coverage*0.7+min(overlap/max(1,len(terms)*2),0.3)
        if score>0: scored.append((score,row))
    return sorted(scored,key=lambda x:x[0],reverse=True)[:5]

def answer_for(query, matches):
    citations=[]
    for score,row in matches:
        citations.append({"document":row["name"],"page":row["page"],"quote":row["content"][:360],"score":round(score,3),"document_id":row["document_id"]})
    context="\n\n".join(f"[{i+1}] {m['document']} page {m['page']}: {m['quote']}" for i,m in enumerate(citations))
    key=os.getenv("OPENAI_API_KEY")
    if key and citations:
        try:
            from openai import OpenAI
            client=OpenAI(api_key=key)
            result=client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),input=f"Answer only from the supplied sources. If sources do not support an answer, say so. Cite evidence inline as [1], [2].\n\nQuestion: {query}\n\nSources:\n{context}")
            return result.output_text,citations,round(sum(c["score"] for c in citations)/len(citations),3)
        except Exception:
            pass
    if not citations: return "I couldn’t find a relevant passage in your uploaded documents. Try different wording or upload a source that covers this topic.",[],0
    chunks=[c["quote"].strip() for c in citations[:3]]
    text="I found these relevant passages in your library:\n\n"+"\n\n".join(f"{i+1}. {p} [{i+1}]" for i,p in enumerate(chunks))
    return text,citations,round(sum(c["score"] for c in citations)/len(citations),3)

class MessageIn(BaseModel): content:str
@app.post("/api/chats/{cid}/messages")
def send_message(cid:int,body:MessageIn,user=Depends(current_user)):
    query=body.content.strip()
    if not query: raise HTTPException(400,"Write a question first")
    with db() as c:
        chat=c.execute("SELECT id,title FROM chats WHERE id=? AND user_id=?",(cid,user["id"])).fetchone()
        if not chat: raise HTTPException(404,"Conversation not found")
        c.execute("INSERT INTO messages(chat_id,role,content,created) VALUES(?,?,?,?)",(cid,"user",query,now()))
        if chat["title"]=="New conversation": c.execute("UPDATE chats SET title=? WHERE id=?",(query[:54]+("…" if len(query)>54 else ""),cid))
    matches=rank(query,user["id"]); answer,citations,score=answer_for(query,matches)
    with db() as c:
        cur=c.execute("INSERT INTO messages(chat_id,role,content,citations,score,created) VALUES(?,?,?,?,?,?)",(cid,"assistant",answer,__import__('json').dumps(citations),score,now())); mid=cur.lastrowid
        c.execute("UPDATE chats SET updated=? WHERE id=?",(now(),cid))
    return {"id":mid,"role":"assistant","content":answer,"citations":citations,"score":score}

@app.get("/api/evaluation")
def evaluation(user=Depends(current_user)):
    with db() as c:
        n=c.execute("SELECT COUNT(*) FROM documents WHERE user_id=?",(user["id"],)).fetchone()[0]
        rows=c.execute("SELECT messages.content,messages.citations,messages.score FROM messages JOIN chats ON chats.id=messages.chat_id WHERE chats.user_id=? AND messages.role='assistant'",(user["id"],)).fetchall()
    answers=len(rows); cited=sum(bool(__import__('json').loads(r["citations"])) for r in rows)
    return {"documents":n,"answers":answers,"citation_rate":round(cited/max(answers,1),2),"average_relevance":round(sum(r["score"] for r in rows)/max(answers,1),2),"questions":["What is the main policy?","Summarize the key requirements.","What exceptions are mentioned?","What dates or deadlines appear?","Which sources support this answer?"]}

STATIC=os.getenv("STATIC_DIR")
if STATIC and Path(STATIC).exists():
    app.mount("/assets",StaticFiles(directory=str(Path(STATIC)/"assets")),name="assets")
    @app.get("/{path:path}")
    def frontend(path:str):
        candidate=Path(STATIC)/path
        return FileResponse(candidate if path and candidate.is_file() else Path(STATIC)/"index.html")
