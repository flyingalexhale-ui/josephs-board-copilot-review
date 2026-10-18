class ClientException(Exception):
    def __init__(self, code, msg=''):
        super().__init__(f'{code} {msg}')
        self.error_code, self.error_msg = code, msg


class ServerException(Exception):
    def __init__(self, code, msg='', http_status=None, request_id=None):
        super().__init__(f'HTTP Status: {http_status}, Code: {code}, Msg: {msg}')
        self.error_code, self.error_msg, self.http_status = code, msg, http_status
