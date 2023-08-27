from configurator import Config

from clm.utils.config import config


def test_config_exists():
    assert isinstance(config, Config)


def test_num_non_worker_cores_exists():
    assert isinstance(config.num_non_worker_cores, int)


def test_num_win_workers_exists():
    assert isinstance(config.num_win_workers, int)
