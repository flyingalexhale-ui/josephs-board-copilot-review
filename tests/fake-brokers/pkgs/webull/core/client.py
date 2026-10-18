class ApiClient:
    def __init__(self, app_key, app_secret, region_id='us', *a, **k):
        self.app_key, self.region_id = app_key, region_id

    def add_endpoint(self, region, host):
        pass

    def set_stream_logger(self, *a, **k):
        pass
