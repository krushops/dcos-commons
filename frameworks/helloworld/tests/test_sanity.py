import logging
import re

import dcos.marathon
import pytest
import retrying

import sdk_cmd
import sdk_install
import sdk_marathon
import sdk_tasks
import sdk_upgrade
import sdk_utils
import shakedown
from tests import config


log = logging.getLogger(__name__)


@pytest.fixture(scope='module', autouse=True)
def configure_package(configure_security):
    try:
        sdk_install.uninstall(config.PACKAGE_NAME, sdk_utils.get_foldered_name(config.SERVICE_NAME))
        sdk_upgrade.test_upgrade(
            config.PACKAGE_NAME,
            sdk_utils.get_foldered_name(config.SERVICE_NAME),
            config.DEFAULT_TASK_COUNT,
            additional_options={"service": {"name": sdk_utils.get_foldered_name(config.SERVICE_NAME), "user": "root"}})

        yield  # let the test session execute
    finally:
        sdk_install.uninstall(config.PACKAGE_NAME, sdk_utils.get_foldered_name(config.SERVICE_NAME))


def close_enough(val0, val1):
    epsilon = 0.00001
    diff = abs(val0 - val1)
    return diff < epsilon


@pytest.mark.smoke
def test_install():
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))


# Note: presently the mesos v1 api does _not_ work in strict mode.
# As such, we expect this test to fail until it does in fact work in strict mode.
@pytest.mark.sanity
def test_mesos_v1_api():
    # Install Hello World using the v1 api.
    # Then, clean up afterwards.
    sdk_install.uninstall(config.PACKAGE_NAME, sdk_utils.get_foldered_name(config.SERVICE_NAME))
    sdk_install.install(
        config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME),
        config.DEFAULT_TASK_COUNT,
        additional_options={"service":
            {"name": sdk_utils.get_foldered_name(config.SERVICE_NAME), "mesos_api_version": "V1"}}
    )
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    sdk_install.uninstall(config.PACKAGE_NAME, sdk_utils.get_foldered_name(config.SERVICE_NAME))

    # reinstall the v0 version for the following tests
    sdk_install.install(
        config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME),
        config.DEFAULT_TASK_COUNT,
        additional_options={"service": {"name": sdk_utils.get_foldered_name(config.SERVICE_NAME)}})


@pytest.mark.sanity
@pytest.mark.smoke
def test_bump_hello_cpus():
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    hello_ids = sdk_tasks.get_task_ids(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'hello')
    log.info('hello ids: ' + str(hello_ids))

    updated_cpus = config.bump_hello_cpus(sdk_utils.get_foldered_name(config.SERVICE_NAME))

    sdk_tasks.check_tasks_updated(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'hello', hello_ids)
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))

    all_tasks = shakedown.get_service_tasks(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    running_tasks = [t for t in all_tasks if t['name'].startswith('hello') and t['state'] == "TASK_RUNNING"]
    assert len(running_tasks) == config.hello_task_count(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    for t in running_tasks:
        assert close_enough(t['resources']['cpus'], updated_cpus)


@pytest.mark.sanity
@pytest.mark.smoke
def test_bump_world_cpus():
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    world_ids = sdk_tasks.get_task_ids(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'world')
    log.info('world ids: ' + str(world_ids))

    updated_cpus = config.bump_world_cpus(sdk_utils.get_foldered_name(config.SERVICE_NAME))

    sdk_tasks.check_tasks_updated(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'world', world_ids)
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))

    all_tasks = shakedown.get_service_tasks(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    running_tasks = [t for t in all_tasks if t['name'].startswith('world') and t['state'] == "TASK_RUNNING"]
    assert len(running_tasks) == config.world_task_count(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    for t in running_tasks:
        assert close_enough(t['resources']['cpus'], updated_cpus)


@pytest.mark.sanity
@pytest.mark.smoke
def test_bump_hello_nodes():
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))

    hello_ids = sdk_tasks.get_task_ids(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'hello')
    log.info('hello ids: ' + str(hello_ids))

    sdk_marathon.bump_task_count_config(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'HELLO_COUNT')

    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    sdk_tasks.check_tasks_not_updated(sdk_utils.get_foldered_name(config.SERVICE_NAME), 'hello', hello_ids)


@pytest.mark.sanity
def test_pod_list():
    jsonobj = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'pod list', json=True)
    assert len(jsonobj) == config.configured_task_count(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    # expect: X instances of 'hello-#' followed by Y instances of 'world-#',
    # in alphanumerical order
    first_world = -1
    for i in range(len(jsonobj)):
        entry = jsonobj[i]
        if first_world < 0:
            if entry.startswith('world-'):
                first_world = i
        if first_world == -1:
            assert jsonobj[i] == 'hello-{}'.format(i)
        else:
            assert jsonobj[i] == 'world-{}'.format(i - first_world)


@pytest.mark.sanity
def test_pod_status_all():
    jsonobj = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'pod status', json=True)
    assert len(jsonobj) == config.configured_task_count(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    for k, v in jsonobj.items():
        assert re.match('(hello|world)-[0-9]+', k)
        assert len(v) == 1
        task = v[0]
        assert len(task) == 3
        assert re.match('(hello|world)-[0-9]+-server__[0-9a-f-]+', task['id'])
        assert re.match('(hello|world)-[0-9]+-server', task['name'])
        assert task['state'] == 'TASK_RUNNING'


@pytest.mark.sanity
def test_pod_status_one():
    jsonobj = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'pod status hello-0', json=True)
    assert len(jsonobj) == 1
    task = jsonobj[0]
    assert len(task) == 3
    assert re.match('hello-0-server__[0-9a-f-]+', task['id'])
    assert task['name'] == 'hello-0-server'
    assert task['state'] == 'TASK_RUNNING'


@pytest.mark.sanity
def test_pod_info():
    jsonobj = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'pod info hello-1', json=True)
    assert len(jsonobj) == 1
    task = jsonobj[0]
    assert len(task) == 2
    assert task['info']['name'] == 'hello-1-server'
    assert task['info']['taskId']['value'] == task['status']['taskId']['value']
    assert task['status']['state'] == 'TASK_RUNNING'


@retrying.retry(
    wait_fixed=10000,
    stop_max_delay=30000)
def wait_for_nonempty_properties():
    """'suppressed' could be missing if the scheduler recently started,
    loop for a bit just in case
    """
        jsonobj = sdk_cmd.svc_cli(config.PACKAGE_NAME, config.FOLDERED_SERVICE_NAME, 'state properties', json=True)
    assert len(jsonobj) > 0


@pytest.mark.sanity
def test_state_properties_get():
    wait_for_nonempty_properties()

    jsonobj = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'state properties', json=True)
    assert len(jsonobj) == 6
    # alphabetical ordering:
    assert jsonobj[0] == "hello-0-server:task-status"
    assert jsonobj[1] == "hello-1-server:task-status"
    assert jsonobj[2] == "last-completed-update-type"
    assert jsonobj[3] == "suppressed"
    assert jsonobj[4] == "world-0-server:task-status"
    assert jsonobj[5] == "world-1-server:task-status"

    stdout = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'state property suppressed')
    assert stdout == "true\n"


@retrying.retry(
    wait_fixed=10000,
    stop_max_delay=120000,
    retry_on_result=lambda res: res is False)
def wait_for_refresh_cache_fails_409conflict():
    """caching disabled, refresh_cache should fail with a
    409 error (eventually, once scheduler is up)
    """
    try:
        sdk_cmd.svc_cli(
            config.PACKAGE_NAME, config.FOLDERED_SERVICE_NAME, 'state refresh_cache')
    except Exception as e:
        if "failed: 409 Conflict" in e.args[0]:
            return True
    return False


@retrying.retry(
    wait_fixed=10000,
    stop_max_delay=120000)
def wait_for_cache_refresh():
    return sdk_cmd.svc_cli(
        config.PACKAGE_NAME, config.FOLDERED_SERVICE_NAME, 'state refresh_cache')


@pytest.mark.sanity
def test_state_refresh_disable_cache():
    '''Disables caching via a scheduler envvar'''
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    task_ids = sdk_tasks.get_task_ids(sdk_utils.get_foldered_name(config.SERVICE_NAME), '')

    # caching enabled by default:
    stdout = sdk_cmd.svc_cli(config.PACKAGE_NAME,
        sdk_utils.get_foldered_name(config.SERVICE_NAME), 'state refresh_cache')
    assert "Received cmd: refresh" in stdout

    marathon_config = sdk_marathon.get_config(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    marathon_config['env']['DISABLE_STATE_CACHE'] = 'any-text-here'
    sdk_marathon.update_app(sdk_utils.get_foldered_name(config.SERVICE_NAME), marathon_config)

    sdk_tasks.check_tasks_not_updated(sdk_utils.get_foldered_name(config.SERVICE_NAME), '', task_ids)
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))

    wait_for_refresh_cache_fails_409conflict()

    marathon_config = sdk_marathon.get_config(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    del marathon_config['env']['DISABLE_STATE_CACHE']
    sdk_marathon.update_app(sdk_utils.get_foldered_name(config.SERVICE_NAME), marathon_config)

    sdk_tasks.check_tasks_not_updated(sdk_utils.get_foldered_name(config.SERVICE_NAME), '', task_ids)
    config.check_running(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    shakedown.deployment_wait()  # ensure marathon thinks the deployment is complete too

    # caching reenabled, refresh_cache should succeed (eventually, once scheduler is up):

    stdout = wait_for_cache_refresh()
    assert "Received cmd: refresh" in stdout


@pytest.mark.sanity
def test_lock():
    '''This test verifies that a second scheduler fails to startup when
    an existing scheduler is running.  Without locking, the scheduler
    would fail during registration, but after writing its config to ZK.
    So in order to verify that the scheduler fails immediately, we ensure
    that the ZK config state is unmodified.'''

    marathon_client = dcos.marathon.create_client()

    # Get ZK state from running framework
    zk_path = "dcos-service-{}/ConfigTarget".format(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    zk_config_old = shakedown.get_zk_node_data(zk_path)

    # Get marathon app
    app = marathon_client.get_app(sdk_utils.get_foldered_name(config.SERVICE_NAME))
    old_timestamp = app.get("lastTaskFailure", {}).get("timestamp", None)

    # Scale to 2 instances
    labels = app["labels"]
    original_labels = labels.copy()
    labels.pop("MARATHON_SINGLE_INSTANCE_APP")
    marathon_client.update_app(sdk_utils.get_foldered_name(config.SERVICE_NAME), {"labels": labels})
    shakedown.deployment_wait()
    marathon_client.update_app(sdk_utils.get_foldered_name(config.SERVICE_NAME), {"instances": 2})

    @retrying.retry(
        wait_fixed=10000,
        stop_max_delay=120000)
    def wait_for_second_scheduler_to_fail():
        timestamp = marathon_client.get_app(FOLDERED_SERVICE_NAME).get("lastTaskFailure", {}).get("timestamp", None)
        assert timestamp != old_timestamp

    wait_for_second_scheduler_to_fail()

    # Verify ZK is unchanged
    zk_config_new = shakedown.get_zk_node_data(zk_path)
    assert zk_config_old == zk_config_new

    # In order to prevent the second scheduler instance from obtaining a lock, we undo the "scale-up" operation
    marathon_client.update_app(sdk_utils.get_foldered_name(config.SERVICE_NAME),
        {"labels": original_labels, "instances": 1}, force=True)
    shakedown.deployment_wait()
