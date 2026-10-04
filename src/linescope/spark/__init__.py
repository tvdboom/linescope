"""LineScope.

Author: Mavs
Description: Optional Spark driver action, plan, and metric observation.

"""

from linescope.spark.listener import SparkIntegration
from linescope.spark.plans import capture_query, parse_plan

__all__ = ["SparkIntegration", "capture_query", "parse_plan"]
