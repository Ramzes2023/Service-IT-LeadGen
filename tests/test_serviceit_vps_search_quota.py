import httpx
from unittest import TestCase

from leadpilot.serviceit_vps_worker import SEARCH_REQUESTS_PER_CARD, SearchBudget


class SearchQuotaTest(TestCase):
    def test_discovery_is_sent_only_in_new_card_scope_and_bounded(self):
        sent = []
        budget = SearchBudget()

        def respond(request):
            sent.append(request.url.host)
            return httpx.Response(200, json={})

        brave = "https://api.search.brave.com/res/v1/web/search"
        with httpx.Client(transport=httpx.MockTransport(respond), event_hooks={"request": [budget.check]}) as client:
            for old_provider in (brave, "https://api.search.tinyfish.ai/", "https://api.tavily.com/search"):
                with self.assertRaises(httpx.RequestError):
                    client.get(old_provider)
            assert sent == []
            client.get("https://www.b2b-center.ru/robots.txt")
            with budget.for_card(True):
                for _ in range(SEARCH_REQUESTS_PER_CARD):
                    client.get(brave)
                with self.assertRaises(httpx.RequestError):
                    client.get(brave)
            with self.assertRaises(httpx.RequestError):
                client.get(brave)
        assert budget.sent == SEARCH_REQUESTS_PER_CARD
        assert len(sent) == SEARCH_REQUESTS_PER_CARD + 1
