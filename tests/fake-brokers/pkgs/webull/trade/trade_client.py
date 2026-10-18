"""Fake Webull trade API: an empty PAPER account. Placing an order is refused and logged loudly (a live run
must never place one)."""
from webull.core.exception.exceptions import ServerException


class _Resp:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class _Account:
    def get_account_list(self):
        return _Resp([{'account_id': 'FAKEWB1', 'account_type': 'MARGIN', 'account_class': 'PAPER SIMULATED'}])

    def get_account_position(self, account_id, *a, **k):
        return _Resp({'holdings': []})

    def get_account_balance(self, account_id, currency='USD', *a, **k):
        return _Resp({'day_buying_power': '400000', 'overnight_buying_power': '200000'})


class _Orders:
    def get_order_history(self, account_id, page_size=100, start_date=None, end_date=None, **k):
        return _Resp({'orders': []})

    def get_order_open(self, account_id, page_size=None, **k):
        return _Resp({'orders': []})

    def get_order_detail(self, account_id, client_order_id):
        raise ServerException('ORDER_NOT_FOUND', 'no such order', 404)

    def place_order(self, account_id, new_orders, **k):
        print('!!! fake Webull: ORDER PLACED -- refused:', new_orders, flush=True)
        raise ServerException('FAKE_NO_ORDERS', 'fake Webull takes no orders', 403)

    def cancel_order(self, account_id, client_order_id):
        return _Resp({'ok': True})

    def preview_order(self, account_id, new_orders, **k):
        return _Resp({'ok': True, 'preview': True})          # a preview is never an order


class TradeClient:
    def __init__(self, api):
        self.account_v2, self.order_v3 = _Account(), _Orders()
