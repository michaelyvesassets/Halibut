"""Version identifiers recorded in every run manifest."""

ENGINE_VERSION = "0.3.0"
CONFIG_SCHEMA_VERSION = "1.2"  # current; older versions listed below stay loadable
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1", "1.2")

RESULT_TYPE = "synthetic_simulation"
SYNTHETIC_DISCLAIMER = (
    "Synthetic simulation output. All parameters are illustrative and have not been "
    "calibrated or validated against empirical data. Results describe the behaviour "
    "of the model under its stated assumptions and must not be read as market forecasts."
)
