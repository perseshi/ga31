import os, json, hashlib, sqlite3, threading, asyncio, uuid, re, datetime
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import traceback

BASE_URL = os.getenv("PUBLIC_BASE_URL", os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000")).rstrip("/") + "/"
LLM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LLM_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LLM_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MEDIA = "application/a2a+json"
BATCH_T = "application/vnd.ga5.invoice-claim-batch+json"
PROP_T = "application/vnd.ga5.invoice-action-proposals+json"
RES_T = "application/vnd.ga5.invoice-action-results+json"
REC_T = "application/vnd.ga5.invoice-action-receipts+json"
ACTIONS = {"settle_invoice", "request_approval", "hold_invoice", "reject_duplicate", "open_exception"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"}

app_Q2 = FastAPI()
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
db = sqlite3.connect(os.path.join(DATA_DIR, "state.db"), check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, principal TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS msgs(principal TEXT, mid TEXT, hash TEXT, task_id TEXT, PRIMARY KEY(principal, mid));
CREATE TABLE IF NOT EXISTS cache(h TEXT PRIMARY KEY, body TEXT);
""")
dblock = threading.RLock()      # makes "receipt vs cancel" a fair one-at-a-time race
key_locks = {}                  # makes concurrent duplicate messages wait for the first one

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def canon(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def reply(obj, code=200): return JSONResponse(obj, status_code=code, media_type=MEDIA)
def err(code, name, msg="Request failed"): return reply({"error": {"code": name, "message": msg}}, code)

# ---------- gatekeeping ----------
def gate(req: Request, post=False):
    auth = req.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not auth[7:].strip():
        return None, err(401, "UNAUTHENTICATED", "Authentication required")
    if req.headers.get("a2a-version") != "1.0":
        return None, err(400, "VERSION_NOT_SUPPORTED", "Unsupported version")
    if post and not req.headers.get("content-type", "").startswith(MEDIA):
        return None, err(415, "UNSUPPORTED_MEDIA_TYPE", "Use application/a2a+json")
    return sha(auth[7:].strip()), None      # principal = hash of the token

# ---------- storage ----------
def load(tid, principal):
    r = db.execute("SELECT body FROM tasks WHERE id=? AND principal=?", (tid, principal)).fetchone()
    return json.loads(r[0]) if r else None
def save(task, principal):
    db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?)", (task["id"], principal, json.dumps(task)))

# ---------- public Agent Card ----------
@app_Q2.get("/.well-known/agent-card.json")
@app_Q2.get("/a2a/.well-known/agent-card.json", include_in_schema=False)
def card():
    return reply({
        "name": "Invoice Action Agent",
        "description": "Reads invoice packages, proposes safe business actions with evidence, executes only approved ones.",
        "version": "1.0.0",
        "supportedInterfaces": [{"url": BASE_URL, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": [BATCH_T, RES_T],
        "defaultOutputModes": [PROP_T, REC_T],
        "skills": [{"id": "invoice_action_agent", "name": "Invoice action agent",
                    "description": "Choose settle/approve/hold/reject/exception for each invoice.",
                    "tags": ["invoice", "finance", "approval"]}],
    })

# ---------- AI reasoning and semantic decision cache ----------
from reasoning import Reasoner, CACHE_VERSION

def decision_key(policy, package):
    content = {k: v for k, v in package.items() if k != "packageId"}
    return sha(CACHE_VERSION + canon({"model": LLM_MODEL, "policy": policy, "package": content}))

def decision_cache_get(policy, package):
    with dblock:
        row = db.execute("SELECT body FROM cache WHERE h=?", (decision_key(policy, package),)).fetchone()
    return json.loads(row[0]) if row else None

def decision_cache_put(policy, package, decision):
    with dblock:
        db.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", (decision_key(policy, package), json.dumps(decision)))
        db.commit()

reasoner = Reasoner(LLM_KEY, LLM_MODEL, decision_cache_get, decision_cache_put)

async def decide(policy, packages):
    return await reasoner.decide(policy, packages)

def proposal(p, d, policy):
    a, r = d["action"], d["evidenceRefs"]
    rationale = (f"Proposing {a} for invoice {d['invoiceNumber']} from {d['vendorName']}. "
                 f"{d.get('reason', '')} Evidence {r[0]}, {r[1]} and {r[2]} in the controlling paragraph support {a}.")[:1500]
    return {"packageId": p["packageId"], "actionId": "act-" + uuid.uuid4().hex, "action": a,
            "facts": {"vendorName": d["vendorName"], "invoiceNumber": d["invoiceNumber"],
                      "amountMinor": d["amountMinor"], "currency": d["currency"]},
            "evidenceRefs": r, "rationale": rationale}

# ---------- message:send ----------
@app_Q2.post("/message:send")
@app_Q2.post("/a2a/message:send", include_in_schema=False)
async def message_send(req: Request):
    principal, bad = gate(req, post=True)
    if bad: return bad
    try:
        body = await req.json(); msg = body["message"]; mid = msg["messageId"]
        assert isinstance(msg["parts"], list)
    except Exception:
        return err(400, "INVALID_REQUEST")
    if not msg.get("taskId") and not LLM_KEY:
        return err(503, "NOT_CONFIGURED", "Set GEMINI_API_KEY in the server environment")
    h = sha(canon(msg))                         # configuration is ignored on purpose
    lock = key_locks.setdefault((principal, mid), asyncio.Lock())
    async with lock:
        with dblock:
            seen = db.execute("SELECT hash, task_id FROM msgs WHERE principal=? AND mid=?", (principal, mid)).fetchone()
        if seen:
            if seen[0] != h: return err(409, "IDEMPOTENCY_CONFLICT", "Message id reused with different content")
            return reply({"task": load(seen[1], principal)})
        part = msg["parts"][0]
        if msg.get("taskId"):
            return continuation(principal, msg, part, mid, h)
        if part.get("mediaType") != BATCH_T: return err(400, "INVALID_REQUEST")
        data = part["data"]; pkgs = data["packages"]
        decisions = await decide(data["policyRevision"], pkgs)       # the only AI call
        props = [proposal(p, decisions[p["packageId"]], data["policyRevision"]) for p in pkgs]
        tid, cid = str(uuid.uuid4()), str(uuid.uuid4())
        task = {"id": tid, "contextId": cid,
                "status": {"state": "TASK_STATE_INPUT_REQUIRED", "timestamp": now()},
                "artifacts": [{"artifactId": "proposals", "name": "invoice-action-proposals",
                               "parts": [{"mediaType": PROP_T, "data": {"batchId": data["batchId"], "proposals": props}}]}],
                "history": [{**msg, "taskId": tid, "contextId": cid}]}
        with dblock:                                                   # store BEFORE responding
            save(task, principal)
            db.execute("INSERT INTO msgs VALUES(?,?,?,?)", (principal, mid, h, tid))
            db.commit()
        return reply({"task": task})

def continuation(principal, msg, part, mid, h):
    with dblock:
        task = load(msg["taskId"], principal)
        if not task: return err(404, "NOT_FOUND", "Task not found")
        if msg.get("contextId") != task["contextId"]: return err(400, "INVALID_REQUEST")
        if task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED": return err(409, "TASK_NOT_ACTIONABLE")
        if part.get("mediaType") != RES_T: return err(400, "INVALID_REQUEST")
        stored = task["artifacts"][0]["parts"][0]["data"]
        data = part["data"]
        if data.get("batchId") != stored["batchId"]: return err(400, "INVALID_REQUEST")
        byid = {p["packageId"]: p for p in stored["proposals"]}
        execs, seen = [], set()
        for r in data.get("results", []):
            p = byid.get(r.get("packageId"))
            if (not p or r["packageId"] in seen or r.get("actionId") != p["actionId"]
                    or r.get("action") != p["action"] or r.get("outcome") not in ("ACCEPTED", "REJECTED")):
                return err(400, "INVALID_REQUEST")
            seen.add(r["packageId"])
            if r["outcome"] == "ACCEPTED":
                if not r.get("receiptNonce"): return err(400, "INVALID_REQUEST")
                execs.append({"packageId": p["packageId"], "actionId": p["actionId"], "action": p["action"],
                              "receiptNonce": r["receiptNonce"], "facts": p["facts"], "evidenceRefs": p["evidenceRefs"]})
        task["artifacts"].append({"artifactId": "receipts", "name": "invoice-action-receipts",
                                  "parts": [{"mediaType": REC_T, "data": {"batchId": stored["batchId"], "executions": execs}}]})
        task["history"].append({**msg})
        task["status"] = {"state": "TASK_STATE_COMPLETED", "timestamp": now()}
        save(task, principal)
        db.execute("INSERT INTO msgs VALUES(?,?,?,?)", (principal, mid, h, task["id"]))
        db.commit()
        return reply({"task": task})

# ---------- reads, list, cancel (NO model calls) ----------
@app_Q2.get("/tasks/{tid}")
@app_Q2.get("/a2a/tasks/{tid}", include_in_schema=False)
def get_task(tid: str, req: Request):
    principal, bad = gate(req)
    if bad: return bad
    with dblock: t = load(tid, principal)
    return reply(t) if t else err(404, "NOT_FOUND", "Task not found")

MAX_LIST_BYTES = 350_000

@app_Q2.get("/tasks")
@app_Q2.get("/a2a/tasks", include_in_schema=False)
def list_tasks(req: Request):
    principal, bad = gate(req)
    if bad: return bad
    with dblock:
        rows = db.execute(
            "SELECT body FROM tasks WHERE principal=? ORDER BY rowid DESC LIMIT 20",
            (principal,)).fetchall()
    tasks, size = [], 0
    for (b,) in rows:
        t = {k: v for k, v in json.loads(b).items() if k != "history"}   # drop the huge history
        n = len(json.dumps(t))
        if tasks and size + n > MAX_LIST_BYTES:
            break
        tasks.append(t)
        size += n
    return reply({"tasks": tasks})

@app_Q2.post("/tasks/{tid}:cancel")
@app_Q2.post("/a2a/tasks/{tid}:cancel", include_in_schema=False)
def cancel(tid: str, req: Request):
    principal, bad = gate(req)
    if bad: return bad
    with dblock:
        t = load(tid, principal)
        if not t: return err(404, "NOT_FOUND", "Task not found")
        if t["status"]["state"] in TERMINAL: return err(409, "TASK_NOT_CANCELABLE")
        t["status"] = {"state": "TASK_STATE_CANCELED", "timestamp": now()}
        save(t, principal); db.commit()
    return reply(t)


@app_Q2.get("/healthz")
def healthz():
    return {"status": "ok", "geminiConfigured": bool(LLM_KEY)}

