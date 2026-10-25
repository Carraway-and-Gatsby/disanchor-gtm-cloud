#!/usr/bin/env python3
"""Run synthetic feedback flow in temporary state, blocking socket access."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def block_network(event, args):
    if event.startswith('socket.'):
        raise RuntimeError('synthetic_flow_network_forbidden')


sys.addaudithook(block_network)
from tests.test_feedback_flow import FeedbackFlowTests

case = FeedbackFlowTests('test_isolated_four_cycle_round_trip')
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(unittest.TestSuite([case]))
if not result.wasSuccessful():
    raise SystemExit(1)
print(json.dumps(case.report, ensure_ascii=False, indent=2))
