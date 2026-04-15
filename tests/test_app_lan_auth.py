import unittest

from app import app, configure_lan_access


class LanAuthTests(unittest.TestCase):
    def tearDown(self):
        configure_lan_access(enabled=False)

    def test_remote_api_requires_pin_when_lan_auth_enabled(self):
        configure_lan_access(enabled=True, pin="123456")
        with app.test_client() as client:
            response = client.get("/api/project/status", environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(401, response.status_code)

    def test_remote_api_allows_after_pin(self):
        configure_lan_access(enabled=True, pin="123456")
        with app.test_client() as client:
            auth = client.post("/api/lan-auth", json={"pin": "123456"}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
            response = client.get("/api/project/status", environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(200, auth.status_code)
        self.assertEqual(200, response.status_code)


if __name__ == "__main__":
    unittest.main()
