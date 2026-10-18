"""Loads watchlist.yaml (and, in Stocks mode, stocklist.yaml) and checks it, failing fast with
a clear message instead of silently scanning nothing because of a typo.

Which list is scanned -- ETFs or stocks -- is kept in scan_mode.txt next to watchlist.yaml
(one word: etfs or stocks). The Scanner tab's ETFs / Stocks buttons write it."""
import os
import re

import yaml


class ConfigError(Exception):
    pass


MODES = ('etfs', 'stocks')


def mode_file(config_path):
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), 'scan_mode.txt')


def read_mode(config_path):
    try:
        with open(mode_file(config_path)) as f:
            m = f.read().strip().lower()
    except OSError:
        return 'etfs'
    return m if m in MODES else 'etfs'


def write_mode(config_path, mode):
    if mode not in MODES:
        raise ValueError(mode)
    tmp = mode_file(config_path) + '.tmp'
    with open(tmp, 'w') as f:
        f.write(mode + '\n')
    os.replace(tmp, mode_file(config_path))


def _symbols(raw, path):
    syms = []
    for s in raw or []:
        s = str(s).upper().strip()
        if not re.match(r'^[A-Z0-9][A-Z0-9.\-]{0,9}$', s):
            raise ConfigError(f"{path}: {s!r} in symbols doesn't look like an ETF/stock ticker "
                              f"(the scanner watches stocks/ETFs, not futures)")
        if s not in syms:
            syms.append(s)
    if not syms:
        raise ConfigError(f"{path}: 'symbols' is empty -- add at least one ticker.")
    return sorted(syms)


def _load(path):
    with open(path) as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f'{path} has a formatting mistake: {e}')


class Config:
    def __init__(self, data, path='<dict>', mode='etfs', stocks=None, stocks_path='stocklist.yaml'):
        if not isinstance(data, dict):
            raise ConfigError(f'{path}: expected settings like "symbols:" and "detectors:" -- is the file empty?')
        self.path = path
        try:
            minutes = float(data.get('window_minutes', 5))
        except (TypeError, ValueError):
            raise ConfigError(f"{path}: window_minutes must be a number")
        if minutes <= 0:
            raise ConfigError(f"{path}: window_minutes must be more than 0")
        self.window_ms = int(minutes * 60 * 1000)
        self.baseline_windows = int(data.get('baseline_windows', 12))

        self.mode = mode if mode in MODES else 'etfs'
        self.detectors, det_path = data.get('detectors') or {}, path
        if self.mode == 'stocks':
            if not isinstance(stocks, dict):
                raise ConfigError(f'{stocks_path}: expected a "symbols:" list of stocks -- is the file empty?')
            self.symbols = _symbols(stocks.get('symbols'), stocks_path)
            if stocks.get('detectors'):           # stocks may have their own thresholds
                self.detectors, det_path = stocks['detectors'], stocks_path
        else:
            self.symbols = _symbols(data.get('symbols'), path)

        from .detectors import REGISTRY
        unknown = sorted(k for k in self.detectors if k not in REGISTRY)
        if unknown:
            raise ConfigError(f"{det_path}: unknown detector name(s) {unknown} -- the available ones are "
                              f"{sorted(REGISTRY)} (check the spelling)")
        if not any(v is not False for v in self.detectors.values()):
            raise ConfigError(f"{det_path}: every detector is off -- nothing would ever be flagged.")

        feeds = data.get('feeds') or {}
        self.use_webull = bool(feeds.get('webull', True))
        self.use_ibkr = bool(feeds.get('ibkr', True))
        self.prefer = str(feeds.get('prefer', 'ibkr')).lower()
        if self.prefer not in ('ibkr', 'webull'):
            raise ConfigError(f"{path}: feeds.prefer must be ibkr or webull")
        if not self.use_webull and not self.use_ibkr:
            raise ConfigError(f"{path}: both feeds are off -- there'd be no data to scan.")

    @classmethod
    def from_yaml(cls, path, mode='etfs'):
        stocks_path = os.path.join(os.path.dirname(os.path.abspath(path)), 'stocklist.yaml')
        stocks = _load(stocks_path) if mode == 'stocks' else None
        return cls(_load(path), path=path, mode=mode, stocks=stocks, stocks_path=stocks_path)
