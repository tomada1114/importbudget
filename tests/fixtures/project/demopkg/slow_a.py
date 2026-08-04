"""Expensive leaf module: sleeps so its self time dominates the profile."""

import time

time.sleep(0.03)

VALUE = "slow_a"
