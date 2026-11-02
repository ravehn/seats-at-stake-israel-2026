"""The test battery (T01–T16). Each test is a pure function Features -> TestResult; see base.py."""

from electionforensics.battery.base import BATTERY, TestResult, run_battery

__all__ = ["BATTERY", "TestResult", "run_battery"]
