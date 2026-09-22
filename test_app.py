"""End-to-end tests against the real Flask app using a throwaway database."""
import os, tempfile, json

tmp = tempfile.mkdtemp()
os.environ["FINANCE_DB"] = os.path.join(tmp, "test.db")
os.environ["SECRET_KEY"] = "test-key"

import app as appmod
client_a = appmod.app.test_client()
client_b = appmod.app.test_client()

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {name}")
    else:    failed += 1; print(f"  FAIL  {name} {extra}")

def reg(c, u, p="secret123"):
    r = c.post("/api/register", json={"username": u, "password": p})
    return r, (r.get_json() or {}).get("csrf")

def call(c, method, path, csrf=None, **kw):
    h = {"X-CSRF-Token": csrf} if csrf else {}
    return getattr(c, method)(path, headers=h, **kw)

print("== auth ==")
r, csrf_a = reg(client_a, "ronjal")
check("register ok", r.status_code == 201)
r, _ = reg(client_a, "RONJAL")
check("duplicate username (case-insensitive) rejected", r.status_code == 409)
r, _ = reg(client_b, "x")
check("short username rejected", r.status_code == 400)
r, _ = reg(client_b, "friend", "123")
check("short password rejected", r.status_code == 400)
r, csrf_b = reg(client_b, "friend")
check("second user registers", r.status_code == 201)
check("no session -> 401", appmod.app.test_client().get("/api/summary").status_code == 401)
bad = appmod.app.test_client().post("/api/login", json={"username": "ronjal", "password": "wrong"})
check("wrong password -> 401", bad.status_code == 401)

print("== CSRF ==")
r = client_a.post("/api/transactions", json={"type": "expense", "amount": 5, "category": "food"})
check("POST without csrf token -> 403", r.status_code == 403)

print("== add income / expense (features 1, 2) ==")
m = "2026-09"
r = call(client_a, "post", "/api/transactions", csrf_a, json={"type": "income", "amount": "50000", "category": "salary", "date": f"{m}-01"})
check("add income", r.status_code == 201 and r.get_json()["category"] == "Salary")
for cat, amt in [("food", 1200.50), ("FOOD", 300), ("Food", 99.5), ("transportation", 500), ("entertainment", 800), ("rent", 10000)]:
    r = call(client_a, "post", "/api/transactions", csrf_a, json={"type": "expense", "amount": amt, "category": cat, "date": f"{m}-05"})
    assert r.status_code == 201, r.get_json()
check("case variants merge into one category", True)
for bad_amt in [0, -5, "abc", None, "nan", "inf", 1e15]:
    r = call(client_a, "post", "/api/transactions", csrf_a, json={"type": "expense", "amount": bad_amt, "category": "food"})
    check(f"invalid amount {bad_amt!r} rejected", r.status_code == 400)
r = call(client_a, "post", "/api/transactions", csrf_a, json={"type": "expense", "amount": 5, "category": "   "})
check("blank category rejected", r.status_code == 400)
r = call(client_a, "post", "/api/transactions", csrf_a, json={"type": "bogus", "amount": 5, "category": "x"})
check("bad type rejected", r.status_code == 400)
r = call(client_a, "post", "/api/transactions", csrf_a, json={"type": "expense", "amount": 5, "category": "x", "date": "31-12-2026"})
check("bad date rejected", r.status_code == 400)

print("== view + search (features 3, 7) ==")
rows = client_a.get(f"/api/transactions?month={m}").get_json()
check("view all in month", len(rows) == 7, f"got {len(rows)}")
check("newest-first ordering", rows == sorted(rows, key=lambda x: (x["date"], x["id"]), reverse=True))
check("filter type=income", len(client_a.get("/api/transactions?type=income").get_json()) == 1)
check("filter category (case-insensitive)", len(client_a.get("/api/transactions?category=food").get_json()) == 3)
check("min/max range", len(client_a.get("/api/transactions?type=expense&min=500&max=1000").get_json()) == 2)
check("text search", len(client_a.get("/api/transactions?text=rent").get_json()) == 1)
check("literal % in search doesn't match everything", len(client_a.get("/api/transactions?text=%25").get_json()) == 0)
check("SQL-injection string is inert", client_a.get("/api/transactions?text=' OR 1=1 --").status_code == 200 and len(client_a.get("/api/transactions?text=' OR 1=1 --").get_json()) == 0)
check("bad month -> 400", client_a.get("/api/transactions?month=2026-13").status_code == 400)
check("bad min -> 400", client_a.get("/api/transactions?min=abc").status_code == 400)

print("== user isolation (friend must NOT see Ronjal's data) ==")
check("friend sees 0 transactions", client_b.get("/api/transactions").get_json() == [])
tx_id = rows[0]["id"]
r = call(client_b, "delete", f"/api/transactions/{tx_id}", csrf_b)
check("friend cannot delete Ronjal's transaction", r.status_code == 404)
check("Ronjal's data intact", len(client_a.get("/api/transactions").get_json()) == 7)

print("== budget (feature 4) ==")
check("default budget is 0", client_a.get(f"/api/budget?month={m}").get_json()["amount"] == 0)
r = call(client_a, "put", "/api/budget", csrf_a, json={"month": m, "amount": 20000})
check("set budget", r.status_code == 200)
r = call(client_a, "put", "/api/budget", csrf_a, json={"month": m, "amount": 25000})
check("update budget (upsert)", client_a.get(f"/api/budget?month={m}").get_json()["amount"] == 25000)
r = call(client_a, "put", "/api/budget", csrf_a, json={"month": m, "amount": -1})
check("negative budget rejected", r.status_code == 400)
check("budget is per-user", client_b.get(f"/api/budget?month={m}").get_json()["amount"] == 0)

print("== category summary (feature 5) ==")
cs = client_a.get(f"/api/categories/summary?month={m}").get_json()
food = next(c for c in cs["categories"] if c["category"] == "Food")
check("Food total = 1200.50+300+99.5 = 1600", food["total"] == 1600.0, str(food))
check("total expenses = 1600+500+800+10000 = 12900", cs["total_expenses"] == 12900.0, str(cs["total_expenses"]))
check("percents sum ~100", abs(sum(c["percent"] for c in cs["categories"]) - 100) < 0.5)
check("custom category 'Rent' appears (not limited to 4)", any(c["category"] == "Rent" for c in cs["categories"]))

print("== summary (feature 6) ==")
s = client_a.get(f"/api/summary?month={m}").get_json()
check("income 50000", s["income"] == 50000)
check("expenses 12900", s["expenses"] == 12900, str(s["expenses"]))
check("remaining 37100", s["remaining"] == 37100, str(s["remaining"]))
check("saving rate 74.2%", s["saving_rate"] == 74.2, str(s["saving_rate"]))
check("budget remaining 25000-12900 = 12100", s["budget_remaining"] == 12100, str(s["budget_remaining"]))
check("not over budget", s["over_budget"] is False)
call(client_a, "put", "/api/budget", csrf_a, json={"month": m, "amount": 10000})
check("over_budget flag works", client_a.get(f"/api/summary?month={m}").get_json()["over_budget"] is True)
e = client_b.get(f"/api/summary?month={m}").get_json()
check("ZERO income + no budget doesn't crash (orig bug)", e["saving_rate"] is None and e["budget_remaining"] is None)
call(client_b, "post", "/api/transactions", csrf_b, json={"type": "expense", "amount": 50, "category": "food", "date": f"{m}-02"})
e = client_b.get(f"/api/summary?month={m}").get_json()
check("expense-only month: no division by zero", e["saving_rate"] is None and e["remaining"] == -50)

print("== delete (feature 8) ==")
for _ in range(2):
    call(client_a, "post", "/api/transactions", csrf_a, json={"type": "expense", "amount": 77, "category": "snacks", "date": f"{m}-09"})
dupes = client_a.get("/api/transactions?category=snacks").get_json()
check("two identical transactions exist", len(dupes) == 2)
r = call(client_a, "delete", f"/api/transactions/{dupes[0]['id']}", csrf_a)
check("delete removes exactly ONE (orig removed all matches)", r.status_code == 200 and len(client_a.get("/api/transactions?category=snacks").get_json()) == 1)
check("delete nonexistent -> 404", call(client_a, "delete", "/api/transactions/99999", csrf_a).status_code == 404)

print("== persistence, export, month isolation ==")
check("other months are empty", client_a.get("/api/summary?month=2026-01").get_json()["income"] == 0)
csvr = client_a.get("/api/export")
check("CSV export", csvr.status_code == 200 and b"date,type,category,amount,note" in csvr.data)
check("logout works", call(client_a, "post", "/api/logout", csrf_a).status_code == 200)
check("after logout -> 401", client_a.get("/api/summary").status_code == 401)
r = client_a.post("/api/login", json={"username": "RONJAL", "password": "secret123"})
check("login (case-insensitive username) and data persisted", r.status_code == 200 and len(client_a.get("/api/transactions").get_json()) == 8)

print("== pages / PWA ==")
check("index page", client_a.get("/").status_code == 200)
check("manifest", client_a.get("/manifest.webmanifest").status_code == 200)
sw = client_a.get("/sw.js")
check("service worker + scope header", sw.status_code == 200 and sw.headers.get("Service-Worker-Allowed") == "/")
check("icons served", all(client_a.get(f"/static/icons/{n}").status_code == 200 for n in ["icon-192.png", "icon-512.png", "icon-maskable-512.png"]))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
