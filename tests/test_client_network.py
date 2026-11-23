from __future__ import annotations

import unittest

from client.network import NetworkClient


class NetworkDeliveryTests(unittest.TestCase):
    def test_queued_request_is_reported_failed_instead_of_replayed(self) -> None:
        network = NetworkClient("127.0.0.1", 9)
        network._connected.set()
        request_id = network.send("message.send", {"content": "hello"})
        network._connected.clear()
        network._fail_queued_requests()
        failure = network.events.get_nowait()
        self.assertEqual(failure["type"], "network.send_failed")
        self.assertEqual(failure["payload"]["request_id"], request_id)
        self.assertTrue(network._outgoing.empty())


if __name__ == "__main__":
    unittest.main()
