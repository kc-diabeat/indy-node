#!/usr/bin/python3.5
import os
import subprocess

from stp_core.common.log import getlogger

from indy_common.util import compose_cmd
from indy_node.utils.node_control_tool import TIMEOUT

logger = getlogger()

migration_script_path = \
    os.path.normpath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'helper_1_1_152_to_1_1_153.py'))

logger.info('script path {}'.format(migration_script_path))
ret = subprocess.run(
    compose_cmd(
        ["su -c 'python3 {}' indy".format(migration_script_path)]
    ),
    shell=True,
    timeout=TIMEOUT)

if ret.returncode != 0:
    msg = 'Migration failed: script returned {}'.format(ret.returncode)
    logger.error(msg)
    raise Exception(msg)
