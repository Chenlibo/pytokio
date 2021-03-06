#!/usr/bin/env python
"""
Test the bin/darshan_bad_ost.py tool
"""

import nose.tools
import test_tools_darshan
import tokiotest
import tokiobin.find_darshanlogs

def wrap_function(test_input):
    """Allow named args to pass through nosetests
    """
    print "Running:", test_input['descr']

    argv = []
    if test_input['params']['datetime_start'] is not None:
        argv += ['--start', test_input['params']['datetime_start'].strftime("%Y-%m-%d")]
    if test_input['params']['datetime_end'] is not None:
        argv += ['--end', test_input['params']['datetime_end'].strftime("%Y-%m-%d")]
    if test_input['params']['username'] is not None:
        argv += ['--username', test_input['params']['username']]
    if test_input['params']['jobid'] is not None:
        argv += ['--jobid', str(test_input['params']['jobid'])]
    if 'which' in test_input['params']:
        argv += ['--load', test_input['params']['which']]
    argv += [test_input['params']['darshan_log_dir']]

    print "Test args:", argv

    expected_exception = test_input.get('expect_exception')
    if expected_exception:
        # assert_raises doesn't seem to work correctly here
#       nose.tools.assert_raises(expected_exception,
#                                tokiotest.run_bin(tokiobin.find_darshanlogs, argv))
        caught = False
        try:
            output_str = tokiotest.run_bin(tokiobin.find_darshanlogs, argv)
        except expected_exception:
            caught = True
        assert caught

    else:
        output_str = tokiotest.run_bin(tokiobin.find_darshanlogs, argv)
        results = output_str.splitlines()
        assert (test_input['pass_criteria'])(results)

def test_bin_find_darshanlogs():
    """
    Run the tools.darshan test cases through the CLI interface
    """

    for test in test_tools_darshan.TEST_MATRIX:
        test_func = wrap_function
        test_func.description = 'bin/find_darshanlogs.py: ' + test['descr']
        yield test_func, test
