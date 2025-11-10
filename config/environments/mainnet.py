from .base import BaseEnvironmentSettings


class MainnetSettings(BaseEnvironmentSettings):
    """Mainnet environment settings."""

    # Override defaults for mainnet
    DEFAULT_PROXY: str = "https://gateway.dharitri.org"
    DEFAULT_API: str = "https://api.dharitri.org"
    GRAPHQL: str = "https://graph.dharitrix.com/graphql"

    DEFAULT_CONFIG_SAVE_PATH: str = "deploy/configs-mainnet"
