/** What this port is: the service rules as they stood at one release, nothing newer. */

export const PORTED_SERVICE_VERSION = "1.0.0";

export const PORTED_SERVICES = ["routing", "grading", "attention", "payouts", "analytics", "delivery"] as const;

/** Later releases whose rules are not modelled here, with the version that introduced them. */
export const NOT_PORTED = [
  ["rubric versions", "2.0.0"],
  ["calibration and tiers", "3.0.0"],
  ["consensus and adjudication", "4.0.0"],
  ["ops overview and tick", "5.0.0"],
] as const;
