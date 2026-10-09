from fastapi.testclient import TestClient

from app.main import app

SHOP = "plans-test.myshopify.com"


def test_plans_and_trial(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", "k")
    h = {"Authorization": "Bearer k"}
    with TestClient(app) as c:
        assert c.get(f"/plans?shop={SHOP}").status_code == 401
        p = c.get(f"/plans?shop={SHOP}", headers=h).json()
        assert p["effective_plan"] == "free" and p["trial_available"] and "behaviour" not in p["features"]
        t = c.post("/plans", headers=h, json={"shop": SHOP, "start_trial": True}).json()
        assert t["on_trial"] and t["effective_plan"] == "trust" and t["trial_days_left"] == 14
        assert c.post("/plans", headers=h, json={"shop": SHOP, "start_trial": True}).status_code == 409
        g = c.post("/plans", headers=h, json={"shop": SHOP, "plan": "growth"}).json()
        assert g["plan"] == "growth" and g["effective_plan"] == "trust"  # trial still running
        assert c.post("/plans", headers=h, json={"shop": SHOP, "plan": "gold"}).status_code == 400
