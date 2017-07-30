import pytest
import sdk_install
import sdk_test_upgrade
import sdk_utils

from tests.utils import (
    PACKAGE_NAME,
    DEFAULT_BROKER_COUNT
)

SERVICE_NAME = "test/upgrade/{}".format(PACKAGE_NAME)

@pytest.fixture(scope='module', autouse=True)
def configure_package(configure_universe):
    try:
        sdk_install.uninstall(PACKAGE_NAME)
        sdk_utils.gc_frameworks()

        yield # let the test session execute
    finally:
        sdk_install.uninstall(SERVICE_NAME)


@pytest.mark.upgrade
@pytest.mark.sanity
@pytest.mark.smoke
def test_upgrade_downgrade():
    options = {
        "service": {
            "name": SERVICE_NAME,
            "beta-optin": True,
            "user":"root"
        }
    }
    sdk_test_upgrade.upgrade_downgrade(
            "beta-{}".format(PACKAGE_NAME),
            PACKAGE_NAME, DEFAULT_BROKER_COUNT,
            additional_options=options,
            service_name=SERVICE_NAME,
            reinstall_test_version=False)


@pytest.mark.soak_upgrade
def test_upgrade():
    # akin to elastic soak_test_upgrade_downgrade
    test_upgrade_downgrade()
