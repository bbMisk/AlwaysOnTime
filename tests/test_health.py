import importlib
from app.factory import create_app

def test_health():
    app = create_app().test_client()
    resp = app.get('/health')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'
