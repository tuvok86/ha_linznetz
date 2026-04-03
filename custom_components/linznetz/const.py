"""Constants for linznetz."""
# Base component constants
NAME = "LINZ NETZ"
DOMAIN = "linznetz"
VERSION = "1.0.0"

# Platforms
SENSOR = "sensor"
PLATFORMS = [SENSOR]

# Services
SERVICE_IMPORT_REPORT = "import_report"
END_TIME_KEY = "Datum bis"
START_TIME_KEY = "Datum von"

# Configuration and options
DEFAULT_NAME = "SmartMeter"
CONF_METER_POINT_NUMBER = "meter_point_number"
CONF_NAME = "name"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Update interval in hours for automatic data fetching
DEFAULT_UPDATE_INTERVAL_HOURS = 6
